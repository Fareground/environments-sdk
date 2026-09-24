"""Static checks for the space (sizes, neighbourhood, capacity, layers) and for sync and ordered events."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List

from ..contract import LAYER_TYPES, EventSpec, Space
from ..effects.runner import select_ops
from ..expr import is_expr
from ..world.geometry import MAX_CELLS, NEIGHBORHOODS

if TYPE_CHECKING:
    from . import _Checker

__all__ = ["check_space", "check_event_order", "SYNC_OPS"]

#: Effect operations a sync event may run: they only assign properties and layer cells, branch or refuse.
SYNC_OPS = frozenset({"if", "each", "layer", "fail"})


def check_space(checker: "_Checker", space: Space) -> None:
    kinds = [kind for kind in ("grid", "graph", "plane") if getattr(space, kind) is not None]
    if len(kinds) != 1:
        checker.error("space", "declare exactly one of grid, graph, plane")
        return
    inputs = {"inputs"}
    if space.grid is not None:
        grid = space.grid
        sizes = [_size(checker, grid.rows, "space.grid.rows", whole=True), _size(checker, grid.cols, "space.grid.cols", whole=True)]
        if all(isinstance(size, int) for size in sizes) and sizes[0] * sizes[1] > MAX_CELLS:
            checker.error("space.grid", f"{sizes[0]}x{sizes[1]} is more than {MAX_CELLS:,} cells", "use a smaller grid")
        if grid.neighborhood not in NEIGHBORHOODS:
            checker.error("space.grid.neighborhood", f"unknown neighborhood '{grid.neighborhood}'",
                          checker._suggest(grid.neighborhood, NEIGHBORHOODS) or ", ".join(NEIGHBORHOODS))
    elif space.graph is not None:
        graph = space.graph
        for key in ("nodes", "edges"):
            raw = getattr(graph, key)
            if isinstance(raw, str):
                checker.expr(raw, f"space.graph.{key}", inputs)
        if isinstance(graph.nodes, list) and isinstance(graph.edges, list):
            _edges(checker, graph.nodes, graph.edges)
    else:
        assert space.plane is not None
        _size(checker, space.plane.width, "space.plane.width", whole=False)
        _size(checker, space.plane.height, "space.plane.height", whole=False)
    cells = space.plane is None
    if space.capacity is not None:
        _capacity(checker, space.capacity, cells)
    for name, layer in space.layers.items():
        path = f"space.layers.{name}"
        if not cells:
            checker.error(path, "layers need a grid or a graph (a plane has no cells)")
        if layer.type not in LAYER_TYPES:
            checker.error(f"{path}.type", f"unknown layer type '{layer.type}'",
                          checker._suggest(layer.type, LAYER_TYPES) or ", ".join(LAYER_TYPES))
        if is_expr(layer.default):
            checker.expr(layer.default, f"{path}.default", inputs | {"cell"})
        elif layer.type == "bool" and not isinstance(layer.default, bool) \
                or layer.type in ("number", "int") and (isinstance(layer.default, bool) or not isinstance(layer.default, (int, float))):
            checker.error(f"{path}.default", f"a {layer.type} layer needs a {layer.type} default, got {layer.default!r}")


def _size(checker: "_Checker", raw: Any, path: str, whole: bool) -> Any:
    if isinstance(raw, str):
        checker.expr(raw, path, {"inputs"})
        return None
    if isinstance(raw, bool) or (whole and not isinstance(raw, int)) or raw <= 0:
        checker.error(path, f"must be {'a whole number ≥ 1' if whole else 'a number > 0'}, got {raw!r}")
    return raw


def _edges(checker: "_Checker", nodes: List[str], edges: List[Any]) -> None:
    known = set(nodes)
    for index, edge in enumerate(edges):
        ends = [edge.get("from"), edge.get("to")] if isinstance(edge, dict) else list(edge) if isinstance(edge, list) else []
        if len(ends) != 2:
            checker.error(f"space.graph.edges[{index}]", "an edge is [a, b] or {from, to, weight}")
            continue
        for end in ends:
            if end not in known:
                checker.error(f"space.graph.edges[{index}]", f"'{end}' is not a place", checker._suggest(str(end), nodes))


def _capacity(checker: "_Checker", raw: Any, cells: bool) -> None:
    if not cells:
        checker.error("space.capacity", "capacity needs a grid or a graph (a plane has no cells)")
    limits: Dict[str, Any] = raw if isinstance(raw, dict) else {"": raw}
    for type_name, limit in limits.items():
        path = f"space.capacity.{type_name}" if type_name else "space.capacity"
        if type_name:
            checker._type(type_name, path)
        if isinstance(limit, str):
            checker.expr(limit, path, {"inputs"})
        elif isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            checker.error(path, f"must be a whole number ≥ 0, got {limit!r}")


def check_event_order(checker: "_Checker", event: EventSpec, path: str, roots: FrozenSet[str], types: Any) -> None:
    """An event's `order` and `sync`: both need `each`; sync effects may only assign."""
    if event.each is None:
        for key in ("order", "sync"):
            if key in event.model_fields_set:
                checker.error(f"{path}.{key}", f"`{key}` needs `each`", "add `each`, or remove it")
        return
    checker.order_setting(event.order, f"{path}.order", ("random",), roots, types)
    if event.sync:
        _sync_effects(checker, event.do, f"{path}.do")


def _sync_effects(checker: "_Checker", effects: Any, path: str) -> None:
    for index, effect in enumerate(effects if isinstance(effects, list) else []):
        where = f"{path}[{index}]"
        if not isinstance(effect, dict):
            continue
        ops = select_ops(effect)
        if len(ops) != 1:
            continue
        op = ops[0]
        if op not in SYNC_OPS or (op == "layer" and ("set" not in effect or "at" not in effect)):
            checker.error(where, f"a sync event only assigns properties and layer cells, so it cannot run `{op}`"
                          + (" on a whole layer" if op == "layer" else ""),
                          "move it to an event without sync")
        for key in ("then", "else", "do"):
            if key in effect:
                _sync_effects(checker, effect[key], f"{where}.{key}")
