"""One agent's turn: what it reads, which tools it has, and how each call is applied.

A turn may have a wall-clock deadline: past it the turn is closed and every later call is
refused. In a stage with `valid` rules the turn's actions stay open until it ends: events, reactions
and invariants wait, and a turn that breaks the rules is undone as a whole.
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from ..actions.book import ACTION_BUDGET, ToolSpec, stage_actions
from ..actions.faults import guarded, refused_text
from ..actions.params import REFUSED_ARGS
from ..actions.reads import (
    READS,
    UNCHANGED,
    find_target,
    handle_filter,
    inspect_text,
    inspect_tool,
    look_tool,
    may_inspect,
    reads_refused,
)
from ..actions.tool_text import cut_text, offer_text
from ..assets.delivery import Attachment
from ..contract import MAX_TURN_ACTIONS, MAX_TURN_CALLS, ActionSpec, StageSpec
from ..errors import RunError
from ..expr import ExprError, compile_expr, shared_budget, truthy
from ..expr.objects import Entity
from ..expr.template import compile_template, entity_handles, format_value
from ..world.build import whole_setting
from ..world.live import _plain
from .measure import Stats
from .session import END_TURN, ToolResult
from .state import Memory

if TYPE_CHECKING:
    from .env import Env
    from .exposure import Exposure

__all__ = ["Turn", "entity_dict"]


_INVALID = {"error": "invalid"}
_REJECTED = {"error": "rejected"}
_ENDED = {"error": "ended"}
_TIMEOUT = {"error": "timeout"}
_UNDONE = {"error": "undone"}
#: A call refused after it drew randomness or read what its agent may not see: played all the same (attempt counted).
_SPENT = {"error": "rejected", "spent": True}
#: What an atomic turn's action says in place of its outcome, until the turn commits.
_HELD = "Its outcome is shown when your turn ends."
#: What a closed turn reads instead of its brief or update (its participant has been left behind).
_CLOSED_TEXT = "This turn is over."


class Turn:
    """A live turn. ``peek`` turns (previews) read the world but never change the run:
    they take no turn number and leave the agent's memory untouched."""

    def __init__(self, env: Env, actor: Entity, stage: StageSpec, reason: str, staged: bool, peek: bool = False,
                 kind: str = "turn"):
        self.env = env
        self.actor = actor
        self.stage = stage
        self.reason = reason
        self.staged = staged
        self.peek = peek
        self.round = env.world.round
        memory = env.state.memories.get(actor.id) if peek else env.state.memory(actor.id)
        memory = memory or Memory()
        self._since = memory.cursor
        self._brief: str | None = None
        self._update: str | None = None
        #: The assets delivered with the brief and with the update.
        self._delivered: list[str] = []
        path = f"stages.{stage.name}"
        #: The stage's `max_actions` and `max_calls` (either may be an expression over $inputs).
        self.max_actions = whole_setting(env.world, stage.max_actions, f"{path}.max_actions", MAX_TURN_ACTIONS)
        self.max_calls = whole_setting(env.world, stage.max_calls, f"{path}.max_calls", MAX_TURN_CALLS)
        self.calls_left = self.max_calls
        #: Looks and inspects that do not spend a call (see :mod:`fg_env.actions.reads`); below zero, the refused ones.
        self.reads_left = self.max_calls
        #: What this turn's reads returned, to answer a repeated read that it is unchanged.
        self._reads: list[str] = []
        #: An action was available and the agent took none, in a stage that required one or with its calls used up
        #: (set when the turn is finished).
        self.did_not_act = False
        self.actions_left = self.max_actions
        self.done = False
        self.used: dict[str, int] = {}
        self.intents: list[tuple[str, dict[str, Any]]] = []
        #: What this agent already did (sequential) or submitted (simultaneous) this turn, as $pending.
        self.pending: list[dict[str, Any]] = []
        self.stats = Stats(wakes=1)
        self._offered = False
        self._tools: list[ToolSpec] | None = None
        #: Wall-clock seconds this turn may take (None: no limit); the deadline is set when it starts.
        self.time_limit = env.time_limit
        self.deadline: float | None = None
        self.timed_out = False
        #: Closed from outside (deadline, a failing run): its participant is no longer waited for.
        self.closed = False
        #: Calls in progress; the engine waits for them to return before moving on from a closed turn.
        self.busy = 0
        #: Its statistics are in the run's totals (the engine is done with it); usage reported later goes there.
        self.tallied = False
        #: Atomic turns: the journal position the turn's changes are undone to, until it settles, and the turn's
        #: counts there (``_part``). An action that draws randomness settles the turn so far, and a new part begins.
        self.atomic = bool(stage.valid) and not staged and not peek
        self._mark: int | None = None
        self._part: tuple[int, dict[str, int], int, int] = (self.actions_left, {}, 0, 0)
        self._counted: list[str] = []
        #: Atomic turns: the outcome texts (and files) of the part's actions, shown once the part commits — an undone
        #: part must not leave its agent knowing what it showed (a peek whose cost was refunded) — and those committed,
        #: shown with the next result.
        self._held: list[tuple[str, list[Attachment]]] = []
        self._committed: list[tuple[str, list[Attachment]]] = []
        if self.atomic:
            self._begin_part()
        if peek:
            self.number = env.state.turn_count + 1
        else:
            env.state.turn_count += 1
            self.number = env.state.turn_count  # assigned in deterministic order, before any concurrency
            env.origin.tape.opened(self.number)
        exposures = env.world.exposures
        self.exposure: Exposure | None = exposures.open(self, kind) if exposures is not None and not peek else None

    # -- time ----------------------------------------------------------------------

    def start_clock(self) -> None:
        if self.time_limit is not None and self.deadline is None:
            self.deadline = time.monotonic() + self.time_limit

    def time_left(self) -> float | None:
        if self.deadline is None:
            return self.time_limit
        return max(0.0, self.deadline - time.monotonic())

    def expired(self, now: float | None = None) -> bool:
        """True once the deadline has passed; the first time, the turn is closed as timed out (call under the lock)."""
        if (not self.timed_out and self.deadline is not None and (time.monotonic() if now is None else now)
            >= self.deadline):
            self.record("timeout")
            self.timed_out = True
            self.stats.timeouts = 1
            self.close()
        return self.timed_out

    def close(self) -> None:
        self.done = self.closed = True

    def record(self, *entry: Any) -> None:
        """Note on the run's tape something the participant did (so a copy can replay it); previews and
        finished turns change nothing, so they are not recorded."""
        if not self.peek and not self.done:
            self.env.origin.tape.record(self.number, self.actor.id, entry)

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
                self._deliver(self.env.state.brief_assets.get(self.actor.id, []), "brief")
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
                attached: list[str] = []
                with shared_budget(ACTION_BUDGET, "update"), entity_handles(handle_filter(self.env, self.actor)), \
                        self._views_luck("update"):
                    self._update = self.env.perception.update(self.actor, self.stage, self.reason, self._since,
                                                              self.time_limit, shown, attached,
                                                              self.calls_left if self.call_limit else None,
                                                              self.call_limit and self._offers_reads())
                self.stats.update_chars = len(self._update)
                self.stats.update_reads = 1
                self._deliver(attached, "update")
                if self.exposure is not None and shown is not None:
                    self.exposure.read_update(self._update, shown)
            return self._update

    def _offers_reads(self) -> bool:
        """Whether the turn offers a read (a look view, or someone to inspect)."""
        env = self.env
        return bool(env.perception.look_views(self.actor)) or \
            inspect_tool(env, self.actor, self.max_calls) is not None

    def _views_luck(self, *site: str) -> Any:
        """A block that renders what the agent reads, drawing from a stream of this turn's own: looking again shows the
        same noise (re-looking cannot average it away), and a preview of the turn shows what the turn will."""
        return self.env.world.drawing_from(self.env.seeds.lazy_rng(*site, self.number))

    def _deliver(self, ids: list[str], where: str) -> None:
        fresh = [key for key in ids if key not in self._delivered]
        self._delivered.extend(fresh)
        if self.exposure is not None and fresh:
            self.exposure.shown(self.env.world.assets.of(fresh), where)

    @property
    def call_limit(self) -> bool:
        """Whether the stage allows fewer calls than stages usually do: then the update states the budget (a larger
        `max_calls` is a backstop the agent never needs to plan around)."""
        return self.max_calls < type(self.stage).model_fields["max_calls"].default

    def attachments(self, ids: list[str] | None = None) -> list[Attachment]:
        """The files delivered with the brief and update (or the assets ``ids``), as participants receive them."""
        store = self.env.world.assets
        return [Attachment(asset, store) for asset in store.of(self._delivered if ids is None else ids)]

    # -- tools ------------------------------------------------------------------

    def _legal(self) -> list[str]:
        if self.actions_left <= 0:
            return []
        env = self.env
        names = stage_actions(env.contract, self.stage, self.actor.entity_type)
        used_round = env.world.used_round.get(self.actor.id, {})
        return [n for n in names if env.actions.blocked(self.actor, n, self.used, used_round, offered=True) is None]

    def _allows(self, name: str) -> bool:
        """Whether action ``name`` is offered now: :meth:`_legal` for one action."""
        if self.actions_left <= 0:
            return False
        env = self.env
        used_round = env.world.used_round.get(self.actor.id, {})
        return name in stage_actions(env.contract, self.stage, self.actor.entity_type) and \
            env.actions.blocked(self.actor, name, self.used, used_round, offered=True) is None

    def tools(self) -> list[ToolSpec]:
        if self.done:
            return []
        if self._tools is not None:  # nothing changed since the last look (reset by every call)
            return self._tools
        env = self.env
        with env._lock:  # never while another agent's sealed choices are tried on the world
            tools = env.actions.tools(self.actor, self._legal(), self.staged)
            looks = env.perception.look_views(self.actor)
            if looks:
                tools.append(look_tool([(name, env.contract.views[name].title) for name in looks], self.max_calls))
            inspect = inspect_tool(env, self.actor, self.max_calls)
        if inspect is not None:
            tools.append(inspect)
        if not self._must_act_now(tools):
            if self.staged:
                end_text = "Finish your turn (your choices are submitted)."
            elif self.atomic and self.stage.valid:
                end_text = "Finish your turn (your actions are checked together; a turn that is not allowed is undone)."
            else:
                end_text = "Finish your turn."
            tools.append(ToolSpec(END_TURN, end_text,
                                  {"type": "object", "properties": {}, "additionalProperties": False}, "end", True))
        if not self._offered:
            self.stats.tools_offered += len(tools)
            self._offered = True
            if not self.peek:
                env.diagnosis.offered(self, any(tool.kind == "act" for tool in tools))
        self._tools = tools
        return tools

    def _must_act_now(self, tools: list[ToolSpec]) -> bool:
        acted = self.actions_left < self.max_actions or bool(self.intents)
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
            if isinstance(name, str) and not self.peek:
                env.diagnosis.called(self, name, args, result)
            return result

    def refusal(self) -> ToolResult | None:
        """Why a call cannot be made now (the turn is over or out of time), or None (call under the lock)."""
        if self.expired():
            return ToolResult(False, "Your time for this turn ran out; nothing was done.", True, dict(_TIMEOUT))
        if self.done:
            limit = ""
            if self.actions_left <= 0:
                count = self.max_actions
                limit = (f" The '{self.stage.name}' stage allows {count} action{'s' if count != 1 else ''} per turn; "
                         "none remain.")
            elif self.calls_left <= 0:
                count = self.max_calls
                limit = (f" The '{self.stage.name}' stage allows {count} tool call{'s' if count != 1 else ''} per "
                         "turn; none remain.")
            text = (f"Your turn is already over.{limit} Nothing was done." if limit
                    else "Your turn is already over; nothing was done.")
            return ToolResult(False, text, True, dict(_ENDED))
        return None

    def _call(self, name: Any, args: Any) -> ToolResult:
        refused = self.refusal()
        if refused is not None:
            return refused
        if name in READS:
            return self._read(name, args)
        if self.calls_left <= 0:
            self.done = True
            return ToolResult(False, "No tool calls left this turn; your turn is over.", True)
        self.calls_left -= 1
        self.stats.calls += 1
        env = self.env
        if not isinstance(name, str):
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"A tool name is text, got {type(name).__name__}. {self._offer()}",
                                          data=_INVALID))
        if args is not None and not isinstance(args, Mapping):
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"{name} was not done: {_not_an_object(args)}", data=_INVALID))
        if args is not None and REFUSED_ARGS in args:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, f"{name} was not done: {args[REFUSED_ARGS]}. Send plain arguments.",
                                          data=_INVALID))
        if name == END_TURN:
            if self._must_act():
                self.stats.invalid_calls += 1
                return self._after(ToolResult(False, f"You must act during {self.stage.name}. {self._offer()}",
                                              data=_INVALID))
            why = self.settle()
            if why is not None:
                return self._after(self._undone(why))
            return self._after(ToolResult(True, "Turn ended.", True))
        available = stage_actions(env.contract, self.stage, self.actor.entity_type)
        spec = env.contract.actions.get(name)
        if spec is None or name not in available:
            self.stats.invalid_calls += 1
            why = "is not a tool" if spec is None else f"is not available during {self.stage.name}"
            return self._after(ToolResult(False, f"'{name}' {why}. {self._offer()}", data=_INVALID))
        if self.actions_left <= 0:
            self.stats.invalid_calls += 1
            return self._after(ToolResult(False, "You have no actions left this turn; call end_turn.", data=_INVALID))
        before = self._tally()
        acted, fault = guarded(env, lambda: self._act(name, spec, args), action=name)
        if acted is None:
            assert fault is not None
            self.stats.rejected_actions += 1
            self.stats.faulted_actions += 1
            drew = env.world.draws() != before[0]
            result, applied = self._refused(name, refused_text(name, fault), before, _REJECTED), False
        else:
            result, applied, drew = acted
        if drew and self._mark is not None:  # luck settles an atomic turn at once: nothing after it can undo it
            why = self.settle()
            if why is not None:
                return self._after(self._undone(why, luck=name))
            self._begin_part()
        if applied:
            if self._mark is None:  # reactions wait for the commit (atomic turns: for the whole turn)
                env.happenings.react(self.stage)
            if result.ended or env.world.end_request is not None:
                result.ended = True
                why = self.settle()
                if why is not None:
                    return self._after(self._undone(why))
        return self._after(result)

    def _act(self, name: str, spec: ActionSpec, args: Any) -> tuple[ToolResult, bool, bool]:
        """Check, then submit (sealed turns) or apply and commit one action call: its result, whether it applied, and
        whether it drew randomness. Runs inside :func:`guarded`, so the turn's own counts change only once nothing can
        fail any more."""
        with self.after_choices():
            return self._checked_act(name, spec, args)

    @contextmanager
    def after_choices(self) -> Iterator[None]:
        """The world as this turn's next sealed choice will meet it when it commits: after the choices the turn
        already submitted, undone on the way out (two buys cannot spend the same coins). Blocks do not nest."""
        if not self.intents:
            yield
            return
        with self.env.actions.trying():
            self.env.actions.replay(self.actor, self.intents)
            yield

    def _tally(self) -> tuple[int, int]:
        """How many random draws and hidden reads this thread has made so far: what :meth:`_refused` compares with."""
        world = self.env.world
        return world.draws(), world.hidden_reads()

    def _refused(self, name: str, text: str, before: tuple[int, int], free: dict[str, Any]) -> ToolResult:
        """The result of a refused call to ``name``, the one place that decides what a refusal costs. Since ``before``
        (:meth:`_tally`), did working it out draw luck or read a value hidden from the actor (see expr/hidden.py)?
        Then the action is spent — a free retry would let an agent reroll its luck, or probe the hidden value again
        and again. Otherwise it is free (its ``free`` data: an invalid call or a rejected one)."""
        draws, hidden = self._tally()
        if (draws, hidden) == before:
            return ToolResult(False, text, data=free)
        self._count(name)
        return ToolResult(False, text, self.actions_left <= 0, dict(_SPENT))

    def _checked_act(self, name: str, spec: ActionSpec, args: Any) -> tuple[ToolResult, bool, bool]:
        env = self.env
        before = self._tally()
        blocked = env.actions.blocked(self.actor, name, self.used, env.world.used_round.get(self.actor.id, {}))
        if blocked:
            self.stats.invalid_calls += 1
            return (self._refused(name, f"You cannot {name.replace('_', ' ')} now: {blocked}.", before, _INVALID),
                    False, False)
        args, cut = _cut(spec.params, args)
        params, problem = env.actions.validate(self.actor, name, args)
        if problem:
            self.stats.invalid_calls += 1
            return (self._refused(name, f"{name} was not done: {problem}. Correct the arguments and call again.",
                                  before, _INVALID), False, False)
        if self.staged:  # checked without its luck (a trial draws nothing): the luck is rolled when it commits
            refusal = env.actions.dry_run(self.actor, name, params)
            if refusal is not None:
                self.stats.rejected_actions += 1
                return self._refused(name, refusal, before, _REJECTED), False, False
            ended = env.actions.ends_turn(self.actor, name, params)
            self.intents.append((name, dict(args or {})))
            self.pending.append({"action": name, **_plain(params)})
            self._count(name)
            text = f"Submitted {name.replace('_', ' ')}{_args_text(params)}; it resolves when everyone has chosen.{cut}"
            return ToolResult(True, text, ended or self.actions_left <= 0), False, False
        drawn = env.world.draws()
        outcome = env.actions.apply(self.actor, name, params)
        drew = env.world.draws() != drawn
        if not outcome.ok:
            self.stats.rejected_actions += 1
            return self._refused(name, outcome.text, before, _REJECTED), False, drew
        self.pending.append({"action": name, **_plain(params)})  # what the commit's rules read as $pending
        try:
            self.committed(f"actions.{name}")
            ended = env.actions.ends_turn(self.actor, name, params)
        except BaseException:
            self.pending.pop()
            raise
        files = self.attachments(outcome.assets)
        self._count(name)
        self.stats.actions += 1
        text, closes = _with_references(outcome.text, files), ended or self.actions_left <= 0
        settles = drew or closes or env.world.end_request is not None  # the part commits in this call
        if self._mark is not None and not settles and (spec.outcome or files):
            self._held.append((text, files))
            text, files = f"{env.actions.default_outcome(name, params)} {_HELD}", []
        return ToolResult(True, text + cut, closes, attachments=files), True, drew

    def _must_act(self) -> bool:
        """The stage requires an action, the turn has taken none, and one is available."""
        return (self.stage.must_act and self.actions_left == self.max_actions and not self.intents
                and bool(self._legal()))

    def _offer(self) -> str:
        """What the agent can call now."""
        return offer_text(self._legal())

    def _count(self, name: str) -> None:
        """Count one use of ``name``: this turn's and, in the world, this round's. An atomic turn's uses are undone with
        the part of the turn they were made in; elsewhere a use stands once counted (its change has committed, or it
        is a sealed choice)."""
        self.used[name] = self.used.get(name, 0) + 1
        self.env.world.count_use(self.actor.id, name, undoable=self.atomic)
        self.actions_left -= 1
        if self.atomic:
            self._counted.append(name)

    # -- atomic turns ------------------------------------------------------------------

    def committed(self, path: str) -> None:
        """A change inside the turn has applied: settle it now, or — atomic turns — when the turn ends. Reactions it
        asks for are the caller's to run, once nothing can undo the change any more."""
        if self._mark is None:
            self.env._after_commit(path)

    def settle(self) -> str | None:
        """Atomic turns: commit a turn that meets `valid` (then run what waited for it), or undo every action of the
        turn and say why — also when a rule fails or an invariant breaks as it commits. A turn that took no action
        has nothing to check. Call under the lock."""
        if self._mark is None:
            return None
        why, fault = guarded(self.env, self._commit_turn, self._mark)
        if fault is not None:
            why = fault
            self.stats.faulted_actions += 1
        if why is not None:
            self._undo()
            return why
        self._mark = None
        self._committed += self._held
        self._held.clear()
        self.env.happenings.react(self.stage)
        return None

    def _begin_part(self) -> None:
        """Atomic turns: start the part of the turn that the next settle checks and an undo returns to."""
        self._mark = self.env.world.journal.mark()
        self._part = (self.actions_left, dict(self.used), len(self.pending), self.stats.actions)
        self._counted.clear()

    def _commit_turn(self) -> str | None:
        """Why the turn as played is not allowed, or None once it has committed."""
        why = self.invalid() if self._counted else None
        if why is None:
            self.env._after_commit(f"stages.{self.stage.name}")
        return why

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

    def invalid(self) -> str | None:
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
        """Undo the turn's part (see :meth:`_begin_part`) — the world, with the part's uses of actions this round — and
        the turn's own counts; what the turn drew stays spent."""
        actions_left, used, pending, actions = self._part
        assert self._mark is not None
        self.env.world.journal.rollback(self._mark)
        self._counted.clear()
        self._held.clear()
        self.used.clear()
        self.used.update(used)
        del self.pending[pending:]  # the same list $pending reads
        self.actions_left = actions_left
        undone = self.stats.actions - actions
        self.stats.actions -= undone
        self.stats.rejected_actions += undone
        self.stats.undone_turns += 1

    def _undone(self, why: str, luck: str | None = None) -> ToolResult:
        if luck is None:
            return ToolResult(False, f"That turn is not allowed: {why}. Everything you did this turn was undone; "
                                     "play your turn again.", data=dict(_UNDONE))
        self.done = True  # its luck is spent: playing the turn again would retry it
        return ToolResult(False, f"That turn is not allowed: {why}. {luck.replace('_', ' ').capitalize()} drew on "
                                 "chance, which settles a turn at once, so it was undone with what you did before it "
                                 "this turn, and your turn is over.", True, dict(_UNDONE))

    def _after(self, result: ToolResult) -> ToolResult:
        if self._committed:  # what the actions of a part that has now committed showed, before the result itself
            result.text = " ".join([*(text for text, _ in self._committed), result.text])
            result.attachments = [*(file for _, files in self._committed for file in files), *result.attachments]
            self._committed.clear()
        if result.ended:
            self.done = True
        elif self.calls_left <= 0:
            self.done = True
            result.ended = True
            result.text += " (No tool calls left; your turn is over.)"
        elif (self.calls_left <= self.actions_left
              + 1):  # the calls left barely cover the actions still allowed and ending
            result.text += f" (Calls left: {self.calls_left}.)"
        return result

    def _may_inspect(self, target: Entity) -> bool:
        return may_inspect(self.env, self.actor, target)

    def _read(self, name: str, args: Any) -> ToolResult:
        """A look or an inspect: free within the turn's allowance, refused past it without spending a call."""
        allowance = self.max_calls
        self.reads_left -= 1
        self.stats.calls += 1
        if self.reads_left < 0:
            self.stats.invalid_calls += 1
            stopped = self.reads_left < -allowance  # the backstop for a participant that only reads
            if stopped:
                self.calls_left = 0
                self.done = True
            return ToolResult(False, reads_refused(allowance, self._must_act(), stopped), stopped, dict(_INVALID))
        if args is not None and not isinstance(args, Mapping):
            self.stats.invalid_calls += 1
            return ToolResult(False, f"{name} was not done: arguments must be a JSON object of named values, "
                                     f"got {type(args).__name__}.", data=_INVALID)
        result = self._look(args) if name == "look" else self._inspect(args)
        if result.ok:
            seen = f"{name}\n{result.text}"
            if seen in self._reads:
                result = ToolResult(True, UNCHANGED)
            else:
                self._reads.append(seen)
            if self.reads_left == 0:
                result.text += " (That was your last free read this turn.)"
        return result

    def _look(self, args: Mapping[str, Any] | None) -> ToolResult:
        env = self.env
        name = (args or {}).get("view")
        looks = env.perception.look_views(self.actor)
        if not isinstance(name, str) or name not in looks:
            self.stats.invalid_calls += 1
            return ToolResult(False, f"view must be one of: {', '.join(looks) or 'none'}.", data=_INVALID)
        shown = _shown() if self.exposure is not None else None
        attached: list[str] = []
        with shared_budget(ACTION_BUDGET, f"views.{name}"), entity_handles(handle_filter(env, self.actor)), \
                self._views_luck("view", name):
            text = env.perception.render_view(name, env.contract.views[name], self.actor, shown, attached)
        if self.exposure is not None and shown is not None and text is not None:
            shown.views.append((name, text))
            self.exposure.looked(shown)
        return ToolResult(True, text or "Nothing to show.", attachments=self.attachments(attached))

    def _inspect(self, args: Mapping[str, Any] | None) -> ToolResult:
        env = self.env
        target, refusal = find_target(env, self.actor, (args or {}).get("id"))
        if target is None:
            self.stats.invalid_calls += 1
            return ToolResult(False, refusal, data=_INVALID)
        text, files = inspect_text(env, self.actor, target)
        return ToolResult(True, text, attachments=self.attachments(files))


