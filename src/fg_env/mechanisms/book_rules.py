"""An order book's venue rules: tick and lot sizes, fees, collar, price band, circuit breaker, short limits, order
expiry and bar length — each a number or an expression over ``$inputs`` (like a world default).

The rules are resolved once, when the world is built, into the world prop ``<name>_rules``, so they can derive
from the price level or an input (a tick of five significant figures, fees by asset class) and still be swept or
calibrated like any input; snapshots, clones and forks carry the resolved numbers. The engine reads them through
:func:`venue`.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from ..errors import RunError
from ..expr import Call, ExprError, function

__all__ = ["CLOSES_WINDOW", "RULES", "Venue", "rules_default", "venue"]

#: Closes kept for coded strategies and rolling breaker references (the full history is in the bars record).
CLOSES_WINDOW = 256


class _Limit(NamedTuple):
    whole: bool
    low: float
    low_allowed: bool
    high: float | None
    optional: bool


#: Every rule and the values it accepts.
RULES: dict[str, _Limit] = {
    "tick_size": _Limit(False, 0, False, None, False),
    "lot_size": _Limit(False, 0, False, None, False),
    "maker_fee_bps": _Limit(False, -1000, True, 1000, False),  # below 0: a rebate, paid out of the taker fee
    "taker_fee_bps": _Limit(False, 0, True, 1000, False),
    "collar_pct": _Limit(False, 0, False, 1, False),
    "price_band_pct": _Limit(False, 0, False, 10, False),
    "halt_pct": _Limit(False, 0, True, 1, True),
    "halt_rounds": _Limit(True, 0, True, None, False),
    "halt_window": _Limit(True, 1, True, CLOSES_WINDOW, True),
    "short_limit": _Limit(False, 0, True, None, False),
    "max_short_leverage": _Limit(False, 0, False, None, True),
    "order_ttl": _Limit(True, 1, True, None, True),
    "max_orders": _Limit(True, 1, True, None, False),
    "bar_rounds": _Limit(True, 1, True, None, False),
}


@dataclass(frozen=True)
class Venue:
    """The resolved rules of one book (fees also as fractions of notional)."""

    tick: float
    lot: float
    maker_fee_bps: float
    taker_fee_bps: float
    collar: float
    band: float
    halt_pct: float | None
    halt_rounds: int
    halt_window: int | None
    short_limit: float
    max_short_leverage: float | None
    order_ttl: int | None
    max_orders: int
    bar_rounds: int

    @property
    def maker(self) -> float:
        return self.maker_fee_bps / 1e4

    @property
    def taker(self) -> float:
        return self.taker_fee_bps / 1e4

    @property
    def hold(self) -> float:
        """Cash a resting buy reserves per unit of notional: the price and the maker fee (a rebate reserves nothing)."""
        return 1 + max(self.maker, 0.0)


def rules_default(name: str, config: Any) -> Any:
    """The default of ``<name>_rules``: the literal rules, or an expression that resolves and checks them."""
    values = {rule: getattr(config, rule) for rule in RULES if getattr(config, rule) is not None}
    if not any(isinstance(value, str) for value in values.values()):
        return values
    parts = ", ".join(f"{rule}: ({value})" if isinstance(value, str) else f"{rule}: {value!r}"
                      for rule, value in values.items())
    return f"$book_rules({name}, {{{parts}}})"


def _checked(name: str, rule: str, value: Any) -> Any:
    limit = RULES.get(rule)
    if limit is None:
        raise ValueError(f"'{rule}' is not a venue rule (rules: {', '.join(RULES)})")
    if value is None and limit.optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"mechanisms.{name}.{rule} must be a number, got {value!r}")
    if limit.whole:
        if float(value) != int(value):
            raise ValueError(f"mechanisms.{name}.{rule} must be a whole number, got {value!r}")
        value = int(value)
    if value < limit.low or (value == limit.low and not limit.low_allowed):
        edge = "at least" if limit.low_allowed else "above"
        raise ValueError(f"mechanisms.{name}.{rule} must be {edge} {limit.low:g}, got {value!r}")
    if limit.high is not None and value > limit.high:
        raise ValueError(f"mechanisms.{name}.{rule} must be at most {limit.high:g}, got {value!r}")
    return value


def _rebate_checked(name: str, values: dict[str, Any]) -> dict[str, Any]:
    """A maker rebate is paid out of the taker fee of the same fill, so it is at most that fee."""
    maker, taker = values.get("maker_fee_bps"), values.get("taker_fee_bps", 0)
    if maker is not None and maker < 0 and -maker > taker:
        raise ValueError(f"mechanisms.{name}.maker_fee_bps {maker:g} is a rebate larger than the taker fee "
                         f"({taker:g} bps) that pays it; set maker_fee_bps to at least {-taker:g}")
    return values


@function("book_rules(name, rules)", "An order book's venue rules {tick_size, lot_size, ...}, each checked against "
          "its limits; the book's generated `<name>_rules` world prop resolves its expressions through it.", min_args=2,
          max_args=2)
def _rules_function(call: Call) -> dict[str, Any]:
    name, rules = call.arg(0), call.arg(1)
    if not isinstance(rules, Mapping):
        raise ExprError(f"$book_rules: expected a map of rules, got {rules!r}", call.source)
    try:
        return _rebate_checked(str(name), {rule: _checked(str(name), rule, value) for rule, value in rules.items()})
    except ValueError as exc:
        raise ExprError(str(exc), call.source) from None


_CACHE: dict[int, tuple[Mapping[str, Any], Venue]] = {}
_CACHE_SIZE = 256


def venue(world: Any, name: str) -> Venue:
    """The rules of the book ``name`` as resolved in this world (checked once per stored rules map)."""
    rules = world.props.get(f"{name}_rules")
    hit = _CACHE.get(id(rules))
    if hit is not None and hit[0] is rules:
        return hit[1]
    if not isinstance(rules, Mapping):
        raise RunError(f"the book has no venue rules in $world.{name}_rules", f"mechanisms.{name}")
    try:
        values = _rebate_checked(name, {rule: _checked(name, rule, rules.get(rule)) for rule in RULES
                                        if rule in rules or not RULES[rule].optional})
    except ValueError as exc:
        raise RunError(str(exc), f"world.{name}_rules") from None
    halt = values.get("halt_pct")
    resolved = Venue(
        tick=float(values["tick_size"]), lot=float(values["lot_size"]), maker_fee_bps=float(values["maker_fee_bps"]),
        taker_fee_bps=float(values["taker_fee_bps"]), collar=float(values["collar_pct"]),
        band=float(values["price_band_pct"]), halt_pct=float(halt) if halt else None, halt_rounds=values["halt_rounds"],
        halt_window=values.get("halt_window"), short_limit=float(values["short_limit"]),
        max_short_leverage=values.get("max_short_leverage"), order_ttl=values.get("order_ttl"),
        max_orders=values["max_orders"], bar_rounds=values["bar_rounds"])
    if len(_CACHE) >= _CACHE_SIZE:
        _CACHE.clear()
    _CACHE[id(rules)] = (rules, resolved)
    return resolved
