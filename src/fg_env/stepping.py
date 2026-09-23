"""Stepping a copy of a run on the caller's own thread: it pauses wherever a controlled agent must decide.

The engine plays a round as a generator. A controlled agent's turn is played in steps: while the turn waits for
a decision the round yields, the run hands control back with its round kept, and the caller's tool calls go
straight into the turn — the same calls, validation and effects an agent's calls go through, with no thread
and no hand-off. A run waiting in a turn is copied directly (:mod:`.run_copy`), so a copy costs a copy of the
world, not a replay. (:mod:`.pilot` pauses a run on a thread of its own instead: for participants that need
one, for copies taken where a direct copy is not possible, and for runs stopped at a safe point.)

A chance node waits inside effects, where a generator cannot pause. With explicit chance, a stepper remembers a
copy of the run it was never played on (its base) and the decisions it has taken since, with the outcomes chosen
inside them. When a decision's effects reach a chance node whose outcome is not chosen yet, the decision is
abandoned there and the run as it was just before it is rebuilt from the base and those decisions. Choosing the
outcome plays the decision again on a copy of that run with the outcomes chosen so far: the engine is
deterministic, so it plays exactly as before up to the node and on from there exactly as a run paused at the node
would. What is read at such a node — beyond the value asked for in advance (``prefetch``, a game's returns) — is
read from a copy replayed on a thread and paused there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, FrozenSet, Iterator, List, Optional, Sequence, Tuple

from .entity import Entity
from .chance import ChanceNode
from .driving import WAITING, Driver
from .errors import RunError
from .expr import ExprError
from .participants import Participant
from .pilot import Pause, PilotedEnv
from .replay import Tape
from .session import ToolResult, Wake

if TYPE_CHECKING:
    from .branch import Branch
    from .turn import Turn

__all__ = ["SteppedEnv", "Stepper", "Waiting"]


class SteppedEnv(PilotedEnv):
    """A run whose controlled agents' turns are played in steps by a :class:`Stepper`."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.driver = _StepDriver(self)
        self.stepper: Optional[Stepper] = None


class _StepDriver(Driver):
    #: A copy has no wall clock: a turn waiting for a decision never runs out of time.
    timed = False

    def participant(self, actor: Entity) -> Participant:
        stepper = getattr(self.env, "stepper", None)
        if stepper is not None and actor.id in stepper.controlled:
            return _SEAT  # type: ignore[return-value]
        return super().participant(actor)


@dataclass
class Waiting:
    """A turn waiting for a decision: the wake its calls go through and the turn's own random stream."""

    wake: Wake
    rng: Any

    def __post_init__(self) -> None:
        self.pause = Pause("turn", wake=self.wake)


class _Seat:
    """The participant of a controlled agent: its turn waits, step by step, until the caller has ended it."""

    def steps(self, turn: "Turn") -> Iterator[object]:
        env: SteppedEnv = turn.env  # type: ignore[assignment]
        waiting = _driving(env)._waiting
        if waiting is None or waiting.wake._turn is not turn:  # a new turn (else a copy resumes its waiting turn)
            waiting = Waiting(env.wake_for(Wake(turn)), env.driver._rng(turn))
        while not waiting.wake.done:
            # Looked up on every step: a stepper that plays a chosen outcome on a copy hands the copy's run over.
            _driving(env)._waiting = waiting
            yield WAITING
        _driving(env)._waiting = None


def _driving(env: SteppedEnv) -> "Stepper":
    """The stepper driving ``env`` now."""
    assert env.stepper is not None
    return env.stepper


_SEAT = _Seat()


class _RoundFailed(BaseException):
    """Ends a round whose run failed while a turn waited, closing its turns as a failure raised inside them would."""


def _end_round(env: SteppedEnv) -> None:
    """End the round ``env`` waits in, on this thread, now: its waiting turns are closed and counted. (Closing the
    round's generator instead discards it without closing anything, as garbage collection does.)"""
    cursor = env._cursor
    if cursor is None:
        return
    try:
        cursor.throw(_RoundFailed())
    except (_RoundFailed, StopIteration):
        pass


class _ChanceWanted(BaseException):
    """A chance node was reached with no outcome chosen: the decision stops there."""

    def __init__(self, node: ChanceNode, tape: Tape, turn_count: int, prefetched: Tuple[Any, Optional[BaseException]]):
        super().__init__(node.name)
        self.node, self.tape, self.turn_count, self.prefetched = node, tape, turn_count, prefetched


@dataclass(frozen=True)
class _Decision:
    """A step a run takes: starting, or a tool call of the waiting turn."""

    kind: str
    name: str = ""
    args: Any = None


@dataclass(frozen=True)
class _Played:
    """A decision a run took since its base, with the chance outcomes chosen inside it (the one before it first)."""

    previous: Optional["_Played"]
    decision: _Decision
    chosen: Tuple[int, ...]


