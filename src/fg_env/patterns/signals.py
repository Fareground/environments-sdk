"""Time patterns: trends, seasons, calendar effects, cycles, lifecycles, step changes and data series.

Each is a function of ``t`` (clock units since round 1; see :mod:`.timebase`) and its parameters.
"""
from __future__ import annotations

import bisect
import calendar as _calendar
import datetime as _dt
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import timebase as tb
from .base import Moment, Number, PatternConfig, kind

__all__ = ["TrendConfig", "SeasonalConfig", "CalendarConfig", "CycleConfig", "LifecycleConfig", "StepConfig",
           "SeriesConfig", "when", "wave"]

NamedPeriod = Literal["year", "quarter", "month", "week", "day", "hour"]


def when(ctx: Any, value: Any, field: str) -> float:
    """A parameter naming a time (clock units or an ISO date) as ``t``."""
    try:
        return tb.to_t(ctx.clock, value)
    except ValueError as exc:
        raise ctx.fail(f"`{field}`: {exc}") from None


def _position(ctx: Any, t: float, period: Any) -> float:
    try:
        return tb.position(ctx.clock, t, period)
    except ValueError as exc:
        raise ctx.fail(str(exc)) from None


# ---------------------------------------------------------------------------
# trend
# ---------------------------------------------------------------------------


class TrendConfig(PatternConfig):
    kind: Literal["trend"] = "trend"
    form: Literal["linear", "exponential", "logistic"] = Field("linear", description="linear: start + slope·t | "
                                                           "exponential: start·e^(rate·t) | logistic: capacity / (1 + "
                                                           "e^(−steepness·(t − midpoint))).")
    start: Number = Field(1.0, description="Value at `origin` (linear, exponential).")
    slope: Number = Field(0.0, description="Change per clock unit (linear).")
    rate: Number = Field(0.0, description="Growth per clock unit, as a log rate: 0.01 ≈ +1% a unit (exponential).")
    capacity: Number = Field(1.0, description="The level it saturates at (logistic).")
    midpoint: Number = Field(0.0, description="Clock units after `origin` when it is half way (logistic).")
    steepness: Number = Field(1.0, description="How fast it rises around the midpoint (logistic).")
    origin: Moment = Field(0.0, description="Where t counts from: clock units from round 1, or an ISO date.")


def _trend_words(cfg: TrendConfig) -> str:
    if cfg.form == "linear":
        return f"a straight-line trend from {cfg.start} changing by {cfg.slope} per unit"
    if cfg.form == "exponential":
        return f"exponential growth from {cfg.start} at log rate {cfg.rate} per unit"
    return f"an S-curve rising to {cfg.capacity}, half way {cfg.midpoint} units in"


@kind("trend", "time", "signal", TrendConfig, "A long-run trend: linear, exponential or a logistic S-curve.",
      example={"kind": "trend", "form": "exponential", "start": 100, "rate": "$inputs.growth"},
      words=_trend_words, params=("start", "slope", "rate", "capacity", "midpoint", "steepness", "origin"),
      context={"inputs": {"growth": {"type": "number", "default": 0.02}}})
def _trend(ctx: Any) -> float:
    cfg: TrendConfig = ctx.cfg
    tau = ctx.t - when(ctx, ctx.param("origin"), "origin")
    if cfg.form == "linear":
        return ctx.number("start") + ctx.number("slope") * tau
    if cfg.form == "exponential":
        exponent = ctx.number("rate") * tau
        if exponent > 700:
            raise ctx.fail(f"grows past what a number holds (rate × t = {exponent:.3g})")
        return ctx.number("start") * math.exp(exponent)
    z = -ctx.number("steepness") * (tau - ctx.number("midpoint"))
    return ctx.number("capacity") / (1 + math.exp(min(z, 700)))


# ---------------------------------------------------------------------------
# seasonal
# ---------------------------------------------------------------------------


