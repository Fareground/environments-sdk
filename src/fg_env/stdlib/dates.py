"""Dates: calendar arithmetic and calendar parts of ISO date texts (``2026-09-14``) and date-times (``2026-09-14T09:30``).

Dates stay text in the world, so they are saved, compared (``<`` orders ISO dates) and shown as written; these
functions read them, move them by calendar units and take them apart.
"""
from __future__ import annotations

import calendar
import datetime as _dt
from typing import Any, List, Optional, Union

from ..expr import Call, _describe, function
from ._args import fail, list_arg, number_arg, optional_text, text_arg

__all__ = ["UNITS", "PARTS", "parse_moment", "shift", "calendar_date"]

Moment = Union[_dt.date, _dt.datetime]

#: Units a date moves by.
UNITS = ("day", "week", "month", "quarter", "year", "hour", "minute")
#: Parts a date is taken apart into.
PARTS = ("year", "quarter", "month", "day", "weekday", "week", "day_of_year", "hour", "minute", "weekday_name",
         "month_name")
_MONTHS = {"month": 1, "quarter": 3, "year": 12}


def parse_moment(text: str) -> Moment:
    """A date (``YYYY-MM-DD``) or a date-time (anything longer, ISO); raises ValueError otherwise."""
    if len(text) <= 10:
        return _dt.date.fromisoformat(text)
    return _dt.datetime.fromisoformat(text)


def _written(moment: Moment) -> str:
    if isinstance(moment, _dt.datetime):
        return moment.isoformat(timespec="minutes" if moment.second == 0 and moment.microsecond == 0 else "seconds")
    return moment.isoformat()


def _unit(name: str) -> Optional[str]:
    unit = name.lower().rstrip("s")
    return unit if unit in UNITS else None


def shift(moment: Moment, amount: float, unit: str) -> Moment:
    """``moment`` moved by ``amount`` calendar units. Months, quarters and years keep the day of the month, or take
    the month's last day when it is shorter; days and weeks move a date by whole days."""
    if unit in _MONTHS:
        months = moment.month - 1 + int(amount) * _MONTHS[unit]
        year, month = moment.year + months // 12, months % 12 + 1
        return moment.replace(year=year, month=month, day=min(moment.day, calendar.monthrange(year, month)[1]))
    if unit in ("hour", "minute"):
        start = moment if isinstance(moment, _dt.datetime) else _dt.datetime.combine(moment, _dt.time())
        return start + (_dt.timedelta(hours=amount) if unit == "hour" else _dt.timedelta(minutes=amount))
    return moment + _dt.timedelta(days=amount * (7 if unit == "week" else 1))


def calendar_date(start: Optional[str], unit: str, step: int, elapsed: float) -> Optional[str]:
    """The calendar label of a moment ``elapsed`` clock steps after ``start`` (``None`` without a start or for units
    that have no calendar). Month clocks retain the start day, clamping only in shorter target months."""
    if not start:
        return None
    n = step * elapsed
    name = unit.lower().rstrip("s")
    if name in ("hour", "minute"):
        moment = _dt.datetime.fromisoformat(start)
        delta = _dt.timedelta(hours=n) if name == "hour" else _dt.timedelta(minutes=n)
        return (moment + delta).isoformat(timespec="minutes")
    first = _dt.date.fromisoformat(start[:10])
    if name in ("day", "week"):
        return (first + _dt.timedelta(days=(7 if name == "week" else 1) * n)).isoformat()
    whole = int(n)
    if name == "month":
        return shift(first, whole, "month").isoformat()
    if name == "year":
        return str(first.year + whole)
    return None


def _moment_arg(call: Call, index: int) -> Moment:
    text = text_arg(call, index, "an ISO date text like 2026-09-14")
    try:
        return parse_moment(text)
    except ValueError:
        raise fail(call, f"argument {index + 1} must be an ISO date like 2026-09-14 (or 2026-09-14T09:30), got {text!r}") from None


@function("date_add(date, n, unit?)",
          "The date `n` units after `date` (unit day, week, month, quarter, year, hour or minute; default day; a "
          "negative `n` goes back). Months keep the day, or take the month's last day: $date_add('2026-01-31', 1, "
          "month) is '2026-02-28'.", min_args=2, max_args=3)
def _date_add(call: Call) -> str:
    moment = _moment_arg(call, 0)
    amount = number_arg(call, 1)
    unit = _unit(optional_text(call, 2, "day", "a unit"))
    if unit is None:
        raise fail(call, f"the unit must be one of {', '.join(UNITS)}, got {call.arg(2)!r}")
    if unit not in ("hour", "minute") and float(amount) != int(amount):
        raise fail(call, f"moving by {unit}s takes a whole number, got {amount}")
    try:
        return _written(shift(moment, amount, unit))
    except (OverflowError, ValueError):
        raise fail(call, f"{amount} {unit}(s) from {_written(moment)} is outside the calendar") from None


@function("days_between(a, b)", "Days from date `a` to date `b`: negative when `b` is earlier, fractional between "
          "date-times ($days_between('2026-09-01', '2026-09-15') is 14).", min_args=2, max_args=2)
def _days_between(call: Call) -> Union[int, float]:
    a, b = _moment_arg(call, 0), _moment_arg(call, 1)
    if isinstance(a, _dt.datetime) or isinstance(b, _dt.datetime):
        whole_a = a if isinstance(a, _dt.datetime) else _dt.datetime.combine(a, _dt.time())
        whole_b = b if isinstance(b, _dt.datetime) else _dt.datetime.combine(b, _dt.time())
        return (whole_b - whole_a).total_seconds() / 86400
    return (b - a).days


@function("date_part(date, part)",
          "A part of a date: year, quarter (1–4), month (1–12), day, weekday (1 Monday … 7 Sunday), week (ISO week "
          "of the year, 1–53), day_of_year, hour, minute, weekday_name ('Monday') or month_name ('September').",
          min_args=2, max_args=2)
def _date_part(call: Call) -> Any:
    moment = _moment_arg(call, 0)
    part = text_arg(call, 1, "a part name")
    if part not in PARTS:
        raise fail(call, f"the part must be one of {', '.join(PARTS)}, got {part!r}")
    if part in ("hour", "minute"):
        return getattr(moment, part) if isinstance(moment, _dt.datetime) else 0
    day = moment.date() if isinstance(moment, _dt.datetime) else moment
    values = {"year": day.year, "quarter": (day.month - 1) // 3 + 1, "month": day.month, "day": day.day,
              "weekday": day.isoweekday(), "week": day.isocalendar()[1], "day_of_year": day.timetuple().tm_yday,
              "weekday_name": calendar.day_name[day.weekday()], "month_name": calendar.month_name[day.month]}
    return values[part]


@function("is_holiday(date, dates)", "True when `date`'s day is one of `dates`: ISO date texts, or rows with a "
          "`date` field (a holidays table).", min_args=2, max_args=2)
def _is_holiday(call: Call) -> bool:
    day = _written(_moment_arg(call, 0))[:10]
    listed: List[str] = []
    for item in list_arg(call, 1):
        value = item.get("date") if isinstance(item, dict) else item
        if not isinstance(value, str):
            raise fail(call, f"holidays must be ISO date texts or rows with a date field, got {_describe(item)}")
        listed.append(value[:10])
    return day in listed
