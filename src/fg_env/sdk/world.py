"""The live world of one run: entities, global properties, links, records, the event log,
physics and space — every mutation journaled so an action commits atomically or not at all."""
from __future__ import annotations

import heapq
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple

from ..entity import Entity
from ..physics import PhysicsModel, _CompiledExpr
from .assets.store import AssetStore
from .contract import Contract, PropSpec
from .errors import RunError
from .expr import ExprError, FUNCTIONS, Scope, Untrusted, World, compile_expr, is_expr, truthy
from .props import finite_number as _finite_number, prop_type, shown_value as _shown_value
from .seeds import SeedTree
from .stdlib.dates import calendar_date
from .space import Spatial, position_of
from .type_index import TypeIndex
from . import links as _links, world_physics
from .patterns.runtime import PatternRuntime
from .links import Link
from .world_parts import ClockView, Entry, Journal, LogEvent, PhysicsView, PropsView

if TYPE_CHECKING:
    from .sync_events import WriteBuffer

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
        #: The calendar date of round 1: ``clock.start`` as written or read from ``$inputs`` at build (None without one).
        self.start: Optional[str] = None
        self.wake_at: Dict[str, float] = {}
        #: Tie-break for scheduled effects due in the same round: the order they were scheduled.
        self._schedule_seq = 0
        #: The declared space, resolved at build (sizes may read $inputs); None without one.
        self.space: Optional[Spatial] = None
        #: While a sync event runs, where property and layer writes wait to land together.
        self.buffer: Optional["WriteBuffer"] = None
        self.end_request: Optional[Dict[str, Any]] = None
        #: Chooses the outcome of a `chance` effect instead of the random stream (explicit chance; see chance.py).
        self.chance_picker: Optional[Callable[[Any], int]] = None
        self.counters: Dict[str, int] = {}
        self.journal = Journal()
        #: Called as ``lifecycle(hook, entity, where)`` after every creation and removal (set by the effect runner).
        self.lifecycle: Optional[Callable[[str, Entity, str], None]] = None
        #: What each agent was shown (an :class:`~fg_env.sdk.exposure.ExposureLog`), when the run records it.
        self.exposures: Any = None
        #: Names of properties written since the build (read by the run's diagnostics; see run_diagnosis.py).
        self.written: "set[str]" = set()
        #: While sealed choices commit or an `each` loop runs, notes `=` assignments (see run_diagnosis.py).
        self.watched_writes: Any = None
        #: The run's diagnosis counts (a run_diagnosis.Diagnosis), set by the run.
        self.diagnosis: Any = None
        #: The files the run knows (the contract's catalog, once loaded from its folder, and submitted files).
        self.assets = AssetStore()
        self._seq = 0
        self._record_seq = 0
        self._props_view = PropsView(self)
        self._physics_view = PhysicsView(self)
        self._clock_view = ClockView(self)
        self.patterns = PatternRuntime(self)
        self._type_props = {t: contract.props_of(t) for t in contract.types}
        #: Def results for the current world state (see :meth:`call_def`).
        self._def_cache: Dict[Any, Any] = {}
        self._def_cache_state: Any = None
        self._def_cache_on = False
        self._subtypes = {t: set(contract.subtypes(t)) for t in contract.types}
        #: The living entities of every type, kept current by create and remove.
        self.types = TypeIndex(contract)

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
        return list(self.alive_of(type_name))

    def alive_of(self, type_name: str) -> List[Entity]:
        """The living entities of a type (subtypes included) in creation order — a shared list: read it, never change it."""
        if type_name not in self.contract.types:
            raise ExprError(f"'{type_name}' is not a declared type (types: {', '.join(self.contract.types)})")
        return self.types.alive(type_name, compact=self.journal.mark() == 0)

    def rebuild_index(self) -> None:
        """Re-index every entity after the entity store was replaced wholesale (a restore)."""
        self.types.rebuild(self.entities.values())
        if self.space is not None:
            self.space.positions.rebuild(self.entities.values())

    def subtypes_of(self, type_name: str) -> Any:
        """``type_name`` and every type that extends it."""
        return self._subtypes[type_name]

    def build_space(self) -> None:
        """Resolve the declared space before anything is placed in it."""
        if self.contract.space is not None:
            self.space = Spatial(self, self.contract.space)

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
        local = self._here()  # the running turn's state, read once: nothing before the evaluation changes it
        key = self._def_key(name, args)
        if key is not None:
            pending = getattr(local, "pending", None)
            state = (self.journal.version, self.round, self.stage, self.time, id(pending), len(pending or ()))
            if state != self._def_cache_state:
                self._def_cache, self._def_cache_state = {}, state
            elif key in self._def_cache:
                return self._def_cache[key]
        depth = getattr(local, "depth", 0)
        if depth >= 32:
            raise ExprError(f"${name}: defs call each other too deeply (recursion?)", source)
        local.depth = depth + 1
        drawn = getattr(local, "draws", 0)
        try:
            value = compile_expr(spec.expr)(self._scope(local, dict(zip(spec.args, args))))
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
        if self.space is None:
            raise ExprError("this environment declares no space")
        start, end = position_of(a), position_of(b)
        if start is None or end is None:
            raise ExprError("both entities need a position (at) to measure distance")
        return self.space.geometry.distance(start, end)

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
        return self._scope(self._here(), values)

    def _scope(self, local: Any, values: Dict[str, Any]) -> Scope:
        """A scope over the world as ``local`` (the running turn's state) sees it, with ``values`` as extra roots."""
        base: Dict[str, Any] = {
            "inputs": self.inputs,
            "world": self._props_view,
            "physics": self._physics_view,
            "clock": self._clock_view,
            "pattern": self.patterns.view,
            "round": self.round,
            "stage": self.stage,
            "metrics": self.metrics,
            "series": self.series,
            "arm": self.arm,
            "pending": getattr(local, "pending", None) or [],
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
        elapsed = self.time if self.continuous else max(0, max(1, self.round) - 1)
        return calendar_date(self.start, clock.unit, clock.step, elapsed)

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
        elif kind == "asset":
            return self.assets.ref(value, where)
        return value

    def set_prop(self, entity: Entity, prop: str, value: Any) -> None:
        specs = self._type_props.get(entity.entity_type, {})
        where = f"{entity.entity_type}.{prop}"
        if prop not in specs:
            known = ", ".join(specs) or "none"
            raise RunError(f"'{entity.entity_type}' has no property '{prop}' (declared: {known})", where)
        self.written.add(prop)
        new = self._coerce(specs[prop], _plain(value), where)
        if self.buffer is not None:
            self.buffer.write(("prop", entity.id, prop), new, lambda: self.set_prop(entity, prop, new), where)
            return
        old = entity.properties.get(prop)
        entity.properties[prop] = new
        self.journal.push(lambda: entity.properties.__setitem__(prop, old))

    def set_world(self, prop: str, value: Any, *, trusted: bool = False) -> None:
        """Set a world property. ``trusted``: the caller built ``value`` from plain data and never changes it in
        place afterwards (a mechanism's fresh list of plain maps), so the defensive copy is skipped."""
        spec = self.contract.world.get(prop)
        if spec is None:
            known = ", ".join(self.contract.world) or "none"
            raise RunError(f"world has no property '{prop}' (declared: {known})", f"world.{prop}")
        self.written.add(prop)
        new = self._coerce(spec, value if trusted else _plain(value), f"world.{prop}")
        if self.buffer is not None:
            self.buffer.write(("world", prop), new, lambda: self.set_world(prop, new), f"world.{prop}")
            return
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
        space = self.space
        if at is not None:  # placed first, so props can read the position: `$layer(sugar, $it.at)`
            entity.location_id = self._check_location(at, where)
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
        if entity.location_id is not None:
            self._make_room(entity, entity.location_id, "cannot be placed")
        self.entities[eid] = entity
        self.types.created(entity)
        if space is not None:
            space.positions.add(entity)

        def undo_create() -> None:
            self.entities.pop(eid, None)
            self.types.uncreated(entity)
            if space is not None:
                space.positions.discard(entity)

        self.journal.push(undo_create)
        if self.lifecycle is not None:
            self.lifecycle("on_create", entity, where)
        return entity

    def remove(self, entity: Entity, where: str = "remove") -> None:
        if not entity.alive:
            return
        entity.alive = False
        self.types.changed(entity)
        space = self.space
        if space is not None:
            space.positions.discard(entity)

        def undo_remove() -> None:
            entity.alive = True
            self.types.changed(entity)
            if space is not None:
                space.positions.add(entity)

        self.journal.push(undo_remove)
        if self.lifecycle is not None:
            self.lifecycle("on_remove", entity, where)

    def move(self, entity: Entity, at: Any, where: str) -> None:
        location = self._check_location(at, where)
        space = self.space
        old = entity.location_id
        if space is None:
            entity.location_id = location
            self.journal.push(lambda: setattr(entity, "location_id", old))
            return
        self._make_room(entity, location, "cannot move there")

        def place(position: Any) -> None:
            space.positions.discard(entity)
            entity.location_id = position
            space.positions.add(entity)

        place(location)
        self.journal.push(lambda: place(old))

    def _make_room(self, entity: Entity, position: Any, what: str) -> None:
        """Refuse (roll back) putting ``entity`` at ``position`` when the cell is full."""
        full = self.space.no_room(entity, position) if self.space is not None else None
        if full is not None:
            raise Abort(f"{entity.name} {what}: {full}.")

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
            elif value is not None and kind == "asset":
                value = self.assets.ref(value, f"{where}.{name}")
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
        return at if self.space is None else self.space.place(at, where)

# ---------------------------------------------------------------------------


#: Def results that are immutable, so a cached value can be handed out again safely.
_CACHEABLE = (int, float, bool, str, type(None), Entity)


def _short(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{value:.10g}" if isinstance(value, float) else str(value)


def _copy(value: Any) -> Any:
    if isinstance(value, list):
        return [_copy(v) for v in value]
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    return value


def _plain(value: Any) -> Any:
    """Store entities by id and links as data: properties never hold live object references."""
    kind = type(value)
    if kind is str or kind is int or kind is float or kind is bool or value is None:
        return value
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

