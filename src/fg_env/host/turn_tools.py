"""Host tools inside a turn: offered beside the agent's actions, usable in any stage, without
using up an action.

Host tools (``host.tool``) and memory tools (``recall``, ``note``) are declared as ordinary
private actions, so a contract checks, previews and runs with the plain engine. Every run offers
them (``Env.run`` passes each participant through :func:`offer`): a :class:`HostWake`
lists them as ``look`` tools and applies them at once — in simultaneous stages too — counting
only a tool call. Their effects touch only the caller's own properties and the tape, and never
write to the shared log, so concurrent turns stay deterministic. The host call itself runs
before the run's lock is taken, so slow hosts do not block other agents' turns. A turn that runs out of time
while its host answers refuses the call and takes that answer off the tape: the tape holds only answers the run
used, so replays stay exact.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Tuple

from ..actions.faults import guarded, refused_text
from ..actions.book import ACTION_BUDGET, ToolSpec
from ..runtime.driving import runs_concurrently
from ..errors import RunError
from ..expr import ExprError, shared_budget
from ..participants import resolve_participant
from ..registry import config_data, use_key
from ..runtime.session import ToolResult, Wake
from ..expr.template import compile_template
from ..world.live import Abort
from .tape import discard

if TYPE_CHECKING:
    from ..world.entity import Entity
    from ..runtime.env import Env
    from ..runtime.turn import Turn

__all__ = ["TurnTool", "turn_tools", "HostWake", "offer", "wrap"]

_INVALID = {"error": "invalid"}


@dataclass(frozen=True)
class TurnTool:
    """A contract action offered as an in-turn tool."""

    action: str
    mechanism: str
    stages: Optional[Tuple[str, ...]] = None
    #: ``prefetch(env, mechanism, actor, params)`` asks the host before the tool applies, returning the tape key
    #: of an answer it added (None when it added none).
    prefetch: Optional[Callable[["Env", str, "Entity", Mapping[str, Any]], Optional[str]]] = None


def turn_tools(contract: Any) -> Dict[str, TurnTool]:
    """The in-turn tools a contract declares, by tool name."""
    from ..mechanisms.memory import MemoryConfig
    from .tools import HostToolConfig, prefetch

    tools: Dict[str, TurnTool] = {}
    for name, raw in contract.mechanisms.items():
        key = use_key(raw)
        if key is None:
            continue
        config = config_data(raw)
        if key == "host.tool":
            stages = HostToolConfig.model_validate(config).stages
            tools[name] = TurnTool(name, name, tuple(stages) if stages else None, prefetch)
        elif key == "mind.memory":
            memory = MemoryConfig.model_validate(config)
            for tool in (memory.recall, memory.note):
                if tool:
                    tools[tool] = TurnTool(tool, name, tuple(memory.stages) if memory.stages else None)
    return {name: tool for name, tool in tools.items() if name in contract.actions}


class HostWake(Wake):
    """A :class:`Wake` that also offers the contract's in-turn tools."""

    def __init__(self, turn: "Turn", tools: Mapping[str, TurnTool]):
        super().__init__(turn)
        self._extras = dict(tools)
        self._used: Dict[str, int] = {}

    @property
    def tools(self) -> List[ToolSpec]:
        turn = self._turn
        base = [tool for tool in turn.tools() if tool.name not in self._extras]
        if turn.done or turn.calls_left <= 0:
            return self._offer(base)
        extra = [self._spec(name) for name in self._extras if self._available(name) is None]
        return self._offer([t for t in base if t.kind != "end"] + extra + [t for t in base if t.kind == "end"])

    def call(self, name: str, args: Optional[Dict[str, Any]] = None) -> ToolResult:
        if name not in self._extras:
            return super().call(name, args)
        result = self._host_call(name, args)
        turn = self._turn
        if turn.exposure is not None:
            with turn.env._lock:
                turn.exposure.called(name, args, result)
        return result

    def _host_call(self, name: str, args: Optional[Dict[str, Any]]) -> ToolResult:
        turn, env = self._turn, self._turn.env
        step = ("call", name, dict(args) if isinstance(args, Mapping) else args)
        with env._lock:
            refused = turn.refusal()
            if refused is not None:
                return refused
            if turn.calls_left <= 0:
                turn.record(*step)
                turn.done = True
                return ToolResult(False, "No tool calls left this turn; your turn is over.", True)
            why = self._available(name)
            params, problem = ({}, None) if why else env.actions.validate(turn.actor, name, args)
            if why or problem:
                self._spend(step)
                turn.stats.invalid_calls += 1
                text = f"You cannot {name.replace('_', ' ')} now: {why}." if why else \
                    f"{name} was not done: {problem}. Correct the arguments and call again."
                return turn._after(ToolResult(False, text, data=dict(_INVALID)))
        tool = self._extras[name]
        added = tool.prefetch(env, tool.mechanism, turn.actor, params) if tool.prefetch is not None else None
        with env._lock:
            refused = turn.refusal()  # the turn may have run out of time while the host answered
            if refused is not None:
                if added is not None:
                    discard(env.world, added)  # the run never used this answer, so no replay may meet it
                return refused
            self._spend(step)
            return turn._after(self._apply(name, params))

    def _spend(self, step: Tuple[Any, ...]) -> None:
        """The call takes effect: record it on the tape and count it (under the run's lock)."""
        turn = self._turn
        turn.record(*step)
        turn._tools = None
        turn.calls_left -= 1
        turn.stats.calls += 1

    def _available(self, name: str) -> Optional[str]:
        turn, env = self._turn, self._turn.env
        tool = self._extras[name]
        spec = env.contract.actions[name]
        if tool.stages is not None and turn.stage.name not in tool.stages:
            return f"{name} is not available during {turn.stage.name}"
        allowed = [spec.by] if isinstance(spec.by, str) else spec.by
        if not any(env.contract.is_a(turn.actor.entity_type, kind) for kind in allowed):
            return f"{name} is not a tool for a {turn.actor.entity_type}"
        with env._lock:
            return env.actions.blocked(turn.actor, name, self._used, {})

    def _spec(self, name: str) -> ToolSpec:
        env = self._turn.env
        with env._lock:
            spec = env.actions.tool(self._turn.actor, name, staged=False)
        return ToolSpec(spec.name, spec.description, spec.input_schema, "look", False)

    def _apply(self, name: str, params: Dict[str, Any]) -> ToolResult:
        """Apply and commit the call; a rule that fails or an invariant it breaks refuses it (see :mod:`fg_env.actions.faults`)."""
        turn = self._turn
        result, fault = guarded(turn.env, lambda: self._commit(name, params))
        if result is None:
            assert fault is not None
            turn.stats.rejected_actions += 1
            turn.stats.faulted_actions += 1
            return ToolResult(False, refused_text(name, fault), data={"error": "rejected"})
        return result

    def _commit(self, name: str, params: Dict[str, Any]) -> ToolResult:
        turn, env = self._turn, self._turn.env
        world, spec, path = env.world, env.contract.actions[name], f"actions.{name}"
        vars: Dict[str, Any] = {"actor": turn.actor, "params": params}
        mark = world.journal.mark()
        try:
            with shared_budget(ACTION_BUDGET, path):
                env.effects.run(spec.do, vars, f"{path}.do")
                text = compile_template(spec.outcome, None).render(world.scope(viewer=turn.actor, **vars)) \
                    if spec.outcome else "Done."
        except Abort as abort:
            world.journal.rollback(mark)
            turn.stats.rejected_actions += 1
            return ToolResult(False, abort.reason, data={"error": "rejected"})
        except ExprError as exc:
            world.journal.rollback(mark)
            raise RunError(str(exc), path) from None
        except BaseException:
            world.journal.rollback(mark)
            raise
        turn.committed(path)
        self._used[name] = self._used.get(name, 0) + 1
        return ToolResult(True, text)


