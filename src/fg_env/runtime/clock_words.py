"""Rounds named the way a reader counts time: ``half-hour``, ``09:30–10:00``, ``Week 7 (2026-10-12)``.

A run's clock (``RunResult.clock``: mode, unit, step, start) says what one round is. A clock of minutes or hours
names its rounds by the time of day they cover — with the weekday and date when the run spans more than a day — and a
clock of days, weeks or months by its unit, number and calendar date. Summaries, narratives, highlights and owner
reports all name rounds through here, so they agree.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Mapping, Optional

from ..stdlib.dates import calendar_date

__all__ = ["unit_word", "plural", "period_label", "span_label", "round_start", "sub_day"]

#: Minutes per round that have a name of their own.
_MINUTE_WORDS = {15: "quarter-hour", 30: "half-hour", 60: "hour"}
_SECONDS = {"second": 1, "minute": 60, "hour": 3600}


def _unit(clock: Mapping[str, Any]) -> str:
    return str(clock.get("unit") or "round").lower().rstrip("s") or "round"


def _step(clock: Mapping[str, Any]) -> int:
    return int(clock.get("step") or 1)


def sub_day(clock: Mapping[str, Any]) -> bool:
    """Rounds shorter than a day (a clock of seconds, minutes or hours on rounds)."""
    return clock.get("mode", "rounds") == "rounds" and _unit(clock) in _SECONDS


def unit_word(clock: Mapping[str, Any]) -> str:
    """What one round is called: ``half-hour`` (30 minutes), ``week`` (7 days), ``2-hour period``, ``round``."""
    if clock.get("mode", "rounds") != "rounds":
        return "round"
    unit, step = _unit(clock), _step(clock)
    if unit in _SECONDS:
        minutes = step * _SECONDS[unit] / 60
        if minutes in _MINUTE_WORDS:
            return _MINUTE_WORDS[int(minutes)]
        return unit if step == 1 else f"{step}-{unit} period"
    if unit == "day" and step == 7:
        return "week"
    return unit if step == 1 else f"{step}-{unit} period"


def plural(word: str, count: int) -> str:
    """``word`` for one, ``words`` for any other count."""
    return word if count == 1 else f"{word}s"


def round_start(clock: Mapping[str, Any], round_: int) -> Optional[_dt.datetime]:
    """When a sub-day round starts, or None without a start date-time."""
    start = clock.get("start")
    if not start or not sub_day(clock):
        return None
    try:
        first = _dt.datetime.fromisoformat(str(start))
    except ValueError:
        return None
    return first + _dt.timedelta(seconds=_SECONDS[_unit(clock)] * _step(clock) * max(0, round_ - 1))


def _clock_time(moment: _dt.datetime) -> str:
    return moment.strftime("%H:%M")


def _day(moment: _dt.datetime) -> str:
    return f"{moment.strftime('%a')} {moment.day} {moment.strftime('%b')}"


def period_label(clock: Mapping[str, Any], round_: int, *, capital: bool = False, rounds: Optional[int] = None) -> str:
    """One round as a reader names it: ``09:30–10:00`` (``Mon 14 Sep 09:30–10:00`` when ``rounds`` span more than a
    day), ``Week 7 (2026-10-12)``, ``week 7`` without a date, ``round 7`` on a clock without units."""
    begin = round_start(clock, round_)
    if begin is not None:
        end = begin + _dt.timedelta(seconds=_SECONDS[_unit(clock)] * _step(clock))
        text = f"{_clock_time(begin)}–{_clock_time(end)}"
        return f"{_day(begin)} {text}" if _spans_days(clock, rounds) else text
    word = unit_word(clock)
    text = f"{word[:1].upper() + word[1:] if capital else word} {round_}"
    unit = _unit(clock)
    date = None if word == "round" or unit in _SECONDS else calendar_date(clock.get("start"), unit, _step(clock),
                                                                          max(0, round_ - 1))
    return f"{text} ({date})" if date else text


def span_label(clock: Mapping[str, Any], first: int, last: int, *, rounds: Optional[int] = None) -> str:
    """Rounds ``first`` to ``last`` together: ``09:00–11:00``, ``weeks 3–5 (2026-01-19 to 2026-02-02)``."""
    if first == last:
        return period_label(clock, first, rounds=rounds)
    begin, final = round_start(clock, first), round_start(clock, last)
    if begin is not None and final is not None:
        end = final + _dt.timedelta(seconds=_SECONDS[_unit(clock)] * _step(clock))
        text = f"{_clock_time(begin)}–{_clock_time(end)}"
        return f"{_day(begin)} {text}" if _spans_days(clock, rounds) else text
    word = unit_word(clock)
    text = f"{plural(word, 2)} {first}–{last}"
    unit = _unit(clock)
    if word != "round" and unit not in _SECONDS:
        a = calendar_date(clock.get("start"), unit, _step(clock), max(0, first - 1))
        b = calendar_date(clock.get("start"), unit, _step(clock), max(0, last - 1))
        if a and b:
            text += f" ({a} to {b})"
    return text


def _spans_days(clock: Mapping[str, Any], rounds: Optional[int]) -> bool:
    if rounds is None:
        return False
    first, last = round_start(clock, 1), round_start(clock, rounds)
    return first is not None and last is not None and first.date() != last.date()