class SeasonalConfig(PatternConfig):
    kind: Literal["seasonal"] = "seasonal"
    period: NamedPeriod | float = Field("year", description="year, quarter, month, week, day, hour — or a number of "
                                                            "clock units. With clock.start, year, week and day follow "
                                                            "the calendar.")
    profile: list[Number] | str | None = Field(None, description="One value per slot of the period: 12 over a year "
                                                                 "are calendar months, 7 over a week weekdays (Monday "
                                                                 "first), 24 over a day hours; other counts are equal "
                                                                 "slices.")
    amplitude: Number = Field(0.0, description="Height of a smooth yearly-style wave (0.2 = ±20% with form multiply).")
    peak: Number = Field(0.0, description="Where in the period the wave peaks, from 0 to 1 (0.5 = the middle).")
    harmonics: list[list[Number]] | str | None = Field(None, description="[[sin, cos], …]: the k-th pair is a wave k "
                                                                         "times per period (fitted by harmonic "
                                                                         "regression).")
    form: Literal["multiply", "add"] = Field("multiply",
                                             description="multiply: an index around 1 (profile × (1 + waves)) | add: "
                                                         "an amount around 0 (profile + waves).")


def wave(position: float, amplitude: float, peak: float, harmonics: list[list[float]]) -> float:
    """``amplitude·cos(2π(position − peak)) + Σ sin_k·sin(2πk·position) + cos_k·cos(2πk·position)``."""
    total = amplitude * math.cos(2 * math.pi * (position - peak)) if amplitude else 0.0
    for k, pair in enumerate(harmonics, start=1):
        angle = 2 * math.pi * k * position
        total += pair[0] * math.sin(angle) + pair[1] * math.cos(angle)
    return total


def _harmonics(ctx: Any) -> list[list[float]]:
    raw = ctx.param("harmonics")
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(p, list) and len(p) == 2 and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in p) for p in raw):
        raise ctx.fail(f"`harmonics` must be a list of [sin, cos] number pairs, got {raw!r}")
    return [[float(a), float(b)] for a, b in raw]


def _seasonal_words(cfg: SeasonalConfig) -> str:
    parts = []
    if cfg.profile is not None:
        parts.append(f"a {len(cfg.profile) if isinstance(cfg.profile, list) else 'data'}-slot profile")
    if cfg.amplitude:
        parts.append(f"a wave of amplitude {cfg.amplitude} peaking at {cfg.peak} of the period")
    if cfg.harmonics:
        parts.append(f"{len(cfg.harmonics) if isinstance(cfg.harmonics, list) else 'fitted'} harmonics")
    how = "multiplies" if cfg.form == "multiply" else "adds to"
    return f"a {cfg.period} seasonality ({' and '.join(parts) or 'flat'}) that {how} what reads it"


@kind("seasonal", "time", "signal", SeasonalConfig,
      "A repeating season: a profile per month, weekday or hour, a smooth wave, or harmonics — around 1 or 0.",
      example={"kind": "seasonal", "period": "year",
               "profile": [0.8, 0.8, 0.9, 1, 1.1, 1.2, 1.3, 1.2, 1.1, 1, 0.9, 0.7]},
      words=_seasonal_words, params=("profile", "amplitude", "peak", "harmonics"),
      context={"clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"}})
