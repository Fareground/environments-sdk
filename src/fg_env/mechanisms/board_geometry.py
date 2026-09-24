"""Board geometry: cells, names, directions and neighbour tables for grid, hex, ring and graph boards.

Every cell is an index; every direction is a precomputed table ``step[dir][cell] -> cell | -1``, so
move generation never parses names or does coordinate arithmetic in its inner loops.
"""
from __future__ import annotations

import string
from collections.abc import Sequence
from dataclasses import dataclass, field

__all__ = ["Geometry", "GeometryError", "grid", "hex_board", "ring", "graph"]

#: Compass directions clockwise from north; relative names clockwise from forward.
_GRID_DIRS = ("n", "ne", "e", "se", "s", "sw", "w", "nw")
_GRID_VECTORS = {"n": (-1, 0), "ne": (-1, 1), "e": (0, 1), "se": (1, 1), "s": (1, 0), "sw": (1, -1),
                 "w": (0, -1), "nw": (-1, -1)}
_GRID_RELATIVE = ("f", "fr", "r", "br", "b", "bl", "l", "fl")
#: Pointy-top axial hex directions clockwise from north-east: (dq, dr).
_HEX_DIRS = ("ne", "e", "se", "sw", "w", "nw")
_HEX_VECTORS = {"ne": (1, -1), "e": (1, 0), "se": (0, 1), "sw": (-1, 1), "w": (-1, 0), "nw": (0, -1)}
_HEX_RELATIVE = ("f", "fr", "br", "b", "bl", "fl")


class GeometryError(ValueError):
    """A board shape or cell reference is invalid; the message says what to fix."""


@dataclass
class Geometry:
    """One board: named cells with neighbour tables per direction."""

    shape: str
    names: list[str]
    #: Absolute directions, clockwise (grid, hex, ring) or edge labels (graph).
    dirs: tuple[str, ...]
    step: dict[str, list[int]]
    #: Every neighbour of each cell (any direction, graph edges included).
    adjacent: list[list[int]]
    #: Coordinates per cell: (row, col) for grids, (q, r) for hex, (i, 0) otherwise.
    coords: list[tuple[int, int]]
    groups: dict[str, tuple[str, ...]]
    #: Direction names relative to a side's forward direction, clockwise from forward.
    relative: tuple[str, ...] = ()
    #: One direction of each opposite pair: the directions a line of cells runs along.
    line_dirs: tuple[str, ...] = ()
    rows: int = 0
    cols: int = 0
    wrap: bool = False
    index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {name: i for i, name in enumerate(self.names)}

    @property
    def size(self) -> int:
        return len(self.names)

    def cell(self, name: str, where: str = "cell") -> int:
        """The index of a named cell; raises with the valid range when it does not exist."""
        found = self.index.get(str(name).strip().lower()) if isinstance(name, str) else None
        if found is None:
            found = self.index.get(str(name).strip()) if isinstance(name, str) else None
        if found is None:
            raise GeometryError(f"{where}: '{name}' is not a cell of this board (cells: {self._range()})")
        return found

    def _range(self) -> str:
        if len(self.names) <= 12:
            return ", ".join(self.names)
        return f"{self.names[0]} … {self.names[-1]}"

    def expand(self, dirs: object, where: str, forward: str | None = None) -> tuple[str, ...]:
        """Absolute direction names for a direction spec: a name, a group, a relative name, or a list."""
        items = [dirs] if isinstance(dirs, str) else list(dirs) if isinstance(dirs, (list, tuple)) else None
        if not items:
            raise GeometryError(f"{where}: give a direction or a list of directions ({self._dir_help()})")
        out: list[str] = []
        for item in items:
            if not isinstance(item, str):
                raise GeometryError(f"{where}: a direction is text, got {item!r}")
            if item in self.groups:
                resolved: Sequence[str] = self.groups[item]
            elif item in self.step:
                resolved = (item,)
            elif item in self.relative:
                if forward is None:
                    raise GeometryError(f"{where}: relative direction '{item}' needs the side's `forward` direction")
                resolved = (self.turn(forward, self.relative.index(item)),)
            else:
                raise GeometryError(f"{where}: '{item}' is not a direction on a {self.shape} board "
                                    f"({self._dir_help()})")
            out.extend(d for d in resolved if d not in out)
        return tuple(out)

    def turn(self, forward: str, clockwise_steps: int) -> str:
        ring_dirs = self.dirs
        return ring_dirs[(ring_dirs.index(forward) + clockwise_steps) % len(ring_dirs)]

    def _dir_help(self) -> str:
        parts = [f"directions: {', '.join(self.dirs)}"]
        if self.groups:
            parts.append(f"groups: {', '.join(self.groups)}")
        if self.relative:
            parts.append(f"relative to forward: {', '.join(self.relative)}")
        return "; ".join(parts)

    def offset_table(self, offset: tuple[int, int]) -> list[int]:
        """Target cell per cell for a coordinate offset (grid: rows, cols; hex: dq, dr)."""
        lookup = {xy: i for i, xy in enumerate(self.coords)}
        return [lookup.get((a + offset[0], b + offset[1]), -1) for a, b in self.coords]

    def ray(self, cell: int, direction: str) -> list[int]:
        """Cells from ``cell`` (exclusive) along ``direction`` until the edge (one lap on a ring)."""
        table, out, current = self.step[direction], [], cell
        for _ in range(self.size):
            current = table[current]
            if current < 0 or current == cell:
                break
            out.append(current)
        return out


def _file_letters(count: int) -> list[str]:
    if count > 26:
        raise GeometryError(f"algebraic names have 26 column letters; this board has {count} columns — use \"coords\": "
                            "\"rc\"")
    return list(string.ascii_lowercase[:count])


