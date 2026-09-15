"""The live world of one run: entities, global properties, links, records, the event log,
physics and space — every mutation journaled so an action commits atomically or not at all."""
from __future__ import annotations

import datetime as _dt
import heapq
import math
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from difflib import get_close_matches
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from ..entity import Entity
from ..physics import PhysicsModel, _CompiledExpr
from .contract import Contract, PropSpec
from .errors import RunError
from .expr import ExprError, FUNCTIONS, Scope, Untrusted, World, compile_expr, is_expr, truthy
from .props import finite_number as _finite_number, prop_type, shown_value as _shown_value
from .seeds import SeedTree
from . import links as _links, world_physics
from .links import Link

__all__ = ["SdkWorld", "Entry", "LogEvent", "Abort", "prop_type"]


class _TurnLocal:
    """Per-turn state (a turn's random stream, its ``$pending``, draw and def-depth counters)."""

    __slots__ = ("rng", "pending", "draws", "depth")
    rng: Any
    pending: Optional[List[Dict[str, Any]]]
    draws: int
    depth: int


#: The turn running in this thread or asyncio task, as ``(world, state)``. A context variable rather
#: than a thread-local, so async participants sharing one event-loop thread each keep their own.
_TURN: ContextVar[Optional[Tuple["SdkWorld", _TurnLocal]]] = ContextVar("fg_env_turn", default=None)


