"""The engine: rounds, stages, turns, events and ending.

A round advances through safe points — before each stage, each pass and each sequential
turn — so a run can stop at any of them and continue exactly where it left off.

The round loop lives in :mod:`.run_rounds` and stage and turn running in :mod:`.run_stages`.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional

from .entity import Entity
from .actions import ActionBook
from .assets.store import AssetStore
from .budget import Budget, is_seconds
from .build import build_world
from .contract import MAX_ROUNDS, Contract
from .copying import Copying
from .diagnostics import diagnose
from .driving import WAITING, Driver, run_on_worker
from .effects import EffectRunner
from .errors import RunError
from .exposure import ExposureLog, asks_seen, recording
from .expr import ExprError
from .happenings import Happenings
from .host.tape import tape_of
from .measure import RunResult, Stats
from .perception import Perception
from .previews import Previews
from .reads import InspectCache, inspect_rule
from .replay import Origin
from .returns import measured
from .run_checks import RunChecks
from .run_diagnosis import Diagnosis
from .run_rounds import RunRounds, _Steps, _Where
from .run_stages import RunStages
from .seeds import SeedTree
from .snapshot import SNAPSHOT_VERSION, restore_env, take_snapshot
from .turn import Memory, entity_dict
from .world import _plain

__all__ = ["Env", "SNAPSHOT_VERSION"]


class Env(Copying, RunChecks, RunRounds, RunStages):
    """A loaded environment. Create with :func:`fg_env.load`; run with :meth:`run`; copy with :meth:`clone`
    and :meth:`fork`."""

    #: Declared here because the round and stage mixins are type-checked before ``__init__`` is.
    status: str
    origin: Origin
    diagnosis: Diagnosis
    happenings: Happenings
    driver: Driver
    previews: Previews
    _lock: threading.RLock
    _signal: threading.Condition
    _turn_count: int
    _emitted: int
    _inspectable: bool
    _end_on_action: bool

    def __init__(self, contract: Contract, inputs: Dict[str, Any], seed: int, arm: Optional[str] = None,
                 parallel: int = 8, exposures: bool = False, assets: Optional[AssetStore] = None):
        self.contract = contract
        self.inputs = inputs
        self.seed = seed
        self.arm = arm
        #: The load-time calibration report (None when the contract fits nothing or this session set the inputs).
        self.calibration: Optional[Dict[str, Any]] = None
        self.parallel = max(1, parallel)
        self.seeds = SeedTree(seed)
        self.world = build_world(contract, inputs, self.seeds, arm, assets)
        self.world.enable_def_cache()
        self.effects = EffectRunner(self.world)
        self.actions = ActionBook(contract, self.world, self.effects)
        self.perception = Perception(contract, self.world)
        self.stats = Stats()
        #: The same numbers per agent entity id (a tournament bills each entrant for its own turns).
        self.agent_stats: Dict[str, Stats] = {}
        self.status = "ready"
        self.ended_by: Optional[str] = None
        self.error: Optional[str] = None
        self._memories: Dict[str, Memory] = {}
        self._briefs: Dict[str, str] = {}
        self._inspect_cache: Optional[InspectCache] = None
        #: The assets each agent's brief attaches (fixed with the brief text).
        self._brief_assets: Dict[str, List[str]] = {}
        self._used_round: Dict[str, Dict[str, int]] = {}
        self._fired_once: set = set()
        self._lock = threading.RLock()
        #: Signalled when a participant's turn lands or a call returns; waiting on it releases the lock.
        self._signal = threading.Condition(self._lock)
        self._running = threading.Lock()
        self.driver = Driver(self)
        #: Wall-clock seconds per turn for stages that set no `time_limit` (None: no limit).
        self.time_limit: Optional[float] = None
        self.budget: Optional[Budget] = None
        #: Recorded when asked, or when the contract's rules ask `$seen`.
        self.world.exposures = ExposureLog() if exposures or asks_seen(contract) else None
        self.happenings = Happenings(self)
        self.previews = Previews(self)
        self._on_event: Optional[Callable[[Dict[str, Any]], None]] = None
        self._emitted = 0
        self._turn_count = 0
        #: The round in progress while a run is stopped inside it, and where in it the run is.
        self._cursor: Optional[_Steps] = None
        self._where = _Where()
        #: Last truth value of each trigger's condition, and triggers that fired once.
        self._trigger_armed: Dict[int, bool] = {}
        self._triggers_fired: set = set()
        self._in_round = False
        self.origin = Origin(contract)  # what copies of this run replay from (see replay.py)
        #: Whether some type lets agents inspect entities besides themselves (whose [id] handles then show).
        self._inspectable = any(self._inspect_rule(kind) is not False for kind in contract.types)
        #: The state each invariant was last found to hold in (see _check_invariants).
        self._invariant_held: Dict[int, Any] = {}
        self._end_on_action = any(end.check == "action" for end in contract.end)
        self.diagnosis = self.world.diagnosis = Diagnosis(self.world.written)
        self._check_invariants("build", "build")

    # -- public API ----------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.status in ("completed", "ended", "failed")

    @property
    def round(self) -> int:
        return self.world.round

    def run(self, participants: Any = None, *, rounds: Optional[int] = None,
            stop: Optional[Callable[["Env"], bool]] = None,
            on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
            raise_errors: bool = False, hosts: Any = None, time_limit: Optional[float] = None,
            budget: Optional[Mapping[str, Any]] = None) -> RunResult:
        """Run to the end, or for ``rounds`` more rounds, or until ``stop(env)`` is true.

        ``participants`` is a callable for every agent, or a mapping from entity id, type or
        ``"*"`` to a participant (a callable — plain or ``async def`` — ``"random"``, ``"idle"``,
        ``"policy:<name>"``). Agents without one use their type's ``policy`` or ``"random"``. Every
        participant is offered the contract's in-turn host tools; ``hosts`` binds the run to host
        adapters first. ``time_limit`` sets :attr:`time_limit`, the wall-clock seconds per turn for
        stages that set none; ``budget`` caps the run (:mod:`fg_env.budget`). In an event loop, use :meth:`arun`.

        ``stop`` is checked before every round, stage, pass and sequential turn. A stopped run
        continues exactly where it stopped on the next call; finishing a round that was
        stopped part-way counts as one of ``rounds``.
        """
        return self._run(participants, rounds, stop, on_event, raise_errors, hosts, time_limit, budget, None)

    async def arun(self, participants: Any = None, *, rounds: Optional[int] = None,
                   stop: Optional[Callable[["Env"], bool]] = None,
                   on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
                   raise_errors: bool = False, hosts: Any = None, time_limit: Optional[float] = None,
                   budget: Optional[Mapping[str, Any]] = None) -> RunResult:
        """:meth:`run` as a coroutine, for use inside a running event loop.

        Async participants run on this loop — so clients bound to it work — and a simultaneous
        stage's async participants run concurrently. The engine itself runs in a worker thread, so
        the loop stays free while it plays; ``stop`` and ``on_event`` are called from that thread.
        Cancelling the call stops the run at its next safe point.
        """
        def play(loop: asyncio.AbstractEventLoop, halt: Callable[["Env"], bool]) -> RunResult:
            return self._run(participants, rounds, halt, on_event, raise_errors, hosts, time_limit, budget, loop)

        result: RunResult = await run_on_worker(play, stop)
        return result

    def _run(self, participants: Any, rounds: Optional[int], stop: Optional[Callable[["Env"], bool]],
             on_event: Optional[Callable[[Dict[str, Any]], None]], raise_errors: bool, hosts: Any,
             time_limit: Optional[float], budget: Any, loop: Optional[asyncio.AbstractEventLoop]) -> RunResult:
        if rounds is not None and (isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0):
            raise ValueError(f"rounds must be a whole number ≥ 0, got {rounds!r}")
        if rounds is not None and rounds > MAX_ROUNDS:
            raise ValueError(f"rounds must be at most {MAX_ROUNDS:,}, got {rounds:,}")
        if time_limit is not None and not is_seconds(time_limit):
            raise ValueError(f"time_limit must be a number of seconds > 0, got {time_limit!r}")
        if not self._running.acquire(blocking=False):
            raise RuntimeError("this environment is already running; run() cannot be called again until it returns")
        try:
            if hosts is not None:
                from .host.hosts import bind

                bind(self, hosts)
            self.driver.bind(participants)
            if time_limit is not None:
                self.time_limit = float(time_limit)
            self.budget = Budget.begin(budget, self.budget)
            self.driver.loop = loop
            self._on_event = on_event
            try:
                self._play(rounds, stop)
            except (RunError, ExprError) as exc:
                self._fail(str(exc))
                if raise_errors:
                    self._flush_events()
                    raise
            except BaseException as exc:
                # A participant callback, stop/on_event callback or engine defect: surfaced, never swallowed.
                self._fail(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
                raise
            self._flush_events()
            return self.result()
        finally:
            self._on_event = None
            self.driver.loop = None
            self._running.release()

    def step(self, participants: Any = None) -> RunResult:
        """Run exactly one round (or finish the round a stopped run is in)."""
        return self.run(participants, rounds=1)

    def entity(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """A copy of one entity: ``{id, name, type, alive, at, props}``, or None."""
        found = self.world.entities.get(entity_id)
        return entity_dict(found) if found is not None else None

    def entities(self, type_name: Optional[str] = None, alive: bool = True) -> List[Dict[str, Any]]:
        """Copies of entities, optionally of one type (subtypes included) and only alive ones."""
        kinds = set(self.contract.subtypes(type_name)) if type_name else None
        return [entity_dict(e) for e in self.world.entities.values()
                if (kinds is None or e.entity_type in kinds) and (e.alive or not alive)]

    def records(self, name: str) -> List[Dict[str, Any]]:
        """A detached, JSON-safe copy of a declared record stream.

        Host applications use records to render engine-native timelines,
        transcripts, market bars, and reports without reaching into the
        runtime's internal world object. Private record visibility remains a
        participant-view concern; this host-level method returns the complete
        authoritative stream.
        """
        return [_plain(dict(entry)) for entry in self.world.records(name)]

    @property
    def props(self) -> Dict[str, Any]:
        """A copy of the world's global properties."""
        return _plain(dict(self.world.props))

    def result(self) -> RunResult:
        outputs: Dict[str, Any] = {}
        issues: List[Dict[str, Any]] = []
        returns: Dict[str, float] = {}
        if self.status != "failed":  # unfinished runs get provisional outputs and returns
            outputs, problems, returns = measured(self.contract, self.world, self.finished)
            issues = [p.to_dict() for p in problems]
        end = self.world.end_request or {}
        return RunResult(
            status=self.status, ended_by=self.ended_by, rounds=self.world.round, seed=self.seed, arm=self.arm,
            inputs=self.inputs, outputs=outputs, metrics=dict(self.world.metrics),
            series={k: list(v) for k, v in self.world.series.items()}, winner=end.get("winner"),
            error=self.error, output_issues=issues, stats=self.stats.to_dict(),
            agent_stats={key: self.agent_stats[key].to_dict() for key in sorted(self.agent_stats)},
            events=[e.to_dict() for e in self.world.log], time=self.world.time if self.world.continuous else None,
            exposures=recording(self),
            frames=[dict(frame) for frame in self.previews.frames], returns=returns,
            host_tape=tape_of(self) if self.world.exposures is not None else {}, budget=Budget.report(self),
            formats={name: spec.format for name, spec in self.contract.outputs.items() if spec.format},
            diagnostics=diagnose(self, outputs),
            clock={"mode": self.contract.clock.mode, "unit": self.contract.clock.unit, "step": self.contract.clock.step,
                   "start": self.world.start},
            assets=self.world.assets.to_dict() if len(self.world.assets) else {},
        )

    @property
    def frames(self) -> List[Dict[str, Any]]:
        """Spectator frames so far: ``[{round, views: {name: text}, time?, final?}]``."""
        return self.previews.frames

    def spectate(self) -> Dict[str, str]:
        """Every spectator view (``"for": "spectator"``) rendered against the world now, by name. Changes
        nothing: views that draw randomness use a stream of their own."""
        return self.previews.spectate()

    def preview(self, entity_id: str, stage: Optional[str] = None) -> Dict[str, Any]:
        """What the agent would receive on its next turn: brief, update, tools and time limit. Changes nothing.

        Between rounds this plays the next round on a copy up to the agent's turn — scheduled
        effects, start events, physics and the turns of agents before it (with their built-in
        or named participants; your own callables are never called) — so the preview shows the
        turn as the agent will get it.
        """
        return self.previews.preview(entity_id, stage)

    def snapshot(self) -> Dict[str, Any]:
        """Everything needed to continue this run later, as JSON-safe data: between rounds, or stopped part-way
        through a round (``run(stop=...)``)."""
        return take_snapshot(self)

    @classmethod
    def restore(cls, contract: Any, snapshot: Mapping[str, Any], parallel: int = 8, hosts: Any = None,
                data_dir: Any = None) -> "Env":
        """Continue a run from :meth:`snapshot`. ``contract`` is the contract it was taken with
        (a :class:`Contract`, dict, path or JSON text; the snapshot's arm is applied if needed).
        ``hosts`` answers host judgment; answers already recorded in the snapshot are never asked again.
        The contract's files are found again in its folder (or ``data_dir``) and checked against the recorded hashes."""
        from .api import default_data_dir
        from .assets.catalog import locate

        env = restore_env(cls, contract, snapshot, parallel)
        locate(env.world.assets, default_data_dir(contract, data_dir))
        if hosts is not None:
            from .host.hosts import bind

            bind(env, hosts)
        return env

    # -- driving -----------------------------------------------------------------------

    def _play(self, rounds: Optional[int], stop: Optional[Callable[["Env"], bool]]) -> None:
        completed = 0
        while not self.finished:
            if self._cursor is None:
                if (rounds is not None and completed >= rounds) or (self.budget is not None and self.budget.enforce(self)):
                    return
                if stop is not None and stop(self):
                    self.status = "stopped"
                    return
                self.origin.round_start(self)
                self._cursor = self._round()
            elif self.status == "stopped":
                self.status = "running"
            for point in self._cursor:
                if point is WAITING:  # a turn waits for a decision: the run pauses here, its round kept
                    return
                self.origin.tape.points += 1
                if (self.budget is not None and self.budget.enforce(self)) or (stop is not None and stop(self)):
                    self.status = self.status if self.finished else "stopped"
                    return
            self._cursor = None
            completed += 1

    def _fail(self, message: str) -> None:
        if self._cursor is not None:
            self._cursor.close()
            self._cursor = None
        self.status, self.error = "failed", message

    # -- checks --------------------------------------------------------------------------------

    def _inspect_rule(self, type_name: str) -> Any:
        """The inspect rule for a type, inherited through `extends`."""
        return inspect_rule(self.contract, type_name)

    # -- helpers --------------------------------------------------------------------------------

    def _memory(self, entity_id: str) -> Memory:
        memory = self._memories.get(entity_id)
        if memory is None:
            memory = self._memories[entity_id] = Memory()
        return memory

    def _brief(self, actor: Entity) -> str:
        brief = self._briefs.get(actor.id)
        if brief is None:
            attached: List[str] = []
            brief = self._briefs[actor.id] = self.perception.brief(actor, attached)
            if attached:
                self._brief_assets[actor.id] = attached
        return brief

    def _flush_events(self) -> None:
        if self._on_event is None:
            self._emitted = len(self.world.log)
            return
        while self._emitted < len(self.world.log):
            event = self.world.log[self._emitted]
            self._emitted += 1
            self._on_event(event.to_dict())
