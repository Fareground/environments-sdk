"""How much each pattern adds to a quantity over a report's horizon, in an owner's words.

A quantity that is a product of patterns (demand = base × season × promotions × price response) is added up over the
horizon twice for every factor: as it was, and with that factor taken out (each round's amount divided by the factor's
value). The difference, as a share of the total without it, is what the factor adds. A factor that follows the
calendar is told at its highest and lowest slot (a month, a weekday, a half-hour); one that switches on and off (a
promotion, a launch, an outage) while it raises the quantity and while it lowers it; any other over the whole horizon.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Effects", "Factor", "factor_of", "clauses"]

#: A pattern that moves the quantity by less than this share (at its strongest) is not named.
TOLD = 0.01
#: A pattern description short enough to name it; longer ones explain rather than name.
_NAME_LENGTH = 40
#: What a kind of pattern is called, and how its effect is told.
_KIND_WORDS = {"trend": ("the trend", "overall"), "promotion": ("promotions", "switch"),
               "elasticity": ("price changes", "overall"),
               "cross_price": ("customers switching between items", "overall"),
               "lifecycle": ("", "switch"), "shocks": ("", "switch")}
_PERIOD_WORDS = {"year": "the time of year", "week": "the day of the week", "day": "the time of day"}


@dataclass(frozen=True)
class Factor:
    """A pattern as a report tells it: its name, ``calendar``, ``switch`` or ``overall``, and for an overall effect
    what it is measured against when that is not the horizon's own start (a trend's level at its origin)."""

    pattern: str
    word: str
    how: str
    against: str = ""


def factor_of(contract: Any, pattern: str) -> Factor:
    """A pattern's owner name — the calendar it follows, a word for its kind, or its own short description."""
    spec = contract.patterns.get(pattern) or {}
    kind = str(spec.get("kind") or "")
    described = str(spec.get("description") or "").split(" (")[0].rstrip(".")
    short = described[:1].lower() + described[1:] if described and len(described) <= _NAME_LENGTH else ""
    if kind == "seasonal":
        return Factor(pattern, _PERIOD_WORDS.get(str(spec.get("period")), short or "the time of day"), "calendar")
    word, how = _KIND_WORDS.get(kind, ("", "overall"))
    origin = spec.get("origin")
    against = f"its level on {origin}" if kind == "trend" and isinstance(origin, str) else \
        "its level at the start of the clock" if kind == "trend" else ""
    return Factor(pattern, word or short or pattern.replace("_", " "), how, against)


@dataclass
class _Tally:
    total: float = 0.0
    without: float = 0.0
    #: The rounds that added to it (many items can add in one round).
    seen: set = field(default_factory=set)

    def add(self, amount: float, value: float, round_: int) -> None:
        self.total += amount
        self.without += amount / value
        self.seen.add(round_)

    @property
    def rounds(self) -> int:
        return len(self.seen)

    @property
    def adds(self) -> float | None:
        return self.total / self.without - 1 if self.without > 0 else None


@dataclass
class _FactorTally:
    overall: _Tally = field(default_factory=_Tally)
    #: Calendar slot → (its phrase, tally), in the order first seen.
    slots: dict[str, tuple[str, _Tally]] = field(default_factory=dict)
    up: _Tally = field(default_factory=_Tally)
    down: _Tally = field(default_factory=_Tally)


@dataclass
class Effects:
    """What every factor added to one quantity (one group of one segment), round by round."""

    amount: float = 0.0
    factors: dict[str, _FactorTally] = field(default_factory=dict)

    def add(self, round_: int, amount: float, values: Mapping[str, float],
            slots: Mapping[str, tuple[str, str]]) -> None:
        """One item's ``amount`` in a round with each factor's ``values`` and, for calendar factors, ``slots``
        (``{pattern: (slot, phrase)}``, like ``("January", "in January")``). Factors at zero are left out (nothing is
        known about what they add)."""
        self.amount += amount
        for pattern, value in values.items():
            if value <= 0:
                continue
            tally = self.factors.setdefault(pattern, _FactorTally())
            tally.overall.add(amount, value, round_)
            if pattern in slots:
                slot, phrase = slots[pattern]
                tally.slots.setdefault(slot, (phrase, _Tally()))[1].add(amount, value, round_)
            if value > 1:
                tally.up.add(amount, value, round_)
            elif value < 1:
                tally.down.add(amount, value, round_)


def _share(value: float) -> str:
    return f"{abs(value):.0%}"


def _verb(value: float, plural: bool = False) -> str:
    if value >= 0:
        return "add" if plural else "adds"
    return "take away" if plural else "takes away"


def _calendar(factor: Factor, tally: _FactorTally) -> str | None:
    rows = [(phrase, slot, t.adds) for slot, (phrase, t) in tally.slots.items() if t.adds is not None]
    if not rows:
        return None
    if len(rows) == 1:
        phrase, slot, adds = rows[0]
        return f"{factor.word} ({slot}) {_verb(adds)} {_share(adds)}" if abs(adds) >= TOLD else None
    high = max(rows, key=lambda row: row[2])
    low = min(rows, key=lambda row: row[2])
    parts = []
    if high[2] >= TOLD:
        parts.append(f"adds {_share(high[2])} {high[0]}")
    if low[2] <= -TOLD:
        parts.append(f"takes away {_share(low[2])} {low[0]}")
    return f"{factor.word} " + " and ".join(parts) if parts else None


def _switch(factor: Factor, tally: _FactorTally, unit: str) -> str | None:
    plural = factor.word.endswith("s")
    parts = []
    for part, verb in ((tally.up, "raise" if plural else "raises"), (tally.down, "lower" if plural else "lowers")):
        adds = part.adds
        if adds is not None and abs(adds) >= TOLD:
            count = part.rounds
            parts.append(f"{_verb(adds, plural)} {_share(adds)} over the {count} {unit if count == 1 else unit + 's'} "
                         f"{'they' if plural else 'it'} {verb} it")
    return f"{factor.word} " + " and ".join(parts) if parts else None


def _overall(factor: Factor, tally: _FactorTally, horizon: str) -> str | None:
    adds = tally.overall.adds
    if adds is None or abs(adds) < TOLD:
        return None
    against = f", against {factor.against}" if factor.against else ""
    return f"{factor.word} {_verb(adds, factor.word.endswith('s'))} {_share(adds)} over the {horizon}{against}"


def clauses(effects: Effects, factors: Mapping[str, Factor], unit: str, horizon: str) -> dict[str, str]:
    """``{pattern: clause}`` for every factor that moves the quantity by at least :data:`TOLD` somewhere, strongest
    first: ``the time of year adds 38% in January and takes away 25% in July``."""
    ranked: list[tuple[float, str, str]] = []
    for pattern, tally in effects.factors.items():
        factor = factors.get(pattern)
        if factor is None:
            continue
        if factor.how == "calendar":
            text = _calendar(factor, tally)
            strength = max((abs(t.adds or 0.0) for _, t in tally.slots.values()), default=0.0)
        elif factor.how == "switch":
            text = _switch(factor, tally, unit)
            strength = max(abs(tally.up.adds or 0.0), abs(tally.down.adds or 0.0))
        else:
            text = _overall(factor, tally, horizon)
            strength = abs(tally.overall.adds or 0.0)
        if text:
            ranked.append((strength, pattern, text))
    ranked.sort(key=lambda row: -row[0])
    return {pattern: text for _, pattern, text in ranked}
