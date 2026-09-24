"""Copies of a run, taken at any moment, to look ahead on without touching the real run.

``wake.clone()`` copies the run paused inside the agent's own turn; ``env.clone()`` copies a run
between rounds or stopped part-way through one. A copy is a copy of the run's state
(:meth:`~fg_env.runtime.env.Env.copy`), so it is exact — state, random streams, turn numbers, log, exposures,
frames, recorded host answers — and nothing it does reaches the original.
"""
from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from ..effects.chance import ChanceNode
from ..errors import RunError, SnapshotError
from ..information.schemas import ToolSpec
from ..participants import Participant
from ..runtime.measure import RunResult
from ..runtime.returns import seat_returns
from ..runtime.session import END_TURN, ToolResult
from .pilot import Pilot, PilotedEnv
from .replay import reseed

if TYPE_CHECKING:
    from ..runtime.env import Env
    from ..runtime.turn import Turn

__all__ = ["Decision", "Branch", "clone_turn", "clone_env", "driven_copy", "piloted", "use_chance", "keep_chance",
           "outcome_index"]

_Driven = TypeVar("_Driven", bound=PilotedEnv)


@dataclass(frozen=True)
class Decision:
    """What a paused copy waits for: an agent's tool call (``kind == "turn"``) or a chance outcome."""

    kind: str
    round: int
    actor: str | None = None
    stage: str | None = None
    #: A sealed turn of a simultaneous stage (choices commit when every agent has chosen).
    simultaneous: bool = False
    chance: ChanceNode | None = None


class Branch:
    """A private copy of a run that you drive: decide for the agents it pauses for, let it play on, read it.

    Everything runs on the copy's own thread; its methods wait for it. Close it (or use ``with``, or let
    it be garbage collected) to discard it.
    """

    def __init__(self, pilot: Pilot):
        self._pilot = pilot
        self._closer = weakref.finalize(self, pilot.abandon)

    # -- what it waits for -------------------------------------------------------------------------

    @property
    def pending(self) -> Decision | None:
        """The decision the copy waits for, or None when it has finished or stopped."""
        pause = self._pilot.pause
        if pause is None:
            return None
        if pause.kind == "chance":
            return Decision("chance", self._pilot.read(lambda: self._pilot.env.world.round), chance=pause.node)
        assert pause.wake is not None
        turn = pause.wake._turn
        return Decision("turn", turn.round, turn.actor.id, turn.stage.name, turn.staged)

    @property
    def tools(self) -> list[ToolSpec]:
        """The tools of the paused turn."""
        return list(self._turn_read(lambda wake: wake.tools))

    @property
    def brief(self) -> str:
        return str(self._turn_read(lambda wake: wake.brief))

    @property
    def update(self) -> str:
        return str(self._turn_read(lambda wake: wake.update))

    def _turn_read(self, fn: Callable[[Any], Any]) -> Any:
        pause = self._pilot._expect("turn")
        return self._pilot.read(lambda: fn(pause.wake))

    # -- deciding ----------------------------------------------------------------------------------

    def call(self, name: str, args: dict[str, Any] | None = None) -> ToolResult | None:
        """Make a tool call in the paused turn. Returns its result, or None when the call now waits on a
        decision inside it (a chance outcome, another agent's reaction): see :attr:`pending`."""
        return self._pilot.call(name, args)

    def end(self) -> ToolResult | None:
        """End the paused turn."""
        return self._pilot.call(END_TURN, {})

    def play(self, participant: Participant) -> None:
        """Let ``participant`` (any plain participant callable) take the rest of the paused turn."""
        self._pilot.play(participant)

    def choose(self, outcome: int | str) -> ToolResult | None:
        """Give the paused chance node an outcome, by index or label."""
        pause = self._pilot._expect("chance")
        assert pause.node is not None
        return self._pilot.choose(outcome_index(pause.node, outcome))

    def advance(self) -> Decision | None:
        """Continue a stopped copy until its next decision (or its end); returns :attr:`pending`."""
        if not self._pilot.running and not self._pilot.env.finished:
            self._pilot.stop = None
            self._pilot.start()
        return self.pending

    def run(self, participants: Any = None, *, rounds: int | None = None) -> RunResult:
        """Play on without pausing: every agent (the ones you controlled too) is played by ``participants``
        (as in ``Env.run``), for ``rounds`` more rounds or to the end. Chance is sampled from here on."""
        pilot = self._pilot
        env = pilot.env
        if participants is not None:
            pilot.read(lambda: env.driver.bind(participants))
        if rounds is not None:
            start, mid = pilot.read(lambda: (env.world.round, env.state.in_round))
            target = start + rounds - (1 if mid else 0)
            pilot.stop = lambda e: not e.state.in_round and e.world.round >= target
        else:
            pilot.stop = None
        if pilot.running:
            pilot.release()
        elif not env.finished:
            pilot.controlled.clear()
            pilot.explicit = False
            pilot.start()
        return self.result()

    def clone(self, *, seed: int | None = None, same_luck: bool = True) -> Branch:
        """Another independent copy at this same moment (exact by default; ``same_luck=False`` or ``seed``
        gives it fresh luck from here on)."""
        pilot = self._pilot
        branch = Branch(pilot.copy())
        if seed is not None or not same_luck:
            branch._reseed(seed if seed is not None else pilot.env.seeds.derive("clone", pilot.env.state.turn_count))
        return branch

    # -- reading -----------------------------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self._pilot.env.finished and not self._pilot.running

    @property
    def round(self) -> int:
        return int(self._pilot.read(lambda: self._pilot.env.world.round))

    def entity(self, entity_id: str) -> dict[str, Any] | None:
        return self._pilot.read(lambda: self._pilot.env.entity(entity_id))

    def entities(self, type_name: str | None = None, alive: bool = True) -> list[dict[str, Any]]:
        return self._pilot.read(lambda: self._pilot.env.entities(type_name, alive))

    @property
    def props(self) -> dict[str, Any]:
        return self._pilot.read(lambda: self._pilot.env.props)

    def result(self) -> RunResult:
        return self._pilot.read(self._pilot.env.result)

    def returns(self) -> dict[str, float]:
        """Each seat's return so far (its type's ``score.value``); ``{}`` when no type scores."""
        env = self._pilot.env
        return self._pilot.read(lambda: seat_returns(env.contract, env.world))

    def close(self) -> None:
        """Discard the copy (its thread stops before this returns)."""
        self._closer.detach()
        self._pilot.close()

    def __enter__(self) -> Branch:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        pending = self.pending
        state = "finished" if self.finished else (f"waiting for {pending.actor or 'chance'}" if pending else "stopped")
        return f"<Branch round {self.round}, {state}>"

    def _reseed(self, seed: int) -> None:
        pause = self._pilot.pause
        if pause is None or pause.kind != "turn" or pause.wake is None:
            raise RunError("fresh luck is drawn from inside a turn; this copy is not paused in one", "clone")
        turn = pause.wake._turn
        self._pilot.read(lambda: reseed(turn, seed))


