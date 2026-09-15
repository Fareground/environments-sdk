"""Copies of a run, taken at any moment, to look ahead on without touching the real run.

``wake.clone()`` copies the run paused inside the agent's own turn; ``env.clone()`` copies a run
between rounds or stopped part-way through one. A copy is rebuilt from the run's base and replays
its tape (:mod:`.replay`), so it is exact — state, random streams, turn numbers, log, exposures,
frames, recorded host answers — and nothing it does reaches the original.
"""
from __future__ import annotations

import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Set, Type, TypeVar, Union

from .actions import ToolSpec
from .chance import ChanceNode
from .errors import RunError, SnapshotError
from .measure import RunResult
from .participants import Participant
from .pilot import Pilot, PilotedEnv
from .replay import Playback, Tape, reseed
from .returns import seat_returns
from .runtime import Env
from .session import END_TURN, ToolResult
from .snapshot import restore_state, take_snapshot

if TYPE_CHECKING:
    from .turn import Turn

__all__ = ["Decision", "Branch", "clone_turn", "clone_env", "copy_pilot", "fresh_copy", "use_chance", "outcome_index"]

_Copy = TypeVar("_Copy", bound=PilotedEnv)


@dataclass(frozen=True)
class Decision:
    """What a paused copy waits for: an agent's tool call (``kind == "turn"``) or a chance outcome."""

    kind: str
    round: int
    actor: Optional[str] = None
    stage: Optional[str] = None
    #: A sealed turn of a simultaneous stage (choices commit when every agent has chosen).
    simultaneous: bool = False
    chance: Optional[ChanceNode] = None


class Branch:
    """A private copy of a run that you drive: decide for the agents it pauses for, let it play on, read it.

    Everything runs on the copy's own thread; its methods wait for it. Close it (or use ``with``, or let
    it be garbage collected) to discard it.
    """

    def __init__(self, pilot: Pilot):
        self._pilot = pilot
        self._closer = weakref.finalize(self, pilot.close)

    # -- what it waits for -------------------------------------------------------------------------

    @property
    def pending(self) -> Optional[Decision]:
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
    def tools(self) -> List[ToolSpec]:
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

    def call(self, name: str, args: Optional[Dict[str, Any]] = None) -> Optional[ToolResult]:
        """Make a tool call in the paused turn. Returns its result, or None when the call now waits on a
        decision inside it (a chance outcome, another agent's reaction): see :attr:`pending`."""
        return self._pilot.call(name, args)

    def end(self) -> Optional[ToolResult]:
        """End the paused turn."""
        return self._pilot.call(END_TURN, {})

    def play(self, participant: Participant) -> None:
        """Let ``participant`` (any plain participant callable) take the rest of the paused turn."""
        self._pilot.play(participant)

    def choose(self, outcome: Union[int, str]) -> Optional[ToolResult]:
        """Give the paused chance node an outcome, by index or label."""
        pause = self._pilot._expect("chance")
        assert pause.node is not None
        return self._pilot.choose(outcome_index(pause.node, outcome))

    def advance(self) -> Optional[Decision]:
        """Continue a stopped copy until its next decision (or its end); returns :attr:`pending`."""
        if not self._pilot.running and not self._pilot.env.finished:
            self._pilot.stop = None
            self._pilot.start()
        return self.pending

    def run(self, participants: Any = None, *, rounds: Optional[int] = None) -> RunResult:
        """Play on without pausing: every agent (the ones you controlled too) is played by ``participants``
        (as in ``Env.run``), for ``rounds`` more rounds or to the end. Chance is sampled from here on."""
        pilot = self._pilot
        env = pilot.env
        if participants is not None:
            pilot.read(lambda: env.driver.bind(participants))
        if rounds is not None:
            start, mid = pilot.read(lambda: (env.world.round, env._in_round))
            target = start + rounds - (1 if mid else 0)
            pilot.stop = lambda e: not e._in_round and e.world.round >= target
        else:
            pilot.stop = None
        if pilot.running:
            pilot.release()
        elif not env.finished:
            pilot.controlled.clear()
            pilot.explicit = False
            pilot.start()
        return self.result()

    def clone(self, *, seed: Optional[int] = None, same_luck: bool = True) -> "Branch":
        """Another independent copy at this same moment (exact by default; ``same_luck=False`` or ``seed``
        gives it fresh luck from here on)."""
        pilot = self._pilot
        env = pilot.env
        origin = env.origin
        tape, count, base, where = pilot.read(lambda: (origin.tape.copy(), env._turn_count, origin.base,
                                                       (env.world.round, env._in_round, origin.tape.points)))
        copy = copy_pilot(env, tape, count, base, controlled=pilot.controlled, explicit=pilot.explicit,
                          checkpoints=pilot.checkpoints)
        if pilot.pause is None and not env.finished:
            copy.stop = _at_point(*where)
        copy.start()
        branch = Branch(copy)
        if seed is not None or not same_luck:
            branch._reseed(seed if seed is not None else env.seeds.derive("clone", count))
        return branch

    # -- reading -----------------------------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self._pilot.env.finished and not self._pilot.running

    @property
    def round(self) -> int:
        return int(self._pilot.read(lambda: self._pilot.env.world.round))

    def entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        return self._pilot.read(lambda: self._pilot.env.entity(entity_id))

    def entities(self, type_name: Optional[str] = None, alive: bool = True) -> List[Dict[str, Any]]:
        return self._pilot.read(lambda: self._pilot.env.entities(type_name, alive))

    @property
    def props(self) -> Dict[str, Any]:
        return self._pilot.read(lambda: self._pilot.env.props)

    def result(self) -> RunResult:
        return self._pilot.read(self._pilot.env.result)

    def returns(self) -> Dict[str, float]:
        """Each seat's return so far (the contract's ``game.returns``); ``{}`` when none is declared."""
        env = self._pilot.env
        return self._pilot.read(lambda: seat_returns(env.contract, env.world))

    def close(self) -> None:
        """Discard the copy (its thread stops)."""
        self._closer()

    def __enter__(self) -> "Branch":
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


