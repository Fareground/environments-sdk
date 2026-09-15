"""The run's space: its geometry, where every entity is, how many fit in a cell, and its layers —
every change journaled through the world, so an action that is refused leaves the space as it was.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..entity import Entity
from .contract import Space
from .errors import RunError
from .expr import ExprError, compile_expr, is_expr
from .geometry import Geometry, SpaceError
from .layers import Layers
from .positions import PositionIndex
from . import layer_effect as _layer_effect  # noqa: F401  (registers the `layer` effect)

if TYPE_CHECKING:
    from .world import SdkWorld

__all__ = ["Spatial", "position_of"]


class Spatial:
    def __init__(self, world: "SdkWorld", spec: Space):
        self._world = world
        try:
            self.geometry = Geometry(spec, self._resolve)
            self.positions = PositionIndex(self.geometry, world.types)
            self.capacity, self.capacities = self._capacity(spec.capacity)
            self.layers = Layers(self.geometry, spec.layers, self._start)
        except SpaceError as exc:
            raise RunError(str(exc), "space") from None

    def _resolve(self, raw: Any, path: str) -> Any:
        try:
            return compile_expr(raw)(self._world.scope()) if is_expr(raw) else raw
        except ExprError as exc:
            raise RunError(str(exc), path) from None

    def _start(self, raw: str, position: Any) -> Any:
        try:
            return compile_expr(raw)(self._world.scope(cell=position))
        except ExprError as exc:
            raise RunError(str(exc), "space.layers") from None

    def _capacity(self, raw: Any) -> "tuple[Optional[int], Dict[str, int]]":
        if raw is None:
            return None, {}
        if self.geometry.kind == "plane":
            raise SpaceError("capacity needs a grid or a graph (a plane has no cells)")
        if not isinstance(raw, dict):
            return _count(self._resolve(raw, "space.capacity"), "space.capacity"), {}
        limits = {}
        for type_name, limit in raw.items():
            if type_name not in self._world.contract.types:
                raise SpaceError(f"space.capacity: '{type_name}' is not a declared type")
            limits[type_name] = _count(self._resolve(limit, f"space.capacity.{type_name}"), f"space.capacity.{type_name}")
        return None, limits

    # -- entities --------------------------------------------------------------------------

    def place(self, at: Any, where: str) -> Any:
        try:
            return self.geometry.place(at)
        except SpaceError as exc:
            raise RunError(str(exc), where) from None

    def no_room(self, entity: Entity, position: Any) -> Optional[str]:
        """Why ``entity`` cannot be at ``position`` (the cell is full), or None when it fits."""
        if self.capacity is None and not self.capacities:
            return None
        others = [e for e in self.positions.occupants(self.geometry.cell(position)) if e.id != entity.id]
        if self.capacity is not None and len(others) >= self.capacity:
            return f"{_shown(position)} is full: it holds {len(others)} of {self.capacity}"
        world = self._world
        for type_name, limit in self.capacities.items():
            if world.is_a(entity.entity_type, type_name):
                held = sum(1 for e in others if world.is_a(e.entity_type, type_name))
                if held >= limit:
                    return f"{_shown(position)} is full: it holds {held} {type_name} of {limit}"
        return None

    # -- layers ----------------------------------------------------------------------------

    def cell_of(self, position: Any, where: str) -> int:
        """The cell number of a position (or of an entity's position)."""
        located = position_of(position)
        if located is None:
            raise RunError(f"expected a position or an entity with one, got {position!r}", where)
        return self.geometry.cell(self.place(located, where))

    def write(self, name: str, cell: int, value: Any, where: str) -> None:
        """Set one cell of a layer (journaled; held back until a sync event commits)."""
        try:
            new = self.layers.coerce(name, value)
        except SpaceError as exc:
            raise RunError(str(exc), where) from None
        world = self._world
        if world.buffer is not None:
            world.buffer.write(("layer", name, cell), new, lambda: self.write(name, cell, new, where), where)
            return
        values = self.layers.values[name]
        old = values[cell]
        values[cell] = new
        world.journal.push(lambda: values.__setitem__(cell, old))

    def replace(self, name: str, values: List[Any]) -> None:
        """Swap in a whole layer's new values as one journaled change."""
        store = self.layers.values
        old = store[name]
        store[name] = values
        self._world.journal.push(lambda: store.__setitem__(name, old))

    def state(self) -> Dict[str, List[Any]]:
        return {name: list(values) for name, values in self.layers.values.items()}

    def restore(self, state: Dict[str, List[Any]]) -> None:
        for name, values in state.items():
            if name in self.layers.values and len(values) == self.geometry.cell_count:
                self.layers.values[name] = list(values)


def position_of(value: Any) -> Any:
    """A position, or the position of an entity."""
    return value.location_id if isinstance(value, Entity) else value


def _count(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpaceError(f"{where} must be a whole number ≥ 0, got {value!r}")
    return value


def _shown(position: Any) -> str:
    return f"[{position[0]}, {position[1]}]" if isinstance(position, list) else f"'{position}'"
