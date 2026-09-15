"""The engine: rounds, stages, turns, events and ending.

A round advances through safe points — before each stage, each pass and each sequential
turn — so a run can stop at any of them and continue exactly where it left off.
"""
from __future__ import annotations

import heapq
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Mapping, Optional, Tuple

from ..entity import Entity
from .actions import ACTION_BUDGET, ActionBook, stage_actions
from .build import build_world
from .delivery import run_delivery
from .contract import MAX_ROUNDS, Contract, StageSpec
from .effects import EffectRunner
from .errors import InvariantViolation, RunError
from .expr import ExprError, compile_expr, shared_budget, truthy
from .feeds import run_feeds
from .measure import RunResult, Stats, compute_outputs, sample_metrics
from .participants import Participant, resolve_participant
from .perception import Perception
from .seeds import SeedTree
from .session import END_TURN, Wake
from .snapshot import SNAPSHOT_VERSION, restore_env, take_snapshot
from .template import compile_template
from .turn import Memory, Turn, entity_dict
from .world import Abort, _plain

__all__ = ["Env", "SNAPSHOT_VERSION"]


@dataclass
class _Point:
    """A safe point in a round. ``reasons`` names the agents about to be woken, and why."""

    stage: Optional[StageSpec] = None
    reasons: Dict[str, str] = field(default_factory=dict)


_Steps = Generator[_Point, None, None]


