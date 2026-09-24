"""Conserved value moves shared by the market mechanisms.

Every change of money or goods a market makes is a :func:`move`: the same amount leaves one
account and arrives in another, through the world's journaled API. A move that would take an
account below its floor refuses with :class:`~fg_env.world.live.Abort`, so the enclosing action
rolls back and the agent reads why. Nothing here creates or destroys value, so a market needs no
conservation check of its own and composes with other markets and with the author's own wages, taxes and
dividends on the same cash. An ``economy`` ledger that proves its currency is conserved counts the money
markets hold for their traders (:func:`market_places`).

An account is an entity property (``Account(entity, "cash")``) or a world property
(``Account(None, "auction_revenue")``).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..expr.objects import Entity
from ..expr.template import format_value
from ..registry import use_key
from ..world.live import Abort

__all__ = ["Account", "EPS", "move", "balance", "clean", "whole", "market_places"]

#: Balances within this of a floor count as at the floor (float dust from fee arithmetic).
EPS = 1e-7


def clean(value: float) -> float:
    """Round away float dust so repeated fills never leave 1e-13 remainders."""
    rounded = round(value, 9)
    return 0.0 if rounded == 0 else rounded


def whole(value: float) -> bool:
    return abs(value - round(value)) < 1e-9


#: Where each market mode keeps money its traders put in, for a use named ``{}``: (trader props, world props).
_HELD = {
    "market.order_book": (("{}_reserved_cash",), ("{}_fees",)),
    "market.prediction": ((), ("{}_vault", "{}_fees")),
    "market.auction": (("{}_escrow",), ("{}_revenue",)),
    "market.posted": ((), ("{}_ad_revenue",)),
}


def market_places(mechanisms: Mapping[str, Any], currency: str) -> tuple[list[str], list[str]]:
    """The entity and world properties where the declared markets of ``currency`` hold money."""
    entity_props: list[str] = []
    world_props: list[str] = []
    for name, raw in (mechanisms or {}).items():
        held = _HELD.get(use_key(raw) or "")
        if held is not None and raw.get("currency", "cash") == currency:
            entity_props += [prop.format(name) for prop in held[0]]
            world_props += [prop.format(name) for prop in held[1]]
    return entity_props, world_props


@dataclass(frozen=True)
class Account:
    """Where value sits: an entity's property, or a world property when ``entity`` is None."""

    entity: Entity | None
    prop: str

    def label(self) -> str:
        return self.entity.name if self.entity is not None else "the market"


def balance(world: Any, account: Account) -> float:
    raw = account.entity.properties.get(account.prop) if account.entity is not None else world.props.get(account.prop)
    if raw is None:
        return 0.0
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise Abort(f"{account.label()}'s {account.prop} is not a number ({raw!r}).")
    return float(raw)


def _store(world: Any, account: Account, value: float) -> None:
    number: Any = clean(value)
    spec = (world.prop_spec(account.entity, account.prop) if account.entity is not None
            else world.contract.world.get(account.prop))
    if spec is not None and (spec.type == "int" or (spec.type is None and isinstance(spec.default, int)
                                                    and not isinstance(spec.default, bool) and whole(number))):
        number = int(round(number))
    if account.entity is not None:
        world.set_prop(account.entity, account.prop, number)
    else:
        world.set_world(account.prop, number)


def move(world: Any, source: Account, target: Account, amount: float, *, what: str = "",
         floor: float = 0.0) -> None:
    """Move ``amount`` from ``source`` to ``target``. ``floor`` is the lowest the source may reach
    (negative for short positions). Refuses (rolls the action back) instead of overdrawing."""
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
        raise Abort(f"cannot move {amount!r} {what or source.prop}")
    if amount == 0 or source == target:
        return
    have = balance(world, source)
    if have - amount < floor - EPS:
        available = max(0.0, have - floor)
        raise Abort(f"{source.label()} has only {format_value(round(available, 6))} {what or source.prop} available; "
                    f"{format_value(round(amount, 6))} is needed.")
    remaining = have - amount
    if abs(remaining - floor) < EPS:
        remaining = floor
    _store(world, source, remaining)
    _store(world, target, balance(world, target) + amount)