def outcome_index(node: ChanceNode, outcome: Union[int, str]) -> int:
    """The index of a possible outcome given by index or label."""
    for item in node.possible:
        if (isinstance(outcome, int) and not isinstance(outcome, bool) and item.index == outcome) or item.label == outcome:
            return item.index
    listed = ", ".join(f"{item.index} ({item.label})" for item in node.possible)
    raise ValueError(f"{outcome!r} is not a possible outcome of chance '{node.name}' (possible: {listed})")


# -- making copies ------------------------------------------------------------------------------------


def copy_pilot(source: Env, tape: Tape, turn_count: int, base: Optional[Mapping[str, Any]], *,
               controlled: Set[str], explicit: bool, participants: Any = None, checkpoints: bool = False) -> Pilot:
    """A pilot for a fresh copy of ``source`` that will replay ``tape`` from ``base``."""
    env = fresh_copy(source, base, participants, PilotedEnv)
    return Pilot(env, playback=Playback(tape, turn_count), controlled=controlled, explicit=explicit,
                 checkpoints=checkpoints)


def fresh_copy(source: Env, base: Optional[Mapping[str, Any]], participants: Any, kind: Type[_Copy]) -> _Copy:
    """A new run of ``kind`` built like ``source`` — from ``base`` (a snapshot), else from its build — bound to its
    hosts and to ``participants`` (default: its named participants)."""
    if base is None:
        seed = source.build_seed if isinstance(source, PilotedEnv) else source.seed
        env = kind(source.contract, source.inputs, seed, source.arm, parallel=1,
                   exposures=source.world.exposures is not None, assets=source.world.assets.catalog())
    else:
        env = restore_state(kind, source.contract, base, parallel=1)
    env.origin.base, env.origin.unarmed = dict(base) if base is not None else None, source.origin.unarmed
    _share_hosts(source, env)
    named = {key: value for key, value in source.driver.spec.items() if isinstance(value, str)}
    env.driver.bind(participants if participants is not None else (named or None))
    return env


