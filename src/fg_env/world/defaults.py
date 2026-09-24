"""Property values that read other properties, evaluated in dependency order.

``"plan": {"default": "$map($world.rates, $it * 2)"}`` is evaluated after ``rates`` whatever the declaration order;
defaults that read each other in a circle cannot be ordered and are a contract error. A new entity's props read each
other through ``$it`` the same way (``"double": "$it.base * 2"``).
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..expr import is_expr

__all__ = ["world_reads", "default_order"]

_READS = {root: re.compile(rf"\${root}\.([A-Za-z_][A-Za-z0-9_]*)") for root in ("world", "it")}


def world_reads(raw: Any, root: str = "world") -> set[str]:
    """The properties of ``$<root>`` an expression (or a list or map holding expressions) reads by name."""
    if isinstance(raw, str):
        return set(_READS[root].findall(raw)) if is_expr(raw) else set()
    if isinstance(raw, list):
        return set().union(*(world_reads(item, root) for item in raw)) if raw else set()
    if isinstance(raw, dict):
        return set().union(*(world_reads(item, root) for item in raw.values())) if raw else set()
    return set()


def default_order(defaults: Mapping[str, Any], root: str = "world") -> tuple[list[str], list[str] | None]:
    """Property names in evaluation order (each after the properties of ``$<root>`` its value reads), and the first
    circle of values reading each other (``["a", "b", "a"]``), or ``None``. Unrelated properties keep their order."""
    reads: dict[str, list[str]] = {name: sorted(world_reads(raw, root) & set(defaults))
                                   for name, raw in defaults.items()}
    order: list[str] = []
    state: dict[str, str] = {}
    for start in defaults:
        cycle = _visit(start, reads, state, order, [])
        if cycle is not None:
            return order, cycle
    return order, None


def _visit(name: str, reads: Mapping[str, list[str]], state: dict[str, str], order: list[str],
           path: list[str]) -> list[str] | None:
    if state.get(name) == "done":
        return None
    if state.get(name) == "open":
        return path[path.index(name):] + [name]
    state[name] = "open"
    for needed in reads[name]:
        cycle = _visit(needed, reads, state, order, path + [name])
        if cycle is not None:
            return cycle
    state[name] = "done"
    order.append(name)
    return None
