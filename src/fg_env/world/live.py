"""The live world of one run: entities, global properties, links, records, the event log,
physics and space — every mutation journaled so an action commits atomically or not at all."""
from __future__ import annotations

import heapq
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from ..assets.store import AssetStore
from ..contract import MAX_ENTITIES, Contract, PropSpec
from ..effects.captures import CAPTURE_VERSION, freeze, thaw
from ..errors import FatalRunError, RunError
from ..expr import ExprError, Scope, Untrusted, World, compile_expr, is_expr, truthy
from ..expr.calls import callable_names, suggest_function
from ..expr.hidden import Hidden
from ..expr.objects import Entity, PropsView
from ..expr.template import format_value
from ..patterns.runtime import PatternRuntime
from ..physics import world as world_physics
from ..physics.model import PhysicsModel, _CompiledExpr
from ..sampling.seeds import SeedTree
from ..stdlib.dates import calendar_date
from . import links as _links
from .defaults import default_order
from .journal import Journal
from .links import Link
from .parts import ClockView, Entry, LogEvent, PhysicsView, private_metrics
from .props import finite_number as _finite_number
from .props import prop_type
from .props import shown_value as _shown_value
from .randomness import Context, Randomness
from .record_events import RecordEvents
from .record_index import RecordAuthors, author_only
from .space import Spatial, position_of
from .type_index import TypeIndex

if TYPE_CHECKING:
    from ..effects.sync import WriteBuffer

__all__ = ["SdkWorld", "Entry", "LogEvent", "Abort", "prop_type"]