@dataclass(frozen=True)
class _AtChance:
    """A run stopped at a chance node: the run just before the decision (never played on), the decision, the
    outcomes chosen so far, and what a replay needs to pause at the node."""

    before: "Stepper"
    decision: _Decision
    chosen: Tuple[int, ...]
    node: ChanceNode
    tape: Tape
    turn_count: int
    prefetched: Tuple[Any, Optional[BaseException]]


class Stepper:
    """Steps ``env`` for the agents in ``controlled``, pausing for their decisions and, when ``explicit``, for chance
    nodes. ``prefetch(env)`` is evaluated at every chance node, for :meth:`read_prefetched`. ``settles``: a run that
    ends takes its result, as ``Env.run`` does — needed only when taking it draws randomness, since it changes nothing
    else."""

    def __init__(self, env: SteppedEnv, controlled: Sequence[str], explicit: bool,
                 prefetch: Optional[Callable[[Any], Any]] = None, settles: bool = True):
        self.controlled: FrozenSet[str] = frozenset(controlled)
        self.explicit, self.prefetch, self.settles = explicit, prefetch, settles
        self._env: Optional[SteppedEnv] = None
        self._waiting: Optional[Waiting] = None
        self._at_chance: Optional[_AtChance] = None
        self._chance_pause: Optional[Pause] = None
        self._chosen: List[int] = []
        self._reader: Optional["Branch"] = None
        #: Never played on: copies of it start from it (the base of their decisions).
        self.frozen = False
        self._base: Optional[Stepper] = None
        self._played: Optional[_Played] = None
        #: (stage, tool, last seat still choosing) → [decisions that stopped at a chance node, decisions]; shared by
        #: every copy, it only decides when copying ahead is cheaper than rebuilding, never what a decision does.
        self._chance_seen: Dict[Tuple[str, str, bool], List[int]] = {}
        self.attach(env, None)

    def attach(self, env: SteppedEnv, waiting: Optional[Waiting]) -> None:
        """Make ``env`` (waiting in ``waiting``, if anywhere) the run this stepper drives."""
        self._env, self._waiting = env, waiting
        env.stepper = self
        env.world.chance_picker = self._pick if self.explicit else None

    # -- what it waits for ----------------------------------------------------------------------------

    @property
    def pause(self) -> Optional[Pause]:
        if self._at_chance is not None:
            if self._chance_pause is None:
                self._chance_pause = Pause("chance", node=self._at_chance.node)
            return self._chance_pause
        return self._waiting.pause if self._waiting is not None else None

    def read(self, fn: Callable[[Any], Any]) -> Any:
        """``fn(env)`` on the run as it is now (inside the waiting turn, as the turn's own calls see the world)."""
        if self._at_chance is not None:
            reader = self._replayed()
            return reader._pilot.read(lambda: fn(reader._pilot.env))
        env = self._run()
        waiting = self._waiting
        if waiting is None:
            return fn(env)
        with env.world.turn_context(waiting.rng, waiting.wake._turn.pending):
            return fn(env)

    def read_prefetched(self) -> Any:
        """``prefetch(env)`` now: as evaluated at the chance node this run waits at, else directly."""
        if self.prefetch is None:
            return None
        if self._at_chance is None:
            return self.read(self.prefetch)
        value, error = self._at_chance.prefetched
        if error is not None:
            raise error
        return value

    # -- deciding -------------------------------------------------------------------------------------

    def start(self) -> None:
        """Run until the first decision or the end."""
        self._play(_Decision("start"), ())

    def call(self, name: str, args: Any) -> Optional[ToolResult]:
        """A tool call in the waiting turn; None when it stopped at a chance node."""
        waiting = self._waiting
        if waiting is None:
            raise RuntimeError(self._not_waiting("a tool call in a turn"))
        if not self.explicit:
            return self._play(_Decision("call", name, args), ())
        turn = waiting.wake._turn
        env = self._run()
        kind = (turn.stage.name, name, sum(1 for t in env.origin.staged if not t.done) <= 1)
        seen = self._chance_seen.setdefault(kind, [0, 0])
        if seen[1] and seen[0] * self._since_base() >= seen[1]:
            self._rebase()  # a rebuild here is expected to cost more than one copy now
        result = self._play(_Decision("call", name, args), ())
        seen[0] += self._at_chance is not None
        seen[1] += 1
        return result

    def _since_base(self) -> int:
        count, step = 0, self._played
        while step is not None:
            count, step = count + 1, step.previous
        return count

    def _rebase(self) -> None:
        """Make a frozen copy of the run as it is now the base its later decisions are rebuilt from."""
        base = self.clone()
        base.frozen = True
        self._base, self._played = base, None

    def choose(self, index: int) -> Optional[ToolResult]:
        """The outcome of the chance node the run waits at; the result of the call it completes, if any."""
        at = self._at_chance
        if at is None:
            raise RuntimeError(self._not_waiting("a chance outcome"))
        twin = at.before.clone()
        result = twin._play(at.decision, at.chosen + (index,))
        self._take(twin)
        return result

    def _play(self, decision: _Decision, chosen: Tuple[int, ...]) -> Optional[ToolResult]:
        if self.frozen:
            raise RuntimeError("a frozen run is only copied, never played on")
        self._chosen = list(chosen)
        result: Optional[ToolResult] = None
        try:
            if decision.kind == "start":
                self._advance()
            else:
                result = self._call_now(decision.name, decision.args)
        except _ChanceWanted as wanted:
            self._env, self._waiting = None, None
            self._at_chance = _AtChance(self._before(), decision, chosen, wanted.node, wanted.tape, wanted.turn_count,
                                        wanted.prefetched)
            return None
        if self.explicit:
            self._played = _Played(self._played, decision, chosen)
        return result

    def _before(self) -> "Stepper":
        """The run as it was before its latest decision, rebuilt from its base, frozen."""
        assert self._base is not None
        played: List[_Played] = []
        step = self._played
        while step is not None:
            played.append(step)
            step = step.previous
        run = self._base.clone()
        for step in reversed(played):
            run._play(step.decision, step.chosen)
            assert run._at_chance is None, "a decision played again with its chosen outcomes stopped at a chance node"
        run.frozen = True
        return run

    def _call_now(self, name: str, args: Any) -> ToolResult:
        env, waiting = self._run(), self._waiting
        assert waiting is not None
        with env.world.turn_context(waiting.rng, waiting.wake._turn.pending):
            try:
                result = waiting.wake.call(name, args)
            except (RunError, ExprError) as exc:  # the rules failed: the run fails, as it would for a participant
                self._fail(str(exc))
                raise
        if waiting.wake.done:
            self._advance()
        return result

    def _advance(self) -> None:
        """Play on until a turn waits for a decision, or the run ends."""
        env = self._run()
        try:
            env._play(None, None)
        except (RunError, ExprError) as exc:
            self._fail(str(exc))
            return
        except _ChanceWanted:
            raise
        except BaseException as exc:  # an engine defect or a participant's BaseException: surfaced
            self._waiting = None
            env._fail(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
            raise
        if self._waiting is None:
            self._ended()

    def _fail(self, message: str) -> None:
        env = self._run()
        self._waiting = None
        _end_round(env)
        env._fail(message)
        self._ended()

    def _ended(self) -> None:
        """What a run's session does when it ends."""
        env = self._run()
        env._flush_events()
        if self.settles:
            env.result()

    def _pick(self, node: ChanceNode) -> int:
        env = self._run()
        if not self._chosen:
            raise _ChanceWanted(node, env.origin.tape.copy(), env._turn_count, self._prefetch_now(env))
        index = self._chosen.pop(0)
        env.origin.tape.pick(index)
        return int(index)

    def _prefetch_now(self, env: SteppedEnv) -> Tuple[Any, Optional[BaseException]]:
        if self.prefetch is None:
            return None, None
        try:
            return self.prefetch(env), None
        except Exception as exc:  # raised again when the value is read, as a read at the node would raise it
            return None, exc

    # -- copies -----------------------------------------------------------------------------------------

    def clone(self) -> "Stepper":
        """An independent copy at this same moment."""
        from .run_copy import copy_run

        twin = Stepper.__new__(Stepper)
        twin.__dict__.update(self.__dict__)
        twin._reader, twin._chance_pause, twin._chosen, twin.frozen = None, None, [], False
        if self.frozen:
            twin._base, twin._played = self, None
        if self._at_chance is not None:
            return twin  # the run it rebuilds from is never played on, so it is shared
        env, waiting = copy_run(self._run(), self._waiting)
        twin.attach(env, waiting)
        return twin

    def _take(self, other: "Stepper") -> None:
        self.close()
        self._at_chance, self._chance_pause = other._at_chance, None
        self._base, self._played = other._base, other._played
        if other._env is not None:
            self.attach(other._env, other._waiting)
        else:
            self._env, self._waiting = None, None

    # -- reading ----------------------------------------------------------------------------------------

    def result(self) -> Any:
        return self.read(lambda env: env.result())

    def entity(self, entity_id: str) -> Any:
        return self.read(lambda env: env.entity(entity_id))

    def close(self) -> None:
        """Discard what the stepper holds (a replayed copy's thread stops)."""
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def _run(self) -> SteppedEnv:
        if self._env is None:
            raise RuntimeError("the run waits at a chance node: choose its outcome first")
        return self._env

    def _replayed(self) -> "Branch":
        """A copy of the run replayed on a thread and paused at the chance node this run waits at."""
        if self._reader is None:
            from .branch import Branch, copy_pilot

            at = self._at_chance
            assert at is not None
            source = at.before._run()
            pilot = copy_pilot(source, at.tape, at.turn_count, source.origin.base, controlled=set(self.controlled),
                               explicit=True)
            pilot.start()
            self._reader = Branch(pilot)
        return self._reader

    def _not_waiting(self, what: str) -> str:
        if self._at_chance is not None:
            return f"the run waits for a chance outcome (choose one), not {what}"
        env = self._env
        return "nothing is waiting for a decision: the run has " + ("finished" if env is None or env.finished else "stopped")
