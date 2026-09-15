"""Time as patterns read it: clock units since round 1, calendar moments, and positions within a period.

``t`` counts clock units from the start of round 1 (round 1 is ``t = 0``; with ``clock.step`` 7 and unit ``day``,
round 2 is ``t = 7``). On a continuous clock ``t`` is the clock time. With ``clock.start`` every ``t`` is also a
calendar moment, so yearly, weekly and daily positions follow the real calendar; without one a named period is
converted from the clock unit and starts at round 1.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from typing import Any, List, Optional

from ..stdlib.dates import parse_moment

__all__ = ["UNIT_DAYS", "Calendar", "calendar_of", "now", "step_length", "unit_days", "moment", "to_t", "position",
           "slot", "days_covered", "parse_date", "PERIODS"]

#: Days in one clock unit (months and years on average).
UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1.0, "week": 7.0, "month": 365.25 / 12, "year": 365.25}
#: Named periods and their length in days.
PERIODS = {"year": 365.25, "quarter": 365.25 / 4, "month": 365.25 / 12, "week": 7.0, "day": 1.0, "hour": 1 / 24}


@dataclass(frozen=True)
class Calendar:
    """The clock as patterns read it: the resolved start date (``clock.start`` may be read from ``$inputs``), the unit,
    the units per round and whether time is continuous."""

    start: Optional[str]
    unit: str
    step: int
    mode: str
    tick: float


def calendar_of(world: Any) -> Calendar:
    """The calendar of a built world."""
    clock = world.contract.clock
    return Calendar(world.start, clock.unit, clock.step, clock.mode, clock.tick)


def _unit(clock: Any) -> str:
    return str(clock.unit).lower().rstrip("s")


def unit_days(clock: Any) -> Optional[float]:
    """Days in one clock unit, or None for a unit without a calendar length (turn, round, tick)."""
    return UNIT_DAYS.get(_unit(clock))


def now(world: Any) -> float:
    """``t`` for the world as it is: clock time when continuous, else units since round 1 (0 before it)."""
    if world.continuous:
        return float(world.time)
    return float(world.contract.clock.step * max(0, world.round - 1))


def step_length(clock: Any) -> float:
    """Clock units in one step of a random process: one round (``clock.step``), or ``clock.tick`` when continuous."""
    return float(clock.tick) if clock.mode == "continuous" else float(clock.step)


def parse_date(text: str) -> _dt.datetime:
    """An ISO date or date-time. Raises ValueError with the text when it is neither."""
    try:
        parsed = parse_moment(text.strip())
    except ValueError:
        raise ValueError(f"'{text}' is not an ISO date (YYYY-MM-DD)") from None
    return parsed if isinstance(parsed, _dt.datetime) else _dt.datetime.combine(parsed, _dt.time())


def moment(clock: Any, t: float) -> Optional[_dt.datetime]:
    """The calendar moment at ``t``, or None without ``clock.start`` or a calendar unit."""
    if not clock.start:
        return None
    unit = _unit(clock)
    start = parse_date(clock.start)
    if unit in ("minute", "hour", "day", "week"):
        return start + _dt.timedelta(days=UNIT_DAYS[unit] * t)
    if unit == "month":
        whole = math.floor(t)
        months = start.month - 1 + whole
        first = start.replace(year=start.year + months // 12, month=months % 12 + 1, day=min(start.day, 28))
        return first + _dt.timedelta(days=(t - whole) * UNIT_DAYS["month"])
    if unit == "year":
        return start.replace(year=start.year + math.floor(t))
    return None


def to_t(clock: Any, when: Any) -> float:
    """``when`` as ``t``: a number is already clock units; a date needs ``clock.start`` and a calendar unit."""
    if isinstance(when, bool):
        raise ValueError(f"{when!r} is not a time")
    if isinstance(when, (int, float)):
        return float(when)
    if not isinstance(when, str):
        raise ValueError(f"{when!r} is not a time: give clock units from round 1 or an ISO date")
    at = parse_date(when)
    days = unit_days(clock)
    if not clock.start or days is None:
        raise ValueError(f"the date '{when}' needs clock.start and a calendar clock.unit (day, week, month …)")
    return (at - parse_date(clock.start)).total_seconds() / 86400 / days


def position(clock: Any, t: float, period: Any) -> float:
    """Where ``t`` falls in ``period`` (a named period or a number of clock units), from 0 up to 1."""
    if isinstance(period, (int, float)) and not isinstance(period, bool):
        return (t / period) % 1.0
    when = moment(clock, t)
    if when is not None and period in ("year", "week", "day"):
        if period == "day":
            return (when.hour * 3600 + when.minute * 60 + when.second) / 86400
        if period == "week":
            return (when.weekday() + (when.hour * 3600 + when.minute * 60) / 86400) / 7
        year_days = 366 if _leap(when.year) else 365
        return (when.timetuple().tm_yday - 1 + (when.hour * 3600 + when.minute * 60) / 86400) / year_days
    days = unit_days(clock)
    if days is None:
        raise ValueError(f"a '{period}' period needs a calendar clock.unit (day, week, month …), not '{clock.unit}'; "
                         "give the period as a number of rounds instead")
    return (t * days / PERIODS[str(period)]) % 1.0


def slot(clock: Any, t: float, period: Any, slots: int) -> int:
    """The profile slot ``t`` falls in: calendar months for 12 slots over a year, weekdays for 7 over a week."""
    when = moment(clock, t)
    if when is not None and period == "year" and slots == 12:
        return when.month - 1
    if when is not None and period == "week" and slots == 7:
        return when.weekday()
    if when is not None and period == "day" and slots == 24:
        return when.hour
    return min(slots - 1, int(position(clock, t, period) * slots))


def days_covered(clock: Any, t: float) -> List[_dt.date]:
    """The calendar days one round starting at ``t`` covers (one day for daily or shorter rounds)."""
    first = moment(clock, t)
    if first is None:
        return []
    days = unit_days(clock) or 1.0
    length = max(1, round(days * (clock.step if clock.mode != "continuous" else 1)))
    if days < 1:
        return [first.date()]
    return [first.date() + _dt.timedelta(days=i) for i in range(length)]


def _leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
