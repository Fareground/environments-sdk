"""Which entities of each type are alive, kept current as entities are created and removed.

Reading every entity of a type (``$count(person)``, ``$choice(person)``, an event over ``person``)
never scans the other types, and between creations and removals a type's living members are one
cached list. Lists are in creation order, exactly as a scan of the world would give them.
"""
from __future__ import annotations

import bisect
import heapq
from collections.abc import Iterable

from ..contract import Contract
from ..expr.objects import Entity

__all__ = ["TypeIndex"]


class TypeIndex:
    """Living members of every declared type (subtypes included), in creation order."""

    def __init__(self, contract: Contract):
        self._kinds = {name: tuple(contract.subtypes(name)) for name in contract.types}
        #: exact type → the declared types whose members it counts as (itself and its ancestors)
        self._queries = {kind: [name for name, kinds in self._kinds.items() if kind in kinds]
                         for kind in contract.types}
        #: exact type → its living entities, in creation order: kept current as entities are removed and brought
        #: back, so no read ever passes over the removed
        self._living: dict[str, list[Entity]] = {name: [] for name in contract.types}
        self._alive: dict[str, list[Entity] | None] = {}
        #: entity id → its creation position (only the order matters)
        self.ordinal: dict[str, int] = {}
        self._next = 0
        #: How many entities are alive, of every type.
        self.living = 0

    def created(self, entity: Entity) -> None:
        self.ordinal[entity.id] = self._next
        self._next += 1
        if not entity.alive:  # a removed entity restored with the world: it keeps its place in creation order
            return
        self.living += 1
        self._living[entity.entity_type].append(entity)
        for name in self._queries[entity.entity_type]:
            cached = self._alive.get(name)
            if cached is not None:
                cached.append(entity)  # the newest entity comes last in creation order

    def uncreated(self, entity: Entity) -> None:
        """Undo a creation (the journal undoes changes newest first, so it is its type's last member)."""
        if entity.alive:
            self._drop(entity)
            self.living -= 1
        self.ordinal.pop(entity.id, None)
        self._refresh(entity)

    def changed(self, entity: Entity) -> None:
        """An entity was removed or brought back (an undone removal): it leaves its type's living list, or returns to
        its place in it, and the cached lists that hold its type refresh on next read."""
        if entity.alive:
            self.living += 1
            members = self._living[entity.entity_type]
            order = self.ordinal
            members.insert(bisect.bisect(members, order[entity.id], key=lambda member: order[member.id]), entity)
        else:
            self.living -= 1
            self._drop(entity)
        self._refresh(entity)

    def _drop(self, entity: Entity) -> None:
        members = self._living[entity.entity_type]
        if members and members[-1] is entity:
            members.pop()
        else:
            members.remove(entity)

    def _refresh(self, entity: Entity) -> None:
        for name in self._queries[entity.entity_type]:
            self._alive[name] = None

    def alive(self, type_name: str) -> list[Entity]:
        """The living members of ``type_name`` — a cached list, which callers must not change. A removal refreshes it
        by making a new one, so a caller still going over the old list is never changed under it."""
        cached = self._alive.get(type_name)
        if cached is not None:
            return cached
        parts = [self._living[kind] for kind in self._kinds[type_name]]
        if len(parts) == 1:
            cached = list(parts[0])
        else:
            order = self.ordinal
            cached = list(heapq.merge(*parts, key=lambda entity: order[entity.id]))
        self._alive[type_name] = cached
        return cached

    def rebuild(self, entities: Iterable[Entity]) -> None:
        """Index ``entities`` (in creation order, removed ones included) from scratch, e.g. after a restore."""
        self._living = {name: [] for name in self._living}
        self._alive = {}
        self.ordinal = {}
        self._next = 0
        self.living = 0
        for entity in entities:
            self.created(entity)