class Abort(Exception):
    """Stop the current action; every change it made is rolled back. The text reaches the actor."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class Entry(dict):
    """One record entry. ``author`` reads as the authoring entity."""

    world: "SdkWorld"

    def expr_attr(self, name: str, source: Optional[str]) -> Any:
        if name == "author":
            author = self.get("author")
            return self.world.entities.get(author) if author else None
        if name in self:
            return self[name]
        raise ExprError(f"record entry has no field '{name}' (fields: {', '.join(sorted(self))})", source)


@dataclass(eq=False)
class LogEvent:
    """Something that happened, in order. ``to`` None means every agent may learn of it."""

    seq: int
    round: int
    kind: str
    text: str = ""
    actor: Optional[str] = None
    to: Optional[Tuple[str, ...]] = None
    data: Dict[str, Any] = field(default_factory=dict)
    stage: Optional[str] = None
    #: Clock time when it happened (continuous clock only).
    time: Optional[float] = None

    def visible_to(self, entity_id: str) -> bool:
        return self.to is None or entity_id in self.to

    def expr_attr(self, name: str, source: Optional[str]) -> Any:
        if name in ("seq", "round", "kind", "text", "actor", "stage", "time"):
            return getattr(self, name)
        if name in self.data:
            return self.data[name]
        raise ExprError(f"event has no field '{name}'", source)

    def to_dict(self) -> Dict[str, Any]:
        out = {"seq": self.seq, "round": self.round, "kind": self.kind}
        for key in ("text", "actor", "stage"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.to is not None:
            out["to"] = list(self.to)
        if self.data:
            out["data"] = self.data
        if self.time is not None:
            out["time"] = self.time
        return out


class _Props:
    """``$world`` — global properties, readable and assignable."""

    def __init__(self, world: "SdkWorld"):
        self._world = world

    def expr_attr(self, name: str, source: Optional[str]) -> Any:
        values = self._world.props
        if name not in values:
            known = ", ".join(sorted(values)) or "none declared"
            raise ExprError(f"world has no property '{name}' (declared: {known})", source)
        return values[name]


class _Physics:
    """``$physics`` — current values of physics variables and params."""

    def __init__(self, world: "SdkWorld"):
        self._world = world

    def expr_attr(self, name: str, source: Optional[str]) -> Any:
        model = self._world.physics
        if model is None:
            raise ExprError("this environment declares no physics", source)
        if name in model.variables:
            return model.variables[name].value
        if name in model.params:
            return model.params[name]
        raise ExprError(f"physics has no variable or param '{name}'", source)


class _Clock:
    def __init__(self, world: "SdkWorld"):
        self._world = world

    def expr_attr(self, name: str, source: Optional[str]) -> Any:
        w = self._world
        if name == "round":
            return w.round
        if name == "rounds":
            return w.rounds
        if name == "unit":
            return w.contract.clock.unit
        if name == "left":
            return max(0, w.rounds - w.round)
        if name == "date":
            return w.date()
        if name == "label":
            return w.clock_label()
        if name == "time":
            return w.now()
        if name == "horizon":
            return w.horizon
        raise ExprError(f"clock has no field '{name}' (round, rounds, left, unit, date, label, time, horizon)", source)


class _Journal:
    def __init__(self) -> None:
        self._undo: List[Callable[[], object]] = []
        #: Bumped by every change and every undo: equal versions mean an unchanged world.
        self.version = 0

    def mark(self) -> int:
        return len(self._undo)

    def push(self, undo: Callable[[], object]) -> None:
        self._undo.append(undo)
        self.version += 1

    def rollback(self, mark: int) -> None:
        while len(self._undo) > mark:
            self._undo.pop()()
            self.version += 1

    def clear(self) -> None:
        self._undo.clear()


class SdkWorld(World):
    """World store for one run. Expressions read it through the :class:`World` interface."""

    def __init__(self, contract: Contract, inputs: Dict[str, Any], seeds: SeedTree, arm: Optional[str] = None):
        self.contract = contract
        self.inputs = inputs
        self.seeds = seeds
        self.arm = arm
        self._local = threading.local()
        self.rng = seeds.rng("world")
        self.entities: Dict[str, Entity] = {}
        self.props: Dict[str, Any] = {}
        self.links: Dict[str, Dict[Tuple[str, str], float]] = {name: {} for name in contract.relations}
        #: relation → (a, b) → the link's fields (relations that declare props)
        self.link_fields: Dict[str, Dict[Tuple[str, str], Dict[str, Any]]] = {name: {} for name in contract.relations}
        #: relation → entity id → {linked entity id: number of edges between them}
        self.adjacent: Dict[str, Dict[str, Dict[str, int]]] = {name: {} for name in contract.relations}
        self.records_store: Dict[str, List[Entry]] = {name: [] for name in contract.records}
        #: Retained record entries by sequence number (entries dropped by `keep` are removed).
        self.entry_by_seq: Dict[int, Entry] = {}
        #: Per-entity brief text rendered at build (from entities.*.brief / population.brief).
        self.entity_briefs: Dict[str, str] = {}
        self.log: List[LogEvent] = []
        self.physics: Optional[PhysicsModel] = None
        self.physics_writes: List[Tuple[str, _CompiledExpr]] = []
        self.entity_dynamics: List[Any] = []
        self.round = 0
        self.stage: Optional[str] = None
        self.rounds = 0
        self.metrics: Dict[str, Any] = {}
        self.series: Dict[str, List[Any]] = {}
        self.scheduled: List[Tuple[float, int, Dict[str, Any]]] = []
        self.wake_requests: Dict[str, str] = {}
        #: Agents asked to react right away (`wake` with `now`), answered as soon as the change commits.
        self.reactions: List[Tuple[str, str]] = []
        #: Continuous clock: the current time, when the run completes, and each agent's next wake time.
        self.time = 0.0
        self.horizon: Optional[float] = None
        self.wake_at: Dict[str, float] = {}
        #: Tie-break for scheduled effects due in the same round: the order they were scheduled.
        self._schedule_seq = 0
        #: Shortest-path distances on the (static) place graph, filled as they are asked for.
        self._graph_distances: Dict[Tuple[Any, Any], float] = {}
        self.end_request: Optional[Dict[str, Any]] = None
        self.counters: Dict[str, int] = {}
        self.journal = _Journal()
        #: Called as ``lifecycle(hook, entity, where)`` after every creation and removal (set by the effect runner).
        self.lifecycle: Optional[Callable[[str, Entity, str], None]] = None
        #: What each agent was shown (an :class:`~fg_env.sdk.exposure.ExposureLog`), when the run records it.
        self.exposures: Any = None
        self._seq = 0
        self._record_seq = 0
        self._props_view = _Props(self)
        self._physics_view = _Physics(self)
        self._clock_view = _Clock(self)
        self._type_props = {t: contract.props_of(t) for t in contract.types}
        #: Def results for the current world state (see :meth:`call_def`).
        self._def_cache: Dict[Any, Any] = {}
        self._def_cache_state: Any = None
        self._def_cache_on = False
        #: Empty while the world is being built (build writes state outside the journal).
        self._subtypes = {t: set(contract.subtypes(t)) for t in contract.types}

    # -- randomness --------------------------------------------------------------

    @property  # type: ignore[override]
    def rng(self) -> Any:
        """The random stream for the current context: a turn's own stream while an agent's turn
        runs (so concurrent turns never race for draws), otherwise the run's main stream."""
        local = self._here()
        local.draws = getattr(local, "draws", 0) + 1
        return getattr(local, "rng", None) or self._rng

    @rng.setter
    def rng(self, value: Any) -> None:
        self._rng = value

    def draws(self) -> int:
        """How many times this thread has used a random stream: equal counts mean nothing random was drawn."""
        return getattr(self._here(), "draws", 0)

    @contextmanager
    def turn_context(self, rng: Any, pending: Optional[List[Dict[str, Any]]]) -> Iterator[None]:
        """Inside the block — in this thread or asyncio task only — random draws use ``rng`` and
        ``$pending`` is ``pending``. Blocks nest (a reaction inside a turn) and restore on exit."""
        local = _TurnLocal()
        local.rng, local.pending = rng, pending
        token = _TURN.set((self, local))
        try:
            yield
        finally:
            _TURN.reset(token)

    def _here(self) -> Any:
        current = _TURN.get()
        return current[1] if current is not None and current[0] is self else self._local

    @contextmanager
    def drawing_from(self, rng: Any) -> Iterator[None]:
        """Inside the block this thread draws from ``rng``, then from the stream it used before."""
        previous = getattr(self._local, "rng", None)
        self._local.rng = rng
        try:
            yield
        finally:
            self._local.rng = previous

    # -- expression interface ------------------------------------------------

    def entities_of(self, type_name: str) -> List[Entity]:
        if type_name not in self.contract.types:
            raise ExprError(f"'{type_name}' is not a declared type (types: {', '.join(self.contract.types)})")
        kinds = self._subtypes[type_name]
        return [e for e in self.entities.values() if e.entity_type in kinds and e.alive]

    def entity(self, entity_id: Any) -> Optional[Entity]:
        if isinstance(entity_id, Entity):
            return entity_id
        return self.entities.get(entity_id) if isinstance(entity_id, str) else None

    def records(self, name: str) -> List[Entry]:
        if name not in self.records_store:
            known = ", ".join(self.records_store) or "none declared"
            raise ExprError(f"'{name}' is not a declared record (records: {known})")
        return self.records_store[name]

    def events(self, kind: Optional[str], viewer: Any = None) -> List[LogEvent]:
        """Events so far; with a ``viewer`` (views, record visibility) only those it may know about."""
        seen = viewer.id if isinstance(viewer, Entity) else None
        return [e for e in self.log if (kind is None or e.kind == kind) and (seen is None or e.visible_to(seen))]

    def relation(self, a: Any, b: Any, kind: str) -> Optional[float]:
        return _links.relation(self, a, b, kind)

    def neighbors(self, entity: Any, kind: str) -> List[Entity]:
        return _links.neighbors(self, entity, kind)

    def link_view(self, a: Any, b: Any, kind: str) -> Optional[Link]:
        """The live ``kind`` link from a to b, or None."""
        return _links.link_view(self, a, b, kind)

    def links_of(self, entity: Any, kind: str) -> List[Link]:
        return _links.links_of(self, entity, kind)

    def visible_records(self, name: str, viewer: Any) -> List[Entry]:
        rows = self.records(name)
        if not isinstance(viewer, Entity):
            return rows
        return [row for row in rows if self.entry_visible(name, row, viewer)]

    def entry_visible(self, record: str, entry: Entry, viewer: Optional[Entity]) -> bool:
        if viewer is None:
            return True
        to = entry.get("to")
        if to is not None and viewer.id not in to and entry.get("author") != viewer.id:
            return False
        visible = self.contract.records[record].visible
        if visible == "all":
            return True
        try:
            return truthy(compile_expr(visible)(self.scope(viewer=viewer, it=entry)))
        except ExprError as exc:
            raise RunError(str(exc), f"records.{record}.visible") from None

    def is_a(self, type_name: str, ancestor: str) -> bool:
        return type_name in self._subtypes.get(ancestor, ())

    def has_def(self, name: str) -> bool:
        spec = self.contract.defs.get(name)
        return spec is not None and not spec.args

    def defines(self, name: str) -> bool:
        return name in self.contract.defs

    def call_def(self, name: str, args: List[Any], source: str) -> Any:
        spec = self.contract.defs.get(name)
        if spec is None:
            hint = get_close_matches(name, list(FUNCTIONS) + list(self.contract.defs), n=1)
            raise ExprError(f"unknown function ${name}" + (f" — did you mean ${hint[0]}?" if hint else ""), source)
        if len(args) != len(spec.args):
            raise ExprError(f"${name} takes {len(spec.args)} argument(s) ({', '.join(spec.args) or 'none'}), got {len(args)}", source)
        key = self._def_key(name, args)
        if key is not None:
            state = self.state_version()
            if state != self._def_cache_state:
                self._def_cache, self._def_cache_state = {}, state
            elif key in self._def_cache:
                return self._def_cache[key]
        local = self._here()
        depth = getattr(local, "depth", 0)
        if depth >= 32:
            raise ExprError(f"${name}: defs call each other too deeply (recursion?)", source)
        local.depth = depth + 1
        drawn = self.draws()
        try:
            value = compile_expr(spec.expr)(self.scope(**dict(zip(spec.args, args))))
        finally:
            local.depth = depth
        # A call that drew a random number is never reused; with the same state and arguments a call that
        # drew nothing takes the same path again, so its value is exactly what a fresh call would return.
        if key is not None and self.draws() == drawn and self.state_version() == state and isinstance(value, _CACHEABLE):
            self._def_cache[key] = value
        return value

    def enable_def_cache(self) -> None:
        """Start caching def results; called once the world is built."""
        self._def_cache_on = True
        self.touch()

    def touch(self) -> None:
        """Record a change made outside the journal (metrics sampling, physics), so cached reads refresh."""
        self.journal.version += 1

    def state_version(self) -> Any:
        """Equal values mean nothing a read could see has changed (for caches of derived values)."""
        pending = getattr(self._here(), "pending", None)
        return (self.journal.version, self.round, self.stage, self.time, id(pending), len(pending or ()))

    def _def_key(self, name: str, args: List[Any]) -> Optional[Tuple[Any, ...]]:
        """A cache key for a def call, or None when the call cannot be cached."""
        if not self._def_cache_on:
            return None
        parts: List[Any] = [name]
        for arg in args:
            if isinstance(arg, Entity):
                parts.append(("$entity", arg.id))
            elif arg is None or type(arg) in (int, float, bool, str):
                parts.append((type(arg).__name__, arg))
            else:
                return None  # lists, maps, records and participant text are not cached
        return tuple(parts)

    def distance(self, a: Any, b: Any) -> float:
        start, end = _location(a), _location(b)
        space = self.contract.space
        if space is None or space.graph is None or start is None or end is None:
            return _distance(self.contract, start, end)
        key = (start, end)
        if key not in self._graph_distances:
            self._graph_distances[key] = self._graph_distances[(end, start)] = _distance(self.contract, start, end)
        return self._graph_distances[key]

    def prop_spec(self, entity: Entity, prop: str) -> PropSpec:
        specs = self._type_props.get(entity.entity_type, {})
        if prop not in specs:
            raise RunError(f"'{entity.entity_type}' has no property '{prop}' (declared: {', '.join(specs) or 'none'})",
                           f"{entity.entity_type}.{prop}")
        return specs[prop]

    def is_type(self, name: str) -> bool:
        return name in self.contract.types

    # -- scopes --------------------------------------------------------------

    def scope(self, **values: Any) -> Scope:
        base: Dict[str, Any] = {
            "inputs": self.inputs,
            "world": self._props_view,
            "physics": self._physics_view,
            "clock": self._clock_view,
            "round": self.round,
            "stage": self.stage,
            "metrics": self.metrics,
            "series": self.series,
            "arm": self.arm,
            "pending": getattr(self._here(), "pending", None) or [],
        }
        base.update(values)
        return Scope(base, self)

    # -- clock ---------------------------------------------------------------

    @property
    def continuous(self) -> bool:
        return self.contract.clock.mode == "continuous"

    def now(self) -> float:
        """The current time: the clock time when continuous, otherwise the round number."""
        return self.time if self.continuous else self.round

    def set_wake_at(self, entity_id: str, when: float) -> None:
        missing = entity_id not in self.wake_at
        old = self.wake_at.get(entity_id)
        self.wake_at[entity_id] = float(when)

        def undo() -> None:
            if missing:
                self.wake_at.pop(entity_id, None)
            else:
                self.wake_at[entity_id] = old  # type: ignore[assignment]

        self.journal.push(undo)

    def date(self) -> Optional[str]:
        clock = self.contract.clock
        if not clock.start:
            return None
        elapsed = self.time if self.continuous else max(0, max(1, self.round) - 1)
        n = clock.step * elapsed
        unit = clock.unit.lower().rstrip("s")
        if unit in ("hour", "minute"):
            moment = _dt.datetime.fromisoformat(clock.start)
            delta = _dt.timedelta(hours=n) if unit == "hour" else _dt.timedelta(minutes=n)
            return (moment + delta).isoformat(timespec="minutes")
        start = _dt.date.fromisoformat(clock.start[:10])
        if unit in ("day", "week"):
            return (start + _dt.timedelta(days=(7 if unit == "week" else 1) * n)).isoformat()
        whole = int(n)
        if unit == "month":
            months = start.month - 1 + whole
            year, month = start.year + months // 12, months % 12 + 1
            return _dt.date(year, month, min(start.day, 28)).isoformat()
        if unit == "year":
            return str(start.year + whole)
        return None

    def clock_label(self) -> str:
        unit = self.contract.clock.unit
        name = f"{unit[:1].upper()}{unit[1:]}"
        if self.continuous:
            label = f"{name} {_short(self.time)}" + (f" of {_short(self.horizon)}" if self.horizon is not None else "")
        else:
            label = f"{name} {max(1, self.round)} of {self.rounds}"
        date = self.date()
        return f"{label} ({date})" if date else label

    # -- mutation (journaled) --------------------------------------------------

    def _coerce(self, spec: Optional[PropSpec], value: Any, where: str) -> Any:
        if spec is None:
            return value
        kind = prop_type(spec)
        if value is None:
            return None
        if kind in ("number", "int"):
            if not _finite_number(value):
                raise RunError(f"must be a finite number that fits in a float, got {_shown_value(value)}", where)
            if spec.min is not None:
                value = max(spec.min, value)
            if spec.max is not None:
                value = min(spec.max, value)
            if kind == "int":
                if float(value) != int(value):
                    raise RunError(f"must be a whole number, got {value}", where)
                value = int(value)
        elif kind == "bool" and not isinstance(value, bool):
            raise RunError(f"must be true or false, got {value!r}", where)
        elif kind == "text" and not isinstance(value, str):
            raise RunError(f"must be text, got {value!r}", where)
        elif kind == "enum" and value not in (spec.values or []):
            raise RunError(f"must be one of {spec.values}, got {value!r}", where)
        elif kind == "list" and not isinstance(value, list):
            raise RunError(f"must be a list, got {value!r}", where)
        elif kind == "map" and not isinstance(value, dict):
            raise RunError(f"must be an object, got {value!r}", where)
        return value

    def set_prop(self, entity: Entity, prop: str, value: Any) -> None:
        specs = self._type_props.get(entity.entity_type, {})
        where = f"{entity.entity_type}.{prop}"
        if prop not in specs:
            known = ", ".join(specs) or "none"
            raise RunError(f"'{entity.entity_type}' has no property '{prop}' (declared: {known})", where)
        new = self._coerce(specs[prop], _plain(value), where)
        old = entity.properties.get(prop)
        entity.properties[prop] = new
        self.journal.push(lambda: entity.properties.__setitem__(prop, old))

    def set_world(self, prop: str, value: Any) -> None:
        spec = self.contract.world.get(prop)
        if spec is None:
            known = ", ".join(self.contract.world) or "none"
            raise RunError(f"world has no property '{prop}' (declared: {known})", f"world.{prop}")
        new = self._coerce(spec, _plain(value), f"world.{prop}")
        old = self.props.get(prop)
        self.props[prop] = new
        self.journal.push(lambda: self.props.__setitem__(prop, old))

    def set_physics(self, name: str, value: Any) -> None:
        model = self.physics
        if model is None:
            raise RunError("this environment declares no physics", f"physics.{name}")
        if not _finite_number(value):
            raise RunError(f"must be a finite number that fits in a float, got {_shown_value(value)}", f"physics.{name}")
        if name in model.variables:
            var = model.variables[name]
            old = var.value
            clamped = float(value)
            if var.min is not None:
                clamped = max(var.min, clamped)
            if var.max is not None:
                clamped = min(var.max, clamped)
            var.value = clamped
            self.journal.push(lambda: setattr(var, "value", old))
        elif name in model.params:
            old_param = model.params[name]
            model.params[name] = float(value)
            self.journal.push(lambda: model.params.__setitem__(name, old_param))
        else:
            raise RunError(f"physics has no variable or param '{name}'", f"physics.{name}")

    def next_id(self, type_name: str) -> str:
        n = self.counters.get(type_name, 0)
        while True:
            n += 1
            candidate = f"{type_name}_{n}"
            if candidate not in self.entities:
                break
        old = self.counters.get(type_name, 0)
        self.counters[type_name] = n
        self.journal.push(lambda: self.counters.__setitem__(type_name, old))
        return candidate

    def create(self, type_name: str, entity_id: Optional[str], name: Optional[str],
               props: Dict[str, Any], at: Any, scope: Scope, where: str, evaluate: bool = True) -> Entity:
        """Create an entity. ``props`` values that are expressions are evaluated when ``evaluate`` is
        true (contract text); runtime values from native ops pass ``evaluate=False``. Participant text
        is never evaluated, whatever it looks like."""
        spec = self.contract.types.get(type_name)
        if spec is None:
            raise RunError(f"'{type_name}' is not a declared type", where)
        eid = entity_id or self.next_id(type_name)
        if eid in self.entities:
            raise RunError(f"an entity with id '{eid}' already exists", where)
        declared = self._type_props[type_name]
        unknown = set(props) - set(declared)
        if unknown:
            raise RunError(f"'{type_name}' has no properties {sorted(unknown)} (declared: {', '.join(declared) or 'none'})", where)
        entity = Entity(id=eid, name=name or eid, entity_type=type_name, properties={}, location_id=None)
        own = scope.child(it=entity)  # props read earlier props of the same entity: `$it.income * 0.3`
        for prop, prop_spec in declared.items():
            raw = props[prop] if prop in props else prop_spec.default
            try:
                expression = evaluate and prop in props and is_expr(raw) and not isinstance(raw, Untrusted)
                if prop not in props and is_expr(raw):  # a type default is contract text
                    expression = True
                value = compile_expr(raw)(own) if expression else _copy(raw)
            except ExprError as exc:
                raise RunError(str(exc), f"{where}.props.{prop}") from None
            entity.properties[prop] = self._coerce(prop_spec, _plain(value), f"{where}.props.{prop}")
        if at is not None:
            entity.location_id = self._check_location(at, where)
        self.entities[eid] = entity
        self.journal.push(lambda: self.entities.pop(eid, None))
        if self.lifecycle is not None:
            self.lifecycle("on_create", entity, where)
        return entity

    def remove(self, entity: Entity, where: str = "remove") -> None:
        if not entity.alive:
            return
        entity.alive = False
        self.journal.push(lambda: setattr(entity, "alive", True))
        if self.lifecycle is not None:
            self.lifecycle("on_remove", entity, where)

    def move(self, entity: Entity, at: Any, where: str) -> None:
        location = self._check_location(at, where)
        old = entity.location_id
        entity.location_id = location
        self.journal.push(lambda: setattr(entity, "location_id", old))

    def link(self, kind: str, a: Any, b: Any, value: Any, where: str, fields: Optional[Dict[str, Any]] = None) -> None:
        """Create or update a link (``value`` None keeps the current value; see :func:`links.link`)."""
        _links.link(self, kind, a, b, value, where, fields)

    def unlink(self, kind: str, a: Any, b: Any, where: str) -> None:
        _links.unlink(self, kind, a, b, where)

    def set_link_field(self, view: Link, name: str, value: Any, where: str) -> None:
        _links.set_link_field(self, view, name, value, where)

    def rebuild_adjacency(self) -> None:
        _links.rebuild_adjacency(self)

    def post(self, record: str, fields: Dict[str, Any], author: Optional[str],
             to: Optional[Tuple[str, ...]], where: str) -> Entry:
        spec = self.contract.records.get(record)
        if spec is None:
            raise RunError(f"'{record}' is not a declared record (records: {', '.join(self.contract.records) or 'none'})", where)
        unknown = set(fields) - set(spec.fields)
        if unknown:
            raise RunError(f"record '{record}' has no fields {sorted(unknown)} (fields: {', '.join(spec.fields)})", where)
        entry = Entry()
        entry.world = self
        for name, kind in spec.fields.items():
            value = _plain(fields.get(name))
            if value is not None and kind == "text" and not isinstance(value, str):
                value = str(value)
            entry[name] = value
        self._record_seq += 1
        entry.update({"seq": self._record_seq, "round": self.round, "stage": self.stage,
                      "author": author, "to": list(to) if to is not None else None})
        rows = self.records_store[record]
        rows.append(entry)
        self.entry_by_seq[entry["seq"]] = entry
        dropped: List[Entry] = []
        if spec.keep is not None and len(rows) > spec.keep:
            dropped = rows[: len(rows) - spec.keep]
            del rows[: len(rows) - spec.keep]
            for old in dropped:
                self.entry_by_seq.pop(old["seq"], None)

        def undo() -> None:
            for index in range(len(rows) - 1, -1, -1):
                if rows[index] is entry:
                    del rows[index]
                    break
            self.entry_by_seq.pop(entry["seq"], None)
            rows[:0] = dropped
            for old in dropped:
                self.entry_by_seq[old["seq"]] = old
            self._record_seq -= 1

        self.journal.push(undo)
        if spec.notify:
            self.emit("record", "", actor=author, to=to, data={
                "record": record, "entry": entry["seq"], "fields": {name: entry[name] for name in spec.fields}})
        return entry

    def emit(self, kind: str, text: str, *, actor: Optional[str] = None,
             to: Optional[Iterable[str]] = None, data: Optional[Dict[str, Any]] = None) -> LogEvent:
        self._seq += 1
        event = LogEvent(self._seq, self.round, kind, text, actor,
                         tuple(to) if to is not None else None, dict(data or {}), self.stage,
                         self.time if self.continuous else None)
        self.log.append(event)

        def undo() -> None:
            for index in range(len(self.log) - 1, -1, -1):  # rolled-back events sit near the end
                if self.log[index] is event:
                    del self.log[index]
                    self._seq -= 1
                    break

        self.journal.push(undo)
        return event

    def schedule(self, due_round: float, effects: List[Any], vars: Dict[str, Any], path: str,
                 delivery: Optional[Dict[str, Any]] = None) -> None:
        """Run ``effects`` when the round (or, on a continuous clock, the time) reaches ``due_round``;
        or, with ``delivery``, deliver that message (see :mod:`delivery`)."""
        item: Dict[str, Any] = {"effects": effects, "vars": {k: _freeze(v) for k, v in vars.items()}, "path": path}
        if delivery is not None:
            item["delivery"] = delivery
        self._schedule_seq += 1
        entry = (due_round, self._schedule_seq, item)
        heapq.heappush(self.scheduled, entry)

        def undo() -> None:
            if entry in self.scheduled:
                self.scheduled.remove(entry)
                heapq.heapify(self.scheduled)

        self.journal.push(undo)

    def request_reaction(self, entity_id: str, why: str) -> None:
        entry = (entity_id, why)
        self.reactions.append(entry)

        def undo() -> None:
            for index in range(len(self.reactions) - 1, -1, -1):
                if self.reactions[index] is entry:
                    del self.reactions[index]
                    break

        self.journal.push(undo)

    def request_wake(self, entity_id: str, why: str) -> None:
        missing = entity_id not in self.wake_requests
        old = self.wake_requests.get(entity_id)
        self.wake_requests[entity_id] = why

        def undo() -> None:
            if missing:
                self.wake_requests.pop(entity_id, None)
            else:
                self.wake_requests[entity_id] = old  # type: ignore[assignment]

        self.journal.push(undo)

    def request_end(self, name: str, winner: Any, text: str) -> None:
        if self.end_request is None:
            self.end_request = {"name": name, "winner": winner, "text": text}
            self.journal.push(lambda: setattr(self, "end_request", None))

    def thaw(self, vars: Dict[str, Any]) -> Dict[str, Any]:
        return {k: _thaw(v, self) for k, v in vars.items()}

    # -- physics (see world_physics) --------------------------------------------------

    def build_physics(self) -> None:
        world_physics.build_physics(self)

    def step_physics(self, elapsed: Optional[float] = None) -> List[Dict[str, Any]]:
        """Advance physics one round, or by ``elapsed`` clock time on a continuous clock."""
        return world_physics.step_physics(self, elapsed)

    # -- helpers ---------------------------------------------------------------

    def _key(self, kind: str, a: str, b: str) -> Tuple[str, str]:
        return _links.edge_key(self, kind, a, b)

    def _check_location(self, at: Any, where: str) -> Any:
        space = self.contract.space
        if space is None:
            return at
        if space.grid is not None:
            if not (isinstance(at, list) and len(at) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in at)):
                raise RunError(f"a grid position is [row, col], got {at!r}", where)
            if not (0 <= at[0] < space.grid.rows and 0 <= at[1] < space.grid.cols):
                raise RunError(f"position {at} is off the {space.grid.rows}x{space.grid.cols} grid", where)
        elif space.graph is not None:
            if at not in space.graph.nodes:
                raise RunError(f"'{at}' is not a place (places: {', '.join(space.graph.nodes)})", where)
        elif space.plane is not None:
            if not (isinstance(at, list) and len(at) == 2):
                raise RunError(f"a position is [x, y], got {at!r}", where)
            if not (0 <= at[0] <= space.plane.width and 0 <= at[1] <= space.plane.height):
                raise RunError(f"position {at} is outside the {space.plane.width}x{space.plane.height} plane", where)
        return list(at) if isinstance(at, list) else at

