"""What happened in a run, as typed facts into one sink; the run's statistics and its diagnosis are folds over them.

The turn, the driver, the schedule, sealed commits and the in-turn tools say what happened — a turn woke, a call was
counted, an action was refused or applied, a turn was undone or finished — by emitting one fact to :class:`Facts`.
Nothing else writes a count. :class:`Stats` (the numbers ``result.stats`` shows) and
:class:`~fg_env.runtime.diagnosis.Diagnosis` (what ``result.diagnostics`` is read from) each fold the facts they
count, and every judgment about a turn — that its attempts went wrong, that it failed, that it had to act and did not —
is made once, in the fold.

A fact about a turn folds into the turn's own numbers, which join the run's totals and its agent's when the turn
finishes. What comes after that is decided once, by :data:`LATE`: the engine's doing with the turn's choices and the
usage its participant reports late count — toward the turn, its agent, the run and the diagnosis alike; anything else
a finished turn's participant does (reading its brief, a call, the clock running out on it) changes nothing, so a
turn's numbers are the ones that joined the totals and never depend on when a participant got round to it.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .session import ToolResult
    from .state import RunState
    from .turn import Turn

__all__ = ["Facts", "Stats", "Fact", "Woke", "Read", "Offered", "Called", "Refused", "Applied", "Undone", "TimedOut",
           "Finished", "Usage", "Answered", "Committed", "CommitRefused", "StageVisit", "Faulted", "PolicyRule",
           "Overwrote", "CALLED", "APPLIED", "TIMED_OUT", "INVALID", "REJECTED", "FAULTED", "LATE"]


# -- the facts ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Woke:
    """A turn opened; ``reaction``: out of turn (`wake` with `now`)."""

    reaction: bool = False


@dataclass(frozen=True, slots=True)
class Read:
    """The participant read its turn's brief or update, ``chars`` long."""

    what: Literal["brief", "update"]
    chars: int


@dataclass(frozen=True, slots=True)
class Offered:
    """A fresh turn's tools were read: ``tools`` of them, ``has_action`` when one is an action."""

    tools: int
    has_action: bool


@dataclass(frozen=True, slots=True)
class Called:
    """A tool call was counted against the turn."""


@dataclass(frozen=True, slots=True)
class Refused:
    """A call was refused: ``invalid`` (a call that could not be made), ``rejected`` (an action its rules refused) or
    ``faulted`` (refused and undone because a rule failed or an invariant broke as it applied)."""

    kind: Literal["invalid", "rejected", "faulted"]


@dataclass(frozen=True, slots=True)
class Applied:
    """An action applied."""


@dataclass(frozen=True, slots=True)
class Undone:
    """An atomic turn (or an agent's sealed choices) was undone as a whole: ``actions`` that had applied, ``faulted``
    when a rule failed or an invariant broke as it committed."""

    actions: int
    faulted: bool = False


@dataclass(frozen=True, slots=True)
class TimedOut:
    """The turn ran past its time limit."""


@dataclass(frozen=True, slots=True)
class Finished:
    """The engine is done with a turn: ``chose`` when it left sealed choices, ``had_to`` when it had to act (a
    must-act stage, or its calls used up), ``timed_out``; ``able()`` says whether the agent had an action it could take
    (asked only when the turn took none and something went wrong or it had to act)."""

    chose: bool
    had_to: bool
    timed_out: bool
    able: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class Usage:
    """Model usage reported: its counts by :class:`Stats` field (with no turn: the run's own, by its hosts)."""

    counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class Answered:
    """A tool call returned ``result`` to the participant."""

    name: Any
    args: Any
    result: ToolResult


@dataclass(frozen=True, slots=True)
class Committed:
    """A sealed choice of ``action`` took effect as the choices committed."""

    action: str


@dataclass(frozen=True, slots=True)
class CommitRefused:
    """A sealed choice of ``action`` accepted when submitted did not happen as the choices committed, told ``text``
    (or what the action set for later with `after`); ``faulted`` when a rule failed or an invariant broke as it
    applied, ``left`` when its agent had left the run."""

    action: str
    text: str
    faulted: bool = False
    left: bool = False


@dataclass(frozen=True, slots=True)
class StageVisit:
    """A stage was reached (and ``ran``), woke agents for a pass, or ran every pass without its `until` holding."""

    stage: str
    reached: int = 0
    ran: int = 0
    woke: int = 0
    capped: int = 0