def _seasonal(ctx: Any) -> float:
    cfg: SeasonalConfig = ctx.cfg
    base = 1.0 if cfg.form == "multiply" else 0.0
    if cfg.profile is not None:
        profile = ctx.numbers("profile")
        if not profile:
            raise ctx.fail("`profile` needs at least one value")
        base = profile[tb.slot(ctx.clock, ctx.t, cfg.period, len(profile))]
    shape = wave(_position(ctx, ctx.t, cfg.period), ctx.number("amplitude"), ctx.number("peak"), _harmonics(ctx)) \
        if cfg.amplitude or cfg.harmonics is not None else 0.0
    return base * (1 + shape) if cfg.form == "multiply" else base + shape


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class CalendarEffect(BaseModel):
    """An effect on the days it matches."""

    model_config = ConfigDict(extra="forbid")

    on: Literal["weekend", "weekday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                "dates", "days_of_month", "month_start", "month_end", "months"] = Field(
        ..., description="Which days: weekend, weekday, a named day, listed `dates`, `days` of the month (paydays), "
                         "the first or last `days` of a month, or listed `months`.")
    effect: Number = Field(..., description="Multiplier on those days (form multiply) or amount added (form add).")
    dates: list[str] = Field(default_factory=list, description="ISO dates (2025-11-28) or yearly dates (12-25).")
    days: int | list[int] | None = Field(None, description="days_of_month: the days of the month ([1, 15]); "
                                                           "month_start/month_end: how many days (default 1).")
    months: list[int] = Field(default_factory=list, description="months: month numbers 1–12.")
    before: int = Field(0, ge=0, description="Days before each matched date also affected (dates, days_of_month).")
    after: int = Field(0, ge=0, description="Days after each matched date also affected (dates, days_of_month).")

    @model_validator(mode="after")
    def _shape(self) -> CalendarEffect:
        if self.on == "dates" and not self.dates:
            raise ValueError("an effect on `dates` needs `dates`")
        if self.on == "days_of_month" and (not isinstance(self.days, list) or not self.days):
            raise ValueError("an effect on `days_of_month` needs `days`: a list of days of the month")
        if self.on in ("month_start", "month_end") and isinstance(self.days, list):
            raise ValueError(f"an effect on `{self.on}` takes `days` as a number of days")
        if self.on == "months" and not self.months:
            raise ValueError("an effect on `months` needs `months`")
        for text in self.dates:
            try:
                _dt.date.fromisoformat(text if len(text) > 5 else f"2000-{text}")
            except ValueError:
                raise ValueError(f"'{text}' is not a date: write YYYY-MM-DD, or MM-DD for every year") from None
        return self


class CalendarConfig(PatternConfig):
    kind: Literal["calendar"] = "calendar"
    effects: list[CalendarEffect] = Field(..., description="[{on, effect, dates, days, months, before, after}]: every "
                                                           "matching effect applies to a day.")
    form: Literal["multiply", "add"] = Field("multiply",
                                             description="multiply: effects multiply a base of 1 | add: effects add to "
                                                         "0.")


def _matches(effect: dict[str, Any], day: _dt.date) -> bool:
    on = effect["on"]
    if on == "weekend":
        return day.weekday() >= 5
    if on == "weekday":
        return day.weekday() < 5
    if on in _DAYS:
        return day.weekday() == _DAYS.index(on)
    if on == "months":
        return day.month in effect["months"]
    last = _calendar.monthrange(day.year, day.month)[1]
    if on == "month_start":
        return day.day <= (effect["days"] or 1)
    if on == "month_end":
        return last - day.day < (effect["days"] or 1)
    before, after = effect["before"], effect["after"]
    for offset in range(-after, before + 1):
        near = day + _dt.timedelta(days=offset)
        if on == "days_of_month" and near.day in effect["days"]:
            return True
        if on == "dates" and (near.isoformat() in effect["dates"] or near.isoformat()[5:] in effect["dates"]):
            return True
    return False


def _calendar_words(cfg: CalendarConfig) -> str:
    return "calendar effects on " + ", ".join(f"{e.on} (×{e.effect})" if cfg.form == "multiply"
                                              else f"{e.on} (+{e.effect})"
                                              for e in cfg.effects)