def grid(rows: int, cols: int, coords: str = "algebraic") -> Geometry:
    """A rows × cols board. Row 0 is the top row; algebraic names count ranks from the bottom (a1 bottom-left),
    ``rc`` names are ``row,col`` from the top-left (1,1). North is up."""
    if rows < 1 or cols < 1:
        raise GeometryError("a grid needs at least 1 row and 1 column")
    coords_list = [(r, c) for r in range(rows) for c in range(cols)]
    if coords == "algebraic":
        files = _file_letters(cols)
        names = [f"{files[c]}{rows - r}" for r, c in coords_list]
    elif coords == "rc":
        names = [f"{r + 1},{c + 1}" for r, c in coords_list]
    else:
        raise GeometryError(f"coords must be algebraic or rc, got '{coords}'")
    step = {d: [(r + dr) * cols + (c + dc) if 0 <= r + dr < rows and 0 <= c + dc < cols else -1
                for r, c in coords_list] for d, (dr, dc) in _GRID_VECTORS.items()}
    # Adjacent means edge-sharing (Go liberties, regions); diagonal neighbours are reached through directions.
    adjacent = [[step[d][i] for d in ("n", "e", "s", "w") if step[d][i] >= 0] for i in range(rows * cols)]
    groups = {"orthogonal": ("n", "e", "s", "w"), "diagonal": ("ne", "se", "sw", "nw"), "all": _GRID_DIRS}
    return Geometry("grid", names, _GRID_DIRS, step, adjacent, coords_list, groups, _GRID_RELATIVE,
                    ("e", "s", "se", "sw"), rows, cols)


def hex_board(radius: int | None = None, rows: int | None = None, cols: int | None = None) -> Geometry:
    """Pointy-top hex cells in axial coordinates: a hexagon of ``radius`` rings around a centre, or a
    ``rows × cols`` rhombus (the Hex game board). Cells are named by column letter and row number from the top."""
    if radius is not None:
        if radius < 0:
            raise GeometryError("a hex radius is 0 or more")
        span = 2 * radius + 1
        coords_list = [(q, r) for r in range(span) for q in range(span) if radius <= q + r <= 3 * radius]
        rows, cols = span, span
    elif rows and cols:
        coords_list = [(q, r) for r in range(rows) for q in range(cols)]
    else:
        raise GeometryError("a hex board needs a radius (\"size\": 4) or rows and columns (\"size\": [11, 11])")
    files = _file_letters(cols)
    names = [f"{files[q]}{r + 1}" for q, r in coords_list]
    lookup = {xy: i for i, xy in enumerate(coords_list)}
    step = {d: [lookup.get((q + dq, r + dr), -1) for q, r in coords_list] for d, (dq, dr) in _HEX_VECTORS.items()}
    adjacent = [[step[d][i] for d in _HEX_DIRS if step[d][i] >= 0] for i in range(len(coords_list))]
    return Geometry("hex", names, _HEX_DIRS, step, adjacent, coords_list, {"all": _HEX_DIRS, "orthogonal": _HEX_DIRS},
                    _HEX_RELATIVE, ("e", "se", "sw"), rows, cols)


def ring(length: int, wrap: bool = True) -> Geometry:
    """A track of cells named 1..length; ``cw`` counts up, ``ccw`` down. With wrap the last cell joins the first."""
    if length < 2:
        raise GeometryError("a ring or track needs at least 2 cells")
    names = [str(i + 1) for i in range(length)]
    cw = [(i + 1) % length if wrap or i + 1 < length else -1 for i in range(length)]
    ccw = [(i - 1) % length if wrap or i > 0 else -1 for i in range(length)]
    adjacent = [sorted({c for c in (cw[i], ccw[i]) if c >= 0}) for i in range(length)]
    return Geometry("ring", names, ("cw", "ccw"), {"cw": cw, "ccw": ccw}, adjacent, [(i, 0) for i in range(length)],
                    {"all": ("cw", "ccw")}, ("f", "b"), ("cw",), 1, length, wrap)


def graph(nodes: Sequence[str], edges: Sequence[Sequence[str]]) -> Geometry:
    """Named places joined by edges ``[a, b]`` (adjacent) or ``[a, b, dir, back]`` (a labelled direction from a
    to b and its opposite from b to a, so pieces can slide and jump along labelled lines)."""
    names = [str(n) for n in nodes]
    if len(set(names)) != len(names) or not names:
        raise GeometryError("graph nodes must be a non-empty list of distinct names")
    lookup = {n: i for i, n in enumerate(names)}
    step: dict[str, list[int]] = {}
    adjacent: list[list[int]] = [[] for _ in names]
    for number, edge in enumerate(edges):
        where = f"edges[{number}]"
        if not isinstance(edge, (list, tuple)) or len(edge) not in (2, 4):
            raise GeometryError(f"{where}: an edge is [a, b] or [a, b, dir, back_dir], got {edge!r}")
        for node in edge[:2]:
            if node not in lookup:
                raise GeometryError(f"{where}: '{node}' is not a node (nodes: {', '.join(names)})")
        a, b = lookup[edge[0]], lookup[edge[1]]
        for x, y in ((a, b), (b, a)):
            if y not in adjacent[x]:
                adjacent[x].append(y)
        if len(edge) == 4:
            for label, x, y in ((edge[2], a, b), (edge[3], b, a)):
                table = step.setdefault(str(label), [-1] * len(names))
                if table[x] >= 0 and table[x] != y:
                    raise GeometryError(f"{where}: node '{names[x]}' already has a '{label}' neighbour")
                table[x] = y
    dirs = tuple(step)
    return Geometry("graph", names, dirs, step, adjacent, [(i, 0) for i in range(len(names))],
                    {"all": dirs} if dirs else {}, (), (), 1, len(names))