@dataclass(frozen=True, slots=True)
class Faulted:
    """A rule at ``path`` failed, or the invariant at ``path`` broke, while an agent's action applied (the contract
    ``action``, when known)."""

    path: str
    error: str
    action: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """The coded policy rule at ``path`` acted, or (given ``refusal``) its call of ``action`` was refused — never
    ``sent`` when the arguments it chose are ones the action does not accept."""

    path: str
    action: str
    refusal: str | None = None
    sent: bool = True


@dataclass(frozen=True, slots=True)
class Overwrote:
    """A write replaced another's at ``where`` (a sealed stage, or an `each` loop's path when ``loop``)."""

    where: str
    example: str
    loop: bool = False


Fact = (Woke | Read | Offered | Called | Refused | Applied | Undone | TimedOut | Finished | Usage | Answered | Committed
        | CommitRefused | StageVisit | Faulted | PolicyRule | Overwrote)

CALLED = Called()
APPLIED = Applied()
TIMED_OUT = TimedOut()
INVALID = Refused("invalid")
REJECTED = Refused("rejected")
FAULTED = Refused("faulted")


# -- the statistics ----------------------------------------------------------------------------------------------------

@dataclass
class Stats:
    """How the run went for its agents — the numbers that keep an environment LLM-native. A fold over the run's facts
    (:meth:`fold`), per turn, per agent and for the run."""

    wakes: int = 0
    calls: int = 0
    actions: int = 0
    invalid_calls: int = 0
    rejected_actions: int = 0
    idle_turns: int = 0
    brief_chars: int = 0
    update_chars: int = 0
    tools_offered: int = 0
    #: Turns whose brief / update was actually read (coded participants often read neither).
    brief_reads: int = 0
    update_reads: int = 0
    #: Reported by LLM participants (see :meth:`Wake.record_usage`): real provider numbers, not estimates.
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    #: Model calls whose provider reported no token usage: each counts its prompt's estimated size instead, so a token
    #: budget still holds.
    unreported_usage: int = 0
    llm_retries: int = 0
    #: Out-of-turn reaction turns (`wake` with `now`).
    reactions: int = 0
    #: Turns an LLM participant lost because its provider still failed after every retry, or its prompt was too long.
    forfeits: int = 0
    #: Of those, the turns whose prompt was longer than the model's context (no retry can help).
    too_long: int = 0
    #: Model replies cut off at their output limit (reported by LLM participants).
    truncated: int = 0
    #: Model replies the provider refused to give (reported by LLM participants).
    refusals: int = 0
    #: Turns an LLM participant ended because it used all its ``max_steps`` model calls.
    out_of_steps: int = 0
    #: Turns an LLM participant ended because the model still answered without a tool call after it was reminded.
    no_tool_replies: int = 0
    #: Turns that ran past their time limit.
    timeouts: int = 0
    #: Atomic turns undone because the whole turn was not `valid`.
    undone_turns: int = 0
    #: Actions refused and undone because a rule failed or an invariant broke while they applied (also counted in
    #: ``rejected_actions``): a contract bug, explained in the run's diagnostics.
    faulted_actions: int = 0
    #: Turns that ended with an action available and none taken after the agent's attempts went wrong: invalid or
    #: refused calls, a model refusal, a reply cut off or with no tool call, its model calls used up — or it ran out of
    #: time; and turns in which the provider refused or cut off a model reply, whatever else the turn did.
    failed_turns: int = 0

    def fold(self, fact: Fact) -> None:
        """Count ``fact`` (a fact these numbers do not count changes nothing)."""
        count = _COUNTS.get(type(fact))
        if count is not None:
            count(self, fact)

    def went_wrong(self, faults: bool = True) -> bool:
        """Whether some attempt went wrong: an invalid or refused call, a model refusal, a reply cut off or with no tool
        call, the model calls used up. Without ``faults``, a refusal a failing rule caused is the contract's, not the
        agent's, and does not count."""
        refused = self.rejected_actions if faults else self.rejected_actions - self.faulted_actions
        return bool(self.invalid_calls or refused or self.refusals or self.truncated or self.out_of_steps
                    or self.no_tool_replies)

    def finish(self, fact: Finished) -> bool:
        """Count a turn's end (these are the turn's own numbers): an idle turn, and a failed one — one that took no
        action though one was there after its attempts went wrong or it ran out of time, or one whose model reply the
        provider refused or cut off. Whether the agent had to act and did not (a timeout is reported as one)."""
        if self.actions == 0 and not fact.chose:
            self.idle_turns += 1
            wrong = self.went_wrong()
            if (wrong or fact.had_to or fact.timed_out) and fact.able():
                self.failed_turns += wrong or fact.timed_out  # an action was there to take
                return fact.had_to and not fact.timed_out
        elif self.refusals or self.truncated:
            self.failed_turns += 1  # the provider refused or cut off a reply: the model's play was not its own
        return False

    def copy(self) -> Stats:
        copied = Stats.__new__(Stats)
        copied.__dict__.update(self.__dict__)  # every field is a count
        return copied

    def add(self, other: Stats) -> None:
        for name, value in vars(other).items():  # every field is a count: most of a turn's are zero
            if value:
                setattr(self, name, getattr(self, name) + value)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = dict(vars(self))
        wakes = max(1, self.wakes)
        out["avg_update_tokens"] = round(self.update_chars / max(1, self.update_reads) / 4)
        out["avg_brief_tokens"] = round(self.brief_chars / max(1, self.brief_reads) / 4)
        out["avg_tools"] = round(self.tools_offered / wakes, 1)
        out["invalid_rate"] = round(self.invalid_calls / max(1, self.calls), 3)
        return out


