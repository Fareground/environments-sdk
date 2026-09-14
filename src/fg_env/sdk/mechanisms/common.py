"""Helpers shared by the market mechanisms: reading a declared mechanism's config at run time,
evaluating number-or-expression config values, and small checks for registered ops."""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel

from ...entity import Entity
from ..errors import RunError
from ..expr import ExprError, compile_expr, is_expr
from ..registry import MechanismError

__all__ = ["config_of", "number", "entity_of", "uses_of", "name_check", "lot_floor", "fmt"]

M = TypeVar("M", bound=BaseModel)


#: Parsed configs by the identity of the raw config object they came from. The raw object is kept
#: alongside, so an id can never be reused for a different config while it is cached.
_PARSED: "OrderedDict[Tuple[int, type], Tuple[Mapping[str, Any], BaseModel]]" = OrderedDict()
_PARSED_LIMIT = 1024
_PARSED_LOCK = threading.Lock()


def config_of(world: Any, name: Any, kind: str, model: Type[M]) -> M:
    """The validated config of the mechanism ``name`` of ``kind`` declared in the running contract."""
    raw = world.contract.mechanisms.get(name) if isinstance(name, str) else None
    if not isinstance(raw, Mapping) or raw.get("kind") != kind:
        declared = [n for n, u in world.contract.mechanisms.items() if isinstance(u, Mapping) and u.get("kind") == kind]
        raise MechanismError(f"'{name}' is not a declared {kind}", f"{kind} mechanisms: {', '.join(declared) or 'none'}")
    key = (id(raw), model)
    with _PARSED_LOCK:
        hit = _PARSED.get(key)
        if hit is not None and hit[0] is raw:
            _PARSED.move_to_end(key)
            parsed = hit[1]
            assert isinstance(parsed, model)
            return parsed
    fresh = model.model_validate({k: v for k, v in raw.items() if k != "kind"})
    with _PARSED_LOCK:
        _PARSED[key] = (raw, fresh)
        while len(_PARSED) > _PARSED_LIMIT:
            _PARSED.popitem(last=False)
    return fresh


def uses_of(world: Any, kind: str) -> Dict[str, Mapping[str, Any]]:
    """Every declared mechanism of ``kind``: {use name: raw config}."""
    return {n: u for n, u in world.contract.mechanisms.items() if isinstance(u, Mapping) and u.get("kind") == kind}


def number(world: Any, raw: Any, where: str, **vars: Any) -> float:
    """A config value that is a number or an expression giving one."""
    value = raw
    if isinstance(raw, str) and is_expr(raw):
        try:
            value = compile_expr(raw)(world.scope(**vars))
        except ExprError as exc:
            raise RunError(str(exc), where) from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunError(f"must be a number, got {value!r}", where)
    return float(value)


def entity_of(world: Any, value: Any, where: str, what: str = "an entity") -> Entity:
    found = world.entity(value)
    if found is None or not found.alive:
        raise RunError(f"expected {what}, got {value!r}", where)
    return found


def lot_floor(qty: float, lot: float) -> float:
    """``qty`` rounded down to a whole number of lots."""
    if lot <= 0:
        return max(0.0, qty)
    lots = int(qty / lot + 1e-9)
    return round(lots * lot, 10)


def fmt(value: float, digits: int = 2) -> str:
    """A compact number: no trailing zeros."""
    text = f"{value:,.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def name_check(op: str, kind: str, actions: Tuple[str, ...]) -> Callable[[Any, Dict[str, Any], str], List[Tuple[str, str, Optional[str]]]]:
    """A static check for an op ``{"<op>": <use name>, "action": ...}``: the name is a declared
    mechanism of ``kind`` and the action is one of ``actions``."""

    def check(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
        issues: List[Tuple[str, str, Optional[str]]] = []
        name = effect.get(op)
        mechanisms = getattr(checker.c, "mechanisms", {}) or {}
        declared = [n for n, u in mechanisms.items() if isinstance(u, Mapping) and u.get("kind") == kind]
        if name not in declared:
            issues.append((f"{path}.{op}", f"'{name}' is not a declared {kind}",
                           f"{kind} mechanisms: {', '.join(declared) or 'none'}"))
        action = effect.get("action")
        if action not in actions:
            issues.append((f"{path}.action", f"action must be one of {', '.join(actions)}, got {action!r}", None))
        return issues

    return check
