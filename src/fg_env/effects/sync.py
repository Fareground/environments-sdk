"""Synchronous loops (``{"each": ..., "sync": true}``): every item's rules read the world as it was before the loop, and
all their writes land together — cellular automata, simultaneous imitation, diffusion.

While an item runs, property and layer-cell writes wait in a buffer instead of changing the world, so later items still
read the old values. When every item has run, the writes land at once, inside whatever change runs the loop. Two items
writing different values to the same property is an error naming both; anything else that changes the world directly
is an error too.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..errors import RunError
from ..expr.objects import Entity
from ..world.live import SdkWorld

__all__ = ["WriteBuffer", "run_synced"]

Key = tuple[Any, ...]


class WriteBuffer:
    """Writes waiting for a sync loop to land: the running item's, then every kept item's."""

    def __init__(self) -> None:
        self.item: dict[Key, tuple[Any, Callable[[], None]]] = {}
        self.kept: dict[Key, tuple[Any, Callable[[], None], str]] = {}

    def write(self, key: Key, value: Any, apply: Callable[[], None], where: str) -> None:
        self.item[key] = (value, apply)  # an item's later write to the same target replaces its earlier one

    def keep(self, label: str, path: str) -> None:
        for key, (value, apply) in self.item.items():
            earlier = self.kept.get(key)
            if earlier is not None and earlier[0] != value:
                raise RunError(f"{earlier[2]} and {label} write different values to {_target(key)} in one sync "
                               "loop; make the writes agree, or drop sync", path)
            self.kept[key] = (value, apply, label)
        self.item.clear()


def run_synced(world: SdkWorld, items: Sequence[Any], run_item: Callable[[int, Any], bool], path: str) -> bool:
    """Run ``run_item(position, item)`` for every item with its writes held back, then land them all. ``run_item``
    returns False for an item it skipped. False when no item ran."""
    if world.buffer is not None:
        raise RunError("a sync loop cannot run inside another sync loop; drop one sync", path)
    buffer = WriteBuffer()
    ran = False
    for position, item in enumerate(items):
        mark = world.mark()
        world.buffer = buffer
        try:
            if not run_item(position, item):
                continue
        finally:
            world.buffer = None
        ran = True
        if world.mark() != mark:
            raise RunError("a sync loop can only assign properties ($it.x, $world.x) and layer cells; create, remove, "
                           "move, messages and other changes belong in a loop without sync", path)
        buffer.keep(_label(item, position), path)
    for _, apply, _ in buffer.kept.values():
        apply()
    return ran


def _label(item: Any, position: int) -> str:
    return f"'{item.id}'" if isinstance(item, Entity) else f"item {position + 1}"


def _target(key: Key) -> str:
    if key[0] == "prop":
        return f"{key[1]}.{key[2]}"
    if key[0] == "world":
        return f"$world.{key[1]}"
    return f"cell {key[2]} of layer '{key[1]}'"
