"""How the rules' expressions read a world: the scope they evaluate in, the contract's defs and the caches of both,
what a viewer may see of the records and the log, and entities created from contract text.

A world's :class:`EvalContext` (``world.evaluation``) holds nothing the run changes: its caches of def results and of
work a turn asks for again are keyed on the state they were worked out in (:meth:`EvalContext.state_version`), and a
copy of the world starts with a context of its own, its caches empty.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from ..errors import RunError
from ..expr import ExprError, Scope, Untrusted, compile_expr, is_expr, truthy
from ..expr.calls import callable_names, suggest_function
from ..expr.objects import Entity, PropsView
from .abort import Abort
from .defaults import default_order
from .parts import ClockView, Entry, LogEvent, PhysicsView
from .randomness import Context
from .record_index import author_only
from .values import copy_value, plain_value

if TYPE_CHECKING:
    from .store import World

__all__ = ["EvalContext"]

#: Def results that are immutable, so a cached value can be handed out again safely.
_CACHEABLE = (int, float, bool, str, type(None), Entity)


class EvalContext:
    """How expressions read ``world`` (see the module docstring)."""

    def __init__(self, world: World):
        self.world = world
        #: The `$world`, `$physics` and `$clock` views scopes hold.
        self.props_view = PropsView(world)
        self.physics_view = PhysicsView(world)
        self.clock_view = ClockView(world)
        #: Whether def results are cached (once the world is built; see :meth:`call_def`).
        self.caching = False
        self._defs: dict[Any, Any] = {}
        self._defs_state: Any = None
        #: What :meth:`remembered` worked out for the current world state.
        self._remembered: dict[Any, Any] = {}
        self._remembered_state: Any = None

    def cache_defs(self) -> None:
        """Start caching def results; called once the world is built."""
        self.caching = True
        self.world.touch()

    # -- scopes -------------------------------------------------------------------------------------------------

    def scope(self, **values: Any) -> Scope:
        """A scope over the world as the running turn sees it, with ``values`` as extra roots."""
        return self.scope_at(self.world.luck.here(), values)

    def scope_at(self, here: Context, values: dict[str, Any]) -> Scope:
        """A scope over the world as ``here`` (the running turn's context) sees it, with ``values`` as extra roots."""
        world = self.world
        base: dict[str, Any] = {
            "inputs": world.inputs,
            "world": self.props_view,
            "physics": self.physics_view,
            "clock": self.clock_view,
            "pattern": world.patterns.view,
            "round": world.round,
            "stage": world.stage,
            "outputs": world.metrics,
            "series": world.series,
            "arm": world.arm,
            "pending": here.pending.items if here.pending is not None else [],
        }
        base.update(values)
        return Scope(base, world)

    # -- the state a read sees ------------------------------------------------------------------------------------

    def state_version(self) -> Any:
        """Equal values mean nothing a read could see has changed (for caches of derived values)."""
        return self._version(self.world.luck.here())

    def _version(self, here: Context) -> Any:
        """:meth:`state_version` as the running context ``here`` sees it."""
        world = self.world
        pending = here.pending  # a turn's (runtime/ledger.py Pending), or None
        return (world.journal.version, world.round, world.stage, pending.version if pending is not None else 0)

    def remembered(self, key: tuple[Any, ...], work: Callable[[], Any]) -> Any:
        """``work()``, reused under ``key`` while the world stays as it is: one turn asks for the same choices and
        tools several times (legality, its tools, validating a call, diagnostics). Work that drew at random or read a
        hidden value is never reused: each call draws and counts its hidden reads afresh."""
        state = self.state_version()
        if state != self._remembered_state:
            self._remembered, self._remembered_state = {}, state
        elif key in self._remembered:
            return self._remembered[key]
        observed = self.world.luck.observe()
        value = work()
        if observed.pure(state, self.state_version()):
            self._remembered[key] = value
        return value

    # -- defs -----------------------------------------------------------------------------------------------------

    def call_def(self, name: str, args: list[Any], source: str, viewer: Any = None) -> Any:
        """Call the def ``name``. It sees the caller's ``viewer`` (bound while rendering for, or offering choices to,
        one agent), so ``$records`` and ``$events`` inside it show what the caller could see."""
        contract = self.world.contract
        spec = contract.defs.get(name)
        if spec is None or spec.expr is None:
            hint = suggest_function(name, callable_names(contract.mechanism_families()) + list(contract.expr_defs()))
            raise ExprError(f"unknown function ${name}" + (f" — did you mean {hint}?" if hint else ""), source)
        if len(args) != len(spec.args):
            raise ExprError(f"${name} takes {len(spec.args)} argument(s) ({', '.join(spec.args) or 'none'}), got "
                            f"{len(args)}", source)
        luck = self.world.luck
        here = luck.here()  # the running turn's context, read once: nothing before the evaluation changes it
        key = self._def_key(name, args, viewer)
        if key is not None:
            state = self._version(here)
            if state != self._defs_state:
                self._defs, self._defs_state = {}, state
            elif key in self._defs:
                return self._defs[key]
        depth = here.depth
        if depth >= 32:
            raise ExprError(f"${name}: defs call each other too deeply (recursion?)", source)
        here.depth = depth + 1
        observed = luck.observe()
        try:
            values = dict(zip(spec.args, args))
            if viewer is not None:
                values["viewer"] = viewer
            value = compile_expr(spec.expr)(self.scope_at(here, values))
        finally:
            here.depth = depth
        # Only a pure call is reused (a reuse would neither draw nor count a hidden read); with the same state and
        # arguments it takes the same path again, so its value is exactly what a fresh call would return.
        if key is not None and observed.pure(state, self._version(here)) and isinstance(value, _CACHEABLE):
            self._defs[key] = value
        return value

    def _def_key(self, name: str, args: list[Any], viewer: Any) -> tuple[Any, ...] | None:
        """A cache key for a def call, or None when the call cannot be cached."""
        if not self.caching:
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

    # -- what a viewer may see ------------------------------------------------------------------------------------

    def refusal(self, entity: Entity, prop: str, told: str, instead: str) -> Abort:
        """The refusal of a rule about ``entity``'s ``prop``: ``told``, or ``instead`` — which says nothing of it —
        when the value is hidden from the agent whose action is running (the read is noted, so the refusal spends the
        action; see expr/hidden.py)."""
        world = self.world
        if world.hides(entity, prop, world.luck.here().actor):
            world.read_hidden()
            return Abort(instead)
        return Abort(told)

    def visible_records(self, name: str, viewer: Any) -> list[Entry]:
        """The entries of record ``name`` ``viewer`` may see: an agent (an :class:`Entity`) those its `visible` rule and
        their `to` let it see; everyone (:data:`~fg_env.expr.EVERYONE`, text sent to several) only those every agent
        sees; game logic (None) every entry, a read of what may be hidden from the acting agent when the record may
        hold one it cannot see (see expr/hidden.py). A reader's entries are numbered (``seq``) in its own view, from 1;
        game logic reads the world's numbering."""
        world = self.world
        rows = world.records(name)
        if viewer is None:
            if name in world.hidden.records:
                world.read_hidden()
            return rows
        if not isinstance(viewer, Entity):
            seen = rows if name not in world.hidden.records else []
        else:
            indexed = world.record_authors.candidates(name, viewer)
            seen = [row for row in (rows if indexed is None else indexed) if self.entry_visible(name, row, viewer)]
        return [row.numbered(position) for position, row in enumerate(seen, 1)]

    def entry_as_read(self, name: str, entry: Entry, viewer: Entity) -> Entry:
        """``entry`` of record ``name`` as ``viewer`` reads it: numbered by its place among the entries it sees."""
        position = next((at for at, row in enumerate(self.visible_records(name, viewer), 1) if row.key == entry.key),
                        None)
        return entry.numbered(position) if position is not None else entry

    def entry_visible(self, record: str, entry: Entry, viewer: Entity | None) -> bool:
        if viewer is None:
            return True
        to = entry.get("to")
        if to is not None and viewer.id not in to and entry.get("author") != viewer.id:
            return False
        visible = self.world.contract.records[record].visible
        if visible == "all":
            return True
        if author_only(visible) and entry.get("author") != viewer.id:
            return False  # Exact author-only predicates cannot hold for another reader.
        try:
            expr = compile_expr(visible)
            # A pure reader/entry rule needs no clock, metric, pattern or turn roots.
            # Keep function calls on the full scope: they may read implicit context.
            scope = Scope({"viewer": viewer, "it": entry}, self.world) \
                if not expr.functions and expr.roots <= {"viewer", "it"} else self.scope(viewer=viewer, it=entry)
            return truthy(expr(scope))
        except ExprError as exc:
            raise RunError(str(exc), f"records.{record}.visible") from None

    def events(self, kind: str | None, viewer: Any = None) -> list[LogEvent]:
        """Events so far (of ``kind``; None: every kind): those an agent ``viewer`` may know of; for everyone
        (:data:`~fg_env.expr.EVERYONE`) those every agent knows of; for game logic (None) every one, a read of what may
        be hidden from the acting agent when events of the kind may be kept from some (see expr/hidden.py)."""
        world = self.world
        if viewer is None:
            if world.hidden.events_hide(kind):
                world.read_hidden()
            return [e for e in world.log if kind is None or e.kind == kind]
        if not isinstance(viewer, Entity):
            return [e for e in world.log if (kind is None or e.kind == kind) and self._public_event(e)]
        candidates = world.record_events.candidates(world.contract, viewer) if kind == "record" else world.log
        return [e for e in candidates if (kind is None or e.kind == kind) and self.event_visible(e, viewer)]

    def event_visible(self, event: LogEvent, viewer: Entity) -> bool:
        """Whether ``viewer`` may know of ``event``: the one rule for every agent-facing reading of the log. An event
        addressed to others is hidden; a record notification carries the visibility of its retained source entry,
        and so does the action that posted entries not every agent may see (its actor always sees it)."""
        if not event.visible_to(viewer.id):
            return False
        if event.kind == "record":
            return self._retained_visible(event.data.get("record"), event.data.get("entry"), viewer)
        posted = event.data.get("posted") if event.kind == "action" else None
        return not posted or event.actor == viewer.id or all(
            self._retained_visible(record, seq, viewer) for record, seq in posted)

    def _public_event(self, event: LogEvent) -> bool:
        """Whether every agent knows of ``event``: addressed to nobody in particular, and carrying no entry some agent
        may not see."""
        if event.to is not None or (event.kind == "action" and event.data.get("posted")):
            return False
        return event.kind != "record" or event.data.get("record") not in self.world.hidden.records

    def _retained_visible(self, record: Any, seq: Any, viewer: Entity) -> bool:
        """Whether entry ``seq`` of ``record`` is still kept and ``viewer`` may see it."""
        entry = self.world.entry_by_seq.get(seq) if seq is not None else None
        return record in self.world.contract.records and entry is not None \
            and self.entry_visible(record, entry, viewer)

    # -- entities from contract text ------------------------------------------------------------------------------

    def create(self, type_name: str, entity_id: str | None, name: str | None,
               props: dict[str, Any], at: Any, scope: Scope, where: str, evaluate: bool = True) -> Entity:
        """Create an entity. ``props`` values that are expressions are evaluated when ``evaluate`` is
        true (contract text); runtime values from native ops pass ``evaluate=False``. Participant text
        is never evaluated, whatever it looks like. The type's defaults for the rest are evaluated in ``scope``.
        Outside any block of logic (the build) each prop draws from a stream keyed by the entity and the prop, so
        adding an entity or a prop never re-deals another's."""
        world = self.world
        entity = world.new_entity(type_name, entity_id, name, props, at, where)
        declared = world.type_props[type_name]
        own = scope.child(it=entity)  # props read other props of the same entity: `$it.income * 0.3`
        raws = {prop: props[prop] if prop in props else prop_spec.default for prop, prop_spec in declared.items()}
        # Expressions: contract text given here, and a type's defaults. Participant text is never one.
        expressions = {prop: raw for prop, raw in raws.items() if is_expr(raw) and (
            prop not in props or (evaluate and not isinstance(raw, Untrusted)))}
        order = _prop_order(raws, expressions, where)
        luck = world.luck
        keyed = luck.here().rng is None  # the build: each prop's luck is its own, which no other entity or prop shifts
        for prop in order:
            raw = raws[prop]
            try:
                if prop not in expressions:
                    value = copy_value(raw)
                elif keyed:
                    with luck.stream("build", "entity", entity.id, prop):
                        value = compile_expr(raw)(own)
                else:
                    value = compile_expr(raw)(own)
            except ExprError as exc:
                raise RunError(str(exc), f"{where}.props.{prop}") from None
            entity.properties[prop] = world.coerce(declared[prop], plain_value(value), f"{where}.props.{prop}",
                                                   entity.name)
        if order is not raws:  # evaluated out of declaration order: keep the declared order
            entity.properties = {prop: entity.properties[prop] for prop in declared}
        return world.add(entity, where)


def _prop_order(raws: dict[str, Any], expressions: dict[str, Any], where: str) -> Iterable[str]:
    """The order a new entity's props are evaluated in: declaration order (``raws`` itself), but each prop after
    the props it reads through `$it`, whichever order they are written in."""
    if not any("$it." in raw or "$outer." in raw for raw in expressions.values()):
        return raws
    order, circle = default_order({prop: expressions.get(prop) for prop in raws}, "it")
    if circle is not None:
        raise RunError(f"props {' → '.join(circle)} read each other through $it in a circle, so none can be "
                       "worked out first: give one of them a plain value", f"{where}.props")
    return order