def clone_turn(turn: "Turn", *, participants: Any = None, seed: Optional[int] = None,
               same_luck: bool = False, controlled: Optional[Set[str]] = None, explicit: bool = False) -> Branch:
    """A copy of ``turn``'s run paused in that turn (see :meth:`Wake.clone`). ``controlled`` names every agent
    the copy pauses for from this turn on (default: the turn's own agent); ``explicit`` makes chance nodes from this
    turn on wait for :meth:`Branch.choose` (the copy's past plays back as it happened)."""
    source = turn.env
    if turn.peek or turn.done:
        raise RuntimeError("this turn is over; clone the run while the turn is in progress")
    with source._lock:
        tape, count, base = source.origin.tape.copy(), source._turn_count, source.origin.base
    source.origin.checkpoint_due = True  # later copies of this run replay from its next round, not from its base
    pilot = copy_pilot(source, tape, count, base, controlled={turn.actor.id} | set(controlled or ()), explicit=False,
                       participants=participants)
    pilot.start()
    if explicit:
        pilot.explicit = True
        pilot.env.world.chance_picker = pilot._pick
    branch = Branch(pilot)
    pending = branch.pending
    if pending is None or pending.kind != "turn" or pending.actor != turn.actor.id:
        why = pilot.env.error or "the run's state was changed outside the engine"
        branch.close()
        raise RunError(f"the copy did not reach {turn.actor.id}'s turn (turn {turn.number}): {why}", "clone")
    if not same_luck:
        branch._reseed(seed if seed is not None else source.seeds.derive("clone", turn.number))
    return branch


def clone_env(source: Env) -> Env:
    """A copy of ``source`` between rounds, or stopped at the same safe point part-way through a round."""
    from .host.hosts import bind, hosts_for

    if not source._in_round:
        snapshot = take_snapshot(source)
        copy = restore_state(type(source), source.contract, snapshot, source.parallel)
        copy.origin.base, copy.origin.unarmed = snapshot, source.origin.unarmed
        copy.driver.spec = dict(source.driver.spec)
        copy.time_limit = source.time_limit
        _keep_chance(source, copy)
        hosts = hosts_for(source.world)
        if hosts is not None:
            bind(copy, hosts)
        return copy
    if source.status != "stopped":
        raise SnapshotError("a round is being played right now; clone a turn from inside it with wake.clone(), "
                            "or stop the run first (env.run(stop=...))")
    pilot = copy_pilot(source, source.origin.tape.copy(), source._turn_count, source.origin.base, controlled=set(),
                       explicit=False)
    env = pilot.env
    pilot.stop = _at_point(source.world.round, True, source.origin.tape.points)
    env.run(stop=pilot.stop_here)
    env.pilot = None
    env.world.chance_picker = None
    _keep_chance(source, env)
    env.parallel, env.time_limit = source.parallel, source.time_limit
    if env.status != "stopped" or env.origin.tape.points != source.origin.tape.points:
        raise RunError("the copy did not stop where the original stopped; the run's state was changed outside the "
                       "engine", "clone")
    source.origin.checkpoint_due = True
    return env


def _at_point(round_: int, in_round: bool, points: int) -> Callable[[Env], bool]:
    """A stop condition for the safe point a run is stopped at."""
    return lambda env: env.world.round == round_ and env._in_round == in_round and \
        (not in_round or env.origin.tape.points >= points)


def _share_hosts(source: Env, copy: Env) -> None:
    """Bind the copy to the source's hosts, answering first from every answer the source has recorded."""
    from .host.hosts import Hosts, bind, hosts_for
    from .host.tape import tape_of

    hosts = hosts_for(source.world)
    if hosts is None:
        return
    adapters = {name: hosts.adapter(name) for name in hosts.names if hosts.adapter(name) is not None}
    bind(copy, Hosts(adapters, replay={**hosts.replay, **tape_of(source)}, live=hosts.live))


def use_chance(env: Env, chance: Any) -> None:
    """Set how ``env`` decides `chance` effects: ``"sampled"``, or a callable choosing each outcome."""
    if chance == "sampled":
        env.world.chance_picker = None
        return
    if chance == "explicit":
        raise ValueError("explicit chance needs something to choose each outcome: enumerate and choose them with "
                         "fg_env.game(..., chance='explicit'), or pass a callable to chance=")
    if not callable(chance):
        raise ValueError(f"chance must be 'sampled' or a callable choosing each outcome, got {chance!r}")

    def pick(node: ChanceNode) -> int:
        index = chance(node)
        env.origin.tape.pick(index)
        return index

    pick.choose = chance  # type: ignore[attr-defined]  # so a copy of the run chooses the same way
    env.world.chance_picker = pick


def _keep_chance(source: Env, copy: Env) -> None:
    """Give a plain copy the chance chooser its source was loaded with."""
    choose = getattr(source.world.chance_picker, "choose", None)
    if choose is not None:
        use_chance(copy, choose)
