"""Copying a stepped run directly while a turn waits for a decision (or before it starts, or once it is over).

The copy gets its own world — entities, properties, links, records, log, schedule, counters, random stream,
exposures, the asset index — its own bookkeeping — statistics, memories, triggers, tape — its own copies of the turns in
progress with their random streams, and a round that resumes where the original's is (:class:`~.runtime._Where`).
Nothing mutable is shared, so the two runs continue independently and each exactly as the original would.
Immutable things are shared: the contract, logged events, scheduled items, snapshots.

Every attribute of a run, its world and its turns is accounted for below. A run holding what is not copied here —
physics, a space, hosts, a budget, changes not yet committed (an atomic turn), a scheduled stage, or an attribute
this module does not know — raises :class:`NotCopyable`, and the caller copies it by replaying instead.
"""
from __future__ import annotations

import random
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from ..entity import Entity
from .exposure import Exposure, ExposureLog
from .host.hosts import hosts_for
from .measure import Stats
from .replay import Origin
from .run_diagnosis import Diagnosis, _copy as _copy_counts
from .runtime import _Where
from .stepping import SteppedEnv, Waiting
from .turn import Memory, Turn
from .type_index import TypeIndex
from .world import SdkWorld, _copy
from .world_parts import ClockView, Entry, Journal, PhysicsView, PropsView

__all__ = ["NotCopyable", "copy_run"]


class NotCopyable(Exception):
    """The run holds something a direct copy does not carry; copy it by replaying."""


_ENV_FIELDS = frozenset({
    "contract", "inputs", "seed", "arm", "parallel", "seeds", "world", "effects", "actions", "perception", "stats",
    "agent_stats", "status", "ended_by", "error", "_memories", "_briefs", "_used_round", "_fired_once", "_lock",
    "_signal", "_running", "driver", "time_limit", "budget", "happenings", "previews", "_on_event", "_emitted",
    "_turn_count", "_cursor", "_where", "_trigger_armed", "_triggers_fired", "_in_round", "origin", "_inspectable",
    "_invariant_held", "pilot", "calibration", "build_seed", "stepper", "diagnosis", "_end_on_action", "_brief_assets", "_inspect_cache"})
_WORLD_FIELDS = frozenset({
    "contract", "inputs", "seeds", "arm", "_local", "_rng", "entities", "props", "links", "link_fields", "adjacent",
    "records_store", "entry_by_seq", "record_authors", "record_events", "entity_briefs", "log", "physics", "physics_writes", "entity_dynamics", "round",
    "stage", "rounds", "metrics", "series", "scheduled", "wake_requests", "reactions", "time", "horizon", "start", "wake_at",
    "_schedule_seq", "space", "buffer", "end_request", "chance_picker", "counters", "journal", "lifecycle",
    "exposures", "written", "watched_writes", "diagnosis", "_seq", "_record_seq", "_props_view", "_physics_view", "_clock_view",
    "_type_props", "_def_cache", "_def_cache_state", "_def_cache_on", "_subtypes", "types", "assets", "patterns"})
#: Mechanisms keep plain data of their own on the world under these prefixes.
_WORLD_STORES = ("_channel_visible:",)
_TURN_FIELDS = frozenset({
    "env", "actor", "stage", "reason", "staged", "peek", "round", "_since", "_views", "_brief", "_update", "max_actions", "max_calls", "calls_left",
    "reads_left", "_reads", "did_not_act", "actions_left", "done", "used", "intents", "pending", "stats", "elapsed", "_offered", "_tools", "time_limit",
    "deadline", "timed_out", "closed", "busy", "tallied", "atomic", "_mark", "_counted", "number", "exposure",
    "_delivered"})
_WAKE_FIELDS = frozenset({"_turn", "_extras", "_used"})
_RECORD_LISTS = ("views", "news", "entries", "view_events", "tools", "tool_sets", "calls")


