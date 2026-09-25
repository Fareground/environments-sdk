"""The world of one run: entities, global properties, links, records, the event log, physics and space — and every
change to them journaled, so a block of changes commits whole or is undone exactly (:meth:`World.mark`,
:meth:`World.rollback`, :meth:`World.commit`).

The world stores and changes; it does not evaluate. Values arrive worked out, and what an expression may read of the
world goes through its :class:`~fg_env.world.evaluation.EvalContext` (``world.evaluation``): scopes, defs, what a viewer
may see of the records and the log, and entities created from contract text.
"""
from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from ..assets.store import AssetStore
from ..contract import MAX_ENTITIES, Contract, PropSpec
from ..effects.captures import CAPTURE_VERSION, freeze
from ..errors import FatalRunError, RunError
from ..expr import ExprError
from ..expr import World as ExpressionWorld
from ..expr.hidden import Hidden
from ..expr.objects import Entity
from ..expr.template import format_value
from ..patterns.runtime import PatternRuntime
from ..physics.model import CompiledExpr, PhysicsModel
from ..sampling.seeds import SeedTree
from ..stdlib.dates import calendar_date
from . import links as _links
from .abort import Abort
from .evaluation import EvalContext
from .journal import Journal
from .links import Link
from .parts import Entry, LogEvent, private_metrics
from .props import finite_number as _finite_number
from .props import prop_type, stored
from .props import shown_value as _shown_value
from .randomness import Randomness
from .record_events import RecordEvents
from .record_index import RecordAuthors
from .record_keep import KeptWindows
from .space import Spatial, position_of
from .type_index import TypeIndex
from .values import plain_value

if TYPE_CHECKING:
    from ..effects.sync import WriteBuffer

__all__ = ["World"]

