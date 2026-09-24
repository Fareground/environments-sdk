"""Playing back what participants did: a recorded run rerun, or a decision taken again.

A turn keeps, as its steps, everything its participant did through its wake — first reads of the brief, update and
tools, every tool call, reported usage, uploads, a reseed, a timeout — and a run that records exposures keeps every
turn's steps. The engine is deterministic under its seed, so playing the steps back into the same run in place of its
participants reproduces it exactly: the same state, random streams, turn numbers and log. That is what a recording's
rerun does (:mod:`fg_env.trace.rerun`), what a copy paused inside a decision does to take the decision again
(:mod:`.pilot`), and what the kernel's tests hold every copy of a run to. Copies of a run are copies of its state
(:meth:`~fg_env.runtime.env.Env.copy`), never replays.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..errors import RunError
from ..runtime.facts import TIMED_OUT
from ..sampling.seeds import SeedTree

if TYPE_CHECKING:
    from ..runtime.session import Wake

__all__ = ["Tape", "Playback", "apply_step", "reseed"]

Entry = tuple[Any, ...]


class Tape:
    """What participants did, to play back: turn by turn, and the chance outcomes chosen."""

    __slots__ = ("turns", "open", "picks")

    def __init__(self) -> None:
        #: turn number → (actor id, what the participant did, in order)
        self.turns: dict[int, tuple[str, list[Entry]]] = {}
        #: Turns still in progress where the playback ends: whoever controls the run decides them on from there.
        self.open: set[int] = set()
        #: Outcomes chance choosers chose, in order.
        self.picks: list[int] = []


class Playback:
    """A tape being played back into the run it was recorded from.

    ``turn_count`` is the number of turns the run had started where the tape begins: a turn after that, or one
    still in progress where the tape ends, is live — once its recorded steps are played it is decided by whoever
    controls the run.
    """

    def __init__(self, tape: Tape, turn_count: int):
        self._turns = dict(tape.turns)
        self._open = set(tape.open)
        self._picks = list(tape.picks)
        self.turn_count = turn_count

    def live(self, number: int) -> bool:
        return number > self.turn_count or number in self._open

    def next_pick(self) -> Any:
        """The next recorded chance outcome, or None when the tape has no more."""
        return self._picks.pop(0) if self._picks else None

    @property
    def has_picks(self) -> bool:
        return bool(self._picks)

    def play(self, wake: Wake) -> bool:
        """Play a turn's recorded steps into ``wake``; False when the tape holds none for it."""
        turn = wake._turn
        recorded = self._turns.pop(turn.number, None)
        if recorded is None:
            return False
        actor, entries = recorded
        if actor != turn.actor.id:
            raise RunError(f"the playback diverged from the run it was recorded in: turn {turn.number} was {actor}'s "
                           f"and is now {turn.actor.id}'s (was the run's state changed outside the engine?)",
                           f"replay:turn {turn.number}")
        for position, entry in enumerate(entries):
            if wake.done:
                raise RunError(f"the playback diverged from the run it was recorded in: turn {turn.number} ended after "
                               f"{position} of its {len(entries)} recorded steps", f"replay:turn {turn.number}")
            apply_step(wake, entry)
        return True


def apply_step(wake: Wake, entry: Entry) -> None:
    """Do one recorded step in ``wake``: a read, a call, reported usage, a reseed or a timeout."""
    kind = entry[0]
    if kind == "brief":
        wake.brief
    elif kind == "update":
        wake.update
    elif kind == "tools":
        wake.tools
    elif kind == "call":
        wake.call(entry[1], _copy(entry[2]))
    elif kind == "usage":
        wake.record_usage(**entry[1])
    elif kind == "upload":
        turn = wake._turn
        with turn.env._lock:
            turn.env.world.assets.adopt(entry[1])
            turn.record("upload", entry[1])
    elif kind == "reseed":
        reseed(wake._turn, entry[1])
    elif kind == "timeout":
        turn = wake._turn
        with turn.env._lock:
            turn.timed_out = True
            turn.note(TIMED_OUT)
            turn.close()
    else:
        raise RunError(f"unknown step {kind!r} on the tape", "replay")


def reseed(turn: Any, seed: int) -> None:
    """Draw the run's luck from ``seed`` from this moment in ``turn`` on (one of its steps, so a playback does it
    too). Call from inside the turn's own context (its thread or task)."""
    env = turn.env
    turn.record("reseed", seed)
    tree = SeedTree(seed)
    env.seed = seed
    luck = env.world.luck
    env.seeds = luck.seeds = tree
    luck.main = tree.rng("run")
    turn.rng = tree.rng("turn", env.world.round, turn.number)
    luck.switch(turn.rng)
    env.driver._resolved.clear()


def _copy(value: Any) -> Any:
    if isinstance(value, list):
        return [_copy(item) for item in value]
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    return value