@kind("calendar", "time", "signal", CalendarConfig,
      "Calendar effects — weekends, named days, holidays, paydays, month ends, months — per day; a longer round "
      "averages the days it covers. Needs clock.start and a calendar unit.",
      example={"kind": "calendar", "effects": [{"on": "weekend", "effect": 1.3},
                                               {"on": "dates", "dates": ["12-25"], "effect": 0.1, "before": 0}]},
      words=_calendar_words, params=("effects",),
      context={"clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"}})
def _calendar_effects(ctx: Any) -> float:
    days = tb.days_covered(ctx.clock, ctx.t)
    if not days:
        raise ctx.fail("calendar effects need clock.start and a calendar clock.unit (day, week, hour …)")
    effects = ctx.param("effects")
    multiply = ctx.cfg.form == "multiply"
    total = 0.0
    for day in days:
        value = 1.0 if multiply else 0.0
        for index, effect in enumerate(effects):
            if not _matches(effect, day):
                continue
            amount = effect["effect"]
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise ctx.fail(f"`effects[{index}].effect` must be a number, got {amount!r}")
            value = value * amount if multiply else value + amount
        total += value
    return total / len(days)


# ---------------------------------------------------------------------------
# cycle
# ---------------------------------------------------------------------------


class CycleConfig(PatternConfig):
    kind: Literal["cycle"] = "cycle"
    period: Number = Field(..., description="Clock units per cycle (a business cycle of 20 weeks: 20).")
    amplitude: Number = Field(1.0, description="Height above and below the level.")
    level: Number = Field(0.0, description="The centre it swings around.")
    phase: Number = Field(0.0, description="Share of a cycle it is shifted later, from 0 to 1.")
    shape: Literal["sine", "square", "triangle", "sawtooth"] = Field("sine", description="The wave's shape.")


@kind("cycle", "time", "signal", CycleConfig, "A regular cycle of any length: sine, square, triangle or sawtooth.",
      example={"kind": "cycle", "period": 26, "amplitude": 0.1, "level": 1},
      words=lambda cfg: f"a {cfg.shape} cycle of {cfg.period} units swinging {cfg.amplitude} around {cfg.level}",
      params=("period", "amplitude", "level", "phase"))
def _cycle(ctx: Any) -> float:
    period = ctx.number("period")
    if period <= 0:
        raise ctx.fail(f"`period` must be more than 0, got {period:g}")
    pos = (ctx.t / period - ctx.number("phase")) % 1.0
    shape = ctx.cfg.shape
    if shape == "sine":
        value = math.sin(2 * math.pi * pos)
    elif shape == "square":
        value = 1.0 if pos < 0.5 else -1.0
    elif shape == "triangle":
        value = 2 / math.pi * math.asin(math.sin(2 * math.pi * pos))
    else:
        value = 2 * pos - 1
    return ctx.number("level") + ctx.number("amplitude") * value


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


class LifecycleConfig(PatternConfig):
    kind: Literal["lifecycle"] = "lifecycle"
    start: Moment | list[Moment] = Field(..., description="When it begins: clock units, an ISO date, or a list "
                                                               "(one curve per start, multiplied: each later model "
                                                               "launch).")
    before: Number = Field(1.0, description="Value before the start.")
    peak: Number = Field(1.0, description="Value when the ramp ends.")
    floor: Number = Field(0.0, description="Level it decays toward.")
    ramp: Number = Field(0.0, description="Clock units rising from `before` to `peak` after the start.")
    half_life: Number | None = Field(None, description="Clock units for the gap above the floor to halve.")
    rate: Number | None = Field(None, description="Decay per clock unit as a log rate (instead of half_life).")

    @model_validator(mode="after")
    def _one_decay(self) -> LifecycleConfig:
        if self.half_life is not None and self.rate is not None:
            raise ValueError("give `half_life` or `rate`, not both")
        return self