def copy_run(source: SteppedEnv, waiting: Optional[Waiting]) -> Tuple[SteppedEnv, Optional[Waiting]]:
    """An independent copy of ``source`` — waiting in ``waiting``, or not in a round — and the copy's waiting turn."""
    _refuse(source, waiting)
    env = SteppedEnv.__new__(SteppedEnv)
    world = _copy_world(source.world)
    exposures = world.exposures
    turns: Dict[int, Turn] = {}

    def turn_of(turn: Turn) -> Turn:
        copied = turns.get(id(turn))
        if copied is None:
            copied = turns[id(turn)] = _copy_turn(turn, env, world.entities, exposures)
        return copied

    env.__dict__.update(
        contract=source.contract, inputs=source.inputs, seed=source.seed, arm=source.arm, parallel=source.parallel,
        seeds=source.seeds, world=world, status=source.status, ended_by=source.ended_by, error=source.error,
        time_limit=source.time_limit, budget=None, _on_event=None, _emitted=source._emitted,
        _turn_count=source._turn_count, _in_round=source._in_round, _inspectable=source._inspectable,
        _end_on_action=source._end_on_action, pilot=None, calibration=source.calibration, _inspect_cache=None,
        build_seed=source.build_seed, stepper=None, _invariant_held={}, _briefs=dict(source._briefs),
        _brief_assets={key: list(ids) for key, ids in source._brief_assets.items()},
        _fired_once=set(source._fired_once), _trigger_armed=dict(source._trigger_armed),
        _triggers_fired=set(source._triggers_fired),
        _used_round={actor: dict(used) for actor, used in source._used_round.items()},
        stats=_copy_stats(source.stats), agent_stats={key: _copy_stats(s) for key, s in source.agent_stats.items()},
        _memories={key: _copy_memory(memory) for key, memory in source._memories.items()})
    env._lock = threading.RLock()
    env._signal = threading.Condition(env._lock)
    env._running = threading.Lock()
    env.effects = _rebound(source.effects, world=world)
    world.lifecycle = env.effects.lifecycle
    env.actions = _rebound(source.actions, world=world, effects=env.effects)
    env.perception = _rebound(source.perception, world=world)
    env.happenings = _rebound(source.happenings, env=env)
    env.previews = _rebound(source.previews, env=env, frames=list(source.previews.frames))
    env.driver = _rebound(source.driver, env=env, spec=dict(source.driver.spec), _resolved={}, loop=None)
    env.diagnosis = world.diagnosis = _copy_diagnosis(source.diagnosis, world.written)
    origin = Origin.__new__(Origin)
    kept = source.origin
    origin.base, origin.start, origin.tape, origin.checkpoint_due = kept.base, kept.start, kept.tape.copy(), kept.checkpoint_due
    origin.staged, origin.unarmed = [turn_of(turn) for turn in kept.staged], kept.unarmed
    env.origin = origin
    copied = None if waiting is None else _copy_waiting(waiting, turn_of(waiting.wake._turn))
    env._where, env._cursor = _Where(), None
    if source._cursor is not None:
        assert copied is not None
        env._where = _copy_where(source, env, copied)
        env._cursor = env._round(resumed=True)
    return env, copied


def _refuse(source: SteppedEnv, waiting: Optional[Waiting]) -> None:
    world = source.world
    why = None
    unknown = set(vars(source)) - _ENV_FIELDS
    if unknown:
        why = f"the run has attributes a copy does not carry: {sorted(unknown)}"
    elif source.budget is not None or source._on_event is not None or source.pilot is not None:
        why = "the run has a budget, an event callback or a pilot"
    elif world.physics is not None or world.space is not None or world.entity_dynamics or world.buffer is not None:
        why = "the world has physics or a space, or a sync event is being applied"
    elif world.reactions or world.journal.mark() or hosts_for(world) is not None or world.watched_writes is not None:
        why = "the world has pending reactions, uncommitted changes or hosts"
    elif source._cursor is not None and (waiting is None or _stage_kind(source) == "scheduled"):
        why = "the run is not waiting in a sequential or simultaneous turn"
    if why is not None:
        raise NotCopyable(why)


def _stage_kind(source: SteppedEnv) -> str:
    return source.contract.stage_list()[source._where.stage].turns