class World(ExpressionWorld):
    """The store of one run. Expressions read it through the :class:`~fg_env.expr.World` interface."""

    def __init__(self, contract: Contract, inputs: dict[str, Any], seeds: SeedTree, arm: str | None = None):
        self.contract = contract
        self.inputs = inputs
        self.arm = arm
        #: The run's luck: its streams, what each draw site drew this round, and what the running logic drew or read.
        self.luck = Randomness(seeds, seeds.rng("world"))
        self.entities: dict[str, Entity] = {}
        self.props: dict[str, Any] = {}
        self.links: dict[str, dict[tuple[str, str], float]] = {name: {} for name in contract.relations}
        #: relation → (a, b) → the link's fields (relations that declare props)
        self.link_fields: dict[str, dict[tuple[str, str], dict[str, Any]]] = {name: {} for name in contract.relations}
        #: relation → entity id → {linked entity id: number of edges between them}
        self.adjacent: dict[str, dict[str, dict[str, int]]] = {name: {} for name in contract.relations}
        self.records_store: dict[str, list[Entry]] = {name: [] for name in contract.records}
        #: Retained record entries by sequence number (entries dropped by `keep` are removed).
        self.entry_by_seq: dict[int, Entry] = {}
        #: Each record's fields as the property specs of their types (empty until given): an entry's field holds what
        #: a property of its type holds, by the same rules (:meth:`coerce`).
        self.record_specs = {name: {field: PropSpec.model_validate({"type": kind, "default": None})
                                    for field, kind in spec.fields.items()} for name, spec in contract.records.items()}
        self.record_authors = RecordAuthors(
            {name: spec.visible for name, spec in contract.records.items()}, self.records_store)
        #: Each reader's window over a record with `keep` whose entries not every agent sees, made as it is first
        #: needed (see record_keep.py).
        self.kept: dict[str, KeptWindows] = {}
        #: Per-entity brief text rendered at build (from entities.*.brief / population.brief).
        self.entity_briefs: dict[str, str] = {}
        self.log: list[LogEvent] = []
        self.record_events = RecordEvents((), self.entry_by_seq, contract)
        self.physics: PhysicsModel | None = None
        self.physics_writes: list[tuple[str, CompiledExpr]] = []
        self.entity_dynamics: list[Any] = []
        self.round = 0
        self.stage: str | None = None
        self.rounds = 0
        self.metrics: dict[str, Any] = {}
        self.series: dict[str, list[Any]] = {}
        self.scheduled: list[tuple[float, int, dict[str, Any]]] = []
        self.wake_requests: dict[str, str] = {}
        #: Agents asked to react right away (`wake` with `now`), answered as soon as the change commits: id, why, and
        #: the actions offered (None: the stage's).
        self.reactions: list[tuple[str, str, list[str] | None]] = []
        #: The calendar date of round 1: ``clock.start`` as written or read from ``$inputs`` at build (None without
        #: one).
        self.start: str | None = None
        #: The sequence numbers the log's last event, the records' last entry and the last scheduled effect took (a
        #: scheduled effect's breaks the tie between effects due in the same round: the order they were scheduled).
        self.event_seq = 0
        self.record_seq = 0
        self.schedule_seq = 0
        #: The declared space, resolved at build (sizes may read $inputs); None without one.
        self.space: Spatial | None = None
        #: While a sync loop runs, where property and layer writes wait to land together.
        self.buffer: WriteBuffer | None = None
        self.end_request: dict[str, Any] | None = None
        #: Chooses the outcome of a `chance` effect instead of the random stream (explicit chance; see
        #: effects/chance.py).
        self.chance_picker: Callable[[Any], int] | None = None
        self.counters: dict[str, int] = {}
        #: The run's bookkeeping of what the rules did, journaled like the store so an undo brings it back: the events
        #: with `once` that fired (by index), the last truth value of each `change` event's `when`, and each agent's
        #: uses of each action this round (see :meth:`count_use`).
        self.fired_once: set[int] = set()
        self.armed: dict[int, bool] = {}
        self.used_round: dict[str, dict[str, int]] = {}
        self.journal = Journal(self)
        #: Called as ``lifecycle(kind, entity, where)`` (create / remove) after every creation and removal (set by the
        #: effect runner).
        self.lifecycle: Callable[[str, Entity, str], None] | None = None
        #: Called with every entity created once the run has begun (set by the run: an agent that joins then hears the
        #: news from its arrival on, not the backlog of everything before it).
        self.joined: Callable[[Entity], None] | None = None
        #: What each agent was shown (an :class:`~fg_env.information.exposure.ExposureLog`), when the run records it.
        self.exposures: Any = None
        #: Names of properties written since the build (read by the run's diagnostics; see runtime/diagnosis.py).
        self.written: set[str] = set()
        #: Ids of the entities created or given property values since the invariants last held, in order (None: not
        #: known, so every invariant is checked whole; see runtime/rules.py).
        self.touched: dict[str, None] | None = None
        #: While sealed choices commit or an `each` loop runs, notes `=` assignments (see runtime/diagnosis.py).
        self.watched_writes: Any = None
        #: The run's sink of facts (a :class:`~fg_env.runtime.facts.Facts`), set by the run.
        self.facts: Any = None
        #: What mechanisms work out from this world and keep while it holds, by name: caches keyed on the version (or
        #: the contract) they were worked out at. A copy of the world takes them as they are.
        self.caches: dict[str, Any] = {}
        #: The files the run knows (the contract's catalog, once loaded from its folder, and submitted files).
        self.assets = AssetStore()
        self.patterns = PatternRuntime(self)
        #: Each type's declared properties (its own and inherited), by name.
        self.type_props = {t: contract.props_of(t) for t in contract.types}
        #: What is hidden from whom (see expr/hidden.py).
        self.hidden = Hidden(contract)
        self.private_names = self.hidden.names
        self.private_metrics = private_metrics(contract, self.private_names)
        self._subtypes = {t: set(contract.subtypes(t)) for t in contract.types}
        #: The living entities of every type, kept current by create and remove.
        self.types = TypeIndex(contract)
        #: How expressions read this world: scopes, defs, and what a viewer may see.
        self.evaluation = EvalContext(self)

    # -- randomness (see world/randomness.py) -----------------------------------------------------------------

    @property
    def seeds(self) -> SeedTree:
        return self.luck.seeds

    @property  # type: ignore[override]
    def rng(self) -> Any:
        """The random stream expressions and mechanisms draw from now (:meth:`Randomness.current`)."""
        return self.luck.current(self.round)

    # -- reads (the expression interface) ------------------------------------------------------------------

    def entities_of(self, type_name: str) -> list[Entity]:
        return list(self.alive_of(type_name))

    def alive_of(self, type_name: str) -> list[Entity]:
        """The living entities of a type (subtypes included) in creation order — a shared list: read it, never change
        it."""
        if type_name not in self.contract.types:
            raise ExprError(f"'{type_name}' is not a declared type (types: {', '.join(self.contract.types)})")
        return self.types.alive(type_name)

    def subtypes_of(self, type_name: str) -> Any:
        """``type_name`` and every type that extends it."""
        return self._subtypes[type_name]

    def entity(self, entity_id: Any) -> Entity | None:
        if isinstance(entity_id, Entity):
            return entity_id
        return self.entities.get(entity_id) if isinstance(entity_id, str) else None

    def named(self, value: Any, where: str, what: str = "an entity") -> Entity:
        """The entity ``value`` names — an entity, or the id of one — or an error saying what it is instead: every id a
        rule names (a recipient, a link's end, a party to a transfer …) is one of the world's entities, never a write
        that silently goes nowhere."""
        found = self.entity(value)
        if found is not None:
            return found
        if isinstance(value, str):
            named = next((e for e in self.entities.values() if e.name == value), None) if value else None
            hint = (f"; '{value}' is the name of {named.id}: name the entity itself (`$params.who`, not "
                    "`$params.who.name`)") if named is not None else ""
            raise RunError(f"expected {what}, but no entity has the id '{value}'{hint}", where)
        raise RunError(f"expected {what}, got {format_value(value)}", where)

    def named_all(self, value: Any, where: str, what: str = "entities") -> tuple[Entity, ...]:
        """:meth:`named` of one entity or of each in a list."""
        if isinstance(value, (list, tuple)):
            return tuple(self.named(item, where, what) for item in value)
        return (self.named(value, where, what),)

    def winner(self, value: Any, where: str) -> Any:
        """What an `end` names as its winner, as plain data: an entity's id, a side's name (text), a list of those, or
        None; anything else is an error."""
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, Entity):
            return value.id
        if isinstance(value, (list, tuple)) and all(isinstance(item, (Entity, str)) for item in value):
            return [item.id if isinstance(item, Entity) else item for item in value]
        raise RunError(f"a winner is an entity, a list of entities or a side's name (text), got "
                       f"{format_value(value)}", where)

    def hides(self, owner: Any, prop: str, agent: Any) -> bool:
        if owner is self.evaluation.props_view:
            return self.hidden.world_hides(prop, agent)
        return type(owner) is Entity and self.hidden.entity_hides(owner, prop, agent)

    def read_hidden(self) -> None:
        self.luck.count_hidden_read()

    def records(self, name: str) -> list[Entry]:
        if name not in self.records_store:
            known = ", ".join(self.records_store) or "none declared"
            raise ExprError(f"'{name}' is not a declared record (records: {known})")
        return self.records_store[name]

    def visible_records(self, name: str, viewer: Any) -> list[Entry]:
        return self.evaluation.visible_records(name, viewer)

    def events(self, kind: str | None, viewer: Any = None) -> list[LogEvent]:
        return self.evaluation.events(kind, viewer)

    def relation(self, a: Any, b: Any, kind: str) -> float | None:
        return _links.relation(self, a, b, kind)

    def neighbors(self, entity: Any, kind: str) -> list[Entity]:
        return _links.neighbors(self, entity, kind)

    def link_view(self, a: Any, b: Any, kind: str) -> Link | None:
        """The live ``kind`` link from a to b, or None."""
        return _links.link_view(self, a, b, kind)

    def links_of(self, entity: Any, kind: str) -> list[Link]:
        return _links.links_of(self, entity, kind)

    def is_a(self, type_name: str, ancestor: str) -> bool:
        return type_name in self._subtypes.get(ancestor, ())

    def is_type(self, name: str) -> bool:
        return name in self.contract.types

    def has_def(self, name: str) -> bool:
        spec = self.contract.defs.get(name)
        return spec is not None and spec.expr is not None and not spec.args

    def defines(self, name: str) -> bool:
        spec = self.contract.defs.get(name)
        return spec is not None and spec.expr is not None

    def call_def(self, name: str, args: list[Any], source: str, viewer: Any = None) -> Any:
        return self.evaluation.call_def(name, args, source, viewer)

    def distance(self, a: Any, b: Any) -> float:
        if self.space is None:
            raise ExprError("this environment declares no space")
        start, end = position_of(a), position_of(b)
        if start is None or end is None:
            raise ExprError("both entities need a position (at) to measure distance")
        return self.space.geometry.distance(start, end)

    def prop_spec(self, entity: Entity, prop: str) -> PropSpec:
        specs = self.type_props.get(entity.entity_type, {})
        if prop not in specs:
            raise RunError(f"'{entity.entity_type}' has no property '{prop}' (declared: {', '.join(specs) or 'none'})",
                           f"{entity.entity_type}.{prop}")
        return specs[prop]

    # -- clock ---------------------------------------------------------------

    def date(self) -> str | None:
        clock = self.contract.clock
        return calendar_date(self.start, clock.unit, clock.step, max(0, max(1, self.round) - 1))

    def clock_label(self) -> str:
        unit = self.contract.clock.unit
        name = f"{unit[:1].upper()}{unit[1:]}"
        label = f"{name} {max(1, self.round)} of {self.rounds}"
        date = self.date()
        return f"{label} ({date})" if date else label

    # -- indexes: rebuilt when a part of the store was replaced wholesale (a restore, a copy) --------------------

    def rebuild_index(self) -> None:
        """Re-index every entity after the entity store was replaced wholesale (a restore)."""
        self.touched = None
        self.types.rebuild(self.entities.values())
        if self.space is not None:
            self.space.positions.rebuild(self.entities.values())

    def rebuild_event_index(self) -> None:
        self.record_events = RecordEvents(self.log, self.entry_by_seq, self.contract, self.record_seq)

    def rebuild_record_index(self) -> None:
        self.record_authors = RecordAuthors(
            {name: spec.visible for name, spec in self.contract.records.items()}, self.records_store)
        self.kept = {}  # each reader's window is made again from the entries kept, as it is next needed

    def windows(self, record: str) -> KeptWindows | None:
        """Each reader's window over ``record`` when it keeps only its latest entries and not every agent sees every
        one (see record_keep.py); None otherwise, when its latest `keep` are every reader's."""
        keep = self.contract.records[record].keep
        if keep is None or record not in self.hidden.records:
            return None
        found = self.kept.get(record)
        if found is None:
            found = self.kept[record] = KeptWindows(self, record, keep)
        return found

    def rebuild_adjacency(self) -> None:
        _links.rebuild_adjacency(self)

    def build_space(self) -> None:
        """Resolve the declared space before anything is placed in it."""
        if self.contract.space is not None:
            self.space = Spatial(self, self.contract.space)

    # -- transactions (see world/journal.py) ------------------------------------------------------------------

    @property
    def version(self) -> int:
        """The world's version: equal versions mean an equal world (the root of every cache key)."""
        return self.journal.version

    def mark(self) -> int:
        """Where the changes since the last commit stand now: :meth:`rollback` undoes every change after it."""
        return self.journal.mark()

    def rollback(self, mark: int) -> None:
        """Undo every journaled change made after ``mark``, newest first."""
        self.journal.rollback(mark)

    def commit(self) -> None:
        """Keep the changes made so far: nothing before now can be undone (inside :meth:`held`, once it ends)."""
        self.journal.clear()

    def held(self) -> Any:
        """A block whose changes stay undoable until it ends, whatever commits inside it (see
        :meth:`Journal.held`)."""
        return self.journal.held()

    @property
    def holding(self) -> bool:
        """Whether a :meth:`held` block is open."""
        return self.journal.holding > 0

    def touch(self) -> None:
        """Record a change made outside the journal (metrics sampling, physics), so cached reads refresh."""
        self.journal.bump()

    # -- changes (journaled) --------------------------------------------------

    def coerce(self, spec: PropSpec | None, value: Any, where: str, owner: str = "") -> Any:
        """``value`` as ``spec`` stores it (see :func:`~fg_env.world.props.stored`): a number past a declared min or
        max is refused (:class:`Abort`), never clamped; an asset is resolved to the run's file."""
        value = stored(spec, value, where, owner)
        if spec is not None and value is not None and prop_type(spec) == "asset":
            return self.assets.ref(value, where)
        return value

    def set_prop(self, entity: Entity, prop: str, value: Any) -> None:
        specs = self.type_props.get(entity.entity_type, {})
        where = f"{entity.entity_type}.{prop}"
        if prop not in specs:
            known = ", ".join(specs) or "none"
            raise RunError(f"'{entity.entity_type}' has no property '{prop}' (declared: {known})", where)
        self.written.add(prop)
        new = self.coerce(specs[prop], plain_value(value), where, entity.name)
        if self.buffer is not None:
            self.buffer.write(("prop", entity.id, prop), new, lambda: self.set_prop(entity, prop, new), where)
            return
        self.journal.push(("prop", entity.id, prop, entity.properties.get(prop)))
        entity.properties[prop] = new
        self.touch_entity(entity)

    def touch_entity(self, entity: Entity) -> None:
        """Note that ``entity`` has values no invariant check has seen together (see :attr:`touched`)."""
        if self.touched is not None:
            self.touched[entity.id] = None

    def set_world(self, prop: str, value: Any, *, trusted: bool = False) -> None:
        """Set a world property. ``trusted``: the caller built ``value`` from plain data and never changes it in
        place afterwards (a mechanism's fresh list of plain maps), so the defensive copy is skipped."""
        spec = self.contract.world.get(prop)
        if spec is None:
            known = ", ".join(self.contract.world) or "none"
            raise RunError(f"world has no property '{prop}' (declared: {known})", f"world.{prop}")
        self.written.add(prop)
        new = self.coerce(spec, value if trusted else plain_value(value), f"world.{prop}")
        if self.buffer is not None:
            self.buffer.write(("world", prop), new, lambda: self.set_world(prop, new), f"world.{prop}")
            return
        self.journal.push(("world", prop, self.props.get(prop)))
        self.props[prop] = new

    def set_physics(self, name: str, value: Any) -> None:
        model = self.physics
        if model is None:
            raise RunError("this environment declares no physics", f"mechanisms.physics.{name}")
        if not _finite_number(value):
            raise RunError(f"must be a finite number that fits in a float, got {_shown_value(value)}",
                           f"mechanisms.physics.{name}")
        if name in model.variables:
            var = model.variables[name]
            clamped = float(value)
            if var.min is not None:
                clamped = max(var.min, clamped)
            if var.max is not None:
                clamped = min(var.max, clamped)
            self.journal.push(("physics_variable", name, var.value))
            var.value = clamped
        elif name in model.params:
            self.journal.push(("physics_param", name, model.params[name]))
            model.params[name] = float(value)
        else:
            raise RunError(f"physics has no variable or param '{name}'", f"mechanisms.physics.{name}")

    def next_id(self, type_name: str) -> str:
        n = self.counters.get(type_name, 0)
        while True:
            n += 1
            candidate = f"{type_name}_{n}"
            if candidate not in self.entities:
                break
        self.journal.push(("counter", type_name, self.counters.get(type_name)))  # None: undoing leaves none
        self.counters[type_name] = n
        return candidate

    def new_entity(self, type_name: str, entity_id: str | None, name: str | None, given: Iterable[str], at: Any,
                   where: str) -> Entity:
        """An entity of ``type_name`` about to be created — its id (``entity_id``, or the next free one), name and
        position, no properties yet — once the world may hold it and it declares every one of the ``given``
        properties. :meth:`add` puts it in the world."""
        if type_name not in self.contract.types:
            raise RunError(f"'{type_name}' is not a declared type", where)
        if self.types.living >= MAX_ENTITIES:  # an engine limit, not a rule failing: the run fails wherever it is
            raise FatalRunError(
                f"cannot create another '{type_name}': the world already holds {MAX_ENTITIES:,} living entities, the "
                f"most a run may hold. Something creates entities without bound (agents creating agents?): create only "
                f"while a limit holds, e.g. {{\"if\": \"$count({type_name}) < 1000\", \"then\": [{{\"create\": "
                f"\"{type_name}\"}}]}}, or remove entities that are done", where)
        eid = entity_id or self.next_id(type_name)
        if eid in self.entities:
            raise RunError(f"an entity with id '{eid}' already exists", where)
        declared = self.type_props[type_name]
        unknown = set(given) - set(declared)
        if unknown:
            raise RunError(f"'{type_name}' has no properties {sorted(unknown)} (declared: "
                           f"{', '.join(declared) or 'none'})", where)
        entity = Entity(id=eid, name=name or eid, entity_type=type_name, properties={}, location_id=None,
                        luck=self.luck.birth(self.round, self.journal) if self.round else None)
        if at is not None:  # placed first, so props can read the position: `$layer(sugar, $it.at)`
            entity.location_id = self.place(at, where)
        return entity

    def add(self, entity: Entity, where: str) -> Entity:
        """Put ``entity`` (from :meth:`new_entity`, its properties set) in the world."""
        space = self.space
        if entity.location_id is not None:
            self._make_room(entity, entity.location_id, "cannot be placed")
        self.entities[entity.id] = entity
        self.types.created(entity)
        self.touch_entity(entity)
        if space is not None:
            space.positions.add(entity)
        self.journal.push(("create", entity.id))
        if self.lifecycle is not None:
            self.lifecycle("create", entity, where)
        if self.joined is not None and self.round:
            self.joined(entity)
        return entity

    def remove(self, entity: Entity, where: str = "remove") -> None:
        if not entity.alive:
            return
        entity.alive = False
        self.types.changed(entity)
        if self.space is not None:
            self.space.positions.discard(entity)
        self.journal.push(("remove", entity.id))
        if self.lifecycle is not None:
            self.lifecycle("remove", entity, where)

    def move(self, entity: Entity, at: Any, where: str) -> None:
        location = self.place(at, where)
        space = self.space
        if space is not None:
            self._make_room(entity, location, "cannot move there")
            space.positions.discard(entity)
        self.journal.push(("move", entity.id, entity.location_id))
        entity.location_id = location
        if space is not None:
            space.positions.add(entity)

    def place(self, at: Any, where: str) -> Any:
        """``at`` as a position in the space (checked against it), or as given without one."""
        return at if self.space is None else self.space.place(at, where)

    def _make_room(self, entity: Entity, position: Any, what: str) -> None:
        """Refuse (roll back) putting ``entity`` at ``position`` when the cell is full."""
        full = self.space.no_room(entity, position) if self.space is not None else None
        if full is not None:
            raise Abort(f"{entity.name} {what}: {full}.")

    def link(self, kind: str, a: Any, b: Any, value: Any, where: str, fields: dict[str, Any] | None = None) -> None:
        """Create or update a link between two entities (``value`` None keeps the current value; see
        :func:`links.link`)."""
        _links.link(self, kind, self.named(a, where, "a link's `from` entity"),
                    self.named(b, where, "a link's `to` entity"), value, where, fields)

    def unlink(self, kind: str, a: Any, b: Any, where: str) -> None:
        _links.unlink(self, kind, self.named(a, where, "a link's `from` entity"),
                      self.named(b, where, "a link's `to` entity"), where)

    def set_link_field(self, view: Link, name: str, value: Any, where: str) -> None:
        _links.set_link_field(self, view, name, value, where)

    def post(self, record: str, fields: dict[str, Any], author: str | None,
             to: tuple[str, ...] | None, where: str) -> Entry:
        spec = self.contract.records.get(record)
        if spec is None:
            raise RunError(f"'{record}' is not a declared record (records: "
                           f"{', '.join(self.contract.records) or 'none'})", where)
        unknown = set(fields) - set(spec.fields)
        if unknown:
            raise RunError(f"record '{record}' has no fields {sorted(unknown)} (fields: {', '.join(spec.fields)})",
                           where)
        entry = Entry()
        entry.world = self
        typed = self.record_specs[record]
        for name in spec.fields:  # a field holds what a property of its type holds, by the same rules
            entry[name] = self.coerce(typed[name], plain_value(fields.get(name)), f"{where}.{name}")
        self.record_seq += 1
        entry.update({"seq": self.record_seq, "round": self.round, "stage": self.stage,
                      "author": author, "to": list(to) if to is not None else None})
        rows = self.records_store[record]
        rows.append(entry)
        self.record_authors.add(record, entry)
        self.entry_by_seq[entry["seq"]] = entry
        dropped: list[Entry] = []
        moves: list[tuple[str, int | None]] = []
        windows = self.windows(record)
        if windows is not None:  # each reader counts only the entries it sees
            moves, dropped = windows.posted(entry)
        elif spec.keep is not None and len(rows) > spec.keep:
            dropped = rows[: len(rows) - spec.keep]
            del rows[: len(rows) - spec.keep]
        for old in dropped:
            self.entry_by_seq.pop(old["seq"], None)
            self.record_authors.remove(record, old)
        notices = self.record_events.drop(old["seq"] for old in dropped)  # nobody sees a dropped entry
        self.journal.push(("post", record, entry["seq"], dropped, notices, moves))
        if spec.notify:
            self.emit("record", "", actor=author, to=to, data={
                "record": record, "entry": entry["seq"], "fields": {name: entry[name] for name in spec.fields}})
        return entry

    def emit(self, kind: str, text: str, *, actor: str | None = None,
             to: Iterable[str] | None = None, data: dict[str, Any] | None = None) -> LogEvent:
        self.event_seq += 1
        event = LogEvent(self.event_seq, self.round, kind, text, actor,
                         tuple(to) if to is not None else None, dict(data or {}), self.stage)
        self.log.append(event)
        record_key = self.record_events.add(event, self.entry_by_seq) if kind == "record" else None
        self.journal.push(("emit", event.seq, record_key))
        return event

    def put_first(self, event: LogEvent, since: int) -> None:
        """Move ``event``, the log's last, ahead of the other events logged after sequence number ``since`` (an action's
        announcement ahead of the news its own effects produced), numbering them again in their new order."""
        log = self.log
        index = len(log) - 1
        while index > 0 and log[index - 1].seq > since:
            index -= 1
        if log[index] is event:
            return
        log.pop()
        log.insert(index, event)
        for offset, item in enumerate(log[index:]):
            item.seq = since + 1 + offset
        self.journal.push(("first", index, since))

    def schedule(self, due_round: int, effects: list[Any], vars: dict[str, Any], path: str,
                 delivery: dict[str, Any] | None = None, owed: tuple[str, str] | None = None) -> None:
        """Run ``effects`` when the round reaches ``due_round`` — as the continuation of agent ``owed[0]``'s action
        ``owed[1]`` when an action scheduled them; or, with ``delivery``, deliver that message (see :mod:`delivery`)."""
        item: dict[str, Any] = {"effects": effects, "vars": {k: freeze(v) for k, v in vars.items()},
                                "capture_version": CAPTURE_VERSION, "path": path}
        if delivery is not None:
            item["delivery"] = delivery
        if owed is not None:
            item["by"], item["action"] = owed
        self.schedule_seq += 1
        entry = (due_round, self.schedule_seq, item)
        heapq.heappush(self.scheduled, entry)
        self.journal.push(("schedule", entry))

    def request_reaction(self, entity_id: str, why: str, actions: list[str] | None = None) -> None:
        entry = (entity_id, why, actions)
        self.reactions.append(entry)
        self.journal.push(("reaction", entry))

    def request_wake(self, entity_id: str, why: str) -> None:
        self.journal.push(("wake", entity_id, entity_id in self.wake_requests, self.wake_requests.get(entity_id)))
        self.wake_requests[entity_id] = why

    def request_end(self, name: str, winner: Any, text: str, where: str = "end") -> None:
        """End the run (the first request stands), naming its ``winner`` (see :meth:`winner`)."""
        if self.end_request is None:
            self.end_request = {"name": name, "winner": self.winner(winner, where), "text": text}
            self.journal.push(("end",))

    def add_to_brief(self, entity_id: str, line: str) -> None:
        """Add ``line`` to the brief of ``entity_id``."""
        briefs = self.entity_briefs
        old = briefs.get(entity_id)
        self.journal.push(("brief", entity_id, entity_id in briefs, old))
        briefs[entity_id] = f"{old}\n{line}" if old else line

    def mark_fired(self, index: int) -> None:
        """The `once` event ``index`` fired: it never fires again, unless an undo takes the firing back."""
        if index not in self.fired_once:
            self.fired_once.add(index)
            self.journal.push(("fired", index))

    def set_armed(self, index: int, holds: bool) -> None:
        """The `change` event ``index``'s `when` now ``holds`` (it fires when this turns true)."""
        armed = self.armed
        if index in armed and armed[index] == holds:
            return
        self.journal.push(("armed", index, armed.get(index)))
        armed[index] = holds

    def count_use(self, actor_id: str, action: str, undoable: bool) -> None:
        """One more use of ``action`` by ``actor_id`` this round. ``undoable``: the use is taken back with the changes
        an undo takes back (an atomic turn's actions); otherwise it stands once counted (see runtime/turn.py)."""
        used = self.used_round.setdefault(actor_id, {})
        used[action] = used.get(action, 0) + 1
        if undoable:
            self.journal.push(("use", actor_id, action))
