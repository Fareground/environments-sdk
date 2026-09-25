"""One agent's turn: what it reads, which tools it has, and how each call is applied.

A turn may have a wall-clock deadline: past it the turn is closed and every later call is
refused. In a stage with `valid` rules the turn's actions stay open until it ends: events, reactions
and invariants wait, and a turn that breaks the rules is undone as a whole.
"""
from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, NamedTuple

from ..actions.book import stage_actions
from ..actions.faults import RETRY, refused_text
from ..actions.params import REFUSED_ARGS, parse_arguments
from ..assets.delivery import Attachment
from ..contract import MAX_TURN_ACTIONS, MAX_TURN_CALLS, ActionSpec, StageSpec
from ..contract.base import spoken
from ..expr.objects import Entity
from ..expr.template import format_value
from ..information.exposure import Shown
from ..information.reads import READS, UNCHANGED, reads_refused
from ..information.schemas import ToolSpec
from ..information.tool_text import cut_text, offer_text
from ..sampling.seeds import copy_stream
from ..world.build import whole_setting
from ..world.values import plain_value
from .facts import (
    APPLIED,
    CALLED,
    FAULTED,
    INVALID,
    REJECTED,
    TIMED_OUT,
    Answered,
    Fact,
    Offered,
    Read,
    Stats,
    Undone,
    Woke,
)
from .ledger import AttemptLedger, attempt_cost
from .session import END_TURN, ToolResult
from .state import Memory

if TYPE_CHECKING:
    from ..information.exposure import Exposure
    from ..world.randomness import Observation
    from .env import Env

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


class Undo(NamedTuple):
    """Why an atomic turn was undone, and whether working that out drew luck or read a value hidden from its agent
    (:func:`~fg_env.runtime.ledger.attempt_cost`): then the turn is over rather than played again."""

    why: str
    spent: bool


