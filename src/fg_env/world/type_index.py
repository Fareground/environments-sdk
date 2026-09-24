"""Which entities of each type are alive, kept current as entities are created and removed.

Reading every entity of a type (``$count(person)``, ``$choice(person)``, an event over ``person``)
never scans the other types, and between creations and removals a type's living members are one
cached list. Lists are in creation order, exactly as a scan of the world would give them.
"""
from __future__ import annotations

import heapq
from collections.abc import Iterable

from ..contract import Contract
from ..expr.objects import Entity

__all__ = ["TypeIndex"]


class TypeIndex:
    """Members of every declared type (subtypes included), in creation order."""

    def __init__(self, contract: Contract):
        self._kinds = {name: tuple(contract.subtypes(name)) for name in contract.types}
        #: exact type → the declared types whose members it counts as (itself and its ancestors)
        self._queries = {kind: [name for name, kinds in self._kinds.items() if kind in kinds]
                         for kind in contract.types}
        #: exact type → every entity ever made of it, in creation order (removed ones until compacted)
        self._members: dict[str, list[Entity]] = {name: [] for name in contract.types}
        self._alive: dict[str, list[Entity] | None] = {}
        #: entity id → its creation position (only the order matters)
        self.ordinal: dict[str, int] = {}
        self._next = 0
        #: How many entities are alive, of every type.
        self.living = 0

    def created(self, entity: Entity) -> None:
        self.ordinal[entity.id] = self._next
        self._next += 1
        self.living += entity.alive
        self._members[entity.entity_type].append(entity)
        for name in self._queries[entity.entity_type]:
            cached = self._alive.get(name)
            if cached is not None:
                cached.append(entity)  # the newest entity comes last in creation order

    def uncreated(self, entity: Entity) -> None:
        """Undo a creation (the journal undoes changes newest first, so it is its type's last member)."""
        members = self._members[entity.entity_type]
        if members and members[-1] is entity:
            members.pop()
        elif entity in members:
            members.remove(entity)
        self.ordinal.pop(entity.id, None)
        self.living -= entity.alive
        self._refresh(entity)

    def changed(self, entity: Entity) -> None:
        """An entity was removed or brought back: the cached lists that hold its type refresh on next read."""
        self.living += 1 if entity.alive else -1
        self._refresh(entity)

    def _refresh(self, entity: Entity) -> None:
        for name in self._queries[entity.entity_type]:
            self._alive[name] = None

    def alive(self, type_name: str, compact: bool) -> list[Entity]:
        """The living members of ``type_name`` — the cached list itself, which callers must not change.
        ``compact`` (nothing left to roll back) also forgets removed members for good."""
        cached = self._alive.get(type_name)
        if cached is not None:
            return cached
        kinds = self._kinds[type_name]
        parts = []
        for kind in kinds:
            living = [entity for entity in self._members[kind] if entity.alive]
            if compact:
                self._members[kind] = list(living)
            parts.append(living)
        if len(parts) == 1:
            cached = parts[0]
        else:
            order = self.ordinal
            cached = list(heapq.merge(*parts, key=lambda entity: order[entity.id]))
        self._alive[type_name] = cached
        return cached

    def rebuild(self, entities: Iterable[Entity]) -> None:
        """Index ``entities`` (in creation order) from scratch, e.g. after a restore."""
        self._members = {name: [] for name in self._members}
        self._alive = {}
        self.ordinal = {}
        self._next = 0
        self.living = 0
        for entity in entities:
            self.created(entity)
