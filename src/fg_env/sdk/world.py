"""The live world of one run: entities, global properties, links, records, the event log,
physics and space — every mutation journaled so an action commits atomically or not at all."""
from __future__ import annotations

import datetime as _dt
import heapq
import math
import threading
from difflib import get_close_matches
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..entity import Entity
from ..physics import PhysicsExprError, PhysicsModel, PhysicsVariable, _CompiledExpr
from .contract import Contract, PropSpec
from .errors import RunError
from .expr import FUNCTIONS, ExprError, Scope, World, compile_expr, is_expr, truthy
from .seeds import SeedTree

__all__ = ["SdkWorld", "Entry", "LogEvent", "Abort", "prop_type"]


class Abort(Exception):
    """Stop the current action; every change it made is rolled back. The text reaches the actor."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def prop_type(spec: PropSpec) -> str:
    if spec.type:
        return spec.type
    value = spec.default
    if isinstance(value, bool):
        return "bool"
    # A numeric default means "a number": `"cash": 0` must accept 12.5 later.
    # Whole-number enforcement is opt-in with `"type": "int"`.
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str) and not is_expr(value):
        return "enum" if spec.values else "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "map"
    return "any"


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

    def visible_to(self, entity_id: str) -> bool:
        return self.to is None or entity_id in self.to

    def expr_attr(self, name: str, source: Optional[str]) -> Any:
        if name in ("seq", "round", "kind", "text", "actor", "stage"):
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
        raise ExprError(f"clock has no field '{name}' (round, rounds, left, unit, date, label)", source)


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
        #: relation → entity id → {linked entity id: number of edges between them}
        self.adjacent: Dict[str, Dict[str, Dict[str, int]]] = {name: {} for name in contract.relations}
        self.records_store: Dict[str, List[Entry]] = {name: [] for name in contract.records}
        #: Retained record entries by sequence number (entries dropped by `keep` are removed).
        self.entry_by_seq: Dict[int, Entry] = {}
        #: Per-entity brief text rendered at build (from entities.*.brief / population.brief).
        self.entity_briefs: Dict[str, str] = {}
        self.log: List[LogEvent] = []
        self.physics: Optional[PhysicsModel] = None
        self.round = 0
        self.stage: Optional[str] = None
        self.rounds = 0
        self.metrics: Dict[str, Any] = {}
        self.series: Dict[str, List[Any]] = {}
        self.scheduled: List[Tuple[int, int, Dict[str, Any]]] = []
        self.wake_requests: Dict[str, str] = {}
        #: Tie-break for scheduled effects due in the same round: the order they were scheduled.
        self._schedule_seq = 0
        #: Shortest-path distances on the (static) place graph, filled as they are asked for.
        self._graph_distances: Dict[Tuple[Any, Any], float] = {}
        self.end_request: Optional[Dict[str, Any]] = None
        self.counters: Dict[str, int] = {}
        self.journal = _Journal()
        self._seq = 0
        self._record_seq = 0
        self._props_view = _Props(self)
        self._physics_view = _Physics(self)
        self._clock_view = _Clock(self)
        self._type_props = {t: contract.props_of(t) for t in contract.types}
        #: Results of pure defs for the current world state (see :meth:`call_def`).
        self._def_cache: Dict[Any, Any] = {}
        self._def_cache_state: Any = None
        #: Empty while the world is being built (build writes state outside the journal).
        self._pure_defs: frozenset = frozenset()
        self._subtypes = {t: set(contract.subtypes(t)) for t in contract.types}

    # -- randomness --------------------------------------------------------------

    @property  # type: ignore[override]
    def rng(self) -> Any:
        """The random stream for the current context: a turn's own stream while an agent's turn
        runs (so concurrent turns never race for draws), otherwise the run's main stream."""
        return getattr(self._local, "rng", None) or self._rng

    @rng.setter
    def rng(self, value: Any) -> None:
        self._rng = value

    def use_turn_rng(self, rng: Any) -> None:
        self._local.rng = rng

    def use_turn_pending(self, pending: Optional[List[Dict[str, Any]]]) -> None:
        self._local.pending = pending

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
        edges = self._edges(kind)
        return edges.get(self._key(kind, _id(a), _id(b)))

    def neighbors(self, entity: Any, kind: str) -> List[Entity]:
        self._edges(kind)
        linked = self.adjacent[kind].get(_id(entity), {})
        out: List[Entity] = []
        for other in linked:
            found = self.entities.get(other)
            if found is not None and found.alive:
                out.append(found)
        return out

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
            state = self._state_version()
            if state != self._def_cache_state:
                self._def_cache, self._def_cache_state = {}, state
            elif key in self._def_cache:
                return self._def_cache[key]
        depth = getattr(self._local, "depth", 0)
        if depth >= 32:
            raise ExprError(f"${name}: defs call each other too deeply (recursion?)", source)
        self._local.depth = depth + 1
        try:
            value = compile_expr(spec.expr)(self.scope(**dict(zip(spec.args, args))))
        finally:
            self._local.depth = depth
        if key is not None and self._state_version() == state and isinstance(value, _CACHEABLE):
            self._def_cache[key] = value
        return value

    def enable_def_cache(self) -> None:
        """Start caching pure def results; called once the world is built."""
        self._pure_defs = _pure_defs(self.contract)
        self.touch()

    def touch(self) -> None:
        """Record a change made outside the journal (metrics sampling, physics), so cached reads refresh."""
        self.journal.version += 1

    def _state_version(self) -> Any:
        pending = getattr(self._local, "pending", None)
        return (self.journal.version, self.round, self.stage, id(pending), len(pending or ()))

    def _def_key(self, name: str, args: List[Any]) -> Optional[Tuple[Any, ...]]:
        """A cache key for a pure def call, or None when the call cannot be cached."""
        if name not in self._pure_defs:
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
            "pending": getattr(self._local, "pending", None) or [],
        }
        base.update(values)
        return Scope(base, self)

    # -- clock ---------------------------------------------------------------

    def date(self) -> Optional[str]:
        clock = self.contract.clock
        if not clock.start:
            return None
        n = clock.step * max(0, max(1, self.round) - 1)
        unit = clock.unit.lower().rstrip("s")
        if unit in ("hour", "minute"):
            moment = _dt.datetime.fromisoformat(clock.start)
            delta = _dt.timedelta(hours=n) if unit == "hour" else _dt.timedelta(minutes=n)
            return (moment + delta).isoformat(timespec="minutes")
        start = _dt.date.fromisoformat(clock.start[:10])
        if unit in ("day", "week"):
            return (start + _dt.timedelta(days=(7 if unit == "week" else 1) * n)).isoformat()
        if unit == "month":
            months = start.month - 1 + n
            year, month = start.year + months // 12, months % 12 + 1
            return _dt.date(year, month, min(start.day, 28)).isoformat()
        if unit == "year":
            return str(start.year + n)
        return None

    def clock_label(self) -> str:
        unit = self.contract.clock.unit
        label = f"{unit[:1].upper()}{unit[1:]} {max(1, self.round)} of {self.rounds}"
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
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise RunError(f"must be a number, got {value!r}", where)
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
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise RunError(f"must be a finite number, got {value!r}", f"physics.{name}")
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
               props: Dict[str, Any], at: Any, scope: Scope, where: str) -> Entity:
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
        for prop, prop_spec in declared.items():
            raw = props[prop] if prop in props else prop_spec.default
            try:
                value = compile_expr(raw)(scope) if is_expr(raw) else _copy(raw)
            except ExprError as exc:
                raise RunError(str(exc), f"{where}.props.{prop}") from None
            entity.properties[prop] = self._coerce(prop_spec, _plain(value), f"{where}.props.{prop}")
        if at is not None:
            entity.location_id = self._check_location(at, where)
        self.entities[eid] = entity
        self.journal.push(lambda: self.entities.pop(eid, None))
        return entity

    def remove(self, entity: Entity) -> None:
        if not entity.alive:
            return
        entity.alive = False
        self.journal.push(lambda: setattr(entity, "alive", True))

    def move(self, entity: Entity, at: Any, where: str) -> None:
        location = self._check_location(at, where)
        old = entity.location_id
        entity.location_id = location
        self.journal.push(lambda: setattr(entity, "location_id", old))

    def link(self, kind: str, a: Any, b: Any, value: Any, where: str) -> None:
        spec = self.contract.relations.get(kind)
        if spec is None:
            raise RunError(f"'{kind}' is not a declared relation (relations: {', '.join(self.contract.relations) or 'none'})", where)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RunError(f"link value must be a number, got {value!r}", where)
        value = float(value)
        if spec.min is not None:
            value = max(spec.min, value)
        if spec.max is not None:
            value = min(spec.max, value)
        edges = self.links[kind]
        key = self._key(kind, _id(a), _id(b))
        missing = key not in edges
        old = edges.get(key)
        edges[key] = value
        if missing:
            self._adjust(kind, key, 1)

        def undo() -> None:
            if missing:
                edges.pop(key, None)
                self._adjust(kind, key, -1)
            else:
                edges[key] = old

        self.journal.push(undo)

    def unlink(self, kind: str, a: Any, b: Any, where: str) -> None:
        edges = self._edges(kind, where)
        key = self._key(kind, _id(a), _id(b))
        if key in edges:
            old = edges.pop(key)
            self._adjust(kind, key, -1)

            def undo() -> None:
                edges[key] = old
                self._adjust(kind, key, 1)

            self.journal.push(undo)

    def _adjust(self, kind: str, key: Tuple[str, str], delta: int) -> None:
        a, b = key
        if a == b:
            return
        index = self.adjacent[kind]
        for x, y in ((a, b), (b, a)):
            row = index.setdefault(x, {})
            count = row.get(y, 0) + delta
            if count > 0:
                row[y] = count
            else:
                row.pop(y, None)

    def rebuild_adjacency(self) -> None:
        self.adjacent = {kind: {} for kind in self.links}
        for kind, edges in self.links.items():
            for key in edges:
                self._adjust(kind, key, 1)

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
                         tuple(to) if to is not None else None, dict(data or {}), self.stage)
        self.log.append(event)

        def undo() -> None:
            for index in range(len(self.log) - 1, -1, -1):  # rolled-back events sit near the end
                if self.log[index] is event:
                    del self.log[index]
                    self._seq -= 1
                    break

        self.journal.push(undo)
        return event

    def schedule(self, due_round: int, effects: List[Any], vars: Dict[str, Any], path: str) -> None:
        item = {"effects": effects, "vars": {k: _freeze(v) for k, v in vars.items()}, "path": path}
        self._schedule_seq += 1
        entry = (due_round, self._schedule_seq, item)
        heapq.heappush(self.scheduled, entry)

        def undo() -> None:
            if entry in self.scheduled:
                self.scheduled.remove(entry)
                heapq.heapify(self.scheduled)

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

    # -- helpers ---------------------------------------------------------------

    def _edges(self, kind: str, where: Optional[str] = None) -> Dict[Tuple[str, str], float]:
        if kind not in self.links:
            raise ExprError(f"'{kind}' is not a declared relation (relations: {', '.join(self.links) or 'none'})", where)
        return self.links[kind]

    def _key(self, kind: str, a: str, b: str) -> Tuple[str, str]:
        spec = self.contract.relations.get(kind)
        if spec is not None and spec.symmetric and b < a:
            return (b, a)
        return (a, b)

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

    # -- physics ------------------------------------------------------------------

    def build_physics(self) -> None:
        spec = self.contract.physics
        if spec is None:
            return
        scope = self.scope()
        params: Dict[str, float] = {}
        for name, raw in spec.params.items():
            params[name] = float(_number(compile_expr(raw)(scope) if is_expr(raw) else raw, f"physics.params.{name}"))
        for name in spec.read:
            if name in spec.vars or name in spec.params:
                raise RunError(f"'{name}' is both a read name and a variable or param; give the read its own name", f"physics.read.{name}")
            params.setdefault(name, 0.0)
        variables = []
        for name, var in spec.vars.items():
            start = compile_expr(var.start)(scope) if is_expr(var.start) else var.start
            variables.append(PhysicsVariable(name=name, value=float(_number(start, f"physics.vars.{name}.start")),
                                             rate=var.rate, min=var.min, max=var.max))
        try:
            self.physics = PhysicsModel(variables=variables, params=params, substeps=spec.substeps)
            self._writes = [(target, _CompiledExpr(src)) for target, src in spec.write.items()]
        except PhysicsExprError as exc:
            raise RunError(str(exc), "physics") from None
        self._refresh_physics_reads()

    def _refresh_physics_reads(self) -> None:
        spec = self.contract.physics
        if spec is None or self.physics is None:
            return
        scope = self.scope()
        for name, src in spec.read.items():
            try:
                value = compile_expr(src)(scope)
            except ExprError as exc:
                raise RunError(str(exc), f"physics.read.{name}") from None
            self.physics.params[name] = float(_number(value, f"physics.read.{name}"))

    def step_physics(self) -> List[Dict[str, Any]]:
        spec = self.contract.physics
        if spec is None or self.physics is None:
            return []
        self._refresh_physics_reads()
        changes = self.physics.integrate(spec.dt)
        self.touch()
        errors = [c for c in changes if c.get("type") == "physics_error"]
        if errors:
            raise RunError(errors[0]["narrative"], "physics")
        values = {**self.physics.params, **self.physics.values}
        namespace = self.physics._namespace(values, self.physics.time)
        for target, expr in self._writes:
            value = expr.eval(namespace)
            owner, _, prop = target.partition(".")
            if owner == "world":
                self.set_world(prop, value)
            else:
                for entity in self.entities_of(owner):
                    self.set_prop(entity, prop, value)
        return changes


