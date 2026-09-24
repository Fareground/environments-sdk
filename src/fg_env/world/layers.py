"""Layers: a value on every cell of a grid (or place of a graph) without an entity per cell.

Values live in one flat list per layer, in cell order. Whole-layer changes (set every cell, diffuse,
decay) compute a new list from the old one, so every cell reads the values as they were before the
change; the world swaps the list in as one journaled change.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ..contract import LAYER_TYPES, LayerSpec
from ..expr import is_expr
from .geometry import Geometry, SpaceError

__all__ = ["Layers"]

#: Evaluates a layer's default expression for one cell: ``start(expression, position)``.
Start = Callable[[str, Any], Any]


class Layers:
    def __init__(self, geometry: Geometry, specs: dict[str, LayerSpec], start: Start):
        self.geometry = geometry
        self.specs = specs
        self.values: dict[str, list[Any]] = {}
        if specs and geometry.kind == "plane":
            raise SpaceError("layers need a grid or a graph (a plane has no cells)")
        for name, spec in specs.items():
            if spec.type not in LAYER_TYPES:
                raise SpaceError(f"layer '{name}' has unknown type '{spec.type}' ({', '.join(LAYER_TYPES)})")
            if is_expr(spec.default):
                self.values[name] = [self.coerce(name, start(spec.default, geometry.position(cell)))
                                     for cell in range(geometry.cell_count)]
            else:
                self.values[name] = [self.coerce(name, spec.default)] * geometry.cell_count

    def spec(self, name: Any) -> LayerSpec:
        spec = self.specs.get(name) if isinstance(name, str) else None
        if spec is None:
            raise SpaceError(f"'{name}' is not a declared layer (layers: {', '.join(self.specs) or 'none'})")
        return spec

    def coerce(self, name: str, value: Any) -> Any:
        """``value`` as layer ``name`` stores it: its type enforced, and a number past its min or max refused
        (:class:`~fg_env.world.live.OutOfBounds`), never clamped."""
        spec = self.spec(name)
        if spec.type == "bool":
            if not isinstance(value, bool):
                raise SpaceError(f"layer '{name}' holds true or false, got {value!r}")
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SpaceError(f"layer '{name}' holds finite numbers, got {value!r}")
        from .live import within_bounds

        within_bounds(spec, value, f"layer '{name}'")
        if spec.type == "int":
            if float(value) != int(value):
                raise SpaceError(f"layer '{name}' holds whole numbers, got {value!r}")
            value = int(value)
        return value

    def diffused(self, name: str, rate: float) -> list[Any]:
        """Every cell hands ``rate`` of its value out equally to its neighbourhood (a grid cell at an edge
        that does not wrap keeps the shares of the neighbours it lacks; a place shares among its neighbours)."""
        values = self._numeric(name, "diffuse")
        geometry = self.geometry
        full = geometry.neighborhood_size() if geometry.kind == "grid" else 0
        out = list(values)
        for cell, value in enumerate(values):
            if not value:
                continue
            neighbours = geometry.neighbor_cells(cell)
            if not neighbours:
                continue
            share = value * rate / (full or len(neighbours))
            out[cell] -= share * len(neighbours)
            for other in neighbours:
                out[other] += share
        return self._kept(name, out)

    def decayed(self, name: str, rate: float) -> list[Any]:
        """Every cell loses ``rate`` of its value."""
        keep = 1.0 - rate
        return self._kept(name, [value * keep for value in self._numeric(name, "decay")])

    def _kept(self, name: str, values: list[float]) -> list[float]:
        """Diffused or decayed values held within the layer's min/max. The engine computed them, not a rule the
        author can guard (float rounding at a bound, a decay under a positive min), so like integrated physics they
        stay inside the bounds instead of being refused."""
        spec = self.spec(name)
        low = -math.inf if spec.min is None else spec.min
        high = math.inf if spec.max is None else spec.max
        return [min(max(value, low), high) for value in values]

    def _numeric(self, name: str, what: str) -> list[Any]:
        if self.spec(name).type != "number":
            raise SpaceError(f"`{what}` needs a number layer; '{name}' is {self.spec(name).type}")
        return self.values[name]