class _Extended:
    """A participant whose wakes offer the in-turn tools."""

    def __init__(self, inner: Callable[[Wake], Any], tools: Mapping[str, TurnTool]):
        self.inner = inner
        self.__wrapped__ = inner  # an async participant is still seen as async through the wrapper
        self.tools = tools
        self.concurrent = runs_concurrently(inner)

    def __call__(self, wake: Wake) -> Any:
        return self.inner(HostWake(wake._turn, self.tools))

    def __repr__(self) -> str:
        return f"HostTools({self.inner!r})"


def offer(participant: Callable[[Wake], Any], tools: Mapping[str, TurnTool]) -> Callable[[Wake], Any]:
    """``participant``, offered ``tools`` in its wakes (unchanged if it already is)."""
    return participant if isinstance(participant, (_Extended, _Default)) else _Extended(participant, tools)


class _Default:
    """What the engine would use for an agent nobody named: its type's policy, else random."""

    def __init__(self, env: "Env", tools: Mapping[str, TurnTool]):
        self.env = env
        self.tools = tools
        self._resolved: Dict[str, Callable[[Wake], Any]] = {}

    def __call__(self, wake: Wake) -> Any:
        actor = wake._turn.actor
        inner = self._resolved.get(actor.id)
        if inner is None:
            contract = self.env.contract
            lineage = list(reversed(contract.lineage(actor.entity_type)))
            policy = next((contract.types[kind].policy for kind in lineage if contract.types[kind].policy), None)
            inner = self._resolved[actor.id] = resolve_participant(policy or "random", contract,
                                                                   self.env.seeds.derive("participant"))
        return inner(HostWake(wake._turn, self.tools))


def wrap(env: "Env", participants: Any = None) -> Any:
    """Participants for ``env.run`` whose wakes offer the contract's in-turn host tools."""
    tools = turn_tools(env.contract)
    if not tools:
        return participants
    if participants is None:
        spec: Dict[str, Any] = {}
    elif callable(participants) or isinstance(participants, str):
        spec = {"*": participants}
    elif isinstance(participants, Mapping):
        spec = dict(participants)
    else:
        raise TypeError("participants must be a callable, a string, or a mapping")
    seed = env.seeds.derive("participant")
    wrapped: Dict[str, Any] = {}
    for key, value in spec.items():
        if isinstance(value, (_Extended, _Default)):  # already offers the tools
            wrapped[key] = value
            continue
        inner = value if callable(value) else resolve_participant(value, env.contract, seed)
        wrapped[key] = _Extended(inner, tools)
    wrapped.setdefault("*", _Default(env, tools))
    return wrapped
