"""Observation patterns: what gets recorded of a true quantity — noisy counts, measurement error, censoring, gaps.

Random observations draw one uniform number per pattern, key and step, and turn it into the outcome through the
distribution's inverse: the same key in the same round always gives the same draw, and a larger mean gives a
larger (never smaller) count, so arms that change a price see demand move coherently (common random numbers).
Pass a key — ``$pattern.sales($demand, $it.id)`` — so each item gets its own draw.
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Dict, Literal, Optional

from pydantic import Field

from .base import Number, PatternConfig, kind
from .responses import driver

__all__ = ["CountsConfig", "MeasurementConfig", "CensoredConfig", "MissingConfig", "count_quantile"]

#: Above this mean, counts use the normal approximation (exact sums would take too long).
_EXACT_MEAN = 5_000.0
_NORMAL = NormalDist()


def count_quantile(u: float, mean: float, dispersion: Optional[float]) -> int:
    """The smallest count whose cumulative probability reaches ``u``: Poisson, or negative binomial with
    ``dispersion`` k (variance mean + mean²/k)."""
    if mean <= 0:
        return 0
    variance = mean + (mean * mean / dispersion if dispersion else 0.0)
    if mean > _EXACT_MEAN:
        return max(0, round(mean + math.sqrt(variance) * _NORMAL.inv_cdf(u)))
    if dispersion:
        success = dispersion / (dispersion + mean)
        log_p = dispersion * math.log(success)
        ratio = mean / (dispersion + mean)
    else:
        log_p, ratio = -mean, 0.0
    p, cumulative, k = math.exp(log_p), 0.0, 0
    limit = int(mean + 50 * math.sqrt(variance) + 50)
    while k < limit:
        cumulative += p
        if cumulative >= u:
            return k
        p *= (k + dispersion) / (k + 1) * ratio if dispersion else mean / (k + 1)
        k += 1
    return k


class CountsConfig(PatternConfig):
    kind: Literal["counts"] = "counts"
    dist: Literal["poisson", "negative_binomial"] = Field("negative_binomial", description="poisson: variance = mean | "
                                                                                           "negative_binomial: variance = mean + mean²/dispersion (over-dispersed).")
    dispersion: Number = Field(10.0, description="negative_binomial: k; smaller is noisier (fitted by the method of moments).")
    every: Optional[float] = Field(None, gt=0, description="Clock units per fresh draw (default: one round).")


@kind("counts", "observation", "response", CountsConfig,
      "Whole-number counts around an expected value: Poisson, or negative binomial for over-dispersed sales and arrivals.",
      example={"kind": "counts", "dist": "negative_binomial", "dispersion": 6},
      args=("mean",), random=True, params=("dispersion",),
      words=lambda cfg: f"{cfg.dist.replace('_', ' ')} counts" + (f" (dispersion {cfg.dispersion})" if cfg.dist != "poisson" else ""))
def _counts(ctx: Any, mean: Any) -> int:
    expected = driver(ctx, mean, "the expected count")
    if expected < 0:
        raise ctx.fail(f"the expected count must be ≥ 0, got {expected:g}")
    dispersion = ctx.number("dispersion") if ctx.cfg.dist == "negative_binomial" else None
    if dispersion is not None and dispersion <= 0:
        raise ctx.fail(f"`dispersion` must be more than 0, got {dispersion:g}")
    return count_quantile(ctx.uniform("count", ctx.step()), expected, dispersion)


class MeasurementConfig(PatternConfig):
    kind: Literal["measurement"] = "measurement"
    sd: Number = Field(..., description="Spread of the error (a share of the value with form multiply).")
    bias: Number = Field(0.0, description="Systematic error (a share with form multiply: 0.05 reads 5% high).")
    form: Literal["add", "multiply"] = Field("multiply", description="add: value + bias + sd·z | multiply: value·(1 + bias + sd·z).")
    whole: bool = Field(False, description="Round the reading to a whole number.")
    every: Optional[float] = Field(None, gt=0, description="Clock units per fresh draw (default: one round).")


@kind("measurement", "observation", "response", MeasurementConfig,
      "Measurement error: a reading of a true value with bias and noise (surveys, sensors, stock counts).",
      example={"kind": "measurement", "sd": 0.08, "bias": -0.03, "whole": True},
      args=("value",), random=True, params=("sd", "bias"),
      words=lambda cfg: f"readings off by {cfg.sd} ({cfg.form}) with bias {cfg.bias}")
def _measurement(ctx: Any, value: Any) -> float:
    true = driver(ctx, value, "the true value")
    z = _NORMAL.inv_cdf(ctx.uniform("measure", ctx.step()))
    error = ctx.number("bias") + ctx.number("sd", 0) * z
    reading = true * (1 + error) if ctx.cfg.form == "multiply" else true + error
    return float(round(reading)) if ctx.cfg.whole else reading


class CensoredConfig(PatternConfig):
    kind: Literal["censored"] = "censored"


@kind("censored", "observation", "response", CensoredConfig,
      "Censoring: what is observed when a quantity is capped — sales = min(demand, stock) — with what was lost. "
      "Gives {value, lost, censored}: $pattern.sold($demand, $it.stock).value.",
      example={"kind": "censored"}, args=("demand", "capacity"),
      words=lambda cfg: "demand capped by capacity, recording what was lost")
def _censored(ctx: Any, demand: Any, capacity: Any) -> Dict[str, Any]:
    d, c = driver(ctx, demand, "the demand"), driver(ctx, capacity, "the capacity")
    value = max(0.0, min(d, c))
    whole = float(d).is_integer() and float(c).is_integer()
    return {"value": int(value) if whole else value, "lost": int(max(0.0, d - c)) if whole else max(0.0, d - c),
            "censored": d > c}


class MissingConfig(PatternConfig):
    kind: Literal["missing"] = "missing"
    chance: Number = Field(..., description="Probability a reading is missing (null).")
    every: Optional[float] = Field(None, gt=0, description="Clock units per fresh draw (default: one round).")


@kind("missing", "observation", "response", MissingConfig,
      "Missing observations: the value, or null with some chance (gaps in a dashboard, unreported sales).",
      example={"kind": "missing", "chance": 0.05}, args=("value",), random=True, params=("chance",),
      words=lambda cfg: f"missing with chance {cfg.chance}")
def _missing(ctx: Any, value: Any) -> Any:
    return None if ctx.uniform("missing", ctx.step()) < ctx.number("chance", 0, 1) else value
