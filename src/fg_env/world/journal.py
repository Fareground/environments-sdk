"""The journal: every change to the world as a typed undo record, so any block of changes can be undone exactly.

Each journaled write pushes one undo op — a plain tuple ``(kind, *what it replaced)`` — and :meth:`Journal.rollback`
undoes ops newest first by their kind (:data:`UNDO`, the complete list). What is not journaled is never undone: the
luck a run has drawn (``world.luck``: its streams and firings), and what a change outside the journal moved
(:meth:`Journal.bump`: metrics sampling, physics steps). How many entities each block created (``world.luck.births``) is
journaled: an undone creation gives its count back, so it shifts no later creation's luck.

Ops name entities, records and events by id or sequence number, not by the objects themselves, and entries a change
dropped are kept as data, so an open journal is data about the world it undoes.
"""
from __future__ import annotations

import bisect
import heapq
import itertools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from . import links as _links

if TYPE_CHECKING:
    from .store import World

__all__ = ["Journal", "UNDO"]

#: One journaled change: its kind (a key of :data:`UNDO`), then what undoing it needs.
Op = tuple[Any, ...]

#: Every journal's versions come from one count, so no two different states anywhere share a version.
_VERSIONS = itertools.count(1)


class Journal:
    """The undo ops of ``world`` since the last commit (:meth:`clear`), and the world's version."""

    def __init__(self, world: World) -> None:
        self.world = world
        #: Each change's undo op, with the version it replaced.
        self._undo: list[tuple[Op, int]] = []
        #: The world's version: a change moves it to one never used before, and undoing a change brings back the one it
        #: replaced (a tried call rolled back leaves the world, and every cache of it, as it was). Equal versions mean
        #: an equal world.
        self.version = 0
        #: Changes below this position were followed by a change outside the journal (:meth:`bump`): undoing them does
        #: not bring back the world of their version.
        self._settled = 0
        #: Open :meth:`held` blocks, and whether a :meth:`clear` inside them waits for them to finish.
        self.holding = 0
        self._clear_due = False

    def mark(self) -> int:
        return len(self._undo)

    def push(self, op: Op) -> None:
        """Record one change by what undoes it (see :data:`UNDO`)."""
        self._undo.append((op, self.version))
        self.version = next(_VERSIONS)

    def bump(self) -> None:
        """Record a change made outside the journal (metrics sampling, physics): no earlier version comes back."""
        self.version = next(_VERSIONS)
        self._settled = len(self._undo)

    def rollback(self, mark: int) -> None:
        world = self.world
        while len(self._undo) > mark:
            op, before = self._undo.pop()
            UNDO[op[0]](world, op)
            self.version = before if len(self._undo) >= self._settled else next(_VERSIONS)
        self._settled = min(self._settled, len(self._undo))

    def clear(self) -> None:
        if self.holding:
            self._clear_due = True
            return
        self._undo.clear()
        self._settled = 0
        self._clear_due = False

    @contextmanager
    def held(self) -> Iterator[None]:
        """Keep every change made inside the block undoable until it ends: commits inside it (an agent's action and
        the events it sets off) clear the journal only once the block finishes without an error, so a failure
        anywhere in it can still undo all of it."""
        self.holding += 1
        try:
            yield
        except BaseException:
            self.holding -= 1
            if not self.holding:
                self._clear_due = False  # the caller undoes the block instead
            raise
        self.holding -= 1
        if not self.holding and self._clear_due:
            self.clear()


# -- the undo of each kind of change ---------------------------------------------------------------------------------


def _prop(world: World, op: Op) -> None:
    _, entity_id, prop, old = op
    entity = world.entities[entity_id]
    entity.properties[prop] = old
    world.touch_entity(entity)  # an undo can bring back values no invariant check has seen together


def _world_prop(world: World, op: Op) -> None:
    _, prop, old = op
    world.props[prop] = old


def _physics_variable(world: World, op: Op) -> None:
    _, name, old = op
    assert world.physics is not None
    world.physics.variables[name].value = old


def _physics_param(world: World, op: Op) -> None:
    _, name, old = op
    assert world.physics is not None
    world.physics.params[name] = old


def _counter(world: World, op: Op) -> None:
    _, type_name, old = op
    if old is None:  # the type had no counter before: an undone create leaves the world as it was
        world.counters.pop(type_name, None)
    else:
        world.counters[type_name] = old


def _birth(world: World, op: Op) -> None:
    _, site, old = op
    if old:
        world.luck.births[site] = old
    else:  # the site had created nothing this round: an undone create leaves no count
        world.luck.births.pop(site, None)


def _create(world: World, op: Op) -> None:
    entity = world.entities.pop(op[1])
    world.types.uncreated(entity)
    if world.space is not None:
        world.space.positions.discard(entity)


def _remove(world: World, op: Op) -> None:
    entity = world.entities[op[1]]
    entity.alive = True
    world.types.changed(entity)
    world.touch_entity(entity)
    if world.space is not None:
        world.space.positions.add(entity)


