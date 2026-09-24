"""Accuracy of ensemble forecasts against actual values: errors, bias, interval coverage and simple baselines.

A forecast is an ensemble of run values; its point forecast is the median. Errors are reported in the units a
business reads — bias and WAPE as shares of the actual total, MAPE over the non-zero actuals, RMSE and CRPS in the
measure's units — and intervals are checked at each nominal level, with a verdict when they hold clearly fewer
actual values than they claim. Baselines forecast each key from the actual values of earlier cases.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .scoring import crps_ensemble, interval_coverage
from .stats import mean, quantile

__all__ = ["Pair", "BASELINES", "accuracy", "point_accuracy", "baseline_points", "coverage_verdict", "bias_verdict",
           "compare_baseline", "per_key"]

#: The simple forecasts every validation is compared with, and how each is described.
BASELINES = {"last": "the previous case's actual value", "mean": "the mean of earlier actual values",
             "seasonal": "the actual value one season earlier"}


@dataclass(frozen=True)
class Pair:
    """One forecast checked against what happened: the case, the key within the measure (``""`` for a single value)."""

    case: int
    key: str
    actual: float
    ensemble: tuple[float, ...]

    @property
    def point(self) -> float:
        return quantile(list(self.ensemble), 0.5)


def point_accuracy(points: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """Errors of point forecasts ``(forecast, actual)``: bias and WAPE as shares of the actual total, MAPE, MAE, RMSE.
    """
    errors = [forecast - actual for forecast, actual in points]
    total = math.fsum(abs(actual) for _, actual in points)
    nonzero = [(forecast, actual) for forecast, actual in points if actual != 0]
    return {"n": len(points), "mae": mean([abs(e) for e in errors]),
            "rmse": math.sqrt(mean([e * e for e in errors])),
            "wape": math.fsum(abs(e) for e in errors) / total if total else None,
            "bias": math.fsum(errors) / total if total else None,
            "mape": mean([abs(f - a) / abs(a) for f, a in nonzero]) if nonzero else None,
            "mape_skipped": len(points) - len(nonzero)}


def accuracy(pairs: Sequence[Pair], levels: Sequence[float]) -> dict[str, Any]:
    """Point errors, CRPS, the standard error of the bias and interval coverage at every nominal level."""
    out = point_accuracy([(pair.point, pair.actual) for pair in pairs])
    out["crps"] = mean([crps_ensemble(list(pair.ensemble), pair.actual) for pair in pairs])
    # The bias's standard error comes from each value's relative error, so a steady over-forecast of big and small
    # keys alike reads as bias rather than as noise.
    relative = [(pair.point - pair.actual) / abs(pair.actual) for pair in pairs if pair.actual != 0]
    spread = math.sqrt(math.fsum((r - mean(relative)) ** 2 for r in relative) / (len(relative) - 1)) \
        if len(relative) > 1 else 0.0
    out["bias_se"] = spread / math.sqrt(len(relative)) if relative else None
    out["coverage"] = {}
    for level in levels:
        tail = (1.0 - level) / 2.0
        intervals = [(quantile(list(p.ensemble), tail), quantile(list(p.ensemble), 1.0 - tail)) for p in pairs]
        out["coverage"][f"{level:g}"] = interval_coverage(intervals, [p.actual for p in pairs], level)
    return out


def baseline_points(actuals: Mapping[int, float], kind: str, season: int | None) -> dict[int, float]:
    """Each case's baseline forecast from the actual values of earlier cases (cases without one are left out)."""
    order = sorted(actuals)
    out: dict[int, float] = {}
    for position, case in enumerate(order):
        earlier = [actuals[c] for c in order[:position]]
        if kind == "last" and earlier:
            out[case] = earlier[-1]
        elif kind == "mean" and earlier:
            out[case] = mean(earlier)
        elif kind == "seasonal" and season is not None and position >= season:
            out[case] = actuals[order[position - season]]
    return out


def coverage_verdict(coverage: Mapping[str, Any]) -> str | None:
    """A plain warning when intervals clearly hold fewer actual values than their nominal level (``None`` otherwise)."""
    nominal = float(coverage["nominal"])
    low, high = (float(bound) for bound in coverage["coverage_ci95"])
    held = round(coverage["coverage"] * coverage["n"])
    if high >= nominal:
        return None
    return (f"{nominal:.0%} intervals held {held} of {coverage['n']} actual values ({coverage['coverage']:.0%}, "
            f"95% CI {low:.0%}–{high:.0%}): the forecast is overconfident, so its ranges are too narrow to plan with; "
            "carry parameter uncertainty into the runs (uncertainty=) or model the variation that is missing")


def bias_verdict(result: Mapping[str, Any]) -> str | None:
    """A plain warning when forecasts run high or low by more than twice the bias's standard error."""
    bias, se = result.get("bias"), result.get("bias_se")
    if bias is None or se is None or result["n"] < 3 or abs(bias) <= 2 * se:
        return None
    return (f"forecasts run {'high' if bias > 0 else 'low'} by {abs(bias):.1%} of the actual total on average (± "
            f"{se:.1%})")


def compare_baseline(model: Mapping[str, Any], baseline: Mapping[str, Any], name: str,
                     season: int | None) -> dict[str, Any]:
    """The model's WAPE against a baseline's on the same cases; ``skill`` > 0 means the model is better."""
    label = (f"seasonal naive ({season} back)" if name == "seasonal" else f"{name} value" if name == "last"
             else "earlier mean")
    skill = None
    if model["wape"] is not None and baseline["wape"]:
        skill = 1.0 - model["wape"] / baseline["wape"]
    return {"baseline": name, "label": label, "about": BASELINES[name], "n": model["n"], "model": model,
            "reference": baseline, "skill": skill}


def per_key(pairs: Sequence[Pair]) -> dict[str, list[Pair]]:
    """Pairs grouped by their key, in first-seen order."""
    grouped: dict[str, list[Pair]] = {}
    for pair in pairs:
        grouped.setdefault(pair.key, []).append(pair)
    return grouped
