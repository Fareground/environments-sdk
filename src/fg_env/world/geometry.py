"""The shape of the declared space: which positions exist, how far apart two are, which cells lie near.

A grid has rows × cols cells whose neighbourhood is von Neumann (4), Moore (8) or hex (6, axial
coordinates), optionally wrapping round as a torus; a graph has named places joined by weighted edges;
a plane is a width × height area. Sizes may be expressions over ``$inputs``, resolved once at build.
"""
from __future__ import annotations

import heapq
import math
from collections.abc import Callable
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any

from ..errors import RunError

if TYPE_CHECKING:
    from ..contract import Space

__all__ = ["EDGE_SHAPE", "Geometry", "SpaceError", "NEIGHBORHOODS", "MAX_CELLS", "graph_edge"]

NEIGHBORHOODS = ("von_neumann", "moore", "hex")
#: Most cells a grid may have.
MAX_CELLS = 1_000_000
#: Neighbourhoods (cell, radius) a grid remembers.
_WITHIN_CACHE = 200_000

#: Resolves a contract value (literal or expression) at a path.
Resolve = Callable[[Any, str], Any]


class SpaceError(ValueError):
    """A position or query does not fit the space; the message says what to fix."""


#: The one shape of a graph edge, as its messages name it.
EDGE_SHAPE = "[a, b] or {from, to, weight}: a and b name places, weight is a number ≥ 0 (1 when left out)"


def graph_edge(edge: Any) -> tuple[str, str, float] | str:
    """A graph edge's two ends and weight, or what is wrong with its shape: the one reading the checker and the built
    space share."""
    if isinstance(edge, dict) and set(edge) <= {"from", "to", "weight"}:
        ends, weight = (edge.get("from"), edge.get("to")), edge.get("weight", 1)
    elif isinstance(edge, list) and len(edge) == 2:
        ends, weight = (edge[0], edge[1]), 1
    else:
        return f"an edge is [a, b] or {{from, to, weight}}, got {edge!r}"
    a, b = ends
    if not isinstance(a, str) or not isinstance(b, str):
        return f"an edge's ends are place names (text), got {a!r} and {b!r}"
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight < math.inf:
        return f"an edge's weight is a number ≥ 0, got {weight!r}"
    return a, b, float(weight)


