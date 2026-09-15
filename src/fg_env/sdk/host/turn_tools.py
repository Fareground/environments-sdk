"""Host tools inside a turn: offered beside the agent's actions, usable in any stage, without
using up an action.

Host tools (``host_tool``) and memory tools (``recall``, ``note``) are declared as ordinary
private actions, so a contract checks, previews and runs with the plain engine. Until the
runtime offers them natively, :func:`wrap` gives each participant a :class:`HostWake` that
lists them as ``look`` tools and applies them at once — in simultaneous stages too — counting
only a tool call. Their effects touch only the caller's own properties and the tape, and never
write to the shared log, so concurrent turns stay deterministic. The host call itself runs
before the run's lock is taken, so slow hosts do not block other agents' turns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Tuple

from ..actions import ACTION_BUDGET, ToolSpec
from ..errors import RunError
from ..expr import ExprError, shared_budget
from ..participants import resolve_participant
from ..session import ToolResult, Wake
from ..template import compile_template
from ..world import Abort

if TYPE_CHECKING:
    from ...entity import Entity
    from ..runtime import Env
    from ..turn import Turn

__all__ = ["TurnTool", "turn_tools", "HostWake", "wrap"]

_INVALID = {"error": "invalid"}


@dataclass(frozen=True)
class TurnTool:
    """A contract action offered as an in-turn tool."""

    action: str
    mechanism: str
    stages: Optional[Tuple[str, ...]] = None
    #: ``prefetch(env, mechanism, actor, params)`` asks the host before the tool applies.
    prefetch: Optional[Callable[["Env", str, "Entity", Mapping[str, Any]], None]] = None


def turn_tools(contract: Any) -> Dict[str, TurnTool]:
    """The in-turn tools a contract declares, by tool name."""
    from ..mechanisms.memory import MemoryConfig
    from .tools import HostToolConfig, prefetch

    tools: Dict[str, TurnTool] = {}
    for name, raw in contract.mechanisms.items():
        if not isinstance(raw, Mapping):
            continue
        config = {k: v for k, v in raw.items() if k != "kind"}
        if raw.get("kind") == "host_tool":
            stages = HostToolConfig.model_validate(config).stages
            tools[name] = TurnTool(name, name, tuple(stages) if stages else None, prefetch)
        elif raw.get("kind") == "memory":
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
        base = [tool for tool in super().tools if tool.name not in self._extras]
        turn = self._turn
        if turn.done or turn.calls_left <= 0:
            return base
        extra = [self._spec(name) for name in self._extras if self._available(name) is None]
        return [t for t in base if t.kind != "end"] + extra + [t for t in base if t.kind == "end"]

    def call(self, name: str, args: Optional[Dict[str, Any]] = None) -> ToolResult:
        if name not in self._extras:
            return super().call(name, args)
        turn, env = self._turn, self._turn.env
        if turn.done:
            return ToolResult(False, "Your turn is already over; nothing was done.", True, {"error": "ended"})
        if turn.calls_left <= 0:
            turn.done = True
            return ToolResult(False, "No tool calls left this turn; your turn is over.", True)
        with env._lock:
            turn._tools = None
            turn.calls_left -= 1
            turn.stats.calls += 1
            why = self._available(name)
            params, problem = ({}, None) if why else env.actions.validate(turn.actor, name, args)
        if why or problem:
            turn.stats.invalid_calls += 1
            text = f"You cannot {name.replace('_', ' ')} now: {why}." if why else \
                f"{name} was not done: {problem}. Correct the arguments and call again."
            return turn._after(ToolResult(False, text, data=dict(_INVALID)))
        tool = self._extras[name]
        if tool.prefetch is not None:
            tool.prefetch(env, tool.mechanism, turn.actor, params)
        with env._lock:
            return turn._after(self._apply(name, params))

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
        turn, env = self._turn, self._turn.env
        world, spec, path = env.world, env.contract.actions[name], f"actions.{name}"
        vars: Dict[str, Any] = {"actor": turn.actor, "params": params}
        mark = world.journal.mark()
        try:
            with shared_budget(ACTION_BUDGET, path):
                env.effects.run(spec.do, vars, f"{path}.do")
                text = compile_template(spec.outcome, None).render(world.scope(**vars)) if spec.outcome else "Done."
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
        self._used[name] = self._used.get(name, 0) + 1
        env._after_commit(path)
        return ToolResult(True, text)


class _Extended:
    """A participant whose wakes offer the in-turn tools."""

    def __init__(self, inner: Callable[[Wake], Any], tools: Mapping[str, TurnTool]):
        self.inner = inner
        self.tools = tools
        self.concurrent = getattr(inner, "concurrent", True)

    def __call__(self, wake: Wake) -> Any:
        return self.inner(HostWake(wake._turn, self.tools))

    def __repr__(self) -> str:
        return f"HostTools({self.inner!r})"


class _Default:
    """What the engine would use for an agent nobody named: its type's policy, else random."""

    concurrent = False

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
        inner = value if callable(value) else resolve_participant(value, env.contract, seed)
        wrapped[key] = _Extended(inner, tools)
    wrapped.setdefault("*", _Default(env, tools))
    return wrapped
