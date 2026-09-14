"""The live world of one run: entities, global properties, links, records, the event log,
physics and space — every mutation journaled so an action commits atomically or not at all."""
from __future__ import annotations

import datetime as _dt
import heapq
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ..entity import Entity
from ..physics import PhysicsExprError, PhysicsModel, PhysicsVariable, _CompiledExpr
from .contract import Contract, PropSpec
from .errors import RunError
from .expr import ExprError, Scope, World, compile_expr, is_expr
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


@dataclass
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

    def mark(self) -> int:
        return len(self._undo)

    def push(self, undo: Callable[[], object]) -> None:
        self._undo.append(undo)

    def rollback(self, mark: int) -> None:
        while len(self._undo) > mark:
            self._undo.pop()()

    def clear(self) -> None:
        self._undo.clear()


class SdkWorld(World):
    """World store for one run. Expressions read it through the :class:`World` interface."""

    def __init__(self, contract: Contract, inputs: Dict[str, Any], seeds: SeedTree, arm: Optional[str] = None):
        self.contract = contract
        self.inputs = inputs
        self.seeds = seeds
        self.arm = arm
        self.rng = seeds.rng("world")
        self.entities: Dict[str, Entity] = {}
        self.props: Dict[str, Any] = {}
        self.links: Dict[str, Dict[Tuple[str, str], float]] = {name: {} for name in contract.relations}
        self.records_store: Dict[str, List[Entry]] = {name: [] for name in contract.records}
        self.log: List[LogEvent] = []
        self.physics: Optional[PhysicsModel] = None
        self.round = 0
        self.stage: Optional[str] = None
        self.rounds = 0
        self.metrics: Dict[str, Any] = {}
        self.series: Dict[str, List[Any]] = {}
        self.scheduled: List[Tuple[int, int, Dict[str, Any]]] = []
        self.wake_requests: Dict[str, str] = {}
        self.end_request: Optional[Dict[str, Any]] = None
        self.counters: Dict[str, int] = {}
        self.journal = _Journal()
        self._seq = 0
        self._record_seq = 0
        self._props_view = _Props(self)
        self._physics_view = _Physics(self)
        self._clock_view = _Clock(self)
        self._type_props = {t: spec.props for t, spec in contract.types.items()}

    # -- expression interface ------------------------------------------------

    def entities_of(self, type_name: str) -> List[Entity]:
        if type_name not in self.contract.types:
            raise ExprError(f"'{type_name}' is not a declared type (types: {', '.join(self.contract.types)})")
        return [e for e in self.entities.values() if e.entity_type == type_name and e.alive]

    def entity(self, entity_id: Any) -> Optional[Entity]:
        if isinstance(entity_id, Entity):
            return entity_id
        return self.entities.get(entity_id) if isinstance(entity_id, str) else None

    def records(self, name: str) -> List[Entry]:
        if name not in self.records_store:
            known = ", ".join(self.records_store) or "none declared"
            raise ExprError(f"'{name}' is not a declared record (records: {known})")
        return self.records_store[name]

    def events(self, kind: Optional[str]) -> List[LogEvent]:
        return [e for e in self.log if kind is None or e.kind == kind]

    def relation(self, a: Any, b: Any, kind: str) -> Optional[float]:
        edges = self._edges(kind)
        return edges.get(self._key(kind, _id(a), _id(b)))

    def neighbors(self, entity: Any, kind: str) -> List[Entity]:
        eid = _id(entity)
        out: List[Entity] = []
        for (a, b) in self._edges(kind):
            other = b if a == eid else a if b == eid else None
            if other is not None and other != eid:
                found = self.entities.get(other)
                if found is not None and found.alive and found not in out:
                    out.append(found)
        return out

    def distance(self, a: Any, b: Any) -> float:
        return _distance(self.contract, _location(a), _location(b))

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
        }
        base.update(values)
        return Scope(base, self)

    # -- clock ---------------------------------------------------------------

    def date(self) -> Optional[str]:
        clock = self.contract.clock
        if not clock.start:
            return None
        start = _dt.date.fromisoformat(clock.start[:10])
        unit = clock.unit.lower().rstrip("s")
        per = {"day": 1, "week": 7}.get(unit)
        if per is None:
            return None
        return (start + _dt.timedelta(days=per * clock.step * max(0, self.round - 1))).isoformat()

    def clock_label(self) -> str:
        unit = self.contract.clock.unit
        label = f"{unit[:1].upper()}{unit[1:]} {self.round} of {self.rounds}"
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
        unknown = set(props) - set(spec.props)
        if unknown:
            raise RunError(f"'{type_name}' has no properties {sorted(unknown)} (declared: {', '.join(spec.props) or 'none'})", where)
        entity = Entity(id=eid, name=name or eid, entity_type=type_name, properties={}, location_id=None)
        for prop, prop_spec in spec.props.items():
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
        self.journal.push(lambda: edges.pop(key) if missing else edges.__setitem__(key, old))

    def unlink(self, kind: str, a: Any, b: Any, where: str) -> None:
        edges = self._edges(kind, where)
        key = self._key(kind, _id(a), _id(b))
        if key in edges:
            old = edges.pop(key)
            self.journal.push(lambda: edges.__setitem__(key, old))

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
            if value is not None and kind == "text":
                value = str(value)
            entry[name] = value
        self._record_seq += 1
        entry.update({"seq": self._record_seq, "round": self.round, "stage": self.stage,
                      "author": author, "to": list(to) if to is not None else None})
        rows = self.records_store[record]
        rows.append(entry)
        dropped: List[Entry] = []
        if spec.keep is not None and len(rows) > spec.keep:
            dropped = rows[: len(rows) - spec.keep]
            del rows[: len(rows) - spec.keep]

        def undo() -> None:
            if entry in rows:
                rows.remove(entry)
            rows[:0] = dropped
            self._record_seq -= 1

        self.journal.push(undo)
        if spec.notify:
            self.emit("record", "", actor=author, to=to, data={"record": record, "entry": entry["seq"]})
        return entry

    def emit(self, kind: str, text: str, *, actor: Optional[str] = None,
             to: Optional[Iterable[str]] = None, data: Optional[Dict[str, Any]] = None) -> LogEvent:
        self._seq += 1
        event = LogEvent(self._seq, self.round, kind, text, actor,
                         tuple(to) if to is not None else None, dict(data or {}), self.stage)
        self.log.append(event)

        def undo() -> None:
            if self.log and self.log[-1] is event:
                self.log.pop()
                self._seq -= 1

        self.journal.push(undo)
        return event

    def schedule(self, due_round: int, effects: List[Any], vars: Dict[str, Any], path: str) -> None:
        item = {"effects": effects, "vars": {k: _freeze(v) for k, v in vars.items()}, "path": path}
        entry = (due_round, self._seq, item)
        heapq.heappush(self.scheduled, entry)
        self.journal.push(lambda: self.scheduled.remove(entry) if entry in self.scheduled else None)

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