class Abort(Exception):
    """Stop the current action; every change it made is rolled back. The text reaches the actor."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class OutOfBounds(Abort):
    """A number past a declared min or max (a property's, a link value's or a layer cell's). An agent's action is
    refused like any :class:`Abort`; world logic (an event no action set off) that does it
    fails the run, because that is a contract bug no agent can fix."""


class SdkWorld(World):
    """World store for one run. Expressions read it through the :class:`World` interface."""

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
        self.record_authors = RecordAuthors(
            {name: spec.visible for name, spec in contract.records.items()}, self.records_store)
        #: Per-entity brief text rendered at build (from entities.*.brief / population.brief).
        self.entity_briefs: dict[str, str] = {}
        self.log: list[LogEvent] = []
        self.record_events = RecordEvents((), self.entry_by_seq, contract)
        self.physics: PhysicsModel | None = None
        self.physics_writes: list[tuple[str, _CompiledExpr]] = []
        self.entity_dynamics: list[Any] = []
        self.round = 0
        self.stage: str | None = None
        self.rounds = 0
        self.metrics: dict[str, Any] = {}
        self.series: dict[str, list[Any]] = {}
        self.scheduled: list[tuple[float, int, dict[str, Any]]] = []
        self.wake_requests: dict[str, str] = {}
        #: Agents asked to react right away (`wake` with `now`), answered as soon as the change commits: id, why, and
        #: the
        #: actions offered (None: the stage's).
        self.reactions: list[tuple[str, str, list[str] | None]] = []
        #: The calendar date of round 1: ``clock.start`` as written or read from ``$inputs`` at build (None without
        #: one).
        self.start: str | None = None
        #: Tie-break for scheduled effects due in the same round: the order they were scheduled.
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
        #: the contract) they were worked out at, never state, so a copy starts without them.
        self.caches: dict[str, Any] = {}
        #: The files the run knows (the contract's catalog, once loaded from its folder, and submitted files).
        self.assets = AssetStore()
        #: The sequence numbers the log's last event and the records' last entry took.
        self.event_seq = 0
        self.record_seq = 0
        self._props_view = PropsView(self)
        self._physics_view = PhysicsView(self)
        self._clock_view = ClockView(self)
        self.patterns = PatternRuntime(self)
        #: Each type's declared properties (its own and inherited), by name.
        self.type_props = {t: contract.props_of(t) for t in contract.types}
        #: What is hidden from whom (see expr/hidden.py).
        self.hidden = Hidden(contract)
        self.private_names = self.hidden.names
        self.private_metrics = private_metrics(contract, self.private_names)
        #: Def results for the current world state (see :meth:`call_def`).
        self._def_cache: dict[Any, Any] = {}
        self._def_cache_state: Any = None
        self._def_cache_on = False
        #: What :meth:`remembered` worked out for the current world state.
        self._remembered: dict[Any, Any] = {}
        self._remembered_state: Any = None
        self._subtypes = {t: set(contract.subtypes(t)) for t in contract.types}
        #: The living entities of every type, kept current by create and remove.
        self.types = TypeIndex(contract)

    # -- randomness (see world/randomness.py) -----------------------------------------------------------------

    @property
    def seeds(self) -> SeedTree:
        return self.luck.seeds

    @property  # type: ignore[override]
    def rng(self) -> Any:
        """The random stream expressions and mechanisms draw from now (:meth:`Randomness.current`)."""
        return self.luck.current(self.round)

    # -- expression interface ------------------------------------------------

    def entities_of(self, type_name: str) -> list[Entity]:
        return list(self.alive_of(type_name))

    def alive_of(self, type_name: str) -> list[Entity]:
        """The living entities of a type (subtypes included) in creation order — a shared list: read it, never change
        it."""
        if type_name not in self.contract.types:
            raise ExprError(f"'{type_name}' is not a declared type (types: {', '.join(self.contract.types)})")
        return self.types.alive(type_name, compact=self.journal.mark() == 0)

    def rebuild_index(self) -> None:
        """Re-index every entity after the entity store was replaced wholesale (a restore)."""
        self.touched = None
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

    def entity(self, entity_id: Any) -> Entity | None:
        if isinstance(entity_id, Entity):
            return entity_id
        return self.entities.get(entity_id) if isinstance(entity_id, str) else None

    def hides(self, owner: Any, prop: str, agent: Any) -> bool:
        if owner is self._props_view:
            return prop in self.hidden.world
        return type(owner) is Entity and self.hidden.entity_hides(owner, prop, agent)

    def read_hidden(self) -> None:
        self.luck.count_hidden_read()

    def refusal(self, entity: Entity, prop: str, told: str, instead: str) -> Abort:
        """The refusal of a rule about ``entity``'s ``prop``: ``told``, or ``instead`` — which says nothing of it —
        when the value is hidden from the agent whose action is running (the read is noted, so the refusal spends the
        action; see expr/hidden.py)."""
        if self.hides(entity, prop, self.luck.here().actor):
            self.read_hidden()
            return Abort(instead)
        return Abort(told)

    def records(self, name: str) -> list[Entry]:
        if name not in self.records_store:
            known = ", ".join(self.records_store) or "none declared"
            raise ExprError(f"'{name}' is not a declared record (records: {known})")
        return self.records_store[name]

    def events(self, kind: str | None, viewer: Any = None) -> list[LogEvent]:
        """Events so far; with a ``viewer`` (views, record visibility) only those it may know about."""
        seen = viewer if isinstance(viewer, Entity) else None
        candidates = self.record_events.candidates(self.contract, seen) if kind == "record" and seen else self.log
        return [e for e in candidates if (kind is None or e.kind == kind)
                and (seen is None or self.event_visible(e, seen))]

    def rebuild_event_index(self) -> None:
        self.record_events = RecordEvents(self.log, self.entry_by_seq, self.contract)

    def event_visible(self, event: LogEvent, viewer: Entity) -> bool:
        """Record notifications carry the same visibility as their retained source entry."""
        if not event.visible_to(viewer.id):
            return False
        if event.kind != "record":
            return True
        record = event.data.get("record")
        entry = self.entry_by_seq.get(event.data.get("entry"))
        return record in self.contract.records and entry is not None and self.entry_visible(record, entry, viewer)

    def relation(self, a: Any, b: Any, kind: str) -> float | None:
        return _links.relation(self, a, b, kind)

    def neighbors(self, entity: Any, kind: str) -> list[Entity]:
        return _links.neighbors(self, entity, kind)

    def link_view(self, a: Any, b: Any, kind: str) -> Link | None:
        """The live ``kind`` link from a to b, or None."""
        return _links.link_view(self, a, b, kind)

    def links_of(self, entity: Any, kind: str) -> list[Link]:
        return _links.links_of(self, entity, kind)

    def visible_records(self, name: str, viewer: Any) -> list[Entry]:
        rows = self.records(name)
        if not isinstance(viewer, Entity):
            return rows
        indexed = self.record_authors.candidates(name, viewer)
        if indexed is not None:
            rows = indexed
        return [row for row in rows if self.entry_visible(name, row, viewer)]

    def rebuild_record_index(self) -> None:
        self.record_authors = RecordAuthors(
            {name: spec.visible for name, spec in self.contract.records.items()}, self.records_store)

    def entry_visible(self, record: str, entry: Entry, viewer: Entity | None) -> bool:
        if viewer is None:
            return True
        to = entry.get("to")
        if to is not None and viewer.id not in to and entry.get("author") != viewer.id:
            return False
        visible = self.contract.records[record].visible
        if visible == "all":
            return True
        if author_only(visible) and entry.get("author") != viewer.id:
            return False  # Exact author-only predicates cannot hold for another reader.
        try:
            expr = compile_expr(visible)
            # A pure reader/entry rule needs no clock, metric, pattern or turn roots.
            # Keep function calls on the full scope: they may read implicit context.
            scope = Scope({"viewer": viewer, "it": entry}, self) \
                if not expr.functions and expr.roots <= {"viewer", "it"} else self.scope(viewer=viewer, it=entry)
            return truthy(expr(scope))
        except ExprError as exc:
            raise RunError(str(exc), f"records.{record}.visible") from None

    def is_a(self, type_name: str, ancestor: str) -> bool:
        return type_name in self._subtypes.get(ancestor, ())

    def has_def(self, name: str) -> bool:
        spec = self.contract.defs.get(name)
        return spec is not None and spec.expr is not None and not spec.args

    def defines(self, name: str) -> bool:
        spec = self.contract.defs.get(name)
        return spec is not None and spec.expr is not None

    def call_def(self, name: str, args: list[Any], source: str, viewer: Any = None) -> Any:
        """Call the def ``name``. It sees the caller's ``viewer`` (bound while rendering for, or offering choices to,
        one agent), so ``$records`` and ``$events`` inside it show what the caller could see."""
        spec = self.contract.defs.get(name)
        if spec is None or spec.expr is None:
            hint = suggest_function(name, callable_names(self.contract.mechanism_families())
                                    + list(self.contract.expr_defs()))
            raise ExprError(f"unknown function ${name}" + (f" — did you mean {hint}?" if hint else ""), source)
        if len(args) != len(spec.args):
            raise ExprError(f"${name} takes {len(spec.args)} argument(s) ({', '.join(spec.args) or 'none'}), got "
                            f"{len(args)}", source)
        here = self.luck.here()  # the running turn's context, read once: nothing before the evaluation changes it
        key = self._def_key(name, args, viewer)
        if key is not None:
            state = self._version(here)
            if state != self._def_cache_state:
                self._def_cache, self._def_cache_state = {}, state
            elif key in self._def_cache:
                return self._def_cache[key]
        depth = here.depth
        if depth >= 32:
            raise ExprError(f"${name}: defs call each other too deeply (recursion?)", source)
        here.depth = depth + 1
        observed = self.luck.observe()
        try:
            values = dict(zip(spec.args, args))
            if viewer is not None:
                values["viewer"] = viewer
            value = compile_expr(spec.expr)(self._scope(here, values))
        finally:
            here.depth = depth
        # Only a pure call is reused (a reuse would neither draw nor count a hidden read); with the same state and
        # arguments it takes the same path again, so its value is exactly what a fresh call would return.
        if key is not None and observed.pure(state, self._version(here)) and isinstance(value, _CACHEABLE):
            self._def_cache[key] = value
        return value

    def remembered(self, key: tuple[Any, ...], work: Callable[[], Any]) -> Any:
        """``work()``, reused under ``key`` while the world stays as it is: one turn asks for the same choices and
        tools several times (legality, its tools, validating a call, diagnostics). Work that drew at random or read a
        hidden value is never reused: each call draws and counts its hidden reads afresh."""
        state = self.state_version()
        if state != self._remembered_state:
            self._remembered, self._remembered_state = {}, state
        elif key in self._remembered:
            return self._remembered[key]
        observed = self.luck.observe()
        value = work()
        if observed.pure(state, self.state_version()):
            self._remembered[key] = value
        return value

    def enable_def_cache(self) -> None:
        """Start caching def results; called once the world is built."""
        self._def_cache_on = True
        self.touch()

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

    def state_version(self) -> Any:
        """Equal values mean nothing a read could see has changed (for caches of derived values)."""
        return self._version(self.luck.here())

    def _version(self, here: Context) -> Any:
        """:meth:`state_version` as the running context ``here`` sees it."""
        pending = here.pending  # a turn's (runtime/ledger.py Pending), or None
        return (self.journal.version, self.round, self.stage, pending.version if pending is not None else 0)

    def _def_key(self, name: str, args: list[Any], viewer: Any) -> tuple[Any, ...] | None:
        """A cache key for a def call, or None when the call cannot be cached."""
        if not self._def_cache_on:
            return None
        parts: list[Any] = [name]
        for arg in [viewer, *args]:
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
        specs = self.type_props.get(entity.entity_type, {})
        if prop not in specs:
            raise RunError(f"'{entity.entity_type}' has no property '{prop}' (declared: {', '.join(specs) or 'none'})",
                           f"{entity.entity_type}.{prop}")
        return specs[prop]

    def is_type(self, name: str) -> bool:
        return name in self.contract.types

    # -- scopes --------------------------------------------------------------

    def scope(self, **values: Any) -> Scope:
        return self._scope(self.luck.here(), values)

    def _scope(self, here: Context, values: dict[str, Any]) -> Scope:
        """A scope over the world as ``here`` (the running turn's context) sees it, with ``values`` as extra roots."""
        base: dict[str, Any] = {
            "inputs": self.inputs,
            "world": self._props_view,
            "physics": self._physics_view,
            "clock": self._clock_view,
            "pattern": self.patterns.view,
            "round": self.round,
            "stage": self.stage,
            "outputs": self.metrics,
            "series": self.series,
            "arm": self.arm,
            "pending": here.pending.items if here.pending is not None else [],
        }
        base.update(values)
        return Scope(base, self)

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

    # -- mutation (journaled) --------------------------------------------------

    def coerce(self, spec: PropSpec | None, value: Any, where: str, owner: str = "") -> Any:
        """``value`` as ``spec`` stores it. A number past a declared min or max is refused (:class:`Abort`), never
        clamped: an action is rolled back and its actor told why, like a transfer that does not fit. ``owner``
        names who holds the property in that refusal."""
        if spec is None:
            return value
        kind = prop_type(spec)
        if value is None:
            if spec.default is None or kind == "any":  # declared without a value: it may be empty
                return None
            raise RunError(
                f"cannot be null: it starts with a value, so it always holds one ({kind}; an empty list's $max, $avg "
                f"or $first is null — guard it, e.g. `$max(xs) if $len(xs) > 0 else 0`); to let it be empty, "
                f'declare it with "default": null', where)
        if kind in ("number", "int"):
            if not _finite_number(value):
                raise RunError(f"must be a finite number that fits in a float, got {_shown_value(value)}", where)
            prop = where.rsplit(".", 1)[-1]
            within_bounds(spec, value, f"{owner}'s {prop}" if owner else prop)
            if kind == "int":
                if float(value) != int(value):
                    raise RunError(f"must be a whole number, got {value}", where)
                value = int(value)
        elif kind == "bool" and not isinstance(value, bool):
            raise RunError(f"must be true or false, got {value!r}", where)
        elif kind == "text" and not isinstance(value, str):
            hint = ('; property shorthand "bool" is a text default, not a type declaration; '
                    'use {"type": "bool", "default": false}'
                    if spec.type is None and spec.default == "bool" and isinstance(value, bool) else '')
            raise RunError(f"must be text, got {value!r}{hint}", where)
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
        specs = self.type_props.get(entity.entity_type, {})
        where = f"{entity.entity_type}.{prop}"
        if prop not in specs:
            known = ", ".join(specs) or "none"
            raise RunError(f"'{entity.entity_type}' has no property '{prop}' (declared: {known})", where)
        self.written.add(prop)
        new = self.coerce(specs[prop], _plain(value), where, entity.name)
        if self.buffer is not None:
            self.buffer.write(("prop", entity.id, prop), new, lambda: self.set_prop(entity, prop, new), where)
            return
        self.journal.push(("prop", entity.id, prop, entity.properties.get(prop)))
        entity.properties[prop] = new
        self._touch_entity(entity)

    def _touch_entity(self, entity: Entity) -> None:
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
        new = self.coerce(spec, value if trusted else _plain(value), f"world.{prop}")
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
        self.journal.push(("counter", type_name, self.counters.get(type_name, 0)))
        self.counters[type_name] = n
        return candidate

    def create(self, type_name: str, entity_id: str | None, name: str | None,
               props: dict[str, Any], at: Any, scope: Scope, where: str, evaluate: bool = True) -> Entity:
        """Create an entity. ``props`` values that are expressions are evaluated when ``evaluate`` is
        true (contract text); runtime values from native ops pass ``evaluate=False``. Participant text
        is never evaluated, whatever it looks like."""
        spec = self.contract.types.get(type_name)
        if spec is None:
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
        unknown = set(props) - set(declared)
        if unknown:
            raise RunError(f"'{type_name}' has no properties {sorted(unknown)} (declared: "
                           f"{', '.join(declared) or 'none'})", where)
        entity = Entity(id=eid, name=name or eid, entity_type=type_name, properties={}, location_id=None)
        space = self.space
        if at is not None:  # placed first, so props can read the position: `$layer(sugar, $it.at)`
            entity.location_id = self.place(at, where)
        own = scope.child(it=entity)  # props read other props of the same entity: `$it.income * 0.3`
        raws = {prop: props[prop] if prop in props else prop_spec.default for prop, prop_spec in declared.items()}
        # Expressions: contract text given here, and a type's defaults. Participant text is never one.
        expressions = {prop: raw for prop, raw in raws.items() if is_expr(raw) and (
            prop not in props or (evaluate and not isinstance(raw, Untrusted)))}
        order = self._prop_order(raws, expressions, where)
        for prop in order:
            raw = raws[prop]
            try:
                value = compile_expr(raw)(own) if prop in expressions else _copy(raw)
            except ExprError as exc:
                raise RunError(str(exc), f"{where}.props.{prop}") from None
            entity.properties[prop] = self.coerce(declared[prop], _plain(value), f"{where}.props.{prop}", entity.name)
        if order is not raws:  # evaluated out of declaration order: keep the declared order
            entity.properties = {prop: entity.properties[prop] for prop in declared}
        if entity.location_id is not None:
            self._make_room(entity, entity.location_id, "cannot be placed")
        self.entities[eid] = entity
        self.types.created(entity)
        self._touch_entity(entity)
        if space is not None:
            space.positions.add(entity)
        self.journal.push(("create", eid))
        if self.lifecycle is not None:
            self.lifecycle("create", entity, where)
        if self.joined is not None and self.round:
            self.joined(entity)
        return entity

    @staticmethod
    def _prop_order(raws: dict[str, Any], expressions: dict[str, Any], where: str) -> Iterable[str]:
        """The order a new entity's props are evaluated in: declaration order (``raws`` itself), but each prop after
        the props it reads through `$it`, whichever order they are written in."""
        if not any("$it." in raw for raw in expressions.values()):
            return raws
        order, circle = default_order({prop: expressions.get(prop) for prop in raws}, "it")
        if circle is not None:
            raise RunError(f"props {' → '.join(circle)} read each other through $it in a circle, so none can be "
                           "worked out first: give one of them a plain value", f"{where}.props")
        return order

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

    def _make_room(self, entity: Entity, position: Any, what: str) -> None:
        """Refuse (roll back) putting ``entity`` at ``position`` when the cell is full."""
        full = self.space.no_room(entity, position) if self.space is not None else None
        if full is not None:
            raise Abort(f"{entity.name} {what}: {full}.")

    def link(self, kind: str, a: Any, b: Any, value: Any, where: str, fields: dict[str, Any] | None = None) -> None:
        """Create or update a link (``value`` None keeps the current value; see :func:`links.link`)."""
        _links.link(self, kind, a, b, value, where, fields)

    def unlink(self, kind: str, a: Any, b: Any, where: str) -> None:
        _links.unlink(self, kind, a, b, where)

    def set_link_field(self, view: Link, name: str, value: Any, where: str) -> None:
        _links.set_link_field(self, view, name, value, where)

    def rebuild_adjacency(self) -> None:
        _links.rebuild_adjacency(self)

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
        for name, kind in spec.fields.items():
            value = _plain(fields.get(name))
            if value is not None and kind == "text" and not isinstance(value, str):
                value = str(value)
            elif value is not None and kind == "asset":
                value = self.assets.ref(value, f"{where}.{name}")
            entry[name] = value
        self.record_seq += 1
        entry.update({"seq": self.record_seq, "round": self.round, "stage": self.stage,
                      "author": author, "to": list(to) if to is not None else None})
        rows = self.records_store[record]
        rows.append(entry)
        self.record_authors.add(record, entry)
        self.entry_by_seq[entry["seq"]] = entry
        dropped: list[Entry] = []
        if spec.keep is not None and len(rows) > spec.keep:
            dropped = rows[: len(rows) - spec.keep]
            del rows[: len(rows) - spec.keep]
            for old in dropped:
                self.entry_by_seq.pop(old["seq"], None)
                self.record_authors.remove(record, old)
        self.journal.push(("post", record, entry["seq"], dropped))
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
                 delivery: dict[str, Any] | None = None) -> None:
        """Run ``effects`` when the round reaches ``due_round``;
        or, with ``delivery``, deliver that message (see :mod:`delivery`)."""
        item: dict[str, Any] = {"effects": effects, "vars": {k: freeze(v) for k, v in vars.items()},
                                "capture_version": CAPTURE_VERSION, "path": path}
        if delivery is not None:
            item["delivery"] = delivery
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

    def request_end(self, name: str, winner: Any, text: str) -> None:
        if self.end_request is None:
            self.end_request = {"name": name, "winner": winner, "text": text}
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

    def thaw(self, vars: dict[str, Any], *, version: int = 0) -> dict[str, Any]:
        return {k: thaw(v, self, version=version) for k, v in vars.items()}

    # -- physics (see world_physics) --------------------------------------------------

    def build_physics(self) -> None:
        world_physics.build_physics(self)

    def step_physics(self) -> list[dict[str, Any]]:
        """Advance physics one round. Integrated variables stay inside their bounds; a formula written to a property
        past its bounds has nothing to refuse, so it fails."""
        try:
            return world_physics.step_physics(self)
        except Abort as refusal:
            raise RunError(f"{refusal.reason} Keep the formula in range, e.g. with clamp(x, low, high)",
                           "mechanisms.physics") from None

    # -- helpers ---------------------------------------------------------------

    def _key(self, kind: str, a: str, b: str) -> tuple[str, str]:
        return _links.edge_key(self, kind, a, b)

    def place(self, at: Any, where: str) -> Any:
        """``at`` as a position in the space (checked against it), or as given without one."""
        return at if self.space is None else self.space.place(at, where)

# ---------------------------------------------------------------------------


#: Def results that are immutable, so a cached value can be handed out again safely.
_CACHEABLE = (int, float, bool, str, type(None), Entity)


def within_bounds(spec: Any, value: float, subject: str) -> None:
    """Refuse (:class:`OutOfBounds`) a number past ``spec``'s min or max, naming ``subject`` ("Ann's coins"). Saturating
    is written out: ``$clamp(x, low, high)``. A private property's value stays out of the reason, which the acting
    agent is told."""
    if spec.min is not None and value < spec.min:
        limit = f"cannot go below {format_value(spec.min)}"
    elif spec.max is not None and value > spec.max:
        limit = f"cannot go above {format_value(spec.max)}"
    else:
        return
    if getattr(spec, "private", False):
        raise OutOfBounds(f"{subject} {limit}.")
    raise OutOfBounds(f"{subject} {limit}: it would be {format_value(value)}.")


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
