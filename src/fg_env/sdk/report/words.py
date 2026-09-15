"""Words and numbers for a reader who does not model: measure names, values in their formats, changes in points."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional

from ..clock_words import unit_word
from ..template import apply_format

__all__ = ["Namer", "QUEUE_MEASURES", "number"]

#: What a service queue's generated outputs are called (the part after the queue's name).
QUEUE_MEASURES = {"service_level": "service level", "asa": "average wait to answer", "abandon_rate": "abandonment",
                  "utilisation": "utilisation", "offered": "customers", "abandoned": "customers who gave up",
                  "cost": "staffing cost", "paid_hours": "paid hours", "intervals_below_target": "intervals below target",
                  "callbacks": "callbacks taken", "callbacks_unserved": "callbacks not served", "retrials": "retries",
                  "staff": "staff", "abandon_rate_by_interval": "abandonment"}
_SHORT_UNITS = {"second": "s", "minute": "min", "hour": "h"}
#: A contract's own description names a measure when it is at most this long; longer ones are explanations.
_NAME_LENGTH = 40


def number(value: float) -> str:
    """A plain number: thousands separated when large, three significant figures when small."""
    if not math.isfinite(value):
        return str(value)
    if abs(value) >= 100 or float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:.3g}"


class Namer:
    """Names and shows the measures of one report."""

    def __init__(self, formats: Mapping[str, str], queues: List[str], clock: Mapping[str, Any],
                 contract: Any = None, units: Optional[Mapping[str, str]] = None):
        self.formats, self.queues, self.clock, self.contract = dict(formats), list(queues), dict(clock), contract
        self.units: Dict[str, str] = dict(units or {})

    def _queue_part(self, measure: str) -> Optional[tuple]:
        for queue in self.queues:
            if measure.startswith(f"{queue}_"):
                return queue, measure[len(queue) + 1:]
        return None

    def name(self, measure: str) -> str:
        found = self._queue_part(measure)
        if found is not None:
            _, part = found
            if part.endswith("_by_interval"):
                base = part[: -len("_by_interval")]
                return f"{QUEUE_MEASURES.get(base, base.replace('_', ' '))} by {unit_word(self.clock)}"
            return QUEUE_MEASURES.get(part, part.replace("_", " "))
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
        fmt = self.formats.get(measure)
        found = self._queue_part(measure)
        if found is not None and found[1] == "asa":
            return f"{value:.0f} {_SHORT_UNITS.get(self.units.get(found[0], 'second'), '')}".strip()
        if fmt == "pct":
            return f"{value:.1%}" if abs(value) < 1 and round(value, 2) != round(value, 3) else apply_format(value, "pct")
        return apply_format(value, fmt) if fmt else number(float(value))

    def change(self, measure: str, delta: float) -> str:
        """A difference as a reader says it: ``+3.2 points`` for shares, ``+$1,200`` for money, ``+4.1`` otherwise."""
        sign = "+" if delta >= 0 else "−"
        if self.is_share(measure):
            return f"{sign}{abs(delta) * 100:.1f} points"
        return f"{sign}{self.value(measure, abs(delta))}"