def _woke(stats: Stats, fact: Woke) -> None:
    stats.wakes += 1
    stats.reactions += fact.reaction


def _read(stats: Stats, fact: Read) -> None:  # a turn reads each once (the text is kept)
    if fact.what == "brief":
        stats.brief_chars, stats.brief_reads = fact.chars, 1
    else:
        stats.update_chars, stats.update_reads = fact.chars, 1


def _offered(stats: Stats, fact: Offered) -> None:
    stats.tools_offered += fact.tools


def _called(stats: Stats, fact: Called) -> None:
    stats.calls += 1


def _refused(stats: Stats, fact: Refused) -> None:
    if fact.kind == "invalid":
        stats.invalid_calls += 1
    else:
        stats.rejected_actions += 1
        stats.faulted_actions += fact.kind == "faulted"


def _applied(stats: Stats, fact: Applied | Committed) -> None:
    stats.actions += 1


def _undone(stats: Stats, fact: Undone) -> None:
    stats.actions -= fact.actions
    stats.rejected_actions += fact.actions
    stats.undone_turns += 1
    stats.faulted_actions += fact.faulted


def _timed_out(stats: Stats, fact: TimedOut) -> None:  # once a turn: it closes
    stats.timeouts = 1


def _usage(stats: Stats, fact: Usage) -> None:
    for name, value in fact.counts.items():
        setattr(stats, name, getattr(stats, name) + value)


def _commit_refused(stats: Stats, fact: CommitRefused) -> None:
    stats.rejected_actions += 1
    stats.faulted_actions += fact.faulted


#: What each fact adds to the statistics.
_COUNTS: dict[type, Callable[[Stats, Any], None]] = {
    Woke: _woke, Read: _read, Offered: _offered, Called: _called, Refused: _refused, Applied: _applied,
    Undone: _undone, TimedOut: _timed_out, Usage: _usage, Committed: _applied, CommitRefused: _commit_refused}
#: The facts about a finished turn that still count: the engine's doing with its sealed choices, and usage its
#: participant reports after the turn is over. The one decision of what a finished turn's facts count (see the module
#: docstring): its brief read late, a refused call, a deadline that passes after it finished count nothing.
LATE = frozenset({Committed, CommitRefused, Undone, Usage})


# -- the sink ----------------------------------------------------------------------------------------------------------

class Facts:
    """The run's one sink of facts, folding each into the statistics and the diagnosis of ``state`` (see the module
    docstring). Emit under the run's gate."""

    def __init__(self, state: RunState):
        self.state = state

    def emit(self, fact: Fact, turn: Turn | None = None) -> None:
        """``fact`` happened, in ``turn`` when it is about one."""
        state = self.state
        kind = type(fact)
        if kind is Finished:
            assert turn is not None and isinstance(fact, Finished)
            turn.did_not_act = turn.stats.finish(fact)
            state.stats.add(turn.stats)
            self._agent(turn).add(turn.stats)
            turn.tallied = True
        elif turn is None:
            state.stats.fold(fact)
        elif not turn.tallied:
            turn.stats.fold(fact)
        elif kind in LATE:
            turn.stats.fold(fact)
            state.stats.fold(fact)
            self._agent(turn).fold(fact)
        else:
            return
        state.diagnosis.fold(fact, turn)

    def _agent(self, turn: Turn) -> Stats:
        """The numbers of ``turn``'s agent (a tournament bills each entrant for its own turns)."""
        agents = self.state.agent_stats
        stats = agents.get(turn.actor.id)
        if stats is None:
            stats = agents[turn.actor.id] = Stats()
        return stats
