"""Delivery latency and lossy channels for messages: ``delay`` and ``drop`` on ``post`` and ``emit``.

A delayed post or emit is evaluated when it is sent — its fields, text, author and recipients are
fixed then — and arrives ``delay`` rounds later as a scheduled delivery. The
payload is stored as data, never as effects, so nothing a participant wrote is ever evaluated,
and snapshots carry pending deliveries with their provenance. A ``drop`` chance is rolled when the
message is sent, from the run's seeded streams, so a run and its replay lose the same messages.
Scheduling is journaled: a message sent by an action that is then refused is never delivered.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..errors import RunError

if TYPE_CHECKING:
    from ..world.store import World

__all__ = ["dropped", "send", "deliver"]


def dropped(world: World, chance: Any, where: str) -> bool:
    """Roll a message's ``drop`` chance: True when it is lost."""
    if isinstance(chance, bool) or not isinstance(chance, (int, float)) or not 0 <= chance <= 1:
        raise RunError(f"`drop` must be a chance from 0 to 1, got {chance!r}", where)
    return chance > 0 and world.rng.random() < chance


def send(world: World, delay: Any, payload: dict[str, Any], where: str) -> None:
    """Deliver ``payload`` now (no delay) or schedule it ``delay`` rounds later."""
    if delay is None:
        deliver(world, payload, where)
        return
    if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
        raise RunError(f"`delay` must be a whole number of rounds ≥ 0, got {delay!r}", where)
    due = world.round + delay
    if delay == 0:
        deliver(world, payload, where)
        return
    world.schedule(due, [], {}, where, delivery=payload)


def deliver(world: World, payload: Mapping[str, Any], where: str) -> None:
    if payload["kind"] == "post":
        world.post(payload["record"], dict(payload["fields"]), payload["author"], _ids(payload["to"]), where)
    else:
        world.emit(payload["event"], payload["text"], actor=payload["actor"], to=_ids(payload["to"]),
                   data=dict(payload["data"]))


def _ids(value: Any | None) -> tuple | None:
    return tuple(value) if value is not None else None