def _copy_where(source: SteppedEnv, env: SteppedEnv, waiting: Waiting) -> _Where:
    kept, entities = source._where, env.world.entities
    turn = waiting.wake._turn
    where = _Where(kept.stage, kept.pass_index, [entities[agent.id] for agent in kept.agents], kept.position)
    if _stage_kind(source) == "simultaneous":
        where.position = env.origin.staged.index(turn)
    else:
        where.turn = turn
    return where


def _copy_world(source: SdkWorld) -> SdkWorld:
    stores = {key: _copy(value) for key, value in vars(source).items() if key.startswith(_WORLD_STORES)}
    unknown = set(vars(source)) - _WORLD_FIELDS - set(stores)
    if unknown:
        raise NotCopyable(f"the world has attributes a copy does not carry: {sorted(unknown)}")
    world = SdkWorld.__new__(SdkWorld)
    entities = {key: _copy_entity(entity) for key, entity in source.entities.items()}
    records: Dict[str, List[Entry]] = {}
    by_seq: Dict[int, Entry] = {}
    for name, rows in source.records_store.items():
        copies = []
        for row in rows:
            entry = Entry({key: _copy(value) for key, value in row.items()})
            entry.world = world
            copies.append(entry)
            if source.entry_by_seq.get(row["seq"]) is row:
                by_seq[row["seq"]] = entry
        records[name] = copies
    journal = Journal()
    journal.version = source.journal.version
    types = TypeIndex.__new__(TypeIndex)
    types.__dict__.update(_kinds=source.types._kinds, _queries=source.types._queries, _members=source.types._members)
    types.rebuild(entities.values())
    world.__dict__.update(
        stores, contract=source.contract, inputs=source.inputs, seeds=source.seeds, arm=source.arm,
        _local=threading.local(), _rng=_copy_rng(source._rng), entities=entities, props=_copy(source.props),
        links={kind: dict(edges) for kind, edges in source.links.items()},
        link_fields={kind: {key: _copy(value) for key, value in fields.items()}
                     for kind, fields in source.link_fields.items()},
        adjacent={kind: {key: dict(counts) for key, counts in by_id.items()} for kind, by_id in source.adjacent.items()},
        records_store=records, entry_by_seq=by_seq, entity_briefs=dict(source.entity_briefs), log=list(source.log),
        physics=None, physics_writes=source.physics_writes, entity_dynamics=[], round=source.round, stage=source.stage,
        rounds=source.rounds, metrics=_copy(source.metrics), series=_copy(source.series),
        scheduled=list(source.scheduled), wake_requests=dict(source.wake_requests), reactions=[], time=source.time,
        horizon=source.horizon, start=source.start, wake_at=dict(source.wake_at), _schedule_seq=source._schedule_seq,
        space=None,
        buffer=None, end_request=_copy(source.end_request), chance_picker=None, counters=dict(source.counters),
        journal=journal, lifecycle=None, exposures=_copy_exposures(source.exposures), written=set(source.written),
        watched_writes=None, diagnosis=None, _seq=source._seq,
        _record_seq=source._record_seq, _type_props=source._type_props, _def_cache={}, _def_cache_state=None,
        _def_cache_on=source._def_cache_on, _subtypes=source._subtypes, types=types, assets=source.assets.copy())
    world._props_view, world._physics_view, world._clock_view = PropsView(world), PhysicsView(world), ClockView(world)
    world.rebuild_record_index()
    world.rebuild_event_index()
    world.patterns = source.patterns.bound_to(world)
    return world


def _copy_diagnosis(source: Diagnosis, written: Set[str]) -> Diagnosis:
    """The run's diagnostic counts, sharing the copied world's set of written properties as the original does."""
    unknown = set(vars(source)) - {"actions", "stages", "agents", "overwrites", "loop_overwrites", "written", "_probed"}
    if unknown:
        raise NotCopyable(f"the run's diagnosis has attributes a copy does not carry: {sorted(unknown)}")
    diagnosis = Diagnosis(written)
    diagnosis.actions, diagnosis.stages = _copy_counts(source.actions), _copy_counts(source.stages)
    diagnosis.agents, diagnosis.overwrites = _copy_counts(source.agents), _copy_counts(source.overwrites)
    diagnosis.loop_overwrites = _copy_counts(source.loop_overwrites)
    diagnosis._probed = (source._probed[0], set(source._probed[1]))
    return diagnosis