class Turn:
    """A live turn. ``peek`` turns (previews) read the world but never change the run:
    they take no turn number and leave the agent's memory untouched."""

    def __init__(self, env: Env, actor: Entity, stage: StageSpec, reason: str, staged: bool, peek: bool = False,
                 kind: str = "turn"):
        self.env = env
        #: The run's gate: the turn reads and changes the run holding it (see runtime/gate.py).
        self.gate = env.gate
        self.actor = actor
        self.stage = stage
        self.reason = reason
        self.staged = staged
        self.peek = peek
        self.round = env.world.round
        memory = env.state.memories.get(actor.id) if peek else env.state.memory(actor.id)
        memory = memory or Memory()
        self._since = memory.cursor
        self._first = memory.turns == 0
        #: What the agent's actions of its previous turn returned (shown atop the update); this turn's are summed up by
        #: :attr:`last_outcome` from what its actions did and the refusal of any call after the last that did.
        self._last: str | None = memory.last
        self._done: list[str] = []
        self._refused_after: str | None = None
        self._brief: str | None = None
        self._update: str | None = None
        #: The assets delivered with the brief and with the update.
        self._delivered: list[str] = []
        path = f"stages.{stage.name}"
        #: What the turn may still do and has used, under the stage's `max_actions` and `max_calls` (either may be an
        #: expression over $inputs). A stage with `valid` rules plays an agent's own turn atomically, in parts, each
        #: undone as a whole when the turn as played is not allowed; an action that draws randomness settles the turn
        #: so far, and a new part begins.
        self.ledger = AttemptLedger(
            env.world, actor.id, whole_setting(env.world, stage.max_actions, f"{path}.max_actions", MAX_TURN_ACTIONS),
            whole_setting(env.world, stage.max_calls, f"{path}.max_calls", MAX_TURN_CALLS),
            atomic=bool(stage.valid) and not staged and not peek)
        #: What this turn's reads returned, to answer a repeated read that it is unchanged.
        self._reads: list[str] = []
        #: An action was available and the agent took none, in a stage that required one or with its calls used up
        #: (set when the turn is finished).
        self.did_not_act = False
        self.done = False
        #: The last action that settled part of an atomic turn (it drew luck or read something hidden): what the turn
        #: did up to it stands, whatever `valid` says of the rest.
        self._stands: str | None = None
        #: The turn's own numbers: a fold over its facts (see :meth:`note`).
        self.stats = Stats()
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
        #: Whether its participant has been handed it.
        self.started = False
        #: The stream it draws from outside a block of logic: set when it starts, replaced when it is reseeded.
        self.rng: Any = None
        #: What its participant did through its wake, in order — reads, calls, reported usage, uploads, a reseed, a
        #: timeout: the steps its exposure records, and what taking the same decision again plays (copying/replay.py).
        self.steps: list[tuple[Any, ...]] = []
        #: Its uses of the contract's in-turn host tools, by name.
        self.host_uses: dict[str, int] = {}
        self.note(Woke(reaction=kind == "reaction"))
        if peek:
            self.number = env.state.turn_count + 1
        else:
            env.state.turn_count += 1
            self.number = env.state.turn_count  # assigned in deterministic order, before any concurrency
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
        """True once the deadline has passed; the first time, the turn is closed as timed out (call holding the run's
        gate)."""
        if (not self.timed_out and self.deadline is not None and (time.monotonic() if now is None else now)
            >= self.deadline):
            self.record("timeout")
            self.timed_out = True
            self.note(TIMED_OUT)
            self.close()
        return self.timed_out

    def close(self) -> None:
        self.done = self.closed = True

    def note(self, fact: Fact) -> None:
        """Tell the run's facts that ``fact`` happened in this turn (the turn's numbers are their fold)."""
        self.env.facts.emit(fact, self)

    def record(self, *entry: Any) -> None:
        """Note among its :attr:`steps` something the participant did; previews and finished turns change nothing, so
        they are not recorded."""
        if not self.peek and not self.done:
            self.steps.append(entry)

    def copy(self, env: Env) -> Turn:
        """This turn in ``env``, a copy of its run (see :meth:`RunState.copy`): its accounting, its statistics, its
        steps, its random stream and its exposure record are the copy's own. It waits between calls."""
        assert not self.busy, "a turn is copied between its calls"
        world = env.world
        turn = Turn.__new__(Turn)
        turn.__dict__.update(self.__dict__)
        turn.__dict__.update(
            env=env, gate=env.gate, actor=world.entities[self.actor.id], ledger=self.ledger.copy(world),
            stats=self.stats.copy(),
            _tools=None, _delivered=list(self._delivered), _reads=list(self._reads), steps=list(self.steps),
            _done=list(self._done),
            host_uses=dict(self.host_uses), rng=None if self.rng is None else copy_stream(self.rng),
            exposure=None if self.exposure is None else self.exposure.copy(world.exposures, world))
        return turn

    def restart(self) -> None:
        """Forget how this sealed turn was played, as if it had not begun, so a search copy's own participant chooses
        for its agent: the choices it submitted (uncommitted, so nothing in the world undoes), its accounting, its
        steps and its stream (a fresh one is derived from the copy's luck)."""
        ledger = self.ledger
        self.ledger = AttemptLedger(self.env.world, self.actor.id, ledger.max_actions, ledger.max_calls, atomic=False)
        self.started = self.done = self.closed = self.timed_out = self.did_not_act = False
        self.deadline, self.rng, self.steps, self._reads, self._tools = None, None, [], [], None
        self._done, self._refused_after = [], None
        self.stats = Stats()

    # Brief and update render on first read, so coded participants that never read them cost nothing.

    @property
    def brief(self) -> str:
        with self.gate:
            if self._brief is None:
                if self.closed:
                    return _CLOSED_TEXT
                self._brief, assets = self.env.information.brief(self.actor)
                self.note(Read("brief", len(self._brief)))
                self._deliver(assets, "brief")
                if self.exposure is not None:
                    self.exposure.read_brief(self._brief)
            return self._brief

    @property
    def update(self) -> str:
        with self.gate:
            if self._update is None:
                if self.closed:
                    return _CLOSED_TEXT
                shown = Shown() if self.exposure is not None else None
                attached: list[str] = []
                info = self.env.information
                self._update = info.update(self.actor, self.stage, self.reason, self._since, self.number,
                                           self.time_limit, shown, attached,
                                           self.ledger.calls_left if self.call_limit else None,
                                           self.call_limit and info.offers_reads(self.actor, self.ledger.max_calls),
                                           self._last, self._first)
                self.note(Read("update", len(self._update)))
                self._deliver(attached, "update")
                if self.exposure is not None and shown is not None:
                    self.exposure.read_update(self._update, shown)
            return self._update

    def _deliver(self, ids: list[str], where: str) -> None:
        fresh = [key for key in ids if key not in self._delivered]
        self._delivered.extend(fresh)
        if self.exposure is not None and fresh:
            self.exposure.shown(self.env.world.assets.of(fresh), where)

    @property
    def call_limit(self) -> bool:
        """Whether the stage allows fewer calls than stages usually do: then the update states the budget (a larger
        `max_calls` is a backstop the agent never needs to plan around)."""
        return self.ledger.max_calls < type(self.stage).model_fields["max_calls"].default

    def attachments(self, ids: list[str] | None = None) -> list[Attachment]:
        """The files delivered with the brief and update (or the assets ``ids``), as participants receive them."""
        store = self.env.world.assets
        return [Attachment(asset, store) for asset in store.of(self._delivered if ids is None else ids)]

    # -- tools ------------------------------------------------------------------

    def _legal(self) -> list[str]:
        if self.ledger.actions_left <= 0:
            return []
        rules, used = self.env.rules, self.ledger.used
        names = stage_actions(rules.contract, self.stage, self.actor.entity_type)
        return [n for n in names if rules.blocked(self.actor, n, used, offered=True) is None]

    def _allows(self, name: str) -> bool:
        """Whether action ``name`` is offered now: :meth:`_legal` for one action."""
        if self.ledger.actions_left <= 0:
            return False
        rules = self.env.rules
        return name in stage_actions(rules.contract, self.stage, self.actor.entity_type) and \
            rules.blocked(self.actor, name, self.ledger.used, offered=True) is None

    def tools(self) -> list[ToolSpec]:
        if self.done:
            return []
        if self._tools is not None:  # nothing changed since the last look (reset by every call)
            return self._tools
        env = self.env
        with self.gate:  # never while another agent's sealed choices are tried on the world
            tools = env.information.tools(self.actor, self._legal(), staged=self.staged, atomic=self.ledger.atomic,
                                          allowance=self.ledger.max_calls,
                                          must_act=self.stage.must_act and not self.ledger.acted)
        if not self._offered:
            self._offered = True
            self.note(Offered(len(tools), any(tool.kind == "act" for tool in tools)))
        self._tools = tools
        return tools

    # -- calls -------------------------------------------------------------------

    def call(self, name: Any, args: Any) -> ToolResult:
        gate = self.gate
        with gate:
            self._tools = None
            self.busy += 1
            live = not self.done  # a call after the turn is over did nothing and tells nothing of the turn
            try:
                result = self._call(name, args)
            finally:
                self.busy -= 1
                gate.notify()
            if self.exposure is not None:
                self.exposure.called(name, args, result)
            # a sealed choice's result reaches its agent as news when it commits
            acted = isinstance(name, str) and name in self.env.contract.actions
            if live and acted and not (self.staged or self.closed):
                self._outcome(result)
            self.note(Answered(name, args, result))
            return result

    @property
    def last_outcome(self) -> str | None:
        """What this turn's actions did, for the agent's next update: what each action that applied returned, in
        order, then the refusal of any call made after the last of them; an undone turn says so first."""
        refused = self._refused_after
        if refused is None:
            return " ".join(self._done) or None
        return " ".join([*self._done, f"Then: {refused}"]) if self._done else refused

    def _outcome(self, result: ToolResult) -> None:
        """Note what an action call of this turn returned (see :attr:`last_outcome`)."""
        if result.ok:
            self._done.append(result.text)
            self._refused_after = None
        elif result.data.get("error") != "undone":  # an undone turn is told by what undid it (:meth:`_undone`)
            self._refused_after = result.text

    def _undo_outcome(self, why: str) -> None:
        """The turn's actions were undone: what they returned no longer holds."""
        self._done, self._refused_after = [f"Your turn was undone: {why}."], None

    def refusal(self) -> ToolResult | None:
        """Why a call cannot be made now (the turn is over or out of time), or None (call holding the run's gate)."""
        if self.expired():
            return ToolResult(False, "Your time for this turn ran out; nothing was done.", True, dict(_TIMEOUT))
        if self.done:
            limit = ""
            if self.ledger.actions_left <= 0:
                count = self.ledger.max_actions
                limit = (f" The '{self.stage.name}' stage allows {count} action{'s' if count != 1 else ''} per turn; "
                         "none remain.")
            elif self.ledger.calls_left <= 0:
                count = self.ledger.max_calls
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
        if not self.ledger.spend_call():
            self.done = True
            return ToolResult(False, "No tool calls left this turn; your turn is over.", True)
        self.note(CALLED)
        env = self.env
        if not isinstance(name, str):
            self.note(INVALID)
            return self._after(ToolResult(False, f"A tool name is text, got {type(name).__name__}. {self._offer()}",
                                          data=_INVALID))
        if args is not None and not isinstance(args, Mapping):
            self.note(INVALID)
            return self._after(ToolResult(False, f"{name} was not done: {_not_an_object(args)}", data=_INVALID))
        if args is not None and REFUSED_ARGS in args:
            self.note(INVALID)
            return self._after(ToolResult(False, f"{name} was not done: {args[REFUSED_ARGS]}. Send plain arguments.",
                                          data=_INVALID))
        if name == END_TURN:
            if self._must_act():
                self.note(INVALID)
                return self._after(ToolResult(False, f"You must act during {self.stage.name}. {self._offer()}",
                                              data=_INVALID))
            undo = self.settle()
            if undo is not None:
                return self._after(self._undone(undo))
            return self._after(ToolResult(True, "Turn ended.", True))
        available = stage_actions(env.contract, self.stage, self.actor.entity_type)
        spec = env.contract.actions.get(name)
        if spec is None or name not in available:
            self.note(INVALID)
            why = "is not a tool" if spec is None else f"is not available during {self.stage.name}"
            return self._after(ToolResult(False, f"'{name}' {why}. {self._offer()}", data=_INVALID))
        if self.ledger.actions_left <= 0:
            self.note(INVALID)
            return self._after(ToolResult(False, "You have no actions left this turn; call end_turn.", data=_INVALID))
        observed = env.world.luck.observe()
        acted, fault = env.rules.guarded(lambda: self._act(name, spec, args, observed), action=name)
        if acted is None:
            assert fault is not None
            self.note(FAULTED)
            spent = attempt_cost(observed) == "spent"
            result, applied = self._refused(name, refused_text(name, fault), observed, _REJECTED, RETRY), False
        else:
            result, applied, spent = acted
        if spent and self.ledger.part_open:  # luck or a hidden read settles an atomic turn: nothing may undo it
            undo = self.settle()
            if undo is not None:
                return self._after(self._undone(undo, settled_by=name))
            self._stands = name
            self.ledger.begin_part()
        if applied:
            if not self.ledger.part_open:  # reactions wait for the commit (atomic turns: for the whole turn)
                env.rules.react(self.stage)
            if result.ended or env.world.end_request is not None or not self.actor.alive:  # a removed actor is done
                result.ended = True
                undo = self.settle()
                if undo is not None:
                    return self._after(self._undone(undo))
        return self._after(result)

    def _act(self, name: str, spec: ActionSpec, args: Any, observed: Observation) -> tuple[ToolResult, bool, bool]:
        """Check, then submit (sealed turns) or apply and commit one action call — ``observed`` from its start: its
        result, whether it applied, and whether applying it was spent (:func:`~fg_env.runtime.ledger.attempt_cost`).
        Runs inside :meth:`Rules.guarded`, so the turn's own counts change only once nothing can fail any more."""
        with self.after_choices():
            return self._checked_act(name, spec, args, observed)

    def after_choices(self) -> AbstractContextManager[None]:
        """The world as this turn's next sealed choice will meet it when it commits (:meth:`Rules.replay_intents`)."""
        return self.env.rules.replay_intents(self.actor, self.ledger.intents)

    def _refused(self, name: str, text: str, observed: Observation, free: dict[str, Any],
                 retry: str = "") -> ToolResult:
        """The result of a refused call to ``name``, ``observed`` from the call's start: spent for good when working it
        out drew luck or read a value hidden from the actor (:func:`~fg_env.runtime.ledger.attempt_cost`), else free
        (its ``free`` data: an invalid call or a rejected one), when the ``retry`` advice is added."""
        if not self.ledger.refused(name, observed):
            return ToolResult(False, text + retry, data=free)
        return ToolResult(False, text + " It used up this action.", self.ledger.actions_left <= 0, dict(_SPENT))

    def _checked_act(self, name: str, spec: ActionSpec, args: Any,
                     observed: Observation) -> tuple[ToolResult, bool, bool]:
        env, rules = self.env, self.env.rules
        blocked = rules.blocked(self.actor, name, self.ledger.used)
        if blocked:
            self.note(INVALID)
            return (self._refused(name, f"You cannot call {name} now: {blocked}.", observed, _INVALID),
                    False, False)
        args, cut = _cut(spec.params, args)
        params, problem = rules.validate(self.actor, name, args)
        if problem:
            self.note(INVALID)
            return (self._refused(name, f"{name} was not done: {problem}.", observed, _INVALID,
                                  " Correct the arguments and call again."), False, False)
        if self.staged:  # checked without its luck (a trial draws nothing): the luck is rolled when it commits
            refusal = _names_unborn(params, rules.trial_born) or rules.trial(self.actor, name, params)
            if refusal is not None:
                self.note(REJECTED)
                return self._refused(name, refusal, observed, _REJECTED), False, False
            ended = env.actions.ends_turn(self.actor, name, params)
            self.ledger.submitted(name, dict(args or {}), {"action": name, **plain_value(params)})
            text = f"Submitted {spoken(name)}{_args_text(params)}; it resolves when everyone has chosen.{cut}"
            return ToolResult(True, text, ended or self.ledger.actions_left <= 0), False, False
        outcome = rules.apply(self.actor, name, params)
        spent = attempt_cost(observed) == "spent"  # what checking and applying the call drew or read
        if not outcome.ok:
            self.note(REJECTED)
            return self._refused(name, outcome.text, observed, _REJECTED), False, spent
        pending = self.ledger.pending
        pending.append({"action": name, **plain_value(params)})  # what the commit's rules read as $pending
        try:
            with env.world.luck.acting_as(self.actor, name):  # the change events it sets off are the action's
                self.committed(f"actions.{name}")
            ended = env.actions.ends_turn(self.actor, name, params)
        except BaseException:
            pending.truncate(len(pending) - 1)
            raise
        files = self.attachments(outcome.assets)
        self.ledger.took(name)
        self.note(APPLIED)
        text, closes = _with_references(outcome.text, files), ended or self.ledger.actions_left <= 0
        settles = spent or closes or env.world.end_request is not None  # the part commits in this call
        if self.ledger.part_open and not settles and (spec.outcome or files):
            self.ledger.hold(text, files)
            text, files = f"{env.actions.default_outcome(name, params)} {_HELD}", []
        return ToolResult(True, text + cut, closes, attachments=files), True, spent

    def _must_act(self) -> bool:
        """The stage requires an action, the turn has taken none, and one is available."""
        return self.stage.must_act and not self.ledger.acted and bool(self._legal())

    def _offer(self) -> str:
        """What the agent can call now."""
        return offer_text(self._legal())

    # -- atomic turns ------------------------------------------------------------------

    def committed(self, path: str) -> None:
        """A change inside the turn has applied: settle it now, or — atomic turns — when the turn ends. Reactions it
        asks for are the caller's to run, once nothing can undo the change any more."""
        if not self.ledger.part_open:
            self.env.rules.commit(path)

    def settle(self) -> Undo | None:
        """Atomic turns: commit a turn that meets `valid` (then run what waited for it), or undo every action of the
        turn and say why — also when a rule fails or an invariant breaks as it commits. A turn that took no action
        has nothing to check. Call holding the run's gate."""
        ledger = self.ledger
        if not ledger.part_open:
            return None
        observed = self.env.world.luck.observe()
        why, fault = self.env.rules.guarded(self._commit_turn, ledger.mark)
        if fault is not None:
            why = fault
        if why is not None:  # the open part is undone; what the turn drew stays spent (see AttemptLedger.undo_part)
            self.note(Undone(ledger.undo_part(), faulted=fault is not None))
            return Undo(why, attempt_cost(observed) == "spent")
        ledger.commit_part()
        self.env.rules.react(self.stage)
        return None

    def _commit_turn(self) -> str | None:
        """Commit the turn and the `change` events it sets off, then hold the world they leave to `valid`: why the
        turn is not allowed, with everything it changed undone, or None once it stands. Runs inside
        :meth:`Rules.guarded`, which keeps the turn undoable."""
        rules, mark = self.env.rules, self.ledger.mark
        assert mark is not None  # the turn's part is open
        rules.commit(f"stages.{self.stage.name}")
        why = rules.invalid(self.actor, self.stage) if self.ledger.counted_in_part else None
        if why is not None:
            self.env.world.rollback(mark)
        return why

    def settle_at_end(self) -> None:
        """Settle an atomic turn the participant left open (it returned, timed out or ran out of calls). An
        undone turn is reported to the agent as news."""
        env = self.env
        with self.gate:
            if not self.ledger.part_open:
                return
            with env.world.luck.turn_context(None, self.ledger.pending):
                undo = self.settle()
            if undo is not None:
                self._undo_outcome(undo.why)
                env.world.emit("outcome", f"Your turn was undone: {undo.why}.", actor=self.actor.id,
                               to=(self.actor.id,), data={"ok": False, "undone": True})
                env.world.commit()

    def _undone(self, undo: Undo, settled_by: str | None = None) -> ToolResult:
        """The result of an undone turn: played again, unless what undid it (``settled_by``, an action that settled the
        turn, or the turn's own commit and `valid`) turned on chance or on something hidden from the agent — then
        the turn is over, since playing it again would retry the luck or probe the hidden value for free."""
        why = undo.why
        self._undo_outcome(why)
        stood = spoken(self._stands) if self._stands is not None else None
        undone = (f"what you did after {stood} was undone ({stood} and what came before it stand)" if stood
                  else "everything you did this turn was undone")
        if settled_by is None and not undo.spent:
            again = "the rest of your turn" if stood else "your turn"
            return ToolResult(False, f"That turn is not allowed: {why}. {undone[0].upper()}{undone[1:]}; play {again} "
                                     "again.", data=dict(_UNDONE))
        self.done = True
        if settled_by is None:
            return ToolResult(False, f"That turn is not allowed: {why}. Whether it is allowed turned on chance or on "
                                     f"something hidden from you, so {undone}, and your turn is over.", True,
                              dict(_UNDONE))
        before = f"what you did after {stood}" if stood else "what you did before it this turn"
        return ToolResult(False, f"That turn is not allowed: {why}. {spoken(settled_by).capitalize()} "
                                 "turned on chance or on something hidden from you, which settles a turn at once, so "
                                 f"it was undone with {before}, and your turn is over.", True, dict(_UNDONE))

    def _after(self, result: ToolResult) -> ToolResult:
        released = self.ledger.released()
        if released:  # what the actions of a part that has now committed showed, before the result itself
            result.text = " ".join([*(text for text, _ in released), result.text])
            result.attachments = [*(file for _, files in released for file in files), *result.attachments]
        if result.ended:
            self.done = True
        elif self.ledger.calls_left <= 0:
            self.done = True
            result.ended = True
            result.text += " (No tool calls left; your turn is over.)"
        elif (self.ledger.calls_left <= self.ledger.actions_left
              + 1):  # the calls left barely cover the actions still allowed and ending
            result.text += f" (Calls left: {self.ledger.calls_left}.)"
        return result

    def _read(self, name: str, args: Any) -> ToolResult:
        """A look or an inspect: free within the turn's allowance, refused past it without spending a call."""
        allowance = self.ledger.max_calls
        self.ledger.reads_left -= 1
        self.note(CALLED)
        if self.ledger.reads_left < 0:
            self.note(INVALID)
            stopped = self.ledger.reads_left < -allowance  # the backstop for a participant that only reads
            if stopped:
                self.ledger.calls_left = 0
                self.done = True
            return ToolResult(False, reads_refused(allowance, self._must_act(), stopped), stopped, dict(_INVALID))
        if args is not None and not isinstance(args, Mapping):
            self.note(INVALID)
            return ToolResult(False, f"{name} was not done: arguments must be a JSON object of named values, "
                                     f"got {type(args).__name__}.", data=_INVALID)
        result = self._look(args) if name == "look" else self._inspect(args)
        if result.ok:
            seen = f"{name}\n{result.text}"
            if seen in self._reads:
                result = ToolResult(True, UNCHANGED)
            else:
                self._reads.append(seen)
            if self.ledger.reads_left == 0:
                result.text += " (That was your last free read this turn.)"
        return result

    def _look(self, args: Mapping[str, Any] | None) -> ToolResult:
        info = self.env.information
        name = (args or {}).get("view")
        looks = info.look_views(self.actor)
        if not isinstance(name, str) or name not in looks:
            self.note(INVALID)
            return ToolResult(False, f"view must be one of: {', '.join(looks) or 'none'}.", data=_INVALID)
        shown = Shown() if self.exposure is not None else None
        attached: list[str] = []
        text = info.look(self.actor, name, self.number, shown, attached)
        if self.exposure is not None and shown is not None and text is not None:
            shown.views.append((name, text))
            self.exposure.looked(shown)
        return ToolResult(True, text or "Nothing to show.", attachments=self.attachments(attached))

    def _inspect(self, args: Mapping[str, Any] | None) -> ToolResult:
        found, text, files = self.env.information.inspect(self.actor, (args or {}).get("id"))
        if not found:
            self.note(INVALID)
            return ToolResult(False, text, data=_INVALID)
        return ToolResult(True, text, attachments=self.attachments(files))


def entity_dict(entity: Entity) -> dict[str, Any]:
    return {"id": entity.id, "name": entity.name, "type": entity.entity_type, "alive": entity.alive,
            "at": entity.location_id, "props": plain_value(dict(entity.properties))}


def _not_an_object(args: Any) -> str:
    """Why arguments that are not a JSON object were refused: text that is not valid JSON (a model's broken arguments)
    says where it broke."""
    if isinstance(args, str):
        _, broken = parse_arguments(args)
        if broken:
            return f"its arguments are {broken}. Call again with the arguments as one JSON object of named values."
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


def _names_unborn(params: dict[str, Any], born: frozenset[str]) -> str | None:
    """Why a sealed choice may not name an entity one of the agent's own earlier choices this turn creates (``born``):
    it exists only once the choices commit, and then its id may belong to another agent's."""
    for value in params.values():
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, Entity) and item.id in born:
                return (f"{item.name} is made by one of your own choices this turn and exists only once everyone's "
                        "choices commit, so it cannot be named yet: name it in a later turn")
    return None


def _args_text(params: Mapping[str, Any]) -> str:
    parts = [f"{k}={format_value(v)}" for k, v in params.items() if v is not None]
    return f" ({', '.join(parts)})" if parts else ""