def _curve(ctx: Any, tau: float) -> float:
    before, peak, floor, ramp = ctx.number("before"), ctx.number("peak"), ctx.number("floor"), ctx.number("ramp", 0)
    if tau < 0:
        return before
    if tau < ramp:
        return before + (peak - before) * tau / ramp
    age = tau - ramp
    if ctx.cfg.half_life is not None:
        decay = 0.5 ** (age / ctx.number("half_life", 1e-12))
    elif ctx.cfg.rate is not None:
        decay = math.exp(-ctx.number("rate") * age)
    else:
        decay = 1.0
    return floor + (peak - floor) * decay


@kind("lifecycle", "time", "signal", LifecycleConfig,
      "A life after a date: before → ramp up to a peak → decay toward a floor (a product launch, a price falling "
      "after a new model). Several starts multiply.",
      example={"kind": "lifecycle", "start": "2025-09-19", "before": 1, "peak": 0.93, "floor": 0.6, "half_life": 40},
      words=lambda cfg: f"a lifecycle from {cfg.start}: {cfg.before} before, {cfg.peak} at its peak, decaying toward "
                        f"{cfg.floor}",
      params=("start", "before", "peak", "floor", "ramp", "half_life", "rate"),
      context={"clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"}})
def _lifecycle(ctx: Any) -> float:
    raw = ctx.param("start")
    starts = raw if isinstance(raw, list) else [raw]
    value = 1.0
    for index, start in enumerate(starts):
        value *= _curve(ctx, ctx.t - when(ctx, start, f"start[{index}]" if isinstance(raw, list) else "start"))
    return value


# ---------------------------------------------------------------------------
# step
# ---------------------------------------------------------------------------


class StepChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at: Moment = Field(..., description="When: clock units or an ISO date.")
    to: Number | None = Field(None, description="The new value.")
    by: Number | None = Field(None, description="Added to the value.")
    times: Number | None = Field(None, description="Multiplies the value.")

    @model_validator(mode="after")
    def _one(self) -> StepChange:
        if sum(v is not None for v in (self.to, self.by, self.times)) != 1:
            raise ValueError("a change gives exactly one of `to`, `by` or `times`")
        return self


class StepConfig(PatternConfig):
    kind: Literal["step"] = "step"
    start: Number = Field(0.0, description="The value before any change.")
    changes: list[StepChange] = Field(...,
                                      description="[{at, to | by | times}]: changes that last (a new tax, a price "
                                                  "list).")


@kind("step", "time", "signal", StepConfig,
      "Step changes that last: a value that jumps to, by or times an amount at set times.",
      example={"kind": "step", "start": 0.2, "changes": [{"at": "2026-01-01", "to": 0.23}]},
      words=lambda cfg: f"starts at {cfg.start} and changes {len(cfg.changes)} time(s)", params=("start", "changes"),
      context={"clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"}})
def _step(ctx: Any) -> float:
    value = ctx.number("start")
    changes = sorted(((when(ctx, change["at"], f"changes[{i}].at"), i, change)
                      for i, change in enumerate(ctx.param("changes"))), key=lambda item: item[:2])
    for at, index, change in changes:
        if at > ctx.t + 1e-9:
            break
        for op in ("to", "by", "times"):
            amount = change.get(op)
            if amount is None:
                continue
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                raise ctx.fail(f"`changes[{index}].{op}` must be a number, got {amount!r}")
            value = amount if op == "to" else (value + amount if op == "by" else value * amount)
    return value


# ---------------------------------------------------------------------------
# series
# ---------------------------------------------------------------------------


class SeriesConfig(PatternConfig):
    kind: Literal["series"] = "series"
    data: str = Field(...,
                      description="Expression over $inputs: a list with one value per round, or rows (a table input).")
    time: str | None = Field(None, description="Rows: the column saying when (an ISO date, or clock units from round "
                                               "1). Without it rows are one per round, in order.")
    value: str | None = Field(None, description="Rows: the column holding the value.")
    match: str | None = Field(None, description="Keyed: the column holding each row's key.")
    missing: Literal["hold", "interpolate", "error"] = Field("hold", description="Between known times: hold the last "
                                                                                 "value, interpolate, or stop with an "
                                                                                 "error.")
    after: Literal["hold", "repeat", "error"] = Field("hold",
                                                      description="Past the last value: hold it, start over, or stop "
                                                                  "with an error.")

    @model_validator(mode="after")
    def _columns(self) -> SeriesConfig:
        if self.time is not None and self.value is None:
            raise ValueError("rows with a `time` column need a `value` column")
        return self