class Env:
    """A loaded environment. Create with :func:`fg_env.load`; run with :meth:`run`."""

    def __init__(self, contract: Contract, inputs: Dict[str, Any], seed: int, arm: Optional[str] = None,
                 parallel: int = 8):
        self.contract = contract
        self.inputs = inputs
        self.seed = seed
        self.arm = arm
        self.parallel = max(1, parallel)
        self.seeds = SeedTree(seed)
        self.world = build_world(contract, inputs, self.seeds, arm)
        self.world.enable_def_cache()
        self.effects = EffectRunner(self.world)
        self.actions = ActionBook(contract, self.world, self.effects)
        self.perception = Perception(contract, self.world)
        self.stats = Stats()
        self.status = "ready"
        self.ended_by: Optional[str] = None
        self.error: Optional[str] = None
        self._memories: Dict[str, Memory] = {}
        self._briefs: Dict[str, str] = {}
        self._used_round: Dict[str, Dict[str, int]] = {}
        self._fired_once: set = set()
        self._lock = threading.RLock()
        self._running = threading.Lock()
        self._participants: Dict[str, Participant] = {}
        self._participants_spec: Dict[str, Any] = {}
        self._on_event: Optional[Callable[[Dict[str, Any]], None]] = None
        self._emitted = 0
        self._turn_count = 0
        #: The round in progress while a run is stopped inside it.
        self._cursor: Optional[_Steps] = None
        #: Last truth value of each trigger's condition, and triggers that fired once.
        self._trigger_armed: Dict[int, bool] = {}
        self._triggers_fired: set = set()
        self._trigger_depth = 0
        self._reaction_depth = 0
        self._in_round = False
        self._inspectable = any(self._inspect_rule(kind) is not False for kind in contract.types)
        self._check_invariants("build")

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
            raise_errors: bool = False, hosts: Any = None) -> RunResult:
        """Run to the end, or for ``rounds`` more rounds, or until ``stop(env)`` is true.

        ``participants`` is a callable for every agent, or a mapping from entity id, type or
        ``"*"`` to a participant (a callable, ``"random"``, ``"idle"``, ``"policy:<name>"``).
        Agents without one use their type's ``policy`` or ``"random"``. Every participant is offered
        the contract's in-turn host tools; ``hosts`` binds the run to host adapters first.

        ``stop`` is checked before every round, stage, pass and sequential turn. A stopped run
        continues exactly where it stopped on the next call; finishing a round that was
        stopped part-way counts as one of ``rounds``.
        """
        if rounds is not None and (isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0):
            raise ValueError(f"rounds must be a whole number ≥ 0, got {rounds!r}")
        if rounds is not None and rounds > MAX_ROUNDS:
            raise ValueError(f"rounds must be at most {MAX_ROUNDS:,}, got {rounds:,}")
        if not self._running.acquire(blocking=False):
            raise RuntimeError("this environment is already running; run() cannot be called again until it returns")
        try:
            if hosts is not None:
                from .host.hosts import bind

                bind(self, hosts)
            self._bind(participants)
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
        if self.status != "failed":  # unfinished runs get provisional outputs
            computed, problems = compute_outputs(self.contract, self.world)
            outputs, issues = computed, [p.to_dict() for p in problems]
        end = self.world.end_request or {}
        return RunResult(
            status=self.status, ended_by=self.ended_by, rounds=self.world.round, seed=self.seed, arm=self.arm,
            inputs=self.inputs, outputs=outputs, metrics=dict(self.world.metrics),
            series={k: list(v) for k, v in self.world.series.items()}, winner=end.get("winner"),
            error=self.error, output_issues=issues, stats=self.stats.to_dict(),
            events=[e.to_dict() for e in self.world.log], time=self.world.time if self.world.continuous else None,
        )

    def snapshot(self) -> Dict[str, Any]:
        """Everything needed to continue this run later, as JSON-safe data (between rounds)."""
        return take_snapshot(self)

    @classmethod
    def restore(cls, contract: Any, snapshot: Mapping[str, Any], parallel: int = 8, hosts: Any = None) -> "Env":
        """Continue a run from :meth:`snapshot`. ``contract`` is the contract it was taken with
        (a :class:`Contract`, dict, path or JSON text; the snapshot's arm is applied if needed).
        ``hosts`` answers host judgment; answers already recorded in the snapshot are never asked again."""
        env = restore_env(cls, contract, snapshot, parallel)
        if hosts is not None:
            from .host.hosts import bind

            bind(env, hosts)
        return env

    def preview(self, entity_id: str, stage: Optional[str] = None) -> Dict[str, Any]:
        """What the agent would receive on its next turn: brief, update and tools. Changes nothing.

        Between rounds this plays the next round on a copy up to the agent's turn — scheduled
        effects, start events, physics and the turns of agents before it (with their built-in
        or named participants; your own callables are never called) — so the preview shows the
        turn as the agent will get it.
        """
        if self.world.entity(entity_id) is None:
            raise KeyError(f"no entity '{entity_id}'")
        if stage is not None and all(s.name != stage for s in self.contract.stage_list()):
            raise KeyError(f"no stage '{stage}' (stages: {', '.join(s.name for s in self.contract.stage_list())})")
        if self.finished or self._in_round:
            return self._preview_now(entity_id, stage)
        snapshot = self.snapshot()
        probe = self._probe(snapshot)
        for point in probe._round():
            if point.stage is not None and entity_id in point.reasons and stage in (None, point.stage.name):
                return probe._preview_turn(entity_id, point.stage, point.reasons[entity_id])
        start = self._probe(snapshot)  # not woken this round: show the round as it opens
        start._begin_round()
        return start._preview_now(entity_id, stage)

    # -- driving -----------------------------------------------------------------------

    def _play(self, rounds: Optional[int], stop: Optional[Callable[["Env"], bool]]) -> None:
        completed = 0
        while not self.finished:
            if self._cursor is None:
                if rounds is not None and completed >= rounds:
                    return
                if stop is not None and stop(self):
                    self.status = "stopped"
                    return
                self._cursor = self._round()
            elif self.status == "stopped":
                self.status = "running"
            for _ in self._cursor:
                if stop is not None and stop(self):
                    self.status = "stopped"
                    return
            self._cursor = None
            completed += 1

    def _fail(self, message: str) -> None:
        if self._cursor is not None:
            self._cursor.close()
            self._cursor = None
        self.status, self.error = "failed", message

    def _probe(self, snapshot: Mapping[str, Any]) -> "Env":
        probe = restore_env(type(self), self.contract, snapshot, parallel=1)
        probe._participants_spec = {k: v for k, v in self._participants_spec.items() if isinstance(v, str)}
        return probe

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
        self._run_scheduled()
        run_feeds(self)
        self._run_events("start")
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
        self._check_triggers("physics")
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

    def _round(self) -> _Steps:
        world = self.world
        if not self._begin_round():
            return
        for stage in self.contract.stage_list():
            yield _Point(stage)
            yield from self._run_stage(stage)
            self._check_end()
            if self._ended():
                self._finish()
                return
        world.stage = None
        self._run_events("end")
        sample_metrics(self.contract, world)
        self._check_triggers("round end")
        self._check_invariants("round")
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
        end = self.world.end_request or {}
        text = end.get("text") or (f"The run ended: {self.ended_by}." if self.ended_by != "rounds" else "Time is up.")
        self.world.emit("end", text, data={"ended_by": self.ended_by, "winner": end.get("winner")})
        self.world.journal.clear()
        self._flush_events()

    def _run_scheduled(self) -> None:
        world = self.world
        while world.scheduled and world.scheduled[0][0] <= world.now():
            _, _, item = heapq.heappop(world.scheduled)
            if "delivery" in item:
                run_delivery(self, item)
            else:
                self._atomic(item["effects"], world.thaw(item["vars"]), item["path"])

    def _run_events(self, phase: str) -> None:
        world = self.world
        for index, event in enumerate(self.contract.events):
            if event.phase != phase:
                continue
            path = f"events[{index}]"
            if event.arms is not None and self.arm not in event.arms:
                continue
            if event.once and index in self._fired_once:
                continue
            if not self._due(event, path):
                continue
            if event.once:
                self._fired_once.add(index)
            if event.each is not None:
                item_name = event.as_ or "it"
                try:
                    items = world.entities_of(event.each) if event.each in self.contract.types else \
                        compile_expr(event.each)(world.scope())
                    for position, item in enumerate(items or []):
                        inner = {item_name: item, "i": position}
                        if event.where is not None and not truthy(compile_expr(event.where)(world.scope(**inner))):
                            continue
                        self._atomic(event.do, inner, f"{path}.do")
                except ExprError as exc:
                    raise RunError(str(exc), path) from None
            else:
                self._atomic(event.do, {}, f"{path}.do")
            if event.say:
                try:
                    text = compile_template(event.say, None).render(world.scope())
                except ExprError as exc:
                    raise RunError(str(exc), f"{path}.say") from None
                if text.strip():
                    world.emit("news", text, data={"event": event.name or index})
                world.journal.clear()
            if self._ended():
                return

    def _due(self, event: Any, path: str) -> bool:
        world = self.world
        scope = world.scope()
        try:
            if event.at is not None:
                at = compile_expr(event.at)(scope) if isinstance(event.at, str) else event.at
                rounds = at if isinstance(at, list) else [at]
                if world.round not in rounds:
                    return False
            if event.every is not None and (world.round - 1) % event.every != 0:
                return False
            if event.when is not None and not truthy(compile_expr(event.when)(scope)):
                return False
            if event.chance is not None:
                p = compile_expr(event.chance)(scope) if isinstance(event.chance, str) else event.chance
                if isinstance(p, bool) or not isinstance(p, (int, float)):
                    raise ExprError(f"chance must be a number from 0 to 1, got {p!r}", str(event.chance))
                if world.rng.random() >= p:
                    return False
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        return True

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
            self._react(self._stage_spec())
        return True

    def _stage_spec(self) -> Optional[StageSpec]:
        name = self.world.stage
        return next((s for s in self.contract.stage_list() if s.name == name), None) if name else None

    def _after_commit(self, path: str) -> None:
        self._check_invariants(path)
        self.world.journal.clear()
        self._check_triggers(path)

    #: How deep triggers may set off further triggers, and reactions further reactions.
    TRIGGER_DEPTH = 8
    REACTION_DEPTH = 4

    def _check_triggers(self, path: str) -> None:
        if not self.contract.triggers or self._ended():
            return
        if self._trigger_depth >= self.TRIGGER_DEPTH:
            raise RunError(f"triggers set each other off more than {self.TRIGGER_DEPTH} levels deep (a loop?)", path)
        world = self.world
        self._trigger_depth += 1
        try:
            for index, trigger in enumerate(self.contract.triggers):
                if trigger.arms is not None and self.arm not in trigger.arms:
                    continue
                if trigger.once and index in self._triggers_fired:
                    continue
                where = f"triggers[{index}]"
                try:
                    holds = truthy(compile_expr(trigger.when)(world.scope()))
                except ExprError as exc:
                    raise RunError(str(exc), f"{where}.when") from None
                was = self._trigger_armed.get(index, False)
                self._trigger_armed[index] = holds
                if not holds or was:
                    continue
                if trigger.once:
                    self._triggers_fired.add(index)
                self._atomic(trigger.do, {}, f"{where}.do")
                if trigger.say:
                    try:
                        text = compile_template(trigger.say, None).render(world.scope())
                    except ExprError as exc:
                        raise RunError(str(exc), f"{where}.say") from None
                    if text.strip():
                        world.emit("news", text, data={"trigger": trigger.name or index})
                    world.journal.clear()
                if self._ended():
                    return
        finally:
            self._trigger_depth -= 1

    def _react(self, stage: Optional[StageSpec]) -> None:
        """Give every agent asked to react (`wake` with `now`) a turn right away, in the current stage."""
        world = self.world
        while world.reactions and not self._ended():
            entity_id, why = world.reactions.pop(0)
            actor = world.entities.get(entity_id)
            if actor is None or not actor.alive or not self.contract.is_agent(actor.entity_type):
                continue
            if self._reaction_depth >= self.REACTION_DEPTH:
                raise RunError(f"reactions set each other off more than {self.REACTION_DEPTH} levels deep", "wake.now")
            spec = stage or next(iter(self.contract.stage_list()))
            self._reaction_depth += 1
            try:
                turn = Turn(self, actor, spec, why, staged=False)
                turn.stats.reactions = 1
                self._drive(turn)
            finally:
                self._reaction_depth -= 1
            memory = self._memory(actor.id)
            memory.cursor = world.log[-1].seq if world.log else 0
            memory.turns += 1

    # -- stages & turns ------------------------------------------------------------------

    def _run_stage(self, stage: StageSpec) -> _Steps:
        world = self.world
        path = f"stages.{stage.name}"
        if not self._stage_runs(stage):
            return
        world.stage = stage.name
        self._atomic(stage.on_enter, {}, f"{path}.on_enter")
        if self._ended():
            return
        passes = stage.passes or (10 if stage.until else 1)
        for pass_index in range(passes):
            if pass_index:
                yield _Point(stage)
            agents = self._eligible(stage)
            if stage.turns == "simultaneous":
                yield from self._simultaneous(stage, agents, pass_index)
            elif stage.turns == "scheduled":
                yield from self._scheduled(stage, agents, pass_index)
            else:
                yield from self._sequential(stage, agents, pass_index)
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
        agents = [e for e in world.entities.values() if e.alive and e.entity_type in agent_types
                  and stage_actions(self.contract, stage, e.entity_type)]
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

    def _sequential(self, stage: StageSpec, agents: List[Entity], pass_index: int) -> _Steps:
        for actor in agents:
            if not actor.alive or self._ended():
                return
            reason = self._reason(actor, stage, pass_index)
            if reason is None:
                continue
            if not self._wake_hook(stage, actor):
                continue
            yield _Point(stage, {actor.id: reason})
            turn = Turn(self, actor, stage, reason, staged=False)
            self._drive(turn)
            if stage.on_idle and turn.stats.actions == 0 and actor.alive:
                self._atomic(stage.on_idle, {"actor": actor}, f"stages.{stage.name}.on_idle")
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
            self._drive(turn)
            if stage.on_idle and turn.stats.actions == 0 and actor.alive:
                self._atomic(stage.on_idle, {"actor": actor}, f"stages.{stage.name}.on_idle")
            self._turn_end_hook(stage, actor)
            scheduled = world.wake_at.get(actor.id, now)
            if scheduled <= now:
                step = turn.elapsed if turn.elapsed > 0 else self._interval(stage, actor)
                world.set_wake_at(actor.id, now + step)
            memory = self._memory(actor.id)
            memory.cursor = world.log[-1].seq if world.log else 0
            memory.turns += 1
            self._flush_events()

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

    def _simultaneous(self, stage: StageSpec, agents: List[Entity], pass_index: int) -> _Steps:
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
        concurrent = [t for t in turns if getattr(self._participant(t.actor), "concurrent", True)]
        if self.parallel > 1 and len(concurrent) > 1:
            with ThreadPoolExecutor(max_workers=min(self.parallel, len(concurrent))) as pool:
                list(pool.map(self._drive, concurrent))
            for turn in turns:
                if turn not in concurrent:
                    self._drive(turn)
        else:
            for turn in turns:
                self._drive(turn)
        for turn in turns:
            for name, args in turn.intents:
                if self._ended():
                    return
                self._commit_intent(turn, name, args)
            if stage.on_idle and not turn.intents and turn.actor.alive and not self._ended():
                self._atomic(stage.on_idle, {"actor": turn.actor}, f"stages.{stage.name}.on_idle")
            self._turn_end_hook(stage, turn.actor)
        self._flush_events()

    def _commit_intent(self, turn: Turn, name: str, args: Dict[str, Any]) -> None:
        actor, world = turn.actor, self.world
        blocked = self.actions.blocked(actor, name, {}, {}) if actor.alive else "you are no longer active"
        params, problem = ({}, blocked) if blocked else self.actions.validate(actor, name, args)
        verb = name.replace("_", " ")
        with self._lock:
            if problem:
                world.emit("outcome", f"Your {verb} did not happen: {str(problem).rstrip('.')}.",
                           actor=actor.id, to=(actor.id,), data={"action": name, "ok": False})
                world.journal.clear()
                self.stats.rejected_actions += 1
                return
            outcome = self.actions.apply(actor, name, params)
            text = outcome.text if outcome.ok else f"Your {verb} failed: {outcome.text}"
            world.emit("outcome", text, actor=actor.id, to=(actor.id,), data={"action": name, "ok": outcome.ok})
            if outcome.ok:
                self.stats.actions += 1
                self._after_commit(f"actions.{name}")
                self._react(turn.stage)
            else:
                self.stats.rejected_actions += 1
                world.journal.clear()

    def _drive(self, turn: Turn) -> None:
        if turn.stage.auto and not turn.staged and self._auto_turn(turn):
            return
        participant = self._participant(turn.actor)
        world = self.world
        world.use_turn_rng(self.seeds.rng("turn", world.round, turn.number))
        world.use_turn_pending(turn.pending)
        try:
            answer = participant(Wake(turn))
            if inspect.isawaitable(answer):
                close = getattr(answer, "close", None)
                if callable(close):
                    close()  # never awaited: close it so it does not linger
                raise RunError(f"participant for {turn.actor.id} is async; participants are plain functions "
                               "(wrap an async agent with asyncio.run or a thread)", f"participant:{turn.actor.id}")
        except (RunError, ExprError):
            raise
        except Exception as exc:
            raise RunError(f"participant for {turn.actor.id} raised {type(exc).__name__}: {exc}",
                           f"participant:{turn.actor.id}") from exc
        finally:
            world.use_turn_rng(None)
            world.use_turn_pending(None)
            turn.done = True
            if turn.stats.actions == 0 and not turn.intents:
                turn.stats.idle_turns += 1
            with self._lock:
                self.stats.add(turn.stats)

    def _auto_turn(self, turn: Turn) -> bool:
        """Play a trivial turn without the agent: the only legal action when it takes no arguments,
        or nothing when no action is legal. False when the agent has a real choice."""
        world = self.world
        world.use_turn_rng(self.seeds.rng("turn", world.round, turn.number))
        world.use_turn_pending(turn.pending)
        try:
            acts = [tool for tool in turn.tools() if tool.kind == "act"]
            if len(acts) > 1 or (acts and acts[0].input_schema.get("properties")):
                return False
            if acts:
                turn.call(acts[0].name, {})
            if not turn.done:
                turn.call(END_TURN, {})
            turn.stats.wakes = 0
            turn.stats.auto_turns = 1
        finally:
            world.use_turn_rng(None)
            world.use_turn_pending(None)
            turn.done = True
            if turn.stats.actions == 0 and not turn.intents:
                turn.stats.idle_turns += 1
            with self._lock:
                self.stats.add(turn.stats)
        return True

    # -- preview ---------------------------------------------------------------------------

    def _preview_now(self, entity_id: str, stage: Optional[str]) -> Dict[str, Any]:
        """The turn as it would look in the current state, without playing anything."""
        actor = self.world.entity(entity_id)
        assert actor is not None
        stages = self.contract.stage_list()
        acting = [s for s in stages if stage_actions(self.contract, s, actor.entity_type)]
        if stage is not None:
            spec = next(s for s in stages if s.name == stage)
        else:
            running = [s for s in acting if self._stage_runs(s)]
            spec = next((s for s in running if actor in self._eligible(s, ordered=False)), None) \
                or next(iter(running or acting or stages))
        reason = "Everyone chooses at the same time." if spec.turns == "simultaneous" else "It is your turn."
        if not self._stage_runs(spec):
            reason = f"(Preview only: stage {spec.name} does not run now.)"
        elif actor not in self._eligible(spec, ordered=False):
            reason = f"(Preview only: {actor.name} would not be woken in {spec.name} now.)"
        return self._preview_turn(entity_id, spec, reason)

    def _preview_turn(self, entity_id: str, spec: StageSpec, reason: str) -> Dict[str, Any]:
        actor = self.world.entities[entity_id]
        turn = Turn(self, actor, spec, reason, spec.turns == "simultaneous", peek=True)
        extras = self._turn_tool_specs()
        if extras:  # what the agent will be offered, in-turn host tools included
            from .host.turn_tools import HostWake

            tools = HostWake(turn, extras).tools
        else:
            tools = turn.tools()
        return {"brief": turn.brief, "update": turn.update, "tools": [t.to_dict() for t in tools],
                "tokens": {"brief": len(turn.brief) // 4, "update": len(turn.update) // 4,
                           "tools": len(json.dumps([t.to_anthropic() for t in tools])) // 4}}

    # -- participants ----------------------------------------------------------------------

    def _bind(self, participants: Any) -> None:
        if participants is None:
            return
        if callable(participants) or isinstance(participants, str):
            participants = {"*": participants}
        if not isinstance(participants, Mapping):
            raise TypeError("participants must be a callable, a string, or a mapping")
        known = set(self.contract.types) | set(self.world.entities) | {"*"}
        for key, value in participants.items():
            if key not in known:
                raise ValueError(f"participants key '{key}' is not an entity id, a type, or '*'")
            if not callable(value):
                resolve_participant(value, self.contract, 0)  # an unknown name fails now, not mid-run
            elif inspect.iscoroutinefunction(value) or inspect.iscoroutinefunction(getattr(value, "__call__", None)):
                raise TypeError(f"participant for '{key}' is async; participants are plain functions "
                                "(wrap an async agent with asyncio.run or a thread)")
        self._participants_spec = dict(participants)
        self._participants.clear()

    def _turn_tool_specs(self) -> Dict[str, Any]:
        """The contract's in-turn host tools (recall, note, host services), by name."""
        tools = self.__dict__.get("_turn_tools")
        if tools is None:
            from .host.turn_tools import turn_tools

            tools = self.__dict__["_turn_tools"] = turn_tools(self.contract)
        return tools

    def _with_turn_tools(self, participant: Participant) -> Participant:
        """The participant, offered the contract's in-turn host tools if it has any."""
        tools = self._turn_tool_specs()
        if not tools:
            return participant
        from .host.turn_tools import offer

        return offer(participant, tools)

    def _participant(self, actor: Entity) -> Participant:
        cached = self._participants.get(actor.id)
        if cached is not None:
            return cached
        spec = self._participants_spec
        lineage = list(reversed(self.contract.lineage(actor.entity_type)))  # most specific type first
        value = spec.get(actor.id)
        if value is None:
            value = next((spec[kind] for kind in lineage if kind in spec), spec.get("*"))
        if value is None:
            value = next((self.contract.types[kind].policy for kind in lineage if self.contract.types[kind].policy),
                         None) or "random"
        participant = resolve_participant(value, self.contract, self.seeds.derive("participant"))
        participant = self._with_turn_tools(participant)
        self._participants[actor.id] = participant
        return participant

    # -- checks --------------------------------------------------------------------------------

    def _inspect_rule(self, type_name: str) -> Any:
        """The inspect rule for a type, inherited through `extends`."""
        for kind in reversed(self.contract.lineage(type_name)):
            if "inspect" in self.contract.types[kind].model_fields_set:
                return self.contract.types[kind].inspect
        return True

    def _check_invariants(self, path: str) -> None:
        scope = self.world.scope()
        for index, invariant in enumerate(self.contract.invariants):
            try:
                holds = truthy(compile_expr(invariant.expr)(scope))
            except ExprError as exc:
                raise RunError(str(exc), f"invariants[{index}]") from None
            if not holds:
                why = f" ({invariant.why})" if invariant.why else ""
                raise InvariantViolation(f"invariant `{invariant.expr}` no longer holds after {path}{why}",
                                         f"invariants[{index}]")

    def _check_end(self) -> None:
        world = self.world
        if world.end_request is not None or world.round == 0:
            return
        scope = world.scope()
        for index, end in enumerate(self.contract.end):
            path = f"end[{index}]"
            try:
                if not truthy(compile_expr(end.when)(scope)):
                    continue
                winner = _plain(compile_expr(end.winner)(scope)) if end.winner else None
                text = compile_template(end.say, None).render(scope) if end.say else ""
            except ExprError as exc:
                raise RunError(str(exc), path) from None
            world.request_end(end.name or f"end_{index}", winner, text)
            return

    # -- helpers --------------------------------------------------------------------------------

    def _memory(self, entity_id: str) -> Memory:
        memory = self._memories.get(entity_id)
        if memory is None:
            memory = self._memories[entity_id] = Memory()
        return memory

    def _brief(self, actor: Entity) -> str:
        brief = self._briefs.get(actor.id)
        if brief is None:
            brief = self._briefs[actor.id] = self.perception.brief(actor)
        return brief

    def _flush_events(self) -> None:
        if self._on_event is None:
            self._emitted = len(self.world.log)
            return
        while self._emitted < len(self.world.log):
            event = self.world.log[self._emitted]
            self._emitted += 1
            self._on_event(event.to_dict())
