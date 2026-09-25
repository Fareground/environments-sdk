"""The values the world stores: plain data, never live objects."""
from __future__ import annotations

from typing import Any

from ..expr.base import view_as_value
from ..expr.objects import Entity
from .links import Link
from .parts import Entry

__all__ = ["plain_value", "copy_value"]


def plain_value(value: Any) -> Any:
    """``value`` as the world stores it: entities by id and links as data, so properties never hold live objects."""
    kind = type(value)
    if kind is str or kind is int or kind is float or kind is bool or value is None:
        return value
    if isinstance(value, Entity):
        return value.id
    if isinstance(value, Link):
        return value.as_dict()
    if isinstance(value, list):
        return [plain_value(v) for v in value]
    if isinstance(value, dict) and not isinstance(value, Entry):
        return {k: plain_value(v) for k, v in value.items()}
    if isinstance(value, Entry):
        return {k: v for k, v in value.items()}
    refused = view_as_value(value)
    if refused is not None:
        raise refused
    return value


def copy_value(value: Any) -> Any:
    """``value`` with its lists and maps copied, so a default written in the contract is never shared."""
    if isinstance(value, list):
        return [copy_value(v) for v in value]
    if isinstance(value, dict):
        return {k: copy_value(v) for k, v in value.items()}
    return value