# ---------------------------------------------------------------------------


#: Functions that draw random numbers: a def using one (directly or through another def) is never cached.
_RANDOM_FUNCTIONS = frozenset({"random", "chance", "uniform", "randint", "normal", "lognormal", "beta",
                               "exponential", "poisson", "choice", "sample", "shuffle"})

#: Def results that are immutable, so a cached value can be handed out again safely.
_CACHEABLE = (int, float, bool, str, type(None), Entity)


def _pure_defs(contract: Contract) -> frozenset:
    """Defs whose value depends only on their arguments and the world state (no random draws)."""
    uses: Dict[str, frozenset] = {}
    for name, spec in contract.defs.items():
        try:
            uses[name] = compile_expr(spec.expr).functions
        except ExprError:
            uses[name] = frozenset(_RANDOM_FUNCTIONS)  # reported by the checker; never cached
    impure = {name for name, fns in uses.items() if fns & _RANDOM_FUNCTIONS}
    changed = True
    while changed:
        changed = False
        for name, fns in uses.items():
            if name not in impure and fns & impure:
                impure.add(name)
                changed = True
    return frozenset(uses) - impure


def _id(value: Any) -> str:
    if isinstance(value, Entity):
        return value.id
    if isinstance(value, str):
        return value
    raise ExprError(f"expected an entity or id, got {value!r}")


def _location(value: Any) -> Any:
    return value.location_id if isinstance(value, Entity) else value


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RunError(f"must be a finite number, got {value!r}", where)
    return value


def _copy(value: Any) -> Any:
    if isinstance(value, list):
        return [_copy(v) for v in value]
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    return value


def _plain(value: Any) -> Any:
    """Store entities by id: properties never hold live object references."""
    if isinstance(value, Entity):
        return value.id
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
