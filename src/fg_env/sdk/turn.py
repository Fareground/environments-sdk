"""One agent's turn: what it reads, which tools it has, and how each call is applied.

A turn may have a wall-clock deadline: past it the turn is closed and every later call is
refused. In an atomic stage the turn's actions stay open until it ends: triggers, reactions
and invariants wait, and a turn that breaks the stage's `valid` rules is undone as a whole.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple

from ..entity import Entity
from .actions import ACTION_BUDGET, ToolSpec, stage_actions
from .contract import StageSpec
from .errors import RunError
from .expr import ExprError, compile_expr, shared_budget, truthy
from .measure import Stats
from .session import END_TURN, ToolResult
from .template import compile_template, format_value
from .world import _plain

if TYPE_CHECKING:
    from .exposure import Exposure
    from .runtime import Env

__all__ = ["Memory", "Turn", "entity_dict"]


class Memory:
    """What the engine remembers per agent between turns."""

    __slots__ = ("cursor", "views", "turns")

    def __init__(self) -> None:
        self.cursor = 0
        self.views: Dict[str, str] = {}
        self.turns = 0


_INVALID = {"error": "invalid"}
_REJECTED = {"error": "rejected"}
_ENDED = {"error": "ended"}
_TIMEOUT = {"error": "timeout"}
_UNDONE = {"error": "undone"}
#: What a closed turn reads instead of its brief or update (its participant has been left behind).
_CLOSED_TEXT = "This turn is over."


class Turn:
    """A live turn. ``peek`` turns (previews) read the world but never change the run:
    they take no turn number and leave the agent's memory untouched."""

    def __init__(self, env: "Env", actor: Entity, stage: StageSpec, reason: str, staged: bool, peek: bool = False,
                 kind: str = "turn"):
        self.env = env
        self.actor = actor
        self.stage = stage
        self.reason = reason
        self.staged = staged
        self.peek = peek
        self.round = env.world.round
        memory = env._memories.get(actor.id) if peek else env._memory(actor.id)
        memory = memory or Memory()
        self._since = memory.cursor
        self._views = dict(memory.views) if peek else memory.views
        self._brief: Optional[str] = None
        self._update: Optional[str] = None
        self.calls_left = stage.max_calls
        self.actions_left = stage.max_actions
        self.done = False
        self.used: Dict[str, int] = {}
        self.intents: List[Tuple[str, Dict[str, Any]]] = []
        #: What this agent already did (sequential) or submitted (simultaneous) this turn, as $pending.
        self.pending: List[Dict[str, Any]] = []
        self.stats = Stats(wakes=1)
        #: Clock time taken by this turn's actions (continuous clock).
        self.elapsed = 0.0
        self._offered = False
        self._tools: Optional[List[ToolSpec]] = None
        #: Wall-clock seconds this turn may take (None: no limit); the deadline is set when it starts.
        self.time_limit = env._time_limit(stage, actor)
        self.deadline: Optional[float] = None
        self.timed_out = False
        #: Closed from outside (deadline, a failing run): its participant is no longer waited for.
        self.closed = False
        #: Calls in progress; the engine waits for them to return before moving on from a closed turn.
        self.busy = 0
        #: Atomic turns: the journal position the turn's changes are undone to, until it settles.
        self.atomic = (stage.atomic or bool(stage.valid)) and not staged and not peek
        self._mark: Optional[int] = env.world.journal.mark() if self.atomic else None
        self._counted: List[str] = []
        if peek:
            self.number = env._turn_count + 1
        else:
            env._turn_count += 1
            self.number = env._turn_count  # assigned in deterministic order, before any concurrency
        exposures = env.world.exposures
        self.exposure: Optional["Exposure"] = exposures.open(self, kind) if exposures is not None and not peek else None

    # -- time ----------------------------------------------------------------------

    def start_clock(self) -> None:
        if self.time_limit is not None and self.deadline is None:
            self.deadline = time.monotonic() + self.time_limit

    def time_left(self) -> Optional[float]:
        if self.deadline is None:
            return self.time_limit
        return max(0.0, self.deadline - time.monotonic())

    def expired(self, now: Optional[float] = None) -> bool:
        """True once the deadline has passed; the first time, the turn is closed as timed out (call under the lock)."""
        if not self.timed_out and self.deadline is not None and (time.monotonic() if now is None else now) >= self.deadline:
            self.timed_out = True
            self.stats.timeouts = 1
            self.close()
        return self.timed_out

    def close(self) -> None:
        self.done = self.closed = True

    # Brief and update render on first read, so coded participants that never read them cost nothing.

    @property
    def brief(self) -> str:
        with self.env._lock:
            if self._brief is None:
                if self.closed:
                    return _CLOSED_TEXT
                with shared_budget(ACTION_BUDGET, "brief"):
                    self._brief = self.env._brief(self.actor)
                self.stats.brief_chars = len(self._brief)
                self.stats.brief_reads = 1
                if self.exposure is not None:
                    self.exposure.read_brief(self._brief)
            return self._brief

    @property
    def update(self) -> str:
        with self.env._lock:
            if self._update is None:
                if self.closed:
                    return _CLOSED_TEXT
                shown = _shown() if self.exposure is not None else None
                with shared_budget(ACTION_BUDGET, "update"):
                    self._update = self.env.perception.update(self.actor, self.stage, self.reason, self._since,
                                                              self._views, self.time_limit, shown)
                self.stats.update_chars = len(self._update)
                self.stats.update_reads = 1
                if self.exposure is not None and shown is not None:
                    self.exposure.read_update(self._update, shown)
            return self._update

    # -- tools ------------------------------------------------------------------

    def _legal(self) -> List[str]:
        if self.actions_left <= 0:
            return []
        env = self.env
        names = stage_actions(env.contract, self.stage, self.actor.entity_type)
        used_round = env._used_round.get(self.actor.id, {})
        return [n for n in names if env.actions.blocked(self.actor, n, self.used, used_round) is None]

    def tools(self) -> List[ToolSpec]:
        if self.done:
            return []
        if self._tools is not None:  # nothing changed since the last look (reset by every call)
            return self._tools
        env = self.env
        with env._lock:
            tools = [env.actions.tool(self.actor, name, self.staged) for name in self._legal()]
        looks = env.perception.look_views(self.actor, self.stage)
        if looks:
            tools.append(ToolSpec("look", "Show one of these views: " + ", ".join(looks) + ".", {
                "type": "object", "properties": {"view": {"type": "string", "enum": looks}},
                "required": ["view"], "additionalProperties": False}, "look"))
        if env._inspectable:
            tools.append(ToolSpec("inspect", "Details of one entity by id (uses one tool call).", {
                "type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"],
                "additionalProperties": False}, "look"))
        if not self._must_act_now(tools):
            if self.staged:
                end_text = "Finish your turn (your choices are submitted)."
            elif self.atomic and self.stage.valid:
                end_text = "Finish your turn (your actions are checked together; a turn that is not allowed is undone)."
            else:
                end_text = "Finish your turn."
            tools.append(ToolSpec(END_TURN, end_text, {"type": "object", "properties": {}, "additionalProperties": False},
                                  "end", True))
        if not self._offered:
            self.stats.tools_offered += len(tools)
            self._offered = True
        self._tools = tools
        return tools

    def _must_act_now(self, tools: List[ToolSpec]) -> bool:
        acted = self.actions_left < self.stage.max_actions or bool(self.intents)
        return self.stage.must_act and not acted and any(t.kind == "act" for t in tools)

    # -- calls -------------------------------------------------------------------

    def call(self, name: Any, args: Any) -> ToolResult:
        env = self.env
        with env._lock:
            self._tools = None
            self.busy += 1
            try:
                result = self._call(name, args)
            finally:
                self.busy -= 1
                env._signal.notify_all()
            if self.exposure is not None:
                self.exposure.called(name, args, result)
            return result

    def refusal(self) -> Optional[ToolResult]:
        """Why a call cannot be made now (the turn is over or out of time), or None (call under the lock)."""
        if self.expired():
            return ToolResult(False, "Your time for this turn ran out; nothing was done.", True, dict(_TIMEOUT))
        if self.done:
            return ToolResult(False, "Your turn is already over; nothing was done.", True, dict(_ENDED))
        return None

    def _call(self, name: Any, args: Any) -> ToolResult:
        refused = self.refusal()
        if refused is not None:
            return refused
        if self.calls_left <= 0:
            self.done = True
            return ToolResult(False, "No tool calls left this turn; your turn is over.", True)
        self.calls_left -= 1
        self.stats.calls += 1
        env = self.env
        if not isinstance(name, str):
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"A tool name is text, got {type(name).__name__}. Available actions: "
                                                 f"{', '.join(self._legal()) or 'none'}.", data=_INVALID))
        if args is not None and not isinstance(args, Mapping):
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"{name} was not done: arguments must be an object of named values, "
                                                 f"got {type(args).__name__}.", data=_INVALID))
        if name == END_TURN:
            if self.stage.must_act and self.actions_left == self.stage.max_actions and not self.intents and self._legal():
                self.stats.invalid_calls += 1
                return self._after(ToolResult(False, f"You must act during {self.stage.name}. Available actions: "
                                                     f"{', '.join(self._legal())}.", data=_INVALID))
            why = self.settle()
            if why is not None:
                return self._after(self._undone(why))
            self.done = True
            return ToolResult(True, "Turn ended.", True)
        if name == "look":
            return self._look(args)
        if name == "inspect":
            return self._inspect(args)
        spec = env.contract.actions.get(name)
        available = stage_actions(env.contract, self.stage, self.actor.entity_type)
        if spec is None or name not in available:
            self.stats.invalid_calls += 1
            legal = ", ".join(self._legal()) or "none"
            why = "is not a tool" if spec is None else f"is not available during {self.stage.name}"
            return self._after(ToolResult(False, f"'{name}' {why}. Available actions: {legal}.", data=_INVALID))
        if self.actions_left <= 0:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, "You have no actions left this turn; call end_turn.", data=_INVALID))
        blocked = env.actions.blocked(self.actor, name, self.used, env._used_round.get(self.actor.id, {}))
        if blocked:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"You cannot {name.replace('_', ' ')} now: {blocked}.", data=_INVALID))
        params, problem = env.actions.validate(self.actor, name, args)
        if problem:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"{name} was not done: {problem}. Correct the arguments and call again.",
                                          data=_INVALID))
        if self.staged:
            refusal = env.actions.dry_run(self.actor, name, params)
            if refusal is not None:
                self.stats.rejected_actions += 1
                return self._after(ToolResult(False, refusal, data=_REJECTED))
            self.intents.append((name, dict(args or {})))
            self.pending.append({"action": name, **_plain(params)})
            self._count(name)
            ended = env.actions.ends_turn(self.actor, name, params) or self.actions_left <= 0
            text = f"Submitted {name.replace('_', ' ')}{_args_text(params)}; it resolves when everyone has chosen."
            return self._after(ToolResult(True, text, ended))
        outcome = env.actions.apply(self.actor, name, params)
        if not outcome.ok:
            self.stats.rejected_actions += 1
            return self._after(ToolResult(False, outcome.text, data=_REJECTED))
        self._count(name)
        self.pending.append({"action": name, **_plain(params)})
        if env.world.continuous:
            self.elapsed += env.actions.duration(self.actor, name, params)
        self.committed(f"actions.{name}", react=True)
        self.stats.actions += 1
        ended = env.actions.ends_turn(self.actor, name, params) or self.actions_left <= 0 or env.world.end_request is not None
        if ended:
            why = self.settle()
            if why is not None:
                return self._after(self._undone(why))
        return self._after(ToolResult(True, outcome.text, ended, {"success": outcome.success}))

    def _count(self, name: str) -> None:
        self.used[name] = self.used.get(name, 0) + 1
        per_round = self.env._used_round.setdefault(self.actor.id, {})
        per_round[name] = per_round.get(name, 0) + 1
        self.actions_left -= 1
        if self.atomic:
            self._counted.append(name)

    # -- atomic turns ------------------------------------------------------------------

    def committed(self, path: str, react: bool) -> None:
        """A change inside the turn has applied: settle it now, or — atomic turns — when the turn ends."""
        if self._mark is not None:
            return
        self.env._after_commit(path)
        if react:
            self.env.happenings.react(self.stage)

    def settle(self) -> Optional[str]:
        """Atomic turns: commit a turn that meets `valid` (then run what waited for it), or undo every
        action of the turn and say why. A turn that took no action has nothing to check. Call under the lock."""
        if self._mark is None:
            return None
        why = self.invalid() if self._counted else None
        if why is not None:
            self._undo()
            return why
        self._mark = None
        self.env._after_commit(f"stages.{self.stage.name}")
        self.env.happenings.react(self.stage)
        return None

    def settle_at_end(self) -> None:
        """Settle an atomic turn the participant left open (it returned, timed out or ran out of calls). An
        undone turn is reported to the agent as news."""
        env = self.env
        with env._lock:
            if self._mark is None:
                return
            with env.world.turn_context(None, self.pending):
                why = self.settle()
            if why is not None:
                env.world.emit("outcome", f"Your turn was undone: {why}.", actor=self.actor.id, to=(self.actor.id,),
                               data={"ok": False, "undone": True})
                env.world.journal.clear()

    def invalid(self) -> Optional[str]:
        """Why the turn as played breaks the stage's `valid` rules, or None when it meets them."""
        env, path = self.env, f"stages.{self.stage.name}.valid"
        scope = env.world.scope(actor=self.actor)
        with shared_budget(ACTION_BUDGET, path):
            for index, condition in enumerate(self.stage.valid):
                try:
                    if truthy(compile_expr(condition.expr)(scope)):
                        continue
                    why = compile_template(condition.why, None).render(scope) if condition.why else ""
                except ExprError as exc:
                    raise RunError(str(exc), f"{path}[{index}]") from None
                return str(why).strip().rstrip(".") or "this turn is not allowed"
        return None

    def _undo(self) -> None:
        env, undone = self.env, self.stats.actions
        assert self._mark is not None
        env.world.journal.rollback(self._mark)
        per_round = env._used_round.get(self.actor.id, {})
        for name in self._counted:
            per_round[name] = per_round.get(name, 1) - 1
        self._counted.clear()
        self.used.clear()
        del self.pending[:]  # the same list $pending reads
        self.actions_left = self.stage.max_actions
        self.elapsed = 0.0
        self.stats.actions -= undone
        self.stats.rejected_actions += undone
        self.stats.undone_turns += 1

    def _undone(self, why: str) -> ToolResult:
        return ToolResult(False, f"That turn is not allowed: {why}. Everything you did this turn was undone; "
                                 "play your turn again.", data=dict(_UNDONE))

    def _after(self, result: ToolResult) -> ToolResult:
        if result.ended:
            self.done = True
        elif self.calls_left <= 0:
            self.done = True
            result.ended = True
            result.text += " (No tool calls left; your turn is over.)"
        return result

    def _may_inspect(self, target: Entity) -> bool:
        rule = self.env._inspect_rule(target.entity_type)
        if isinstance(rule, bool):
            return rule or target.id == self.actor.id
        try:
            return target.id == self.actor.id or truthy(
                compile_expr(rule)(self.env.world.scope(viewer=self.actor, it=target)))
        except ExprError as exc:
            raise RunError(str(exc), f"types.{target.entity_type}.inspect") from None

    def _look(self, args: Optional[Mapping[str, Any]]) -> ToolResult:
        env = self.env
        name = (args or {}).get("view")
        looks = env.perception.look_views(self.actor, self.stage)
        if not isinstance(name, str) or name not in looks:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"view must be one of: {', '.join(looks) or 'none'}.", data=_INVALID))
        shown = _shown() if self.exposure is not None else None
        with shared_budget(ACTION_BUDGET, f"views.{name}"):
            text = env.perception.render_view(name, env.contract.views[name], self.actor, shown)
        if self.exposure is not None and shown is not None and text is not None:
            shown.views.append((name, text))
            self.exposure.looked(shown)
        return self._after(ToolResult(True, text or "Nothing to show."))

    def _inspect(self, args: Optional[Mapping[str, Any]]) -> ToolResult:
        env = self.env
        wanted = (args or {}).get("id")
        target = env.world.entity(wanted) if isinstance(wanted, str) else None
        if target is None or not target.alive or not self._may_inspect(target):
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, "No entity with that id is available to inspect.", data=_INVALID))
        specs = env.contract.props_of(target.entity_type)
        own = target.id == self.actor.id
        shown = [f"{k}: {format_value(v)}" for k, v in target.properties.items()
                 if own or not specs.get(k) or not specs[k].private]
        where = f" at {format_value(target.location_id)}" if target.location_id is not None else ""
        text = f"{target.name} [{target.id}] ({target.entity_type}){where}" + ("\n" + "\n".join(shown) if shown else "")
        return self._after(ToolResult(True, text))


def _shown() -> Any:
    from .exposure import Shown

    return Shown()


def entity_dict(entity: Entity) -> Dict[str, Any]:
    return {"id": entity.id, "name": entity.name, "type": entity.entity_type, "alive": entity.alive,
            "at": entity.location_id, "props": _plain(dict(entity.properties))}


def _args_text(params: Mapping[str, Any]) -> str:
    parts = [f"{k}={format_value(v)}" for k, v in params.items() if v is not None]
    return f" ({', '.join(parts)})" if parts else ""