# ---------------------------------------------------------------------------


#: Def results that are immutable, so a cached value can be handed out again safely.
_CACHEABLE = (int, float, bool, str, type(None), Entity)


def _short(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.10g}" if isinstance(value, float) else str(value)


def _location(value: Any) -> Any:
    return value.location_id if isinstance(value, Entity) else value


def _copy(value: Any) -> Any:
    if isinstance(value, list):
        return [_copy(v) for v in value]
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    return value


def _plain(value: Any) -> Any:
    """Store entities by id and links as data: properties never hold live object references."""
    if isinstance(value, Entity):
        return value.id
    if isinstance(value, Link):
        return value.as_dict()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict) and not isinstance(value, Entry):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, Entry):
        return {k: v for k, v in value.items()}
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Entity):
        return {"$entity": value.id}
    if isinstance(value, Link):
        return _freeze(value.as_dict())
    if isinstance(value, list):
        return [_freeze(v) for v in value]
    if isinstance(value, dict):
        return {k: _freeze(v) for k, v in value.items()}
    return value


def _thaw(value: Any, world: SdkWorld) -> Any:
    if isinstance(value, dict) and set(value) == {"$entity"}:
        return world.entities.get(value["$entity"])
    if isinstance(value, list):
        return [_thaw(v, world) for v in value]
    if isinstance(value, dict):
        return {k: _thaw(v, world) for k, v in value.items()}
    return value


