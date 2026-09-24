"""Where every entity is: living entities with a position, bucketed by cell (grid, graph) or by area
(plane), so neighbourhood queries read a few buckets instead of every entity.

The world keeps the index current on every creation, move and removal — and on their undo — and
rebuilds it after a restore. Query results are in creation order (nearest first for ``nearest``),
so they never depend on how the buckets happen to be filled.
"""
from __future__ import annotations

import heapq
import math
from typing import Any, Callable, Collection, Dict, Iterable, List, Optional, Tuple

from .entity import Entity
from .geometry import Geometry
from .type_index import TypeIndex

__all__ = ["PositionIndex"]

#: Plane areas per side.
_AREAS = 32
#: Random cells tried before listing the empty ones, on a space that is mostly empty.
_TRIES = 64

Kinds = Optional[Collection[str]]


class PositionIndex:
    def __init__(self, geometry: Geometry, types: TypeIndex):
        self.geometry = geometry
        self._types = types
        #: bucket → entity id → entity
        self._buckets: Dict[Any, Dict[str, Entity]] = {}
        self._bucket_of: Dict[str, Any] = {}

    # -- upkeep ----------------------------------------------------------------------------

    def add(self, entity: Entity) -> None:
        if entity.alive and entity.location_id is not None:
            bucket = self.bucket(entity.location_id)
            self._buckets.setdefault(bucket, {})[entity.id] = entity
            self._bucket_of[entity.id] = bucket

    def discard(self, entity: Entity) -> None:
        bucket = self._bucket_of.pop(entity.id, None)
        if bucket is not None:
            members = self._buckets[bucket]
            del members[entity.id]
            if not members:
                del self._buckets[bucket]

    def rebuild(self, entities: Iterable[Entity]) -> None:
        self._buckets, self._bucket_of = {}, {}
        for entity in entities:
            self.add(entity)

    def bucket(self, position: Any) -> Any:
        geometry = self.geometry
        if geometry.kind != "plane":
            return geometry.cell(position)
        return (min(_AREAS - 1, int(position[0] * _AREAS / geometry.width)),
                min(_AREAS - 1, int(position[1] * _AREAS / geometry.height)))

    # -- queries ----------------------------------------------------------------------------

    def at(self, position: Any, kinds: Kinds) -> List[Entity]:
        found: Iterable[Entity] = self._buckets.get(self.bucket(position), {}).values()
        if self.geometry.kind == "plane":
            found = [entity for entity in found if entity.location_id == position]
        return self._ordered(entity for entity in found if kinds is None or entity.entity_type in kinds)

    def occupants(self, cell: Any) -> List[Entity]:
        """Entities in one grid cell or graph place, in no particular order."""
        return list(self._buckets.get(cell, {}).values())

    def near(self, center: Any, radius: float, kinds: Kinds, exclude: Optional[str]) -> List[Entity]:
        """Entities within ``radius`` of a stored position (itself excluded when it is an entity's)."""
        geometry = self.geometry
        buckets: Optional[List[Any]]
        if geometry.kind == "plane":
            buckets = self._areas(center, radius)
        elif geometry.kind == "grid" and (2 * radius + 1) ** 2 > len(self._bucket_of):
            buckets = None  # a wide search: reading every entity is cheaper than every cell
        else:
            buckets = geometry.cells_within(center, radius)
        # Cells within the radius hold only entities within it; areas and the whole pool are measured.
        measure = buckets is None or geometry.kind == "plane"
        found = [entity for entity in self._pool(buckets)
                 if entity.id != exclude and (kinds is None or entity.entity_type in kinds)
                 and (not measure or geometry.distance(center, entity.location_id) <= radius)]
        return self._ordered(found)

    def nearest(self, center: Any, kinds: Kinds, exclude: Optional[str],
                qualifies: Callable[[Entity], bool]) -> Optional[Entity]:
        """The closest entity ``qualifies`` accepts, trying candidates nearest first (ties in creation
        order); each candidate is tried at most once."""
        geometry = self.geometry
        tried: set = set()
        if geometry.kind == "grid":
            radius = 1
            while (2 * radius + 1) ** 2 <= 4 * len(self._bucket_of) and radius < geometry.rows + geometry.cols:
                ring = self._pool(geometry.cells_within(center, radius))
                chosen = self._first(center, ring, radius, kinds, exclude, qualifies, tried)
                if chosen is not None:
                    return chosen
                radius *= 2
        return self._first(center, self._pool(None), math.inf, kinds, exclude, qualifies, tried)

    def _first(self, center: Any, pool: Iterable[Entity], radius: float, kinds: Kinds, exclude: Optional[str],
               qualifies: Callable[[Entity], bool], tried: set) -> Optional[Entity]:
        geometry, order = self.geometry, self._types.ordinal
        keyed: List[Tuple[float, int, Entity]] = []
        for entity in pool:
            if entity.id == exclude or entity.id in tried or (kinds is not None and entity.entity_type not in kinds):
                continue
            distance = geometry.distance(center, entity.location_id)
            if distance <= radius:
                keyed.append((distance, order[entity.id], entity))
        heapq.heapify(keyed)
        while keyed:
            _, _, entity = heapq.heappop(keyed)
            tried.add(entity.id)
            if qualifies(entity):
                return entity
        return None

    def empty(self, kinds: Kinds) -> List[Any]:
        geometry = self.geometry
        return [geometry.position(cell) for cell in range(geometry.cell_count) if self._empty(cell, kinds)]

    def random_empty(self, kinds: Kinds, rng: Any) -> Optional[Any]:
        """A cell holding no entity (of ``kinds``) picked at random from the run's stream, or None."""
        geometry = self.geometry
        total = geometry.cell_count
        if 2 * len(self._buckets) < total:  # mostly empty: draw cells until an empty one comes up
            for _ in range(_TRIES):
                cell = rng.randrange(total)
                if self._empty(cell, kinds):
                    return geometry.position(cell)
        cells = [cell for cell in range(total) if self._empty(cell, kinds)]
        return geometry.position(cells[rng.randrange(len(cells))]) if cells else None

    def _empty(self, cell: int, kinds: Kinds) -> bool:
        members = self._buckets.get(cell)
        return not members or (kinds is not None and not any(e.entity_type in kinds for e in members.values()))

    # -- helpers ------------------------------------------------------------------------------

    def _pool(self, buckets: Optional[Iterable[Any]]) -> List[Entity]:
        if buckets is None:
            return [entity for members in self._buckets.values() for entity in members.values()]
        return [entity for bucket in buckets for entity in self._buckets.get(bucket, {}).values()]

    def _ordered(self, entities: Iterable[Entity]) -> List[Entity]:
        order = self._types.ordinal
        return sorted(entities, key=lambda entity: order[entity.id])

    def _areas(self, center: Any, radius: float) -> Optional[List[Tuple[int, int]]]:
        """Plane areas that may hold a position within ``radius`` of ``center``; None for all of them."""
        geometry = self.geometry
        reach_x = math.ceil(radius * _AREAS / geometry.width)
        reach_y = math.ceil(radius * _AREAS / geometry.height)
        if 2 * reach_x + 1 >= _AREAS or 2 * reach_y + 1 >= _AREAS:
            return None
        bx, by = self.bucket(center)
        areas: Dict[Tuple[int, int], None] = {}
        for x in range(bx - reach_x, bx + reach_x + 1):
            for y in range(by - reach_y, by + reach_y + 1):
                if geometry.torus:
                    areas[(x % _AREAS, y % _AREAS)] = None
                elif 0 <= x < _AREAS and 0 <= y < _AREAS:
                    areas[(x, y)] = None
        return list(areas)
