"""Stepping a copy of a run on the caller's own thread: it pauses wherever a controlled agent must decide.

The engine plays a round as a generator. A controlled agent's turn is played in steps: while the turn waits for
a decision the round yields, the run hands control back with its round kept, and the caller's tool calls go
straight into the turn — the same calls, validation and effects an agent's calls go through, with no thread
and no hand-off. A run waiting in a turn is copied directly (:mod:`.run_copy`), so a copy costs a copy of the
world, not a replay. (:mod:`.pilot` pauses a run on a thread of its own instead: for participants that need
one, for copies taken where a direct copy is not possible, and for runs stopped at a safe point.)

A chance node waits inside effects, where a generator cannot pause. With explicit chance, every decision starts
from a copy of the run as it was just before it; when the decision's effects reach a chance node whose outcome
is not chosen yet, it is abandoned there. Choosing the outcome plays the decision again on a fresh copy with
the outcomes chosen so far: the engine is deterministic, so it plays exactly as before up to the node and on
from there exactly as a run paused at the node would. What is read at such a node — beyond the value asked for
in advance (``prefetch``, a game's returns) — is read from a copy replayed on a thread and paused there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, FrozenSet, Iterator, List, Optional, Sequence, Tuple

from ..entity import Entity
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

    concurrent = False

    def steps(self, turn: "Turn") -> Iterator[object]:
        stepper: Stepper = turn.env.stepper  # type: ignore[attr-defined]
        env = stepper._run()
        waiting = stepper._waiting
        if waiting is None or waiting.wake._turn is not turn:  # a new turn (else a copy resumes its waiting turn)
            waiting = Waiting(env.wake_for(Wake(turn)), env.driver._rng(turn))
        while not waiting.wake.done:
            stepper._waiting = waiting
            yield WAITING
        stepper._waiting = None


_SEAT = _Seat()


class _ChanceWanted(BaseException):
    """A chance node was reached with no outcome chosen: the decision stops there."""

    def __init__(self, node: ChanceNode, tape: Tape, turn_count: int, prefetched: Tuple[Any, Optional[BaseException]]):
        super().__init__(node.name)
        self.node, self.tape, self.turn_count, self.prefetched = node, tape, turn_count, prefetched


@dataclass(frozen=True)
class _Decision:
    """What a run was doing when a chance node stopped it: starting, or a tool call of the waiting turn."""

    kind: str
    name: str = ""
    args: Any = None


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

    @property
    def pause(self) -> Pause:
        return Pause("chance", node=self.node)


class Stepper:
    """Steps ``env`` for the agents in ``controlled``, pausing for their decisions and, when ``explicit``, for chance
    nodes. ``prefetch(env)`` is evaluated at every chance node, for :meth:`read_prefetched`."""

    def __init__(self, env: SteppedEnv, controlled: Sequence[str], explicit: bool,
                 prefetch: Optional[Callable[[Any], Any]] = None):
        self.controlled: FrozenSet[str] = frozenset(controlled)
        self.explicit = explicit
        self.prefetch = prefetch
        self._env: Optional[SteppedEnv] = None
        self._waiting: Optional[Waiting] = None
        self._at_chance: Optional[_AtChance] = None
        self._chosen: List[int] = []
        self._reader: Optional["Branch"] = None
        self._chance_pause: Optional[Pause] = None
        self.attach(env, None)

    def attach(self, env: SteppedEnv, waiting: Optional[Waiting]) -> None:
        """Make ``env`` (waiting in ``waiting``, if anywhere) the run this stepper drives."""
        self._env, self._waiting = env, waiting
        env.stepper = self
        env.world.chance_picker = self._pick if self.explicit else None

    def _twin(self) -> "Stepper":
        twin = Stepper.__new__(Stepper)
        twin.controlled, twin.explicit, twin.prefetch = self.controlled, self.explicit, self.prefetch
        twin._env, twin._waiting, twin._at_chance, twin._chosen = None, None, None, []
        twin._reader, twin._chance_pause = None, None
        return twin

    # -- what it waits for ----------------------------------------------------------------------------

    @property
    def pause(self) -> Optional[Pause]:
        if self._at_chance is not None:
            if self._chance_pause is None:
                self._chance_pause = self._at_chance.pause
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
        """``prefetch(env)`` now: evaluated at the chance node this run waits at, else directly."""
        if self._at_chance is None or self.prefetch is None:
            return self.read(self.prefetch) if self.prefetch is not None else None
        value, error = self._at_chance.prefetched
        if error is not None:
            raise error
        return value

    # -- deciding -------------------------------------------------------------------------------------

    def start(self) -> None:
        """Run until the first decision or the end."""
        self._decide(_Decision("start"))

    def call(self, name: str, args: Any) -> Optional[ToolResult]:
        """A tool call in the waiting turn; None when it stopped at a chance node."""
        if self._waiting is None:
            raise RuntimeError(self._not_waiting("a tool call in a turn"))
        return self._decide(_Decision("call", name, args))

    def choose(self, index: int) -> Optional[ToolResult]:
        """The outcome of the chance node the run waits at; the result of the call it completes, if any."""
        at = self._at_chance
        if at is None:
            raise RuntimeError(self._not_waiting("a chance outcome"))
        twin = at.before.clone()
        result = twin._play(at.decision, at.before, at.chosen + (index,))
        self._take(twin)
        return result

    def _decide(self, decision: _Decision) -> Optional[ToolResult]:
        before = self.clone() if self.explicit else None
        return self._play(decision, before, ())

    def _play(self, decision: _Decision, before: Optional["Stepper"], chosen: Tuple[int, ...]) -> Optional[ToolResult]:
        self._chosen = list(chosen)
        try:
            if decision.kind == "start":
                self._advance()
                return None
            return self._call_now(decision.name, decision.args)
        except _ChanceWanted as wanted:
            assert before is not None
            self._env, self._waiting = None, None
            self._at_chance = _AtChance(before, decision, chosen, wanted.node, wanted.tape, wanted.turn_count,
                                        wanted.prefetched)
            return None

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
        env._fail(message)
        self._ended()

    def _ended(self) -> None:
        """What a run's session does when it ends (its result is taken, as ``Env.run`` returns it)."""
        env = self._run()
        env._flush_events()
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

        twin = self._twin()
        if self._at_chance is not None:
            twin._at_chance = self._at_chance  # never played on, so shared
            return twin
        env, waiting = copy_run(self._run(), self._waiting)
        twin.attach(env, waiting)
        return twin

    def _take(self, other: "Stepper") -> None:
        self.close()
        self._at_chance, self._chance_pause = other._at_chance, None
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
