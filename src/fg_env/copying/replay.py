"""Replays: what a run did since its base, so a copy can be rebuilt exactly at any point.

A run's base is the state it started from: its build, a restored snapshot, or a checkpoint taken at
the start of a round. The tape records, turn by turn, everything a participant did through its
wake — first reads of the brief, update and tools, every tool call, reported usage — plus every
chance outcome a picker chose and how many safe points the run has passed. The engine is
deterministic under its seed, so rebuilding the base and playing the tape back in place of the
participants reproduces the run exactly: the same state, random streams, turn numbers and log.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from ..errors import RunError
from ..sampling.seeds import SeedTree

if TYPE_CHECKING:
    from ..runtime.session import Wake

__all__ = ["Origin", "Tape", "Playback", "apply_step", "reseed"]

Entry = tuple[Any, ...]


class Origin:
    """Where copies of a run start from: its base (a snapshot; None for its build) and the tape since."""

    __slots__ = ("base", "start", "tape", "checkpoint_due", "staged", "unarmed")

    def __init__(self, contract: Any):
        self.base: dict[str, Any] | None = None
        #: Where the run's recording replays from when its build cannot rebuild it: the snapshot a fork continued
        #: from, exposures given as counts (see :func:`~fg_env.copying.snapshot.recording_start`); None otherwise.
        self.start: dict[str, Any] | None = None
        self.tape = Tape()
        #: Take a fresh base at the next round start (the run was copied part-way through a round).
        self.checkpoint_due = False
        #: The sealed turns of the simultaneous stage being played.
        self.staged: list[Any] = []
        #: The contract before its arm was applied (forks switch arms from it).
        self.unarmed = contract

    def round_start(self, env: Any) -> None:
        """Called before every round: the base moves here when a copy asked for it."""
        if self.checkpoint_due:
            from .snapshot import take_snapshot

            self.base, self.tape, self.checkpoint_due = take_snapshot(env), Tape(), False


class Tape:
    """Everything participants did since the run's base."""

    __slots__ = ("_lock", "turns", "open", "picks", "points")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        #: turn number → (actor id, what the participant did, in order)
        self.turns: dict[int, tuple[str, list[Entry]]] = {}
        #: Turns in progress (a participant is deciding, or a reaction runs inside one of its calls).
        self.open: set[int] = set()
        #: Outcomes chance pickers chose, in order.
        self.picks: list[int] = []
        #: Safe points passed since the base (see ``Env.run(stop=...)``).
        self.points = 0

    def record(self, number: int, actor: str, entry: Entry) -> None:
        with self._lock:
            self.turns.setdefault(number, (actor, []))[1].append(entry)

    def opened(self, number: int) -> None:
        with self._lock:
            self.open.add(number)

    def closed(self, number: int) -> None:
        with self._lock:
            self.open.discard(number)

    def pick(self, index: int) -> None:
        with self._lock:
            self.picks.append(index)

    def copy(self) -> Tape:
        with self._lock:
            out = Tape()
            out.turns = {number: (actor, list(entries)) for number, (actor, entries) in self.turns.items()}
            out.open, out.picks, out.points = set(self.open), list(self.picks), self.points
            return out


class Playback:
    """A tape being played back into a copy of the run it was recorded from.

    ``turn_count`` is the number of turns the original had started when the copy was taken: a turn
    after that, or one still in progress then, is live — once its recorded steps are played it is
    decided by whoever controls the copy.
    """

    def __init__(self, tape: Tape, turn_count: int):
        self._turns = dict(tape.turns)
        self._open = set(tape.open)
        self._picks = list(tape.picks)
        self.turn_count = turn_count
        self.points = tape.points

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
            raise RunError(f"the copy diverged from the run it was taken from: turn {turn.number} was {actor}'s and is "
                           f"now {turn.actor.id}'s (was the original's state changed outside the engine?)",
                           f"replay:turn {turn.number}")
        for position, entry in enumerate(entries):
            if wake.done:
                raise RunError(f"the copy diverged from the run it was taken from: turn {turn.number} ended after "
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
            turn.stats.timeouts = 1
            turn.close()
    else:
        raise RunError(f"unknown step {kind!r} on the tape", "replay")


def reseed(turn: Any, seed: int) -> None:
    """Draw the run's luck from ``seed`` from this moment in ``turn`` on (recorded, so copies replay it).
    Call from inside the turn's own context (its thread or task)."""
    env = turn.env
    turn.record("reseed", seed)
    tree = SeedTree(seed)
    env.seed = seed
    env.seeds = env.world.seeds = tree
    env.world.rng = tree.rng("run")
    env.world._here().rng = tree.rng("turn", env.world.round, turn.number)
    env.driver._resolved.clear()


def _copy(value: Any) -> Any:
    if isinstance(value, list):
        return [_copy(item) for item in value]
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    return value
