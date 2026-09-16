"""Versioned captures for deferred rules: live references and frozen literal data.

Callers persist capture_version=1 beside newly frozen data. Untagged thaw retains
legacy entity-only decoding, so old literal maps are not reinterpreted as new tags.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..entity import Entity
from .links import Link

__all__ = ["freeze", "thaw"]


def freeze(value: Any) -> Any:
    if isinstance(value, Entity):
        return {"$entity": value.id}
    if isinstance(value, Link):
        return {"$link": [value.kind, *value.key]}
    if isinstance(value, (list, tuple)):
        return [freeze(v) for v in value]
    if isinstance(value, Mapping):
        frozen = {k: freeze(v) for k, v in value.items()}
        # User data may look exactly like a reference tag. Escape the container,
        # while retaining reference handling for values nested inside it.
        if len(value) == 1 and next(iter(value)) in ("$entity", "$link", "$literal"):
            return {"$literal": frozen}
        return frozen
    return value


def thaw(value: Any, world: Any, *, tagged: bool = False) -> Any:
    if tagged and isinstance(value, Mapping) and set(value) == {"$literal"}:
        return {k: thaw(v, world, tagged=tagged) for k, v in value["$literal"].items()}
    if isinstance(value, Mapping) and set(value) == {"$entity"}:
        return world.entities.get(value["$entity"])
    if tagged and isinstance(value, Mapping) and set(value) == {"$link"}:
        kind, source, target = value["$link"]
        return Link(world, kind, (source, target))
    if isinstance(value, (list, tuple)):
        return [thaw(v, world, tagged=tagged) for v in value]
    if isinstance(value, Mapping):
        return {k: thaw(v, world, tagged=tagged) for k, v in value.items()}
    return value
