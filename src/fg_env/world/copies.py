"""A copy of a live world that plays on apart from it: the world's part of a copy of the run (``RunState.copy``).

Everything the run changes is copied — entities and their properties, global properties, links, records, the log, the
schedule of effects, counters, the rules' journaled bookkeeping, the open journal itself (its undo ops are data, so an
atomic turn part-way through its changes copies too), luck, physics, the space and its layers, exposures, assets — and
every index over it is rebuilt or copied. What only the contract and its build decide (types, what is private, compiled
physics, pattern parameters) is shared: nothing changes it while a run plays. Caches start empty — the copy's
:class:`~fg_env.world.evaluation.EvalContext` is its own, and what mechanisms keep in ``world.caches`` stays behind —
and the callbacks the run wires into its world (its facts, its lifecycle and join hooks, a chance chooser) are left for
the copy's run to set. ``tests/kernel/test_kernel_copies.py`` holds a copy to sharing nothing mutable with its original.
"""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from ..expr.objects import Entity
from .evaluation import EvalContext
from .journal import Journal
from .layers import Layers
from .parts import Entry
from .positions import PositionIndex
from .space import Spatial
from .type_index import TypeIndex

if TYPE_CHECKING:
    from ..physics.model import PhysicsModel
    from .store import World

__all__ = ["copy_world"]

#: What the contract and the build decide, shared by a copy (never changed while a run plays).
_SHARED = frozenset({
    "contract", "inputs", "arm", "physics_writes", "entity_dynamics", "start", "type_props", "hidden", "private_names",
    "private_metrics", "_subtypes"})
#: Wired by the run, or caches: a copy starts without them.
_UNSET = {"lifecycle": None, "joined": None, "facts": None, "chance_picker": None, "caches": dict}
#: Copied by what they are (below).
_COPIED = frozenset({
    "luck", "entities", "props", "links", "link_fields", "adjacent", "records_store", "entry_by_seq", "entity_briefs",
    "log", "physics", "scheduled", "reactions", "end_request", "fired_once", "written", "touched", "journal", "types",
    "exposures", "assets", "evaluation", "space", "record_authors", "record_events", "patterns"})


def copy_world(source: World) -> World:
    """A copy of ``source`` that shares nothing it changes (see the module docstring). Taken between blocks of
    logic: no sync loop is holding writes back and no sealed choices are committing."""
    assert source.buffer is None and source.watched_writes is None, "a world is copied between blocks of logic"
    world = type(source).__new__(type(source))
    data = world.__dict__
    for key, value in vars(source).items():
        if key in _SHARED:
            data[key] = value
        elif key in _UNSET:
            empty = _UNSET[key]
            data[key] = empty() if callable(empty) else empty
        elif key not in _COPIED:
            data[key] = _plain_copy(value)  # plain data: counters, metrics, the rules' bookkeeping
    entities = {key: _copy_entity(entity) for key, entity in source.entities.items()}
    records, by_seq = _copy_records(source, world)
    journal = Journal(world)
    journal._undo = [(tuple(_plain_copy(part) for part in op), version) for op, version in source.journal._undo]
    journal.version, journal._settled = source.journal.version, source.journal._settled
    types = TypeIndex.__new__(TypeIndex)
    types.__dict__.update(_kinds=source.types._kinds, _queries=source.types._queries, _members=source.types._members)
    types.rebuild(entities.values())
    data.update(
        luck=source.luck.copy(), entities=entities, props=_plain_copy(source.props),
        links={kind: dict(edges) for kind, edges in source.links.items()},
        link_fields={kind: {key: _plain_copy(value) for key, value in fields.items()}
                     for kind, fields in source.link_fields.items()},
        adjacent={kind: {key: dict(counts) for key, counts in by_id.items()}
                  for kind, by_id in source.adjacent.items()},
        records_store=records, entry_by_seq=by_seq, entity_briefs=dict(source.entity_briefs), log=list(source.log),
        physics=_copy_physics(source.physics), scheduled=list(source.scheduled), reactions=list(source.reactions),
        end_request=_plain_copy(source.end_request), fired_once=set(source.fired_once), written=set(source.written),
        touched=None if source.touched is None else dict(source.touched), journal=journal, types=types,
        exposures=None if source.exposures is None else source.exposures.copy(), assets=source.assets.copy())
    world.evaluation = EvalContext(world)
    world.evaluation.caching = source.evaluation.caching
    world.space = None if source.space is None else _copy_space(source.space, world)
    world.rebuild_record_index()
    world.rebuild_event_index()
    world.patterns = source.patterns.bound_to(world)
    return world


def _plain_copy(value: Any) -> Any:
    """``value`` with its lists and maps copied (what a world stores is plain data; entries and entities are copied
    by their own owners)."""
    if isinstance(value, list):
        return [_plain_copy(item) for item in value]
    if isinstance(value, dict) and not isinstance(value, Entry):
        return {key: _plain_copy(item) for key, item in value.items()}
    return value


def _copy_entity(source: Entity) -> Entity:
    entity = Entity.__new__(Entity)
    entity.__dict__.update(source.__dict__)
    entity.properties = _plain_copy(source.properties)
    return entity


def _copy_records(source: World, world: World) -> tuple[dict[str, list[Entry]], dict[int, Entry]]:
    records: dict[str, list[Entry]] = {}
    by_seq: dict[int, Entry] = {}
    for name, rows in source.records_store.items():
        copies = []
        for row in rows:
            entry = Entry({key: _plain_copy(value) for key, value in row.items()})
            entry.world = world
            copies.append(entry)
            if source.entry_by_seq.get(row["seq"]) is row:
                by_seq[row["seq"]] = entry
        records[name] = copies
    return records, by_seq


def _copy_physics(source: PhysicsModel | None) -> PhysicsModel | None:
    """The continuous state (each variable's value, the parameters, the time); the compiled equations are shared."""
    if source is None:
        return None
    physics = type(source).__new__(type(source))
    physics.__dict__.update(source.__dict__)
    physics.variables = {name: dataclasses.replace(variable) for name, variable in source.variables.items()}
    physics.params = dict(source.params)
    return physics


def _copy_space(source: Spatial, world: World) -> Spatial:
    """The space of ``world``: the same geometry and capacities, its own layer values and position index."""
    space = Spatial.__new__(Spatial)
    space.__dict__.update(source.__dict__)
    space._world = world
    layers = Layers.__new__(Layers)
    layers.__dict__.update(source.layers.__dict__)
    layers.values = {name: list(values) for name, values in source.layers.values.items()}
    space.layers = layers
    space.positions = PositionIndex(source.geometry, world.types)
    space.positions.rebuild(world.entities.values())
    return space
