"""Words and numbers for a reader who does not model: measure names, values in their formats, changes in points."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..expr.template import apply_format
from ..runtime.clock_words import unit_word

__all__ = ["Namer", "QUEUE_MEASURES", "number"]

#: What a service queue's generated outputs are called (the part after the queue's name).
QUEUE_MEASURES = {"service_level": "service level", "asa": "average wait to answer", "aht": "average handle time",
                  "abandon_rate": "abandonment",
                  "utilisation": "utilisation", "offered": "customers", "abandoned": "customers who gave up",
                  "cost": "staffing cost", "paid_hours": "paid hours",
                  "intervals_below_target": "intervals below target",
                  "callbacks": "callbacks taken", "callbacks_unserved": "callbacks not served", "retrials": "retries",
                  "staff": "staff", "abandon_rate_by_interval": "abandonment"}
#: What the demand and replenishment modes' generated outputs and metrics are called (the part after the mechanism's
#: name).
STOCK_MEASURES = {"demand": "units asked for", "sold": "units sold", "lost": "lost sales", "fill_rate": "fill rate",
                  "revenue": "revenue", "margin": "margin", "returned": "units returned", "orders": "orders placed",
                  "units_ordered": "units ordered", "purchases": "purchases", "holding_cost": "holding cost",
                  "order_cost": "ordering cost", "stockout_cost": "stockout cost", "backorder_cost": "backorder cost",
                  "total_cost": "total cost", "profit": "profit", "average_stock_value": "average stock value",
                  "stock_value": "stock value", "on_order": "units on order", "stock": "units in stock"}
#: The demand and replenishment measures that are amounts of money, whatever format a run gave them.
_STOCK_MONEY = frozenset({"revenue", "margin", "purchases", "holding_cost", "order_cost", "stockout_cost",
                          "backorder_cost", "total_cost", "profit", "average_stock_value", "stock_value"})
_STOCK_MODES = ("demand", "replenishment")
_SPLITS = {"item": "item", "segment": "channel"}
_SHORT_UNITS = {"second": "s", "minute": "min", "hour": "h"}
#: A contract's own description names a measure when it is at most this long; longer ones are explanations.
_NAME_LENGTH = 40
#: Money at least this large is shown in whole units: $118,924, not $118,923.54.
_WHOLE_MONEY = 100


def number(value: float) -> str:
    """A plain number: thousands separated when large, three significant figures when small."""
    if not math.isfinite(value):
        return str(value)
    if abs(value) >= 100 or float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:.3g}"


class Namer:
    """Names and shows the measures of one report."""

    def __init__(self, formats: Mapping[str, str], queues: list[str], clock: Mapping[str, Any],
                 contract: Any = None, units: Mapping[str, str] | None = None):
        self.formats, self.queues, self.clock, self.contract = dict(formats), list(queues), dict(clock), contract
        self.units: dict[str, str] = dict(units or {})
        #: Demand and replenishment mechanisms by name, with the word their groups are called (``category``).
        self.stock: dict[str, str] = {}
        for name, raw in (contract.mechanisms.items() if contract is not None else ()):
            if isinstance(raw, Mapping) and raw.get("kind") == "economy" and raw.get("mode") in _STOCK_MODES:
                group = str(raw.get("group") or "")
                self.stock[name] = group[len("$it."):].replace("_", " ") if group.startswith("$it.") else "group"

    def _queue_part(self, measure: str) -> tuple | None:
        for queue in self.queues:
            if measure.startswith(f"{queue}_"):
                return queue, measure[len(queue) + 1:]
        return None

    def _stock_name(self, measure: str) -> str | None:
        """A demand or replenishment measure called by what it counts (``lost sales``, ``fill rate by category``); the
        mechanism's own name is kept only when two of the same mode could be confused."""
        for mechanism in sorted(self.stock, key=len, reverse=True):
            if not measure.startswith(f"{mechanism}_"):
                continue
            part, split = measure[len(mechanism) + 1:], ""
            for suffix in ("group", *_SPLITS):
                if part.endswith(f"_by_{suffix}"):
                    part, split = part[: -len(f"_by_{suffix}")], _SPLITS.get(suffix, self.stock[mechanism])
            if part not in STOCK_MEASURES:
                return None
            modes = [raw.get("mode") for raw in self.contract.mechanisms.values() if isinstance(raw, Mapping)]
            mode = self.contract.mechanisms[mechanism].get("mode")
            prefix = f"{mechanism.replace('_', ' ')} " if modes.count(mode) > 1 else ""
            return prefix + STOCK_MEASURES[part] + (f" by {split}" if split else "")
        return None

    def name(self, measure: str) -> str:
        found = self._queue_part(measure)
        if found is not None:
            _, part = found
            if part.endswith("_by_interval"):
                base = part[: -len("_by_interval")]
                return f"{QUEUE_MEASURES.get(base, base.replace('_', ' '))} by {unit_word(self.clock)}"
            if part.startswith("worst_interval_"):
                base = part[len("worst_interval_"):]
                return f"{QUEUE_MEASURES.get(base, base.replace('_', ' '))} in the worst {unit_word(self.clock)}"
            return QUEUE_MEASURES.get(part, part.replace("_", " "))
        stocked = self._stock_name(measure)
        if stocked is not None:
            return stocked
        spec = self.contract.outputs.get(measure) if self.contract is not None else None
        if spec is not None and spec.description and len(spec.description) <= _NAME_LENGTH:
            return spec.description.rstrip(".")
        return measure.replace("_", " ")

    def is_share(self, measure: str) -> bool:
        return self.formats.get(measure, "") in ("pct", "pct1")

    def value(self, measure: str, value: Any) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return str(value)
        fmt = self.formats.get(measure) or ("money" if self.is_money(measure) else None)
        found = self._queue_part(measure)
        if found is not None and found[1] == "asa":
            return f"{value:.0f} {_SHORT_UNITS.get(self.units.get(found[0], 'second'), '')}".strip()
        if fmt == "pct":
            return (f"{value:.1%}" if abs(value) < 1 and round(value, 2) != round(value, 3)
                    else apply_format(value, "pct"))
        if fmt == "money" and abs(value) >= _WHOLE_MONEY:
            return f"{'-' if value < 0 else ''}${abs(value):,.0f}"
        return apply_format(value, fmt) if fmt else number(float(value))

    def is_money(self, measure: str) -> bool:
        """Formatted as money, or a demand or replenishment amount of money (``stock value``, ``holding cost``)."""
        if self.formats.get(measure, "") == "money":
            return True
        return any(measure.startswith(f"{mechanism}_") and measure[len(mechanism) + 1:] in _STOCK_MONEY
                   for mechanism in self.stock)

    def change(self, measure: str, delta: float) -> str:
        """A difference as a reader says it: ``+3.2 points`` for shares, ``+$1,200`` for money (whole units from a
        dollar up, like the outcomes it is a difference of), ``+4.1`` otherwise."""
        sign = "+" if delta >= 0 else "−"
        if self.is_share(measure):
            return f"{sign}{abs(delta) * 100:.1f} points"
        if self.is_money(measure) and abs(delta) >= 1:
            return f"{sign}${abs(delta):,.0f}"
        return f"{sign}{self.value(measure, abs(delta))}"