def _series_points(ctx: Any) -> tuple[list[float], list[Any]]:
    cfg: SeriesConfig = ctx.cfg
    data = ctx.param("data")
    if not isinstance(data, list):
        raise ctx.fail(f"`data` must give a list or rows, got {type(data).__name__}")
    rows = data
    if cfg.match is not None:
        rows = [row for row in data if isinstance(row, dict) and str(row.get(cfg.match)) == ctx.key]
        if not rows:
            raise ctx.fail(f"`data` has no rows whose '{cfg.match}' is '{ctx.key}'")
    if cfg.value is not None:
        missing = next((i for i, row in enumerate(rows) if not isinstance(row, dict) or cfg.value not in row), None)
        if missing is not None:
            raise ctx.fail(f"`data` row {missing} has no column '{cfg.value}'")
    values = [_number_cell(ctx, row[cfg.value] if cfg.value is not None else row, i) for i, row in enumerate(rows)]
    if cfg.time is None:
        step = tb.step_length(ctx.clock)
        return [i * step for i in range(len(values))], values
    points = sorted((when(ctx, row.get(cfg.time), f"data[{i}].{cfg.time}"), v)
                    for i, (row, v) in enumerate(zip(rows, values)))
    return [p[0] for p in points], [p[1] for p in points]


def _number_cell(ctx: Any, raw: Any, index: int) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ctx.fail(f"`data` value {index} is not a number: {raw!r}") from None
    return value if math.isfinite(value) else None


@kind("series", "time", "signal", SeriesConfig,
      "Values from data: a real history (weather, prices, footfall) read at the current time — how a pattern is driven "
      "by the customer's own series.",
      example={"kind": "series", "data": "$inputs.weather", "time": "date", "value": "temp_c",
               "missing": "interpolate"},
      words=lambda cfg: f"values read from {cfg.data}" + (f" (column {cfg.value})" if cfg.value else ""),
      params=("data",),
      context={"clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"},
               "inputs": {"weather": {"type": "table",
                                      "default": [{"date": "2025-12-20", "temp_c": 4},
                                                  {"date": "2025-12-22", "temp_c": 6}]}}})
def _series(ctx: Any) -> float | None:
    times, values = ctx.cached("points", lambda: _series_points(ctx))
    if not times:
        raise ctx.fail("`data` is empty")
    t = ctx.t
    if t > times[-1] + 1e-9:
        if ctx.cfg.after == "error":
            raise ctx.fail(f"has no value past t = {times[-1]:g} (now {t:g})")
        if ctx.cfg.after == "repeat":
            span = times[-1] - times[0] + (times[1] - times[0] if len(times) > 1 else 1)
            t = times[0] + (t - times[0]) % span
    index = bisect.bisect_right(times, t + 1e-9) - 1
    if index < 0:
        return values[0]
    if abs(times[index] - t) <= 1e-9 or ctx.cfg.missing == "hold" or index + 1 >= len(times):
        if abs(times[index] - t) > 1e-9 and ctx.cfg.missing == "error" and index + 1 < len(times):
            raise ctx.fail(f"has no value at t = {t:g}")
        return values[index]
    if ctx.cfg.missing == "error":
        raise ctx.fail(f"has no value at t = {t:g}")
    a, b = values[index], values[index + 1]
    if a is None or b is None:
        return a
    share = (t - times[index]) / (times[index + 1] - times[index])
    return a + (b - a) * share
