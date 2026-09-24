"""Synchronous events (``events[].sync``): every item's rules read the world as it was before the
event, and all their writes land together — cellular automata, simultaneous imitation, diffusion.

While an item runs, property and layer-cell writes wait in a buffer instead of changing the world, so
later items still read the old values. An item that is refused (``fail``) drops its writes. When every
item has run, the writes commit as one atomic change. Two items writing different values to the same
property is an error naming both; anything else that changes the world directly is an error too.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from ..actions.book import ACTION_BUDGET
from ..contract import EventSpec
from ..errors import RunError
from ..expr import compile_expr, shared_budget, truthy
from ..world.entity import Entity
from ..world.live import Abort

if TYPE_CHECKING:
    from .env import Env

__all__ = ["WriteBuffer", "run_sync"]

Key = tuple[Any, ...]


class WriteBuffer:
    """Writes waiting for a sync event to commit: the running item's, then every kept item's."""

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
                               "event; make the writes agree, or drop sync", path)
            self.kept[key] = (value, apply, label)
        self.item.clear()


def run_sync(env: Env, event: EventSpec, items: Sequence[Any], name: str, path: str) -> None:
    world = env.world
    buffer = WriteBuffer()
    refusals: list[str] = []
    ran = False
    with env._lock:
        for position, item in enumerate(items):
            inner = {name: item, "i": position}
            if event.where is not None:
                with world.drawing_for(f"{path}.where", item):
                    if not truthy(compile_expr(event.where)(world.scope(**inner))):
                        continue
            ran = True
            mark = world.journal.mark()
            world.buffer = buffer
            try:
                with shared_budget(ACTION_BUDGET, f"{path}.do"), world.drawing_for(f"{path}.do", item):
                    env.effects.run(event.do, dict(inner), f"{path}.do")
            except Abort as refusal:
                buffer.item.clear()
                refusals.append(refusal.reason)
            except BaseException:
                world.journal.rollback(mark)
                raise
            finally:
                world.buffer = None
            if world.journal.mark() != mark:
                world.journal.rollback(mark)
                raise RunError("a sync event can only assign properties ($it.x, $world.x) and layer cells; create, "
                               "remove, move, messages and other changes belong in an event without sync", f"{path}.do")
            buffer.keep(_label(item, position), path)
        if not ran:
            return
        mark = world.journal.mark()
        try:
            for _, apply, _ in buffer.kept.values():
                apply()
        except BaseException:
            world.journal.rollback(mark)
            raise
        for reason in refusals:
            world.emit("refused", f"{path}.do was refused: {reason}", to=[],
                       data={"path": f"{path}.do", "reason": reason})
        env._after_commit(f"{path}.do")
        env.happenings.react(env._stage_spec())


def _label(item: Any, position: int) -> str:
    return f"'{item.id}'" if isinstance(item, Entity) else f"item {position + 1}"


def _target(key: Key) -> str:
    if key[0] == "prop":
        return f"{key[1]}.{key[2]}"
    if key[0] == "world":
        return f"$world.{key[1]}"
    return f"cell {key[2]} of layer '{key[1]}'"
