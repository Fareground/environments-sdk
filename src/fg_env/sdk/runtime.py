"""The engine: rounds, stages, turns, events and ending.

A round advances through safe points — before each stage, each pass and each sequential
turn — so a run can stop at any of them and continue exactly where it left off.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Mapping, Optional, Tuple

from ..entity import Entity
from .actions import ACTION_BUDGET, ActionBook, stage_actions
from .assets.store import AssetStore
from .budget import Budget, is_seconds
from .build import build_world, whole_setting
from .contract import MAX_ROUNDS, MAX_STAGE_PASSES, Contract, StageSpec
from .copying import Copying
from .diagnostics import diagnose
from .driving import WAITING, Driver, run_on_worker
from .effects import EffectRunner
from .errors import RunError
from .exposure import ExposureLog, asks_seen, recording
from .expr import ExprError, compile_expr, shared_budget, truthy
from .feeds import run_feeds
from .happenings import Happenings
from .host.tape import tape_of
from .measure import RunResult, Stats, sample_metrics
from .perception import Perception
from .previews import Previews
from .reads import inspect_rule
from .replay import Origin
from .returns import measured
from .run_checks import RunChecks
from .run_diagnosis import Diagnosis, SealedWrites
from .seeds import SeedTree
from .snapshot import SNAPSHOT_VERSION, restore_env, take_snapshot
from .turn import Memory, Turn, entity_dict
from .world import Abort, _plain

__all__ = ["Env", "SNAPSHOT_VERSION"]


@dataclass
class _Point:
    """A safe point in a round. ``reasons`` names the agents about to be woken, and why."""

    stage: Optional[StageSpec] = None
    reasons: Dict[str, str] = field(default_factory=dict)


@dataclass
class _Where:
    """Where the round in progress is, kept current as it plays, so a copy of the run taken while a turn waits for a
    decision continues that round from the same place (see :mod:`fg_env.sdk.stepping`)."""

    stage: int = 0
    pass_index: int = 0
    #: The agents of the pass being played, in turn order.
    agents: List[Entity] = field(default_factory=list)
    #: The waiting turn's place: in ``agents`` (sequential), or among the stage's sealed turns (simultaneous).
    position: int = 0
    #: The waiting turn itself (set on a copy only).
    turn: Optional[Turn] = None


#: A round's steps: its safe points (:class:`_Point`), and ``WAITING`` while a turn waits for a decision.
_Steps = Generator[Any, None, None]


class Env(Copying, RunChecks):
    """A loaded environment. Create with :func:`fg_env.load`; run with :meth:`run`; copy with :meth:`clone`
    and :meth:`fork`."""

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
        stages that set none; ``budget`` caps the run (:mod:`fg_env.sdk.budget`). In an event loop, use :meth:`arun`.

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
        """Everything needed to continue this run later, as JSON-safe data (between rounds)."""
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

    # -- round -----------------------------------------------------------------------

    def _begin_round(self) -> bool:
        """Start the next round: scheduled effects, feeds, start events, physics. False if the run ended."""
        world = self.world
        self._in_round = True
        if self.status in ("ready", "stopped"):
            self.status = "running"
        elapsed: Optional[float] = None
        if world.continuous:
            elapsed = self._advance_time()
            if elapsed is None:  # the next moment is past the horizon
                self._in_round = False
                self.ended_by, self.status = "horizon", "completed"
                self._final_event()
                return False
        world.round += 1
        world.stage = None
        self._used_round.clear()
        self.happenings.run_scheduled()
        run_feeds(self)
        self.happenings.run_events("start")
        self._check_end()
        if self._ended():
            self._finish()
            return False
        with self._lock:
            if elapsed is None:
                world.step_physics()
            elif elapsed > 0:
                world.step_physics(elapsed)
            world.journal.clear()
        self.happenings.check_triggers("physics")
        if self._ended():
            self._finish()
            return False
        return True

    def _advance_time(self) -> Optional[float]:
        """Move a continuous clock to the next round's moment; the time elapsed, or None past the horizon."""
        world, clock = self.world, self.contract.clock
        if world.round == 0:
            return 0.0
        previous = world.time
        target = previous + clock.tick
        if clock.jump:
            due = self._next_due()
            if due is not None:
                target = max(previous, due)
        if world.horizon is not None and target > world.horizon:
            return None
        world.time = target
        world.touch()
        return target - previous

    def _next_due(self) -> Optional[float]:
        """The earliest moment something is due: a living agent's wake time or a scheduled effect."""
        world = self.world
        times = [at for entity_id, at in world.wake_at.items()
                 if (entity := world.entities.get(entity_id)) is not None and entity.alive]
        if world.scheduled:
            times.append(world.scheduled[0][0])
        return min(times) if times else None

    def _round(self, resumed: bool = False) -> _Steps:
        """A round, from its start — or, ``resumed``, from the waiting turn a copy of the run was taken in (see
        :class:`_Where`)."""
        world = self.world
        if not resumed:
            if not self._begin_round():
                return
            self._where = _Where()
        stages = self.contract.stage_list()
        for index in range(self._where.stage, len(stages)):
            stage = stages[index]
            if resumed:
                resumed = False
                yield from self._run_stage(stage, resumed=True)
            else:
                self._where.stage = index
                yield _Point(stage)
                yield from self._run_stage(stage)
            self._check_end()
            if self._ended():
                self._finish()
                return
        world.stage = None
        self.happenings.run_events("end")
        world.patterns.commit()
        sample_metrics(self.contract, world)
        self.happenings.check_triggers("round end")
        self._check_invariants("round", "round")
        self._check_end()
        self._flush_events()
        if self._ended():
            self._finish()
            return
        self._in_round = False
        if world.round >= world.rounds:
            self.ended_by = "rounds"
            self.status = "completed"
            self._final_event()
        else:
            self.previews.frame(final=False)

    def _ended(self) -> bool:
        return self.world.end_request is not None

    def _finish(self) -> None:
        world = self.world
        world.stage = None
        if not world.series or len(next(iter(world.series.values()), [])) < world.round:
            sample_metrics(self.contract, world)
        end = world.end_request or {}
        self.ended_by = end.get("name") or "end"
        self.status = "ended"
        self._in_round = False
        self._final_event()

    def _final_event(self) -> None:
        self._check_invariants("the run", "end")
        end = self.world.end_request or {}
        text = end.get("text") or (f"The run ended: {self.ended_by}." if self.ended_by != "rounds" else "Time is up.")
        self.world.emit("end", text, data={"ended_by": self.ended_by, "winner": end.get("winner")})
        self.world.journal.clear()
        self.previews.frame(final=True)
        self._flush_events()

    def _atomic(self, effects: List[Any], vars: Dict[str, Any], path: str) -> bool:
        if not effects:
            return True
        with self._lock:
            mark = self.world.journal.mark()
            try:
                with shared_budget(ACTION_BUDGET, path):
                    self.effects.run(effects, dict(vars), path)
            except Abort as refusal:
                self.world.journal.rollback(mark)
                self.world.emit("refused", f"{path} was refused: {refusal.reason}", to=[],
                                data={"path": path, "reason": refusal.reason})
                return False
            except BaseException:
                self.world.journal.rollback(mark)
                raise
            self._after_commit(path)
            self.happenings.react(self._stage_spec())
        return True

    def _stage_spec(self) -> Optional[StageSpec]:
        name = self.world.stage
        return next((s for s in self.contract.stage_list() if s.name == name), None) if name else None

    def _after_commit(self, path: str) -> None:
        self._check_invariants(path)
        if self._end_on_action:
            self._check_end("action")
        self.world.journal.clear()
        self.happenings.check_triggers(path)

    # -- stages & turns ------------------------------------------------------------------

    def _run_stage(self, stage: StageSpec, resumed: bool = False) -> _Steps:
        world, where = self.world, self._where
        path = f"stages.{stage.name}"
        if not resumed:
            runs = self._stage_runs(stage)
            self.diagnosis.stage(stage.name, reached=1, ran=int(runs))
            if not runs:
                return
            world.stage = stage.name
            self._atomic(stage.on_enter, {}, f"{path}.on_enter")
            if self._ended():
                return
            where.pass_index = 0
        passes = whole_setting(world, stage.passes, f"{path}.passes", MAX_STAGE_PASSES) or (10 if stage.until else 1)
        for pass_index in range(where.pass_index, passes):
            if resumed:
                agents = where.agents
            else:
                if pass_index:
                    yield _Point(stage)
                agents = self._eligible(stage)
                where.pass_index, where.agents = pass_index, agents
                self.diagnosis.stage(stage.name, woke=len(agents))
            if stage.turns == "simultaneous":
                yield from self._simultaneous(stage, agents, pass_index, resumed)
            elif stage.turns == "scheduled":  # never resumed: copies are not taken in scheduled stages
                yield from self._scheduled(stage, agents, pass_index)
            else:
                yield from self._sequential(stage, agents, pass_index, resumed)
            resumed = False
            if self._ended():
                return
            if stage.until is not None:
                try:
                    if truthy(compile_expr(stage.until)(world.scope())):
                        break
                except ExprError as exc:
                    raise RunError(str(exc), f"{path}.until") from None
        self._atomic(stage.on_exit, {}, f"{path}.on_exit")

    def _stage_runs(self, stage: StageSpec) -> bool:
        if stage.when is None:
            return True
        try:
            return truthy(compile_expr(stage.when)(self.world.scope()))
        except ExprError as exc:
            raise RunError(str(exc), f"stages.{stage.name}.when") from None

    def _eligible(self, stage: StageSpec, ordered: bool = True) -> List[Entity]:
        """Agents woken in ``stage``, in turn order. ``ordered=False`` skips ordering (no random draws)."""
        world = self.world
        agent_types = set(self.contract.agent_types())  # includes types that inherit `agent`
        acting = {kind: bool(stage_actions(self.contract, stage, kind)) for kind in agent_types}
        agents = [e for e in world.entities.values() if e.alive and acting.get(e.entity_type)]
        path = f"stages.{stage.name}"
        try:
            if stage.who is not None:
                who = compile_expr(stage.who)
                agents = [a for i, a in enumerate(agents) if truthy(who(world.scope(it=a, i=i)))]
            if not ordered:
                return agents
            if stage.order == "random":
                world.rng.shuffle(agents)
            elif stage.order != "seat":
                key = compile_expr(stage.order)
                keyed = [(key(world.scope(it=a, i=i)), i, a) for i, a in enumerate(agents)]
                keyed.sort(key=lambda t: (t[0], t[1]))
                agents = [a for _, _, a in keyed]
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        except TypeError:
            raise RunError("`order` must give comparable values (numbers or text)", f"{path}.order") from None
        return agents

    def _reason(self, actor: Entity, stage: StageSpec, pass_index: int) -> Optional[str]:
        requested = self.world.wake_requests.pop(actor.id, None)
        memory = self._memory(actor.id)
        if stage.quiet == "skip" and requested is None and pass_index > 0:
            if not self.perception.news(actor, memory.cursor, 1)[0]:
                return None
        if requested:
            return requested
        if stage.turns == "simultaneous":
            return "Everyone chooses at the same time."
        return "It is your turn." if pass_index == 0 else "Your turn again."

    def _sequential(self, stage: StageSpec, agents: List[Entity], pass_index: int, resumed: bool = False) -> _Steps:
        where = self._where
        for position in range(where.position if resumed else 0, len(agents)):
            actor = agents[position]
            if resumed:
                resumed, turn, where.turn = False, where.turn, None
                assert turn is not None
                yield from self.driver.drive_steps([turn], resume=0)
            else:
                if not actor.alive or self._ended():
                    return
                reason = self._reason(actor, stage, pass_index)
                if reason is None:
                    continue
                if not self._wake_hook(stage, actor):
                    continue
                where.position = position
                yield _Point(stage, {actor.id: reason})
                turn = Turn(self, actor, stage, reason, staged=False)
                yield from self.driver.drive_steps([turn])
            self._after_turn(stage, turn, turn.stats.actions > 0)
            self._turn_end_hook(stage, actor)
            memory = self._memory(actor.id)
            memory.cursor = self.world.log[-1].seq if self.world.log else 0
            memory.turns += 1
            self._flush_events()

    def _scheduled(self, stage: StageSpec, agents: List[Entity], pass_index: int) -> _Steps:
        """Continuous clock: every agent whose wake time has come takes a turn, earliest first. Its
        next wake is now plus the duration of what it did, or the stage interval if it did nothing
        timed — unless something during the turn already scheduled it later."""
        world = self.world
        now = world.time
        due: List[Tuple[float, int, Entity]] = []
        for position, actor in enumerate(agents):
            at = world.wake_at.get(actor.id)
            if at is None:
                at = self._stage_time(stage.first_wake, 0.0, f"stages.{stage.name}.first_wake", it=actor, i=position)
                world.set_wake_at(actor.id, at)
            if at <= now:
                due.append((at, position, actor))
        due.sort(key=lambda item: (item[0], item[1]))
        for _, _, actor in due:
            if not actor.alive or self._ended():
                return
            reason = self._reason(actor, stage, pass_index)
            if reason is None:
                world.set_wake_at(actor.id, now + self._interval(stage, actor))
                continue
            if not self._wake_hook(stage, actor):
                continue
            yield _Point(stage, {actor.id: reason})
            turn = Turn(self, actor, stage, reason, staged=False)
            yield from self.driver.drive_steps([turn])
            self._after_turn(stage, turn, turn.stats.actions > 0)
            self._turn_end_hook(stage, actor)
            scheduled = world.wake_at.get(actor.id, now)
            if scheduled <= now:
                step = turn.elapsed if turn.elapsed > 0 else self._interval(stage, actor)
                world.set_wake_at(actor.id, now + step)
            memory = self._memory(actor.id)
            memory.cursor = world.log[-1].seq if world.log else 0
            memory.turns += 1
            self._flush_events()

    def _after_turn(self, stage: StageSpec, turn: Turn, acted: bool, stop_when_ended: bool = False) -> None:
        """A played turn is over: record a timeout (running `on_timeout`), or — for a living agent that took no
        action — report one that had to act and did not, then run the stage's `on_idle`."""
        actor = turn.actor
        if self._timed_out(turn) or acted or not actor.alive or (stop_when_ended and self._ended()):
            return
        if turn.did_not_act:
            with self._lock:
                self.world.emit("idle", f"{actor.name} did not act.", actor=actor.id, data={"stage": stage.name})
                self.world.journal.clear()
        if stage.on_idle:
            self._atomic(stage.on_idle, {"actor": actor}, f"stages.{stage.name}.on_idle")

    def _timed_out(self, turn: Turn) -> bool:
        """Record a turn that ran out of time (a `timeout` event) and run the stage's `on_timeout`.
        True when `on_timeout` took the place of `on_idle`."""
        if not turn.timed_out:
            return False
        stage, actor, world = turn.stage, turn.actor, self.world
        with self._lock:
            world.emit("timeout", f"{actor.name} ran out of time.", actor=actor.id,
                       data={"stage": stage.name, "limit": turn.time_limit})
            world.journal.clear()
        if not stage.on_timeout:
            return False
        if actor.alive and not self._ended():
            self._atomic(stage.on_timeout, {"actor": actor}, f"stages.{stage.name}.on_timeout")
        return True

    def _time_limit(self, stage: StageSpec, actor: Entity) -> Optional[float]:
        """Wall-clock seconds ``actor`` has for a turn in ``stage``: the stage's `time_limit`, else the run's."""
        raw = stage.time_limit
        if raw is None:
            return self.time_limit
        path = f"stages.{stage.name}.time_limit"
        try:
            value = compile_expr(raw)(self.world.scope(actor=actor)) if isinstance(raw, str) else raw
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        if value is None:
            return self.time_limit
        if not is_seconds(value):
            raise RunError(f"must be a number of seconds > 0 (or null for the run's limit), got {value!r}", path)
        return float(value)

    def _wake_hook(self, stage: StageSpec, actor: Entity) -> bool:
        """Run the stage's ``on_wake`` for ``actor`` before its turn; False when it no longer takes the turn."""
        if stage.on_wake:
            self._atomic(stage.on_wake, {"actor": actor}, f"stages.{stage.name}.on_wake")
        return actor.alive and not self._ended()

    def _turn_end_hook(self, stage: StageSpec, actor: Entity) -> None:
        """Run the stage's ``on_turn_end`` for ``actor`` after its turn (and its actions) are done."""
        if stage.on_turn_end and actor.alive and not self._ended():
            self._atomic(stage.on_turn_end, {"actor": actor}, f"stages.{stage.name}.on_turn_end")

    def _interval(self, stage: StageSpec, actor: Entity) -> float:
        value = self._stage_time(stage.interval, self.contract.clock.tick, f"stages.{stage.name}.interval", actor=actor)
        if value <= 0:
            raise RunError(f"interval must be greater than 0, got {value}", f"stages.{stage.name}.interval")
        return value

    def _stage_time(self, raw: Any, default: float, path: str, **vars: Any) -> float:
        if raw is None:
            return default
        try:
            value = compile_expr(raw)(self.world.scope(**vars)) if isinstance(raw, str) else raw
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
            raise RunError(f"must be a time ≥ 0, got {value!r}", path)
        return float(value)

    def _simultaneous(self, stage: StageSpec, agents: List[Entity], pass_index: int, resumed: bool = False) -> _Steps:
        if resumed:
            turns = list(self.origin.staged)
            yield from self.driver.drive_steps(turns, together=True, resume=self._where.position)
        else:
            reasons: Dict[str, str] = {}
            for actor in agents:
                reason = self._reason(actor, stage, pass_index)
                if reason is not None:
                    reasons[actor.id] = reason
            for actor in agents:
                if actor.id in reasons and not self._wake_hook(stage, actor):
                    del reasons[actor.id]
            if reasons:
                yield _Point(stage, dict(reasons))
            turns = [Turn(self, actor, stage, reasons[actor.id], staged=True) for actor in agents if actor.id in reasons]
            cursor = self.world.log[-1].seq if self.world.log else 0
            for turn in turns:
                memory = self._memory(turn.actor.id)
                memory.cursor = cursor
                memory.turns += 1
            yield from self.driver.drive_steps(turns, together=True)
        try:
            yield from self._commit_choices(stage, turns)
        finally:
            for turn in turns:  # in turn order, so `$seen` indexes the same way every run
                if turn.exposure is not None:
                    turn.exposure.close(turn)
        self._flush_events()

    def _commit_choices(self, stage: StageSpec, turns: List[Turn]) -> _Steps:
        """Commit each agent's sealed choices in turn order; atomic stages commit or undo each agent's as a whole."""
        atomic = stage.atomic or bool(stage.valid)
        writes = self.world.watched_writes = SealedWrites(stage.name, self.diagnosis)
        try:
            for turn in turns:
                mark = self.world.journal.mark() if atomic else None
                applied = 0
                writes.writer = turn.actor.name or turn.actor.id
                for name, args in turn.intents:
                    if self._ended() and mark is None:
                        return
                    writes.action = name
                    applied += self._commit_intent(turn, name, args, deferred=mark is not None)
                acted = bool(turn.intents)
                if mark is not None:
                    acted = self._settle_choices(turn, mark, applied)
                    if self._ended():
                        return
                self._after_turn(stage, turn, acted, stop_when_ended=True)
                self._turn_end_hook(stage, turn.actor)
        finally:
            self.world.watched_writes = None
        yield from ()

    def _tally(self, actor_id: str, stats: Stats) -> None:
        """Add numbers to the run's totals and to the agent's own (callers hold the lock)."""
        self.stats.add(stats)
        self.agent_stats.setdefault(actor_id, Stats()).add(stats)

    def _settle_choices(self, turn: Turn, mark: int, applied: int) -> bool:
        """An atomic simultaneous stage: keep one agent's committed choices when they meet `valid`, else undo
        them all and tell the agent why. True when the agent's choices stand."""
        world, stage = self.world, turn.stage
        with self._lock:
            with world.turn_context(None, turn.pending):
                why = turn.invalid() if applied else None
            if why is None:
                self._after_commit(f"stages.{stage.name}")
                self.happenings.react(stage)
                return bool(turn.intents)
            world.journal.rollback(mark)
            world.emit("outcome", f"Your choices were undone: {why}.", actor=turn.actor.id, to=(turn.actor.id,),
                       data={"ok": False, "undone": True})
            world.journal.clear()
            self._tally(turn.actor.id, Stats(actions=-applied, rejected_actions=applied, undone_turns=1))
            turn.stats.undone_turns = 1
        return False

    def _commit_intent(self, turn: Turn, name: str, args: Dict[str, Any], deferred: bool = False) -> int:
        """Apply one sealed choice; 1 when it applied. ``deferred`` (atomic stages) leaves the commit to the
        whole turn's settling."""
        actor, world = turn.actor, self.world
        blocked = self.actions.blocked(actor, name, {}, {}) if actor.alive else "you are no longer active"
        params, problem = ({}, blocked) if blocked else self.actions.validate(actor, name, args)
        verb = name.replace("_", " ")
        with self._lock:
            if problem:
                world.emit("outcome", f"Your {verb} did not happen: {str(problem).rstrip('.')}.",
                           actor=actor.id, to=(actor.id,), data={"action": name, "ok": False})
                self.diagnosis.refused_at_commit(name, str(problem))
                if not deferred:
                    world.journal.clear()
                self._tally(actor.id, Stats(rejected_actions=1))
                return 0
            outcome = self.actions.apply(actor, name, params)
            text = outcome.text if outcome.ok else f"Your {verb} failed: {outcome.text}"
            data = {"action": name, "ok": outcome.ok, **({"assets": outcome.assets} if outcome.assets else {})}
            world.emit("outcome", text, actor=actor.id, to=(actor.id,), data=data)
            if not outcome.ok:
                self.diagnosis.refused_at_commit(name, outcome.text)
                self._tally(actor.id, Stats(rejected_actions=1))
                if not deferred:
                    world.journal.clear()
                return 0
            self._tally(actor.id, Stats(actions=1))
            if not deferred:
                self._after_commit(f"actions.{name}")
                self.happenings.react(turn.stage)
            return 1

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