class Geometry:
    """One declared space, resolved."""

    def __init__(self, spec: Space, resolve: Resolve):
        self.torus = False
        self.neighborhood = "von_neumann"
        self.rows = self.cols = 0
        self.width = self.height = 0.0
        self.nodes: list[str] = []
        self._node_index: dict[str, int] = {}
        self._adjacent: dict[str, list[tuple[str, float]]] = {}
        self._paths: dict[str, dict[str, float]] = {}
        self._offsets: dict[int, list[tuple[int, int]]] = {}
        self._within: dict[tuple[int, int], list[int]] = {}
        if spec.grid is not None:
            self.kind = "grid"
            self.rows = _whole(resolve(spec.grid.rows, "space.grid.rows"), "space.grid.rows")
            self.cols = _whole(resolve(spec.grid.cols, "space.grid.cols"), "space.grid.cols")
            if self.rows * self.cols > MAX_CELLS:
                raise SpaceError(f"a {self.rows}x{self.cols} grid has more than {MAX_CELLS:,} cells")
            if spec.grid.neighborhood not in NEIGHBORHOODS:
                raise SpaceError(f"unknown neighborhood '{spec.grid.neighborhood}' ({', '.join(NEIGHBORHOODS)})")
            self.neighborhood, self.torus = spec.grid.neighborhood, spec.grid.torus
        elif spec.graph is not None:
            self.kind = "graph"
            self._graph(resolve(spec.graph.nodes, "space.graph.nodes"), resolve(spec.graph.edges, "space.graph.edges"))
        elif spec.plane is not None:
            self.kind = "plane"
            self.width = _size(resolve(spec.plane.width, "space.plane.width"), "space.plane.width")
            self.height = _size(resolve(spec.plane.height, "space.plane.height"), "space.plane.height")
            self.torus = spec.plane.torus
        else:
            raise SpaceError("declare one of grid, graph, plane")

    def _graph(self, nodes: Any, edges: Any) -> None:
        if not isinstance(nodes, list) or not all(isinstance(node, str) for node in nodes):
            raise SpaceError(f"graph nodes are a list of place names, got {nodes!r}")
        if not isinstance(edges, list):
            raise SpaceError(f"graph edges are a list, got {edges!r}")
        self.nodes = list(nodes)
        self._node_index = {node: index for index, node in enumerate(self.nodes)}
        self._adjacent = {node: [] for node in self.nodes}
        for index, edge in enumerate(edges):
            parts = graph_edge(edge)
            if isinstance(parts, str):
                raise RunError(parts, f"space.graph.edges[{index}]")
            *ends, weight = parts
            a, b = (self._place_of_edge(end, index) for end in ends)
            self._adjacent[a].append((b, weight))
            self._adjacent[b].append((a, weight))

    def _place_of_edge(self, end: str, index: int) -> str:
        """``end`` of edge ``index``, refused when the nodes lack it (checked at build: either may be an expression)."""
        if end in self._adjacent:
            return end
        hint = get_close_matches(end, self.nodes, n=1)
        raise RunError(f"'{end}' is not a place (places: {', '.join(self.nodes)}); "
                       f"{f'did you mean {hint[0]!r}? ' if hint else ''}add it to the nodes or fix the edge",
                       f"space.graph.edges[{index}]")

    # -- positions ---------------------------------------------------------------------

    @property
    def cell_count(self) -> int:
        """How many cells (grid) or places (graph) there are; a plane has none."""
        return self.rows * self.cols if self.kind == "grid" else len(self.nodes)

    def place(self, at: Any) -> Any:
        """``at`` as a stored position: checked, and wrapped round on a torus."""
        if self.kind == "grid":
            if not (isinstance(at, list) and len(at) == 2
                    and all(isinstance(v, int) and not isinstance(v, bool) for v in at)):
                raise SpaceError(f"a grid position is [row, col], got {at!r}")
            if self.torus:
                return [at[0] % self.rows, at[1] % self.cols]
            if not (0 <= at[0] < self.rows and 0 <= at[1] < self.cols):
                raise SpaceError(f"position {at} is off the {self.rows}x{self.cols} grid")
            return list(at)
        if self.kind == "graph":
            if not isinstance(at, str) or at not in self._node_index:
                raise SpaceError(f"'{at}' is not a place (places: {', '.join(self.nodes)})")
            return at
        if not (isinstance(at, list) and len(at) == 2 and all(_real(v) for v in at)):
            raise SpaceError(f"a position is [x, y], got {at!r}")
        if self.torus:
            return [at[0] % self.width, at[1] % self.height]
        if not (0 <= at[0] <= self.width and 0 <= at[1] <= self.height):
            raise SpaceError(f"position {at} is outside the {self.width}x{self.height} plane")
        return list(at)

    def cell(self, position: Any) -> int:
        """The number of a stored grid or graph position (row-major on a grid, declaration order on a graph)."""
        return position[0] * self.cols + position[1] if self.kind == "grid" else self._node_index[position]

    def position(self, cell: int) -> Any:
        return [cell // self.cols, cell % self.cols] if self.kind == "grid" else self.nodes[cell]

    # -- distance ----------------------------------------------------------------------

    def distance(self, a: Any, b: Any) -> float:
        if self.kind == "grid":
            dr, dc = b[0] - a[0], b[1] - a[1]
            if self.neighborhood == "hex":
                if not self.torus:
                    return float(_hex(dr, dc))
                return float(min(_hex(r, c) for r in _images(dr, self.rows) for c in _images(dc, self.cols)))
            if self.torus:
                dr, dc = _wrapped(dr, self.rows), _wrapped(dc, self.cols)
            dr, dc = abs(dr), abs(dc)
            return float(max(dr, dc) if self.neighborhood == "moore" else dr + dc)
        if self.kind == "plane":
            dx, dy = b[0] - a[0], b[1] - a[1]
            if self.torus:
                dx, dy = _wrapped(dx, self.width), _wrapped(dy, self.height)
            return math.hypot(dx, dy)
        if a == b:
            return 0.0
        return self._shortest(a).get(b, math.inf)

    def _shortest(self, source: Any) -> dict[str, float]:
        """Shortest-path distances from ``source`` to every place it reaches (the graph never changes)."""
        found = self._paths.get(source)
        if found is not None:
            return found
        best = {source: 0.0}
        queue = [(0.0, source)]
        while queue:
            d, node = heapq.heappop(queue)
            if d > best.get(node, math.inf):
                continue
            for nxt, weight in self._adjacent.get(node, []):
                if d + weight < best.get(nxt, math.inf):
                    best[nxt] = d + weight
                    heapq.heappush(queue, (d + weight, nxt))
        self._paths[source] = best
        return best

    # -- cells near a cell ------------------------------------------------------------

    def cells(self, center: Any | None = None, radius: float = 1) -> list[Any]:
        """Every cell (no center), or the cells within ``radius`` of ``center`` other than itself — on a grid
        row by row around it, on a graph nearest first (then in declaration order)."""
        if self.kind == "plane":
            raise SpaceError("a plane has no cells; use $near and $nearest for distances")
        if center is None:
            return [self.position(cell) for cell in range(self.cell_count)]
        return [self.position(cell) for cell in self.cells_within(center, radius)[1:]]

    def cells_within(self, center: Any, radius: float) -> list[int]:
        """Cell numbers within ``radius`` of a stored position, the center's own first, each once."""
        if self.kind == "graph":
            reach = self._shortest(center)
            near = sorted((d, self._node_index[node]) for node, d in reach.items() if d <= radius and node != center)
            return [self._node_index[center]] + [cell for _, cell in near]
        steps = int(radius)
        key = (self.cell(center), steps)
        found = self._within.get(key)
        if found is not None:
            return found
        seen = {key[0]: None}
        for dr, dc in self.offsets(steps):
            r, c = center[0] + dr, center[1] + dc
            if self.torus:
                r, c = r % self.rows, c % self.cols
            elif not (0 <= r < self.rows and 0 <= c < self.cols):
                continue
            seen.setdefault(r * self.cols + c, None)
        found = list(seen)
        if len(self._within) < _WITHIN_CACHE:  # the grid never changes: remember the busiest neighbourhoods
            self._within[key] = found
        return found

    def offsets(self, radius: int) -> list[tuple[int, int]]:
        """Grid steps within ``radius`` of a cell (not the cell itself), row by row."""
        found = self._offsets.get(radius)
        if found is None:
            span = range(-radius, radius + 1)
            found = [(dr, dc) for dr in span for dc in span if (dr or dc) and self._steps(dr, dc) <= radius]
            self._offsets[radius] = found
        return found

    def _steps(self, dr: int, dc: int) -> int:
        if self.neighborhood == "moore":
            return max(abs(dr), abs(dc))
        if self.neighborhood == "hex":
            return _hex(dr, dc)
        return abs(dr) + abs(dc)

    def adjacent(self, position: Any) -> list[Any]:
        """The positions next to one: its grid neighbourhood (row by row; fewer at an edge that does not
        wrap), or the places an edge joins it to (in edge order)."""
        if self.kind == "graph":
            return list(dict.fromkeys(node for node, _ in self._adjacent.get(position, []) if node in self._node_index))
        return self.cells(position, 1)

    def neighbor_cells(self, cell: int) -> list[int]:
        """The cell numbers of :meth:`adjacent`."""
        if self.kind == "graph":
            return [self._node_index[node] for node in self.adjacent(self.nodes[cell])]
        return self.cells_within(self.position(cell), 1)[1:]

    def neighborhood_size(self) -> int:
        """How many neighbours a cell has away from any edge (grids only)."""
        return len(self.offsets(1))


def _hex(dr: Any, dc: Any) -> Any:
    return max(abs(dr), abs(dc), abs(dr + dc))


def _images(delta: int, size: int) -> tuple[int, int, int]:
    delta %= size
    return delta, delta - size, delta + size


def _wrapped(delta: Any, size: Any) -> Any:
    """The shorter way round a torus of ``size``."""
    delta %= size
    return delta - size if delta > size / 2 else delta


def _whole(value: Any, where: str) -> int:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SpaceError(f"{where} must be a whole number ≥ 1, got {value!r}")
    return value


def _size(value: Any, where: str) -> float:
    if not _real(value) or value <= 0 or not math.isfinite(value):
        raise SpaceError(f"{where} must be a number > 0, got {value!r}")
    return float(value)


def _real(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