def outcome_index(node: ChanceNode, outcome: int | str) -> int:
    """The index of a possible outcome given by index or label."""
    for item in node.possible:
        if ((isinstance(outcome, int) and not isinstance(outcome, bool) and item.index == outcome) or item.label
            == outcome):
            return item.index
    listed = ", ".join(f"{item.index} ({item.label})" for item in node.possible)
    raise ValueError(f"{outcome!r} is not a possible outcome of chance '{node.name}' (possible: {listed})")


# -- making copies ------------------------------------------------------------------------------------


def driven_copy(source: Env, kind: type[_Driven], participants: Any = None, waiting: Turn | None = None) -> _Driven:
    """A copy of ``source`` as it is now (``waiting`` in that turn), as a run of ``kind`` that search code or a
    controller drives: its turns one at a time, its agents played by ``participants`` — by default the run's named
    participants (its callables are the caller's own, never called by a copy)."""
    with source.gate:
        env = source.copy(kind, waiting=waiting)
    env.parallel = 1
    env.driver.spec = {key: value for key, value in source.driver.spec.items() if isinstance(value, str)}
    env.driver.bind(participants)
    return env


def piloted(source: Env, *, controlled: set[str], explicit: bool = False, participants: Any = None,
            waiting: Turn | None = None) -> Pilot:
    """A pilot, not yet started, of a :func:`driven_copy` of ``source``."""
    return Pilot(driven_copy(source, PilotedEnv, participants, waiting), controlled=controlled, explicit=explicit)


def clone_turn(turn: Turn, *, participants: Any = None, seed: int | None = None,
               same_luck: bool = False, controlled: set[str] | None = None, explicit: bool = False) -> Branch:
    """A copy of ``turn``'s run paused in that turn (see :meth:`Wake.clone`). ``controlled`` names every agent
    the copy pauses for from this turn on (default: the turn's own agent); ``explicit`` makes chance nodes from this
    turn on wait for :meth:`Branch.choose`."""
    source = turn.env
    if turn.peek or turn.done:
        raise RuntimeError("this turn is over; clone the run while the turn is in progress")
    if turn is not source.state.cursor.turn and turn not in source.state.staged:
        raise RunError(f"{turn.actor.id}'s turn is a reaction inside another agent's call, where a copy of the run "
                       "cannot begin; clone the turn it reacts to, or the run between turns", "clone")
    pilot = piloted(source, controlled={turn.actor.id} | set(controlled or ()), participants=participants,
                    waiting=turn)
    pilot.start()
    env = pilot.env
    if explicit:
        pilot.explicit = True
        env.world.chance_picker = pilot._pick
    branch = Branch(pilot)
    pending = branch.pending
    if pending is None or pending.kind != "turn" or pending.actor != turn.actor.id:
        why = env.error or "the run's state was changed outside the engine"
        branch.close()
        raise RunError(f"the copy did not reach {turn.actor.id}'s turn (turn {turn.number}): {why}", "clone")
    if not same_luck:
        branch._reseed(seed if seed is not None else source.seeds.derive("clone", turn.number))
    return branch


def clone_env(source: Env) -> Env:
    """A copy of ``source`` between rounds, or stopped at the same safe point part-way through a round."""
    if source.state.in_round and source.status != "stopped":
        raise SnapshotError("a round is being played right now; clone a turn from inside it with wake.clone(), "
                            "or stop the run first (env.run(stop=...))")
    return source.copy()


def use_chance(env: Env, chance: Any) -> None:
    """Set how ``env`` decides `chance` effects: ``"sampled"``, or a callable choosing each outcome."""
    if chance == "sampled":
        env.world.chance_picker = None
        return
    if chance == "explicit":
        raise ValueError("explicit chance needs something to choose each outcome: enumerate and choose them with "
                         "fg_env.rl.game(..., chance='explicit'), or pass a callable to chance=")
    if not callable(chance):
        raise ValueError(f"chance must be 'sampled' or a callable choosing each outcome, got {chance!r}")

    def pick(node: ChanceNode) -> int:
        return int(chance(node))

    pick.choose = chance  # type: ignore[attr-defined]  # so a copy of the run chooses the same way
    env.world.chance_picker = pick


def keep_chance(source: Env, copy: Env) -> None:
    """Give a copy the chance chooser its source was loaded with (``use_chance``)."""
    choose = getattr(source.world.chance_picker, "choose", None)
    if choose is not None:
        use_chance(copy, choose)