def _move(world: World, op: Op) -> None:
    _, entity_id, old = op
    entity, space = world.entities[entity_id], world.space
    if space is None:
        entity.location_id = old
        return
    space.positions.discard(entity)
    entity.location_id = old
    space.positions.add(entity)


def _link(world: World, op: Op) -> None:
    _, kind, key, missing, old, had_fields, old_fields = op
    edges, table = world.links[kind], world.link_fields[kind]
    if missing:
        edges.pop(key, None)
        _links._adjust(world, kind, key, -1)
    else:
        edges[key] = old
    if had_fields:
        table[key] = old_fields
    else:
        table.pop(key, None)


def _link_field(world: World, op: Op) -> None:
    _, kind, key, name, old = op
    world.link_fields[kind][key][name] = old


def _unlink(world: World, op: Op) -> None:
    _, kind, key, old, old_fields, (at, fields_at, rows) = op
    _links.reinsert(world.links[kind], key, old, at)
    if old_fields is not None and fields_at is not None:
        _links.reinsert(world.link_fields[kind], key, old_fields, fields_at)
    for x, y, position, count in rows:
        row = world.adjacent[kind].setdefault(x, {})
        if y in row:  # still linked another way: only the count went down
            row[y] = count
        else:
            _links.reinsert(row, y, count, position)


def _post(world: World, op: Op) -> None:
    _, record, seq, dropped, notices, moves = op
    rows = world.records_store[record]
    for index in range(len(rows) - 1, -1, -1):  # a rolled-back entry sits near the end
        if rows[index]["seq"] == seq:
            entry = rows.pop(index)
            world.record_authors.remove(record, entry)
            break
    world.entry_by_seq.pop(seq, None)
    if dropped:  # back in their places: the oldest, or (each reader keeping its own) anywhere among the kept
        rows[:] = sorted([*rows, *dropped], key=lambda row: row["seq"])
    for old in dropped:
        world.entry_by_seq[old["seq"]] = old
        world.record_authors.add(record, old)
    windows = world.kept.get(record)
    if windows is not None:
        windows.undo(seq, moves)
    log = world.log
    for number in notices:  # the dropped entries' notifications, back in the index with them
        at = bisect.bisect_left(log, number, key=lambda event: event.seq)
        world.record_events.add(log[at], world.entry_by_seq)
    world.record_seq -= 1


def _emit(world: World, op: Op) -> None:
    _, seq, record_key = op
    log = world.log
    for index in range(len(log) - 1, -1, -1):  # rolled-back events sit near the end
        if log[index].seq == seq:
            event = log.pop(index)
            if record_key is not None:
                world.record_events.remove(event, record_key)
            world.event_seq -= 1
            break


def _first(world: World, op: Op) -> None:
    _, index, since = op
    log = world.log
    log.append(log.pop(index))  # the announcement back at the end, and every event after ``since`` numbered as before
    for offset, item in enumerate(log[index:]):
        item.seq = since + 1 + offset


def _schedule(world: World, op: Op) -> None:
    entry = op[1]
    if entry in world.scheduled:
        world.scheduled.remove(entry)
        heapq.heapify(world.scheduled)
    world.schedule_seq = entry[1] - 1  # the number it took


def _reaction(world: World, op: Op) -> None:
    reactions = world.reactions
    for index in range(len(reactions) - 1, -1, -1):
        if reactions[index] == op[1]:
            del reactions[index]
            break


def _wake(world: World, op: Op) -> None:
    _, entity_id, had, old = op
    if had:
        world.wake_requests[entity_id] = old
    else:
        world.wake_requests.pop(entity_id, None)


def _end(world: World, op: Op) -> None:
    world.end_request = None


def _brief(world: World, op: Op) -> None:
    _, entity_id, had, old = op
    if had:
        world.entity_briefs[entity_id] = old
    else:
        world.entity_briefs.pop(entity_id, None)


def _cell(world: World, op: Op) -> None:
    _, name, cell, old = op
    assert world.space is not None
    world.space.layers.values[name][cell] = old


def _layer(world: World, op: Op) -> None:
    _, name, old = op
    assert world.space is not None
    world.space.layers.values[name] = old


def _fired(world: World, op: Op) -> None:
    world.fired_once.discard(op[1])


def _armed(world: World, op: Op) -> None:
    _, index, old = op
    if old is None:
        world.armed.pop(index, None)
    else:
        world.armed[index] = old


def _use(world: World, op: Op) -> None:
    _, actor_id, action = op
    world.used_round[actor_id][action] -= 1


#: How each kind of op is undone: the complete list of what the journal can take back.
UNDO: dict[str, Callable[[World, Op], None]] = {
    "prop": _prop, "world": _world_prop, "physics_variable": _physics_variable, "physics_param": _physics_param,
    "counter": _counter, "birth": _birth, "create": _create, "remove": _remove, "move": _move, "link": _link,
    "link_field": _link_field, "unlink": _unlink, "post": _post, "emit": _emit, "first": _first,
    "schedule": _schedule,
    "reaction": _reaction, "wake": _wake, "end": _end, "brief": _brief, "cell": _cell, "layer": _layer,
    "fired": _fired, "armed": _armed, "use": _use,
}
