"""Random patterns: paths over time drawn from each pattern's own seeded stream.

A path is drawn step by step in order (one step per round by default, or every ``every`` clock units), so the value
at any time is fixed by the seed, the pattern's name and its key — whoever asks, in whatever order.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, model_validator

from . import timebase as tb
from .base import Number, PatternConfig, kind
from .signals import when

__all__ = ["RandomWalkConfig", "MeanReversionConfig", "AutoregressiveConfig", "VolatilityConfig", "RegimesConfig",
           "ShocksConfig", "NoiseConfig", "WeatherConfig"]


class _Stepped(PatternConfig):
    every: float | None = Field(None, gt=0, description="Clock units per step of the path (default: one round).")


def _bound(ctx: Any, value: float) -> float:
    if ctx.cfg.min is not None:
        value = max(ctx.number("min"), value)
    if ctx.cfg.max is not None:
        value = min(ctx.number("max"), value)
    return value


# ---------------------------------------------------------------------------
# random walk
# ---------------------------------------------------------------------------


class RandomWalkConfig(_Stepped):
    kind: Literal["random_walk"] = "random_walk"
    start: Number = Field(0.0, description="Value at round 1.")
    drift: Number = Field(0.0, description="Added each step (form add) or log growth each step (form multiply).")
    sd: Number = Field(1.0, description="Standard deviation of each step's change (of its log with form multiply).")
    form: Literal["add", "multiply"] = Field("add",
                                             description="add: x + drift + sd·z | multiply (geometric): x·e^(drift + "
                                                         "sd·z).")


def _walk_step(ctx: Any, rng: Any, state: Any, step: int) -> tuple[float, Any]:
    z = rng.gauss(0.0, 1.0)
    drift, sd = ctx.number("drift"), ctx.number("sd", 0)
    if ctx.cfg.form == "add":
        value = state + drift + sd * z
    else:
        exponent = drift + sd * z
        if state * math.exp(min(exponent, 700)) == math.inf:
            raise ctx.fail("grew past what a number holds")
        value = state * math.exp(min(exponent, 700))
    value = _bound(ctx, value)
    return value, value


@kind("random_walk", "random", "process", RandomWalkConfig, "A random walk, additive or geometric, with drift.",
      example={"kind": "random_walk", "start": 100, "drift": 0.001, "sd": 0.02, "form": "multiply"},
      random=True, params=("start", "drift", "sd", "min", "max"),
      words=lambda cfg: f"a {'geometric ' if cfg.form == 'multiply' else ''}random walk from {cfg.start} (drift "
                        f"{cfg.drift}, sd {cfg.sd})")
def _random_walk(ctx: Any) -> float:
    start = _bound(ctx, ctx.number("start"))
    return float(ctx.path(ctx.step(), lambda c, rng: (start, start), _walk_step))


# ---------------------------------------------------------------------------
# mean reversion (Ornstein–Uhlenbeck)
# ---------------------------------------------------------------------------


class MeanReversionConfig(_Stepped):
    kind: Literal["mean_reversion"] = "mean_reversion"
    mean: Number = Field(..., description="The level it is pulled back to.")
    rate: Number = Field(..., description="Pull per clock unit (0.1: a gap shrinks by e^−0.1 ≈ 10% a unit).")
    sd: Number = Field(0.0, description="Noise per √unit (the long-run spread is sd / √(2·rate)).")
    start: Number | None = Field(None, description="Value at round 1 (default: the mean).")


def _ou_step(ctx: Any, rng: Any, state: float, step: int) -> tuple[float, float]:
    mean, rate, sd, dt = ctx.number("mean"), ctx.number("rate", 0), ctx.number("sd", 0), ctx.every()
    decay = math.exp(-rate * dt)
    spread = sd * math.sqrt((1 - decay ** 2) / (2 * rate)) if rate > 0 else sd * math.sqrt(dt)
    value = _bound(ctx, mean + (state - mean) * decay + spread * rng.gauss(0.0, 1.0))
    return value, value


@kind("mean_reversion", "random", "process", MeanReversionConfig,
      "Mean reversion (Ornstein–Uhlenbeck, exact at any step length): wanders but is pulled back to a mean.",
      example={"kind": "mean_reversion", "mean": 0.7, "rate": 0.05, "sd": 0.02},
      random=True, params=("mean", "rate", "sd", "start", "min", "max"),
      words=lambda cfg: f"mean reversion to {cfg.mean} at rate {cfg.rate} per unit (noise {cfg.sd})")
def _mean_reversion(ctx: Any) -> float:
    start = _bound(ctx, ctx.number("mean") if ctx.cfg.start is None else ctx.number("start"))
    return float(ctx.path(ctx.step(), lambda c, rng: (start, start), _ou_step))


# ---------------------------------------------------------------------------
# autoregression
# ---------------------------------------------------------------------------


class AutoregressiveConfig(_Stepped):
    kind: Literal["autoregressive"] = "autoregressive"
    coefficients: list[Number] | str = Field(...,
                                             description="[φ1, φ2, …]: how much each earlier step carries into the "
                                                         "next.")
    mean: Number = Field(0.0, description="The level deviations are measured from.")
    sd: Number = Field(1.0, description="Standard deviation of each step's new shock.")
    start: Number | None = Field(None, description="Value of the first steps (default: the mean).")


def _ar_step(ctx: Any, rng: Any, state: list[float], step: int) -> tuple[float, list[float]]:
    phis, mean = ctx.numbers("coefficients"), ctx.number("mean")
    deviation = sum(phi * past for phi, past in zip(phis, state)) + ctx.number("sd", 0) * rng.gauss(0.0, 1.0)
    value = _bound(ctx, mean + deviation)
    return value, [value - mean, *state[:len(phis) - 1]]


@kind("autoregressive", "random", "process", AutoregressiveConfig,
      "Autoregression AR(p): each step carries a share of the last ones, plus a new shock (persistence, momentum).",
      example={"kind": "autoregressive", "coefficients": [0.6, 0.2], "mean": 20, "sd": 2},
      random=True, params=("coefficients", "mean", "sd", "start", "min", "max"),
      words=lambda cfg: f"an autoregressive process around {cfg.mean} with coefficients {cfg.coefficients}")
def _autoregressive(ctx: Any) -> float:
    phis = ctx.numbers("coefficients")
    if not phis:
        raise ctx.fail("`coefficients` needs at least one number")
    mean = ctx.number("mean")
    start = mean if ctx.cfg.start is None else ctx.number("start")
    return float(ctx.path(ctx.step(), lambda c, rng: (start, [start - mean] * len(phis)), _ar_step))


# ---------------------------------------------------------------------------
# volatility clustering (GARCH(1,1))
# ---------------------------------------------------------------------------


class VolatilityConfig(_Stepped):
    kind: Literal["volatility"] = "volatility"
    omega: Number = Field(..., description="Baseline variance added each step (> 0).")
    alpha: Number = Field(0.1, description="How much the last return's size raises the next variance.")
    beta: Number = Field(0.85, description="How much the last variance carries over (alpha + beta < 1 is stable).")
    mean: Number = Field(0.0, description="Mean return per step.")
    start: Number = Field(100.0, description="Level at round 1 (output level).")
    output: Literal["returns", "level", "volatility"] = Field("returns",
                                                              description="returns: each step's log return | level: "
                                                                          "start compounded by the returns | "
                                                                          "volatility: the standard deviation now.")


def _garch_first(ctx: Any, rng: Any) -> tuple[float, tuple[float, float, float]]:
    omega, alpha, beta = ctx.number("omega", 1e-300), ctx.number("alpha", 0), ctx.number("beta", 0)
    variance = omega / (1 - alpha - beta) if alpha + beta < 1 else omega
    state = (variance, 0.0, ctx.number("start"))
    return _garch_value(ctx, state), state


def _garch_step(ctx: Any, rng: Any, state: tuple[float, float, float],
                step: int) -> tuple[float, tuple[float, float, float]]:
    variance, last, level = state
    variance = ctx.number("omega") + ctx.number("alpha") * last ** 2 + ctx.number("beta") * variance
    if not math.isfinite(variance) or variance > 1e300:
        raise ctx.fail("its variance exploded (alpha + beta ≥ 1?)")
    ret = ctx.number("mean") + math.sqrt(variance) * rng.gauss(0.0, 1.0)
    level = level * math.exp(min(ret, 700))
    new = (variance, ret, level)
    return _garch_value(ctx, new), new


def _garch_value(ctx: Any, state: tuple[float, float, float]) -> float:
    variance, ret, level = state
    output = ctx.cfg.output
    return ret if output == "returns" else (level if output == "level" else math.sqrt(variance))


@kind("volatility", "random", "process", VolatilityConfig,
      "Volatility clustering (GARCH(1,1)): calm and turbulent spells, as returns, a price level or the volatility "
      "itself.",
      example={"kind": "volatility", "omega": 0.00001, "alpha": 0.08, "beta": 0.9, "output": "level", "start": 50},
      random=True, params=("omega", "alpha", "beta", "mean", "start"),
      words=lambda cfg: f"clustered volatility (GARCH ω {cfg.omega}, α {cfg.alpha}, β {cfg.beta}) giving its "
                        f"{cfg.output}")
def _volatility(ctx: Any) -> float:
    return float(ctx.path(ctx.step(), _garch_first, _garch_step))


# ---------------------------------------------------------------------------
# regimes (Markov switching)
# ---------------------------------------------------------------------------


class RegimesConfig(_Stepped):
    kind: Literal["regimes"] = "regimes"
    states: dict[str, Any] = Field(..., description="{state: value}: what each state gives (a number, or a map read as "
                                                    "$pattern.economy.growth); null gives the state's name.")
    transitions: dict[str, dict[str, Number]] = Field(..., description="{from: {to: probability per step}}; the "
                                                                       "rest of the probability stays in the state.")
    start: str | None = Field(None, description="The state at round 1 (default: the first declared).")

    @model_validator(mode="after")
    def _names(self) -> RegimesConfig:
        if not self.states:
            raise ValueError("regimes need at least one state")
        for origin, targets in self.transitions.items():
            for name in (origin, *targets):
                if name not in self.states:
                    raise ValueError(f"'{name}' is not a declared state (states: {', '.join(self.states)})")
        if self.start is not None and self.start not in self.states:
            raise ValueError(f"start '{self.start}' is not a declared state")
        return self


def _regime_step(ctx: Any, rng: Any, state: str, step: int) -> tuple[str, str]:
    targets = ctx.param("transitions").get(state) or {}
    roll, total = rng.random(), 0.0
    for target, chance in targets.items():
        if isinstance(chance, bool) or not isinstance(chance, (int, float)) or chance < 0:
            raise ctx.fail(f"`transitions.{state}.{target}` must be a probability, got {chance!r}")
        total += chance
        if total > 1 + 1e-9:
            raise ctx.fail(f"`transitions.{state}` probabilities add up to more than 1")
        if roll < total:
            return target, target
    return state, state


@kind("regimes", "random", "process", RegimesConfig,
      "Regime switching: a Markov chain of states (boom, recession) each with its own values.",
      example={"kind": "regimes", "states": {"boom": {"growth": 0.02}, "bust": {"growth": -0.01}},
               "transitions": {"boom": {"bust": 0.05}, "bust": {"boom": 0.2}}},
      random=True, params=("states", "transitions"),
      words=lambda cfg: f"switches between {', '.join(cfg.states)} (a Markov chain)")
def _regimes(ctx: Any) -> Any:
    first = ctx.cfg.start or next(iter(ctx.cfg.states))
    state = ctx.path(ctx.step(), lambda c, rng: (first, first), _regime_step)
    value = ctx.param("states")[state]
    return state if value is None else value


# ---------------------------------------------------------------------------
# shocks
# ---------------------------------------------------------------------------


class ShocksConfig(_Stepped):
    kind: Literal["shocks"] = "shocks"
    chance: Number = Field(0.0, description="Probability a shock starts in a step.")
    at: list[float | str] = Field(default_factory=list,
                                  description="Times shocks certainly start (clock units or ISO dates).")
    recur: Number | None = Field(None,
                                 description="Clock units between shocks that recur on schedule (from the window's "
                                             "start).")
    size: Number = Field(1.0, description="Each shock's size (form add: added; multiply: 1 + size).")
    size_sd: Number = Field(0.0, description="Spread of each shock's size (normal).")
    lasts: int = Field(1, ge=1, description="Steps a shock stays at full size.")
    half_life: Number | None = Field(None, description="Steps for what is left of a shock to halve once it has lasted "
                                                       "(without one it ends at once).")
    window: list[float | str | None] | None = Field(None,
                                                    description="[first, last] times shocks may start (last may be "
                                                                "null).")
    limit: int | None = Field(None, ge=1, description="Most shocks in a run.")
    gap: int = Field(0, ge=0, description="Steps after a shock starts before another can.")
    form: Literal["add", "multiply"] = Field("add",
                                             description="add: a baseline of 0 plus every shock | multiply: 1 × (1 + "
                                                         "each shock).")


def _in_window(ctx: Any, t: float) -> bool:
    window = ctx.param("window")
    if not window:
        return True
    first = when(ctx, window[0], "window[0]") if window[0] is not None else -math.inf
    last = when(ctx, window[1], "window[1]") if len(window) > 1 and window[1] is not None else math.inf
    return first - 1e-9 <= t <= last + 1e-9


def _shock_step(ctx: Any, rng: Any, state: dict[str, Any], step: int) -> tuple[float, dict[str, Any]]:
    every, lasts = ctx.every(), ctx.cfg.lasts
    t = step * every
    scheduled = ctx.cached("scheduled",
                           lambda: {ctx.step(when(ctx, moment, f"at[{i}]"))
                                    for i, moment in enumerate(ctx.param("at"))})
    roll, jitter = rng.random(), rng.gauss(0.0, 1.0)  # both drawn every step, so a change of chance never shifts a size
    recur = ctx.optional("recur", 1e-9)
    window = ctx.param("window")
    origin = when(ctx, window[0], "window[0]") if window and window[0] is not None else 0.0
    due = step in scheduled or (recur is not None and t + 1e-9 >= origin
                                and abs(((t - origin) / recur) - round((t - origin) / recur)) < 1e-9)
    starts: list[list[float]] = list(state["starts"])
    blocked = (ctx.cfg.limit is not None and state["count"] >= ctx.cfg.limit) or \
        (starts and step - starts[-1][0] <= ctx.cfg.gap) or not _in_window(ctx, t)
    if not blocked and (due or roll < ctx.number("chance", 0, 1)):
        starts.append([step, ctx.number("size") + ctx.number("size_sd", 0) * jitter])
        state = {"count": state["count"] + 1}
    else:
        state = {"count": state["count"]}
    half_life = ctx.optional("half_life", 1e-9)
    total = 0.0 if ctx.cfg.form == "add" else 1.0
    kept = []
    for begun, size in starts:
        age = step - begun
        if age < lasts:
            share = 1.0
        elif half_life is not None:
            share = 0.5 ** ((age - lasts + 1) / half_life)
        else:
            share = 0.0
        if share > 1e-9 or age <= ctx.cfg.gap:
            kept.append([begun, size])
        total = total + size * share if ctx.cfg.form == "add" else total * (1 + size * share)
    state["starts"] = kept
    return total, state


@kind("shocks", "random", "process", ShocksConfig,
      "Shocks: one-off (at), recurring (recur) or random (chance) jumps that last and then fade (half_life). "
      "An event can act while one is on: when: $pattern.strike > 0.",
      example={"kind": "shocks", "chance": 0.05, "size": -0.4, "lasts": 2, "half_life": 3, "form": "multiply"},
      random=True, params=("chance", "at", "recur", "size", "size_sd", "half_life", "window"),
      words=lambda cfg: f"shocks of size {cfg.size} (chance {cfg.chance} a step, lasting {cfg.lasts}"
                        + (f", fading with half-life {cfg.half_life}" if cfg.half_life is not None else "") + ")")
def _shocks(ctx: Any) -> float:
    empty = 0.0 if ctx.cfg.form == "add" else 1.0
    step0 = _shock_step(ctx, ctx.stream("first"), {"count": 0, "starts": []}, 0)
    return float(ctx.path(ctx.step(), lambda c,
                          rng: step0 if step0[0] != empty or step0[1]["starts"] else (empty, step0[1]), _shock_step))


# ---------------------------------------------------------------------------
# noise
# ---------------------------------------------------------------------------


class NoiseConfig(_Stepped):
    kind: Literal["noise"] = "noise"
    dist: Literal["normal", "uniform", "lognormal", "laplace"] = Field("normal", description="The distribution of each "
                                                                                             "step's draw.")
    mean: Number = Field(0.0, description="Centre (normal, laplace); log-mean (lognormal).")
    sd: Number = Field(1.0, description="Spread (normal, laplace: scale·√2; lognormal: log-sd).")
    low: Number = Field(0.0, description="Lowest value (uniform).")
    high: Number = Field(1.0, description="Highest value (uniform).")


@kind("noise", "random", "process", NoiseConfig,
      "Fresh noise every step, independent over time; a key (or a keyed pattern) gives each item its own draws.",
      example={"kind": "noise", "dist": "normal", "sd": 0.01, "keys": "resident"},
      random=True, params=("mean", "sd", "low", "high"),
      words=lambda cfg: f"independent {cfg.dist} noise each step",
      context={"types": {"resident": {}}, "entities": {"resident": {"type": "resident", "count": 2}}})
def _noise(ctx: Any) -> float:
    rng = ctx.stream("noise", ctx.step())
    dist = ctx.cfg.dist
    if dist == "uniform":
        return rng.uniform(ctx.number("low"), ctx.number("high"))
    if dist == "lognormal":
        return rng.lognormvariate(ctx.number("mean"), ctx.number("sd", 0))
    if dist == "laplace":
        u = rng.random() - 0.5
        scale = ctx.number("sd", 0) / math.sqrt(2)
        return ctx.number("mean") - scale * math.copysign(1.0, u) * math.log(max(1e-300, 1 - 2 * abs(u)))
    return rng.gauss(ctx.number("mean"), ctx.number("sd", 0))


# ---------------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------------


class WeatherConfig(_Stepped):
    kind: Literal["weather"] = "weather"
    mean: Number = Field(..., description="Average over the year.")
    amplitude: Number = Field(0.0, description="Seasonal swing above and below the mean.")
    peak: Number = Field(0.55, description="Where in the year it is highest, from 0 to 1 (0.55 ≈ mid-July).")
    persistence: Number = Field(0.7,
                                description="How much of a step's departure from normal carries into the next (0–1).")
    sd: Number = Field(1.0, description="Typical departure from the seasonal normal.")


def _weather_step(ctx: Any, rng: Any, state: float, step: int) -> tuple[float, float]:
    phi = ctx.number("persistence", 0, 0.999999)
    anomaly = phi * state + ctx.number("sd", 0) * math.sqrt(1 - phi * phi) * rng.gauss(0.0, 1.0)
    return anomaly, anomaly


@kind("weather", "random", "process", WeatherConfig,
      "Weather-like driver: a yearly seasonal normal plus persistent departures (autocorrelated), e.g. temperature.",
      example={"kind": "weather", "mean": 18, "amplitude": 9, "peak": 0.55, "persistence": 0.75, "sd": 3},
      random=True, params=("mean", "amplitude", "peak", "persistence", "sd"),
      words=lambda cfg: f"weather-like: {cfg.mean} ± {cfg.amplitude} over the year, departures of {cfg.sd} that "
                        f"persist ({cfg.persistence})",
      context={"clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"}})
def _weather(ctx: Any) -> float:
    anomaly = ctx.path(ctx.step(), lambda c, rng: (c.number("sd", 0) * rng.gauss(0.0, 1.0),) * 2, _weather_step)
    try:
        position = tb.position(ctx.clock, ctx.t, "year")
    except ValueError as exc:
        raise ctx.fail(str(exc)) from None
    seasonal = ctx.number("amplitude") * math.cos(2 * math.pi * (position - ctx.number("peak")))
    return ctx.number("mean") + seasonal + float(anomaly)
