"""Space functions: who is where, who is near, which cells are free, how far round obstacles, and layer values.

Every query reads the space's position index (a few cells or areas, never every entity) and lists
entities in creation order — ``$nearest`` tries candidates nearest first.
"""
from __future__ import annotations

from typing import Any

from ..expr import Call, ExprError, _describe, check_size, function, truthy
from ..world.entity import Entity
from ..world.geometry import SpaceError

__all__: list = []


def _space(call: Call) -> Any:
    space = getattr(call.scope.world, "space", None)
    if space is None:
        raise ExprError(f"${call.name} needs a space: declare `space` (grid, graph or plane)", call.source)
    return space


def _kinds(call: Call, index: int) -> set[str] | None:
    """The types (with subtypes) argument ``index`` names; None when it is left out or null."""
    name = call.arg(index) if len(call) > index else None
    if name is None:
        return None
    world = call.scope.world
    if not isinstance(name, str) or not world.is_type(name):
        raise ExprError(f"${call.name}: {_describe(name)} is not a declared type", call.source)
    kinds: set[str] = world.subtypes_of(name)
    return kinds


def _center(call: Call, index: int) -> tuple[Any, str | None]:
    """The position argument ``index`` gives (an entity gives its own, and is itself left out)."""
    space, value = _space(call), call.arg(index)
    exclude = None
    if isinstance(value, Entity):
        exclude, value = value.id, value.location_id
        if value is None:
            raise ExprError(f"${call.name}: '{exclude}' has no position (at)", call.source)
    try:
        return space.geometry.place(value), exclude
    except SpaceError as exc:
        raise ExprError(f"${call.name}: {exc}", call.source) from None


def _radius(call: Call, index: int) -> float:
    radius = call.number(index)
    if radius < 0:
        raise ExprError(f"${call.name}: the radius must be ≥ 0, got {radius}", call.source)
    return float(radius)


@function("at(position, type?)", "Entities at a position (of `type`, subtypes included), in creation order; "
          "given an entity, the others at its position.", min_args=1, max_args=2)
def _at(call: Call) -> Any:
    center, exclude = _center(call, 0)
    found = _space(call).positions.at(center, _kinds(call, 1))
    return check_size([entity for entity in found if entity.id != exclude], call.source)


@function("near(center, radius, type?)", "Entities within `radius` of a position or entity (itself left out), in "
          "creation order: grid steps by its neighborhood (wrapping on a torus), path length on a graph, straight-line "
          "distance on a plane.", min_args=2, max_args=3)
def _near(call: Call) -> Any:
    center, exclude = _center(call, 0)
    radius = _radius(call, 1)
    return check_size(_space(call).positions.near(center, radius, _kinds(call, 2), exclude), call.source)


@function("nearest(center, type, where?)", "The closest entity of `type` to a position or entity (itself left out) "
          "for which `where` holds ($it), or null; candidates are tried nearest first, ties in creation order.",
          min_args=2, max_args=3, lazy=[2])
def _nearest(call: Call) -> Any:
    center, exclude = _center(call, 0)
    tried = [0]

    def qualifies(entity: Entity) -> bool:
        tried[0] += 1
        return len(call) < 3 or truthy(call.each(2, entity, tried[0] - 1))

    found = _space(call).positions.nearest(center, _kinds(call, 1), exclude, qualifies)
    check_size([None] * tried[0], call.source)  # charge the candidates tried
    return found


@function("path_distance(from, to, open?)", "Grid steps from one position or entity to another along a path that "
          "only crosses cells for which `open` holds ($it is the cell; the start and the goal are always open), e.g. "
          "`not $layer(wall, $it)`; null when no such path exists. Without `open`, every cell is open.",
          min_args=2, max_args=3, lazy=[2])
def _path_distance(call: Call) -> int | None:
    geometry = _space(call).geometry
    if geometry.kind != "grid":
        raise ExprError(f"${call.name} needs a grid (on a graph, $distance is already the shortest path)", call.source)
    start, goal = geometry.cell(_center(call, 0)[0]), geometry.cell(_center(call, 1)[0])
    steps, frontier, tested = {start: 0}, [start], 0
    while frontier and goal not in steps:
        reached = []
        for cell in frontier:
            for other in geometry.neighbor_cells(cell):
                if other in steps:
                    continue
                if other != goal and len(call) > 2:
                    tested += 1
                    if not truthy(call.each(2, geometry.position(other), tested - 1)):
                        steps[other] = -1  # closed: never tried again
                        continue
                steps[other] = steps[cell] + 1
                reached.append(other)
        frontier = reached
    check_size([None] * len(steps), call.source)  # charge the cells explored
    return steps.get(goal)


@function("cells(center?, radius?)", "Every cell of a grid (or place of a graph) with no arguments; with a position "
          "or entity, the cells within `radius` (default 1) of it, not its own — on a grid row by row, on a graph "
          "nearest first.", min_args=0, max_args=2)
def _cells(call: Call) -> Any:
    geometry = _space(call).geometry
    try:
        if not len(call):
            return check_size(geometry.cells(), call.source)
        center, _ = _center(call, 0)
        return check_size(geometry.cells(center, _radius(call, 1) if len(call) > 1 else 1), call.source)
    except SpaceError as exc:
        raise ExprError(f"${call.name}: {exc}", call.source) from None


@function("empty(type?)", "Cells (grid) or places (graph) holding no entity (of `type`), in cell order.",
          min_args=0, max_args=1)
def _empty(call: Call) -> Any:
    space = _space(call)
    if space.geometry.kind == "plane":
        raise ExprError("$empty: a plane has no cells", call.source)
    return check_size(space.positions.empty(_kinds(call, 0)), call.source)


@function("random_empty(type?)", "One cell holding no entity (of `type`), picked at random; null when every cell "
          "is taken.", min_args=0, max_args=1)
def _random_empty(call: Call) -> Any:
    space = _space(call)
    if space.geometry.kind == "plane":
        raise ExprError("$random_empty: a plane has no cells", call.source)
    return space.positions.random_empty(_kinds(call, 0), call.rng)


@function("layer(name, position)", "The value of a declared layer at a position (or at an entity's position).",
          min_args=2, max_args=2)
def _layer(call: Call) -> Any:
    space = _space(call)
    name = call.arg(0)
    try:
        space.layers.spec(name)
    except SpaceError as exc:
        raise ExprError(f"$layer: {exc}", call.source) from None
    center, _ = _center(call, 1)
    return space.layers.values[name][space.geometry.cell(center)]
