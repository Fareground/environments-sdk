"""Helpers shared by the market family's modes: reading a declared mechanism's config at run time,
evaluating number-or-expression config values, and small value helpers."""
from __future__ import annotations

from typing import Any, Type, TypeVar

from pydantic import BaseModel

from ...entity import Entity
from ..errors import RunError
from ..expr import ExprError, compile_expr, is_expr
from . import _common

__all__ = ["config_of", "number", "entity_of", "lot_floor", "fmt"]

M = TypeVar("M", bound=BaseModel)


def config_of(world: Any, name: Any, key: str, model: Type[M], where: str = "mechanisms") -> M:
    """The validated config of the mechanism ``name`` of ``key`` (``market.<mode>``) in the running contract."""
    if not isinstance(name, str):
        raise RunError(f"a market mechanism is named by text, got {name!r}", where)
    return _common.config(world, name, key, model, where)


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
