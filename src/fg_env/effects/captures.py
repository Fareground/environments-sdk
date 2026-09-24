"""Versioned captures for deferred rules: live references and frozen literal data.

Callers persist CAPTURE_VERSION beside newly frozen data. Version 0 retains
legacy entity-only decoding; version 1 adds links and literal escaping; version 2
adds shared-state views; version 3 preserves record entries; version 4 preserves
logged events; version 5 binds pattern views to the executing model runtime.
Old literal maps are not reinterpreted as newer tags.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..patterns.runtime import PatternsView
from ..world.entity import Entity
from ..world.links import Link
from ..world.parts import ClockView, Entry, LogEvent, PhysicsView, PropsView

__all__ = ["CAPTURE_VERSION", "freeze", "thaw"]

CAPTURE_VERSION = 5
_VIEW_TYPES = {"world": PropsView, "physics": PhysicsView, "clock": ClockView}
_VIEW_NAMES: dict[type, str] = {cls: name for name, cls in _VIEW_TYPES.items()}
_VIEW_NAMES[PatternsView] = "pattern"


def freeze(value: Any) -> Any:
    if isinstance(value, Entity):
        return {"$entity": value.id}
    if isinstance(value, Link):
        return {"$link": [value.kind, *value.key]}
    view = _VIEW_NAMES.get(type(value))
    if view is not None:
        return {"$view": view}
    if isinstance(value, (list, tuple)):
        return [freeze(v) for v in value]
    if isinstance(value, LogEvent):
        return {"$event": freeze(value.to_dict())}
    if isinstance(value, Entry):
        return {"$entry": {k: freeze(v) for k, v in value.items()}}
    if isinstance(value, Mapping):
        frozen = {k: freeze(v) for k, v in value.items()}
        # User data may look exactly like a reference tag. Escape the container,
        # while retaining reference handling for values nested inside it.
        if len(value) == 1 and next(iter(value)) in ("$entity", "$link", "$literal", "$view", "$entry", "$event"):
            return {"$literal": frozen}
        return frozen
    return value


def thaw(value: Any, world: Any, *, version: int = 0) -> Any:
    if version >= 1 and isinstance(value, Mapping) and set(value) == {"$literal"}:
        return {k: thaw(v, world, version=version) for k, v in value["$literal"].items()}
    if isinstance(value, Mapping) and set(value) == {"$entity"}:
        return world.entities.get(value["$entity"])
    if version >= 1 and isinstance(value, Mapping) and set(value) == {"$link"}:
        kind, source, target = value["$link"]
        return Link(world, kind, (source, target))
    if version >= 2 and isinstance(value, Mapping) and set(value) == {"$view"}:
        if version >= 5 and value["$view"] == "pattern":
            return world.patterns.view
        return _VIEW_TYPES[value["$view"]](world)
    if version >= 3 and isinstance(value, Mapping) and set(value) == {"$entry"}:
        entry = Entry({k: thaw(v, world, version=version) for k, v in value["$entry"].items()})
        entry.world = world
        return entry
    if version >= 4 and isinstance(value, Mapping) and set(value) == {"$event"}:
        fields = thaw(value["$event"], world, version=version)
        if fields.get("to") is not None:
            fields["to"] = tuple(fields["to"])
        return LogEvent(**fields)
    if isinstance(value, (list, tuple)):
        return [thaw(v, world, version=version) for v in value]
    if isinstance(value, Mapping):
        return {k: thaw(v, world, version=version) for k, v in value.items()}
    return value
