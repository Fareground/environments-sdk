"""Shared plumbing for host mechanisms: config lookup at run time, type checks, agent lists."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any, TypeVar, cast

from pydantic import BaseModel

from ..errors import RunError
from ..expr import Untrusted
from ..expr.objects import Entity
from ..registry import MechanismError, config_data, describe, use_key

__all__ = ["NAME", "config_of", "type_list", "agents_of", "clip", "ellipsis"]

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
M = TypeVar("M", bound=BaseModel)

ellipsis = "…"


def config_of(world: Any, name: str, kind: str, model: type[M], where: str) -> M:
    """The validated config of the mechanism ``name`` of ``kind`` declared in the run's contract."""
    raw = world.contract.mechanisms.get(name)
    if not isinstance(raw, Mapping) or use_key(raw) != kind:
        raise RunError(f"'{name}' is not a declared {describe(kind)} mechanism", where)
    return cast(M, _parse(model, _frozen(raw)))


@lru_cache(maxsize=512)
def _parse(model: type[BaseModel], frozen: tuple[tuple[str, str], ...]) -> BaseModel:
    return model.model_validate(config_data({key: json.loads(value) for key, value in frozen}))


def _frozen(raw: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, json.dumps(value, sort_keys=True, default=str)) for key, value in raw.items()))


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