def _shown() -> Any:
    from .exposure import Shown

    return Shown()


def entity_dict(entity: Entity) -> dict[str, Any]:
    return {"id": entity.id, "name": entity.name, "type": entity.entity_type, "alive": entity.alive,
            "at": entity.location_id, "props": _plain(dict(entity.properties))}


def _not_an_object(args: Any) -> str:
    """Why arguments that are not a JSON object were refused: text that is not valid JSON (a model's broken arguments)
    says where it broke."""
    if isinstance(args, str):
        try:
            json.loads(args)
        except ValueError as exc:
            return (f"its arguments are not valid JSON ({exc}). Call again with the arguments as one JSON object of "
                    "named values.")
    return f"arguments must be a JSON object of named values, got {type(args).__name__}."


def _cut(params: Mapping[str, Any], args: Any) -> tuple[Any, str]:
    """``args`` with text past its `max_len` cut where its parameter says `overflow: truncate`, and the note that tells
    the agent what was cut (empty when nothing was)."""
    if not isinstance(args, Mapping):
        return args, ""
    out, notes = dict(args), []
    for pname, param in params.items():
        raw = out.get(pname)
        if (param.overflow == "truncate" and param.max_len is not None and isinstance(raw, str) and len(raw)
            > param.max_len):
            out[pname] = cut_text(raw, param.max_len)
            notes.append(f" (Your {pname} was cut to {len(out[pname])} of {len(raw)} characters; the rest was not "
                         "said.)")
    return (out, "".join(notes)) if notes else (args, "")


def _with_references(text: str, files: list[Attachment]) -> str:
    return f"{text} {' '.join(file.reference for file in files)}" if files else text


def _args_text(params: Mapping[str, Any]) -> str:
    parts = [f"{k}={format_value(v)}" for k, v in params.items() if v is not None]
    return f" ({', '.join(parts)})" if parts else ""