def _distance(contract: Contract, a: Any, b: Any) -> float:
    space = contract.space
    if space is None:
        raise ExprError("this environment declares no space")
    if a is None or b is None:
        raise ExprError("both entities need a position (at) to measure distance")
    if space.grid is not None:
        dr, dc = abs(a[0] - b[0]), abs(a[1] - b[1])
        return float(max(dr, dc) if space.grid.diagonal else dr + dc)
    if space.plane is not None:
        return math.hypot(a[0] - b[0], a[1] - b[1])
    graph = space.graph
    assert graph is not None
    adjacency: Dict[str, List[Tuple[str, float]]] = {n: [] for n in graph.nodes}
    for edge in graph.edges:
        if isinstance(edge, dict):
            x, y, w = edge["from"], edge["to"], float(edge.get("weight", 1))
        else:
            x, y, w = edge[0], edge[1], 1.0
        adjacency.setdefault(x, []).append((y, w))
        adjacency.setdefault(y, []).append((x, w))
    best = {a: 0.0}
    queue = [(0.0, a)]
    while queue:
        d, node = heapq.heappop(queue)
        if node == b:
            return d
        if d > best.get(node, math.inf):
            continue
        for nxt, w in adjacency.get(node, []):
            nd = d + w
            if nd < best.get(nxt, math.inf):
                best[nxt] = nd
                heapq.heappush(queue, (nd, nxt))
    return math.inf
