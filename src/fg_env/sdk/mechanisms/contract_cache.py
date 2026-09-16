"""Mechanism configs of a running contract, parsed once per contract object.

Native ops and functions read their mechanism's config on every call; parsing it each time would
be wasted work. Entries are keyed by the contract's identity, checked through a weak reference
(a contract is not hashable) and dropped when the contract is garbage collected, so a long
experiment holds no stale configs. The cache holds only data derived from the contract, never
run state.
"""
from __future__ import annotations

import threading
import weakref
from typing import Any, Callable, Dict, Mapping, Tuple, Type, TypeVar

from pydantic import BaseModel

from ..registry import config_data, use_key

__all__ = ["per_contract", "parse_kind"]

T = TypeVar("T")
M = TypeVar("M", bound=BaseModel)

#: Re-entrant: a weakref callback may run during garbage collection while the lock is held.
_LOCK = threading.RLock()
_CACHE: Dict[Tuple[int, str], Tuple["weakref.ref[Any]", Any]] = {}


def per_contract(world: Any, key: str, build: Callable[[Any], T], empty: T) -> T:
    """``build(contract)`` for the world's contract, computed once per contract; ``empty`` without a contract."""
    contract = getattr(world, "contract", None)
    if contract is None:
        return empty
    slot = (id(contract), key)
    with _LOCK:
        entry = _CACHE.get(slot)
        if entry is not None and entry[0]() is contract:
            return entry[1]  # type: ignore[no-any-return]
    value = build(contract)

    def forget(dead: "weakref.ref[Any]", slot: Tuple[int, str] = slot) -> None:
        with _LOCK:
            current = _CACHE.get(slot)
            if current is not None and current[0] is dead:
                del _CACHE[slot]

    with _LOCK:
        _CACHE[slot] = (weakref.ref(contract, forget), value)
    return value


def parse_kind(contract: Any, kind: str, model: Type[M]) -> Dict[str, M]:
    """Every mechanism of ``kind`` in the contract, its config validated by ``model``, by name."""
    return {name: model.model_validate(config_data(use))
            for name, use in contract.mechanisms.items() if isinstance(use, Mapping) and use_key(use) == kind}
