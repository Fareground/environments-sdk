"""Shared plumbing for host mechanisms: type checks, agent lists, clipping."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from ..expr import Untrusted
from ..expr.objects import Entity
from ..registry import MechanismError

__all__ = ["NAME", "type_list", "agents_of", "clip", "ellipsis"]

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")

ellipsis = "…"


def type_list(contract: Mapping[str, Any], value: str | Sequence[str], field: str) -> list[str]:
    """The type names in ``value``, each checked against the contract's types."""
    names = [value] if isinstance(value, str) else list(value)
    types = contract.get("types") or {}
    if not names:
        raise MechanismError("names no type", f"types: {', '.join(types) or 'none'}", field)
    for name in names:
        if name not in types:
            raise MechanismError(f"'{name}' is not a declared type", f"types: {', '.join(types) or 'none'}", field)
    return names


def agents_of(world: Any, types: str | Sequence[str]) -> list[Entity]:
    """Living entities of any of ``types`` (subtypes included), in seat order, each once."""
    kinds = [types] if isinstance(types, str) else list(types)
    return [e for e in world.entities.values() if e.alive and any(world.is_a(e.entity_type, k) for k in kinds)]


def clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters, keeping its provenance."""
    if len(text) <= limit:
        return text
    cut = str.__str__(text)[: max(0, limit - 1)] + ellipsis
    return Untrusted(cut) if isinstance(text, Untrusted) else cut


def prop_of(entity: Any, name: str, default: Any = None) -> Any:
    """An entity property as plain data, or ``default`` when it is unset."""
    value = entity.properties.get(name)
    return default if value is None else value
