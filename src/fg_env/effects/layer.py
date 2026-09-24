"""The ``layer`` effect: set one cell or every cell of a layer, diffuse it, or let it decay.

Setting every cell, diffusing and decaying compute the whole new layer from the old one (every cell
reads the values as they were), then swap it in as one journaled change.
"""
from __future__ import annotations

import copy
from typing import Any

from ..errors import RunError
from ..expr import ExprError, compile_expr, is_expr, truthy
from ..registry import effect_op
from ..world.geometry import SpaceError

__all__: list = []

_MODES = ("set", "diffuse", "decay")
_SKIP = object()


def _check(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str]]:
    issues: list[tuple[str, str, str]] = []
    space = checker.c.space
    layers = space.layers if space is not None else {}
    name = effect.get("layer")
    if name not in layers:
        issues.append((f"{path}.layer", f"'{name}' is not a declared layer",
                       f"layers: {', '.join(layers)}" if layers else "declare it under space.layers"))
    modes = [mode for mode in _MODES if mode in effect]
    if len(modes) != 1:
        issues.append((path, "a `layer` effect does exactly one of set, diffuse, decay", "keep one of them"))
    elif modes[0] != "set":
        if "at" in effect:
            issues.append((f"{path}.at", f"`at` goes with `set`, not `{modes[0]}`", "remove it"))
        if "where" in effect and modes[0] != "diffuse":
            issues.append((f"{path}.where", f"`where` goes with `set` or `diffuse`, not `{modes[0]}`", "remove it"))
        if name in layers and layers[name].type != "number":
            issues.append((f"{path}.{modes[0]}", f"`{modes[0]}` needs a number layer; '{name}' is {layers[name].type}",
                           "use set, or make the layer a number"))
    for key in ("set", "where"):
        raw = effect.get(key)
        if isinstance(raw, str) and is_expr(raw):
            try:  # $cell, $value and the effect's locals are bound here, so only the expression itself is checked
                checker.expr(raw, f"{path}.{key}", compile_expr(raw).roots)
            except ExprError as exc:
                issues.append((f"{path}.{key}", exc.detail, f"expression: {raw}"))
    return issues


@effect_op("layer", ("set", "at", "where", "diffuse", "decay"),
           '{"layer": "sugar", "set": "$min($value + 1, 4)"}  (every cell: `$cell` is its position, `$value` its '
           'value, all reading the old values; `"at": "$it.at"` sets one cell, `"where"` limits which) · '
           '{"layer": "scent", "diffuse": 0.1} (each cell hands that share out to its neighbours; with `"where"` only '
           'the cells it holds for take part, the others are walls that keep what would cross them) · '
           '{"layer": "scent", "decay": 0.05}',
           literal=("set", "where"), check=_check)
def _layer(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    space = runner.world.space
    name = effect["layer"]
    if space is None or name not in space.layers.specs:
        known = ", ".join(space.layers.specs) if space is not None else ""
        raise RunError(f"'{name}' is not a declared layer (layers: {known or 'none'})", where)
    modes = [mode for mode in _MODES if mode in effect]
    if len(modes) != 1:
        raise RunError("a `layer` effect does exactly one of set, diffuse, decay", where)
    try:
        if modes[0] == "set":
            _set(runner, space, name, effect, vars, where)
            return
        rate = runner.eval(effect[modes[0]], vars)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
            raise RunError(f"`{modes[0]}` is a share from 0 to 1, got {rate!r}", where)
        layers = space.layers
        if modes[0] == "decay":
            space.replace(name, layers.decayed(name, rate))
        else:
            space.replace(name, layers.diffused(name, rate, _open_cells(runner, space, name, effect, vars)))
    except SpaceError as exc:
        raise RunError(str(exc), where) from None


def _open_cells(runner: Any, space: Any, name: str, effect: dict[str, Any], vars: dict[str, Any]
                ) -> list[bool] | None:
    """Which cells a diffusion reaches: those `where` holds for ($cell, $value), or every cell without one."""
    if "where" not in effect:
        return None
    condition, geometry, values = compile_expr(effect["where"]), space.geometry, space.layers.values[name]
    base = runner.world.scope(**vars)
    return [truthy(condition(base.child(cell=geometry.position(cell), value=value)))
            for cell, value in enumerate(values)]


def _set(runner: Any, space: Any, name: str, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    raw, geometry, layers = effect["set"], space.geometry, space.layers
    value = compile_expr(raw) if is_expr(raw) else None
    condition = compile_expr(effect["where"]) if "where" in effect else None
    values = layers.values[name]
    base = runner.world.scope(**vars)

    def new_value(cell: int) -> Any:
        scope = base.child(cell=geometry.position(cell), value=values[cell])
        if condition is not None and not truthy(condition(scope)):
            return _SKIP
        return value(scope) if value is not None else copy.deepcopy(raw)

    if "at" in effect:
        cell = space.cell_of(runner.eval(effect["at"], vars), where)
        result = new_value(cell)
        if result is not _SKIP:
            space.write(name, cell, result, where)
        return
    updated = []
    for cell, old in enumerate(values):
        result = new_value(cell)
        updated.append(old if result is _SKIP else layers.coerce(name, result))
    space.replace(name, updated)