def _copy_entity(source: Entity) -> Entity:
    entity = Entity.__new__(Entity)
    entity.__dict__.update(source.__dict__)
    entity.properties = _copy(source.properties)
    return entity


def _copy_rng(source: random.Random) -> random.Random:
    rng = random.Random.__new__(random.Random)
    rng.setstate(source.getstate())
    return rng


def _copy_stats(source: Stats) -> Stats:
    stats = Stats.__new__(Stats)
    stats.__dict__.update(source.__dict__)
    return stats


def _copy_memory(source: Memory) -> Memory:
    memory = Memory()
    memory.cursor, memory.views, memory.turns = source.cursor, dict(source.views), source.turns
    return memory


def _rebound(source: Any, **changes: Any) -> Any:
    """A shallow copy of one of the run's parts, pointed at the copy's world or run."""
    part = object.__new__(type(source))
    part.__dict__.update(source.__dict__)
    part.__dict__.update(changes)
    return part


def _copy_turn(source: Turn, env: SteppedEnv, entities: Dict[str, Entity], log: Optional[ExposureLog]) -> Turn:
    unknown = set(vars(source)) - _TURN_FIELDS
    if unknown or source._mark is not None or source.busy or source.deadline is not None or source.peek:
        raise NotCopyable(f"a turn in progress cannot be copied directly ({sorted(unknown) or 'uncommitted or timed'})")
    turn = Turn.__new__(Turn)
    turn.__dict__.update(source.__dict__)
    actor = entities[source.actor.id]
    memory, kept = env._memories.get(actor.id), source.env._memories.get(actor.id)
    shares_memory = memory is not None and kept is not None and source._views is kept.views
    turn.__dict__.update(
        env=env, actor=actor, _views=memory.views if shares_memory and memory is not None else dict(source._views),
        used=dict(source.used), intents=list(source.intents), pending=list(source.pending),
        stats=_copy_stats(source.stats), _tools=None, _counted=list(source._counted), _delivered=list(source._delivered),
        exposure=_copy_exposure(source.exposure, log) if source.exposure is not None else None)
    return turn


def _copy_waiting(source: Waiting, turn: Turn) -> Waiting:
    wake = source.wake
    unknown = set(vars(wake)) - _WAKE_FIELDS
    if unknown:
        raise NotCopyable(f"the waiting turn's wake has attributes a copy does not carry: {sorted(unknown)}")
    copied = type(wake).__new__(type(wake))
    copied.__dict__.update(wake.__dict__)
    copied._turn = turn
    if isinstance(getattr(wake, "_used", None), dict):
        copied._used = dict(wake._used)  # type: ignore[attr-defined]
    return Waiting(copied, _copy_rng(source.rng))


def _copy_exposures(source: Optional[ExposureLog]) -> Optional[ExposureLog]:
    if source is None:
        return None
    log = ExposureLog.__new__(ExposureLog)
    log.texts, log.chance = dict(source.texts), list(source.chance)
    log.wakes = [{**record, **{key: dict(record[key]) for key in ("usage", "late_usage") if key in record}}
                 for record in source.wakes]
    log._events = {key: set(value) for key, value in source._events.items()}
    log._entries = {key: set(value) for key, value in source._entries.items()}
    log._views = {key: set(value) for key, value in source._views.items()}
    return log


def _copy_exposure(source: Exposure, log: Optional[ExposureLog]) -> Exposure:
    assert log is not None and source.logged is None
    exposure = Exposure.__new__(Exposure)
    record = dict(source.record)
    for key in _RECORD_LISTS:
        record[key] = list(record[key])
    if "usage" in record:
        record["usage"] = dict(record["usage"])
    if "assets" in record:  # the files delivered so far (ids and hashes); the copy appends to its own list
        record["assets"] = list(record["assets"])
    exposure.log, exposure.staged, exposure.record = log, source.staged, record
    exposure._deferred, exposure.logged = list(source._deferred), None
    return exposure
