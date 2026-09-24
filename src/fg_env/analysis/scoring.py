"""Forecast verification: is a forecast right as often as it claims?

Probability forecasts of yes/no events (Brier, log loss, reliability diagram, ECE, Murphy
decomposition, skill vs climatology), forecasts over categories (multiclass Brier, log loss,
accuracy), ensembles of numbers (CRPS, interval coverage) and explicit intervals (coverage).
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .stats import is_number, mean, quantile, wilson

__all__ = ["brier", "brier_multiclass", "log_loss", "log_loss_multiclass", "crps_ensemble", "crps",
           "interval_coverage", "reliability", "ece", "murphy", "skill_score", "score", "ReliabilityBin"]

#: Probabilities are clipped away from 0 and 1 by this much before taking logs.
DEFAULT_EPSILON = 1e-15
DEFAULT_BINS = 10


@dataclass(frozen=True)
class ReliabilityBin:
    """One bucket of a reliability diagram: forecasts in [low, high) and how often the event happened."""

    low: float
    high: float
    n: int
    mean_forecast: float
    observed: float
    observed_low: float
    observed_high: float

    def to_dict(self) -> dict[str, Any]:
        return {"low": self.low, "high": self.high, "n": self.n, "mean_forecast": self.mean_forecast,
                "observed": self.observed, "observed_ci95": [self.observed_low, self.observed_high]}


def _pairs(forecasts: Sequence[Any], outcomes: Sequence[Any]) -> None:
    if len(forecasts) != len(outcomes):
        raise ValueError(f"{len(forecasts)} forecast(s) but {len(outcomes)} outcome(s)")
    if not forecasts:
        raise ValueError("nothing to score: no forecasts")


def _probability(p: Any) -> float:
    if not is_number(p) or not 0.0 <= p <= 1.0:
        raise ValueError(f"a probability must be a number from 0 to 1, got {p!r}")
    return float(p)


def _event(o: Any) -> int:
    if isinstance(o, bool) or o in (0, 1):
        return int(o)
    raise ValueError(f"a yes/no outcome must be true/false or 0/1, got {o!r}")


def brier(probabilities: Sequence[float], outcomes: Sequence[Any]) -> float:
    """Mean squared error of probability forecasts for a yes/no event (0 perfect, 1 worst)."""
    _pairs(probabilities, outcomes)
    return mean([(_probability(p) - _event(o)) ** 2 for p, o in zip(probabilities, outcomes)])


def _distribution(forecast: Mapping[Any, float]) -> dict[str, float]:
    if not isinstance(forecast, Mapping) or not forecast:
        raise ValueError(f"a category forecast must be a non-empty mapping of category → probability, got {forecast!r}")
    out = {str(k): _probability(v) for k, v in forecast.items()}
    if not math.isclose(sum(out.values()), 1.0, abs_tol=1e-6):
        raise ValueError(f"category probabilities must sum to 1, got {sum(out.values()):.6g}")
    return out


def brier_multiclass(forecasts: Sequence[Mapping[Any, float]], outcomes: Sequence[Any]) -> float:
    """Σ over categories of (p − 1[outcome])², averaged over cases (0 perfect, 2 worst).

    An outcome that no forecast lists counts as a category given probability 0.
    """
    _pairs(forecasts, outcomes)
    total = []
    for forecast, outcome in zip(forecasts, outcomes):
        dist = _distribution(forecast)
        seen = str(outcome)
        total.append(math.fsum((p - (1.0 if k == seen else 0.0)) ** 2 for k, p in dist.items())
                     + (0.0 if seen in dist else 1.0))
    return mean(total)


def log_loss(probabilities: Sequence[float], outcomes: Sequence[Any], epsilon: float = DEFAULT_EPSILON) -> float:
    """Mean negative log likelihood of yes/no outcomes (0 perfect; punishes confident misses hard)."""
    _pairs(probabilities, outcomes)
    losses = []
    for p, o in zip(probabilities, outcomes):
        q = min(1.0 - epsilon, max(epsilon, _probability(p)))
        losses.append(-math.log(q if _event(o) else 1.0 - q))
    return mean(losses)


def log_loss_multiclass(forecasts: Sequence[Mapping[Any, float]], outcomes: Sequence[Any],
                        epsilon: float = DEFAULT_EPSILON) -> float:
    _pairs(forecasts, outcomes)
    return mean([-math.log(max(epsilon, _distribution(f).get(str(o), 0.0))) for f, o in zip(forecasts, outcomes)])


def crps_ensemble(members: Sequence[float], observation: float) -> float:
    """Continuous ranked probability score of one ensemble: E|X − y| − ½·E|X − X′|.

    It is the mean absolute error generalised to a whole distribution, in the outcome's units.
    """
    values = [float(m) for m in members if is_number(m)]
    if not values:
        raise ValueError("an ensemble needs at least one numeric member")
    if not is_number(observation):
        raise ValueError(f"the observation must be a number, got {observation!r}")
    n = len(values)
    first = math.fsum(abs(v - observation) for v in values) / n
    ordered = sorted(values)
    # Σ_i Σ_j |x_i − x_j| = 2 Σ_k (2k − n + 1) x_(k) over sorted values: O(n log n).
    pair_sum = 2.0 * math.fsum((2 * k - n + 1) * x for k, x in enumerate(ordered))
    return first - pair_sum / (2.0 * n * n)


def crps(ensembles: Sequence[Sequence[float]], observations: Sequence[float]) -> float:
    _pairs(ensembles, observations)
    return mean([crps_ensemble(e, o) for e, o in zip(ensembles, observations)])


def interval_coverage(intervals: Sequence[tuple[float, float]], outcomes: Sequence[float],
                      nominal: float | None = None) -> dict[str, Any]:
    """How often outcomes fall inside their intervals (ends included), with a Wilson interval."""
    _pairs(intervals, outcomes)
    hits, widths = 0, []
    for interval, outcome in zip(intervals, outcomes):
        low, high = interval
        if not (is_number(low) and is_number(high) and low <= high):
            raise ValueError(f"an interval must be [low, high] numbers with low ≤ high, got {interval!r}")
        hits += 1 if low <= outcome <= high else 0
        widths.append(high - low)
    n = len(outcomes)
    low_ci, high_ci = wilson(hits, n)
    out: dict[str, Any] = {"n": n, "coverage": hits / n, "coverage_ci95": [low_ci, high_ci],
                           "mean_width": mean(widths)}
    if nominal is not None:
        out["nominal"] = nominal
        out["gap"] = hits / n - nominal
        out["consistent"] = low_ci <= nominal <= high_ci
    return out


def reliability(probabilities: Sequence[float], outcomes: Sequence[Any],
                bins: int = DEFAULT_BINS) -> list[ReliabilityBin]:
    """Non-empty bins of equal width over [0, 1]; a forecast of exactly 1 falls in the last bin."""
    _pairs(probabilities, outcomes)
    if bins < 1:
        raise ValueError("bins must be at least 1")
    grouped: dict[int, list[tuple[float, int]]] = {}
    for p, o in zip(probabilities, outcomes):
        q = _probability(p)
        index = min(bins - 1, int(math.floor(q * bins + 1e-9)))
        grouped.setdefault(index, []).append((q, _event(o)))
    out = []
    for index in sorted(grouped):
        members = grouped[index]
        k = sum(o for _, o in members)
        low, high = wilson(k, len(members))
        out.append(ReliabilityBin(index / bins, (index + 1) / bins, len(members), mean([p for p, _ in members]),
                                  k / len(members), low, high))
    return out


def ece(probabilities: Sequence[float], outcomes: Sequence[Any], bins: int = DEFAULT_BINS) -> float:
    """Expected calibration error: bin-size-weighted |mean forecast − observed frequency|."""
    table = reliability(probabilities, outcomes, bins)
    n = sum(b.n for b in table)
    return math.fsum(b.n * abs(b.mean_forecast - b.observed) for b in table) / n


def murphy(probabilities: Sequence[float], outcomes: Sequence[Any], bins: int = DEFAULT_BINS) -> dict[str, float]:
    """Brier = reliability − resolution + uncertainty (+ a within-bin residual).

    reliability: calibration error (lower is better); resolution: how much forecasts separate
    cases from the base rate (higher is better); uncertainty: base rate × (1 − base rate). The
    identity is exact when forecasts inside a bin are equal; ``residual`` holds the difference.
    """
    table = reliability(probabilities, outcomes, bins)
    n = sum(b.n for b in table)
    base = math.fsum(b.n * b.observed for b in table) / n
    rel = math.fsum(b.n * (b.mean_forecast - b.observed) ** 2 for b in table) / n
    res = math.fsum(b.n * (b.observed - base) ** 2 for b in table) / n
    unc = base * (1.0 - base)
    score = brier(probabilities, outcomes)
    return {"brier": score, "reliability": rel, "resolution": res, "uncertainty": unc,
            "residual": score - (rel - res + unc), "base_rate": base}


def skill_score(value: float, reference: float, perfect: float = 0.0) -> float | None:
    """1 − (score − perfect)/(reference − perfect): 1 perfect, 0 no better than the reference, < 0 worse."""
    if reference == perfect:
        return None
    return 1.0 - (value - perfect) / (reference - perfect)


def _kind(forecasts: Sequence[Any]) -> str:
    """Categorical for mappings, ensemble for lists; intervals must be named (a pair looks like an ensemble)."""
    first = forecasts[0]
    if isinstance(first, Mapping):
        return "categorical"
    if isinstance(first, (list, tuple)):
        return "ensemble"
    return "binary"


def score(forecasts: Sequence[Any], outcomes: Sequence[Any], *, kind: str = "auto",
          climatology: Any = None, bins: int = DEFAULT_BINS, nominal: float | None = None,
          epsilon: float = DEFAULT_EPSILON) -> dict[str, Any]:
    """Every applicable score for a set of forecasts against what happened.

    ``kind``: ``binary`` (probabilities of a yes/no event), ``categorical`` (mappings of
    category → probability), ``ensemble`` (lists of simulated numbers), ``interval``
    ([low, high] pairs), or ``auto`` (``interval`` must be named: a two-member ensemble looks
    the same). ``climatology`` is the reference forecast for the skill score: a base rate
    (binary), a category distribution (categorical) or a sample of numbers (ensemble). Without
    it the reference is the outcomes' own frequency — in-sample, and labelled as such.
    """
    _pairs(forecasts, outcomes)
    chosen = _kind(forecasts) if kind == "auto" else kind
    if chosen == "binary":
        return _score_binary(forecasts, outcomes, climatology, bins, epsilon)
    if chosen == "categorical":
        return _score_categorical(forecasts, outcomes, climatology, epsilon)
    if chosen == "ensemble":
        return _score_ensemble(forecasts, outcomes, climatology, nominal)
    if chosen == "interval":
        return {"kind": "interval", **interval_coverage(forecasts, outcomes, nominal)}
    raise ValueError(f"unknown kind '{kind}' (binary, categorical, ensemble, interval or auto)")


def _score_binary(forecasts: Sequence[Any], outcomes: Sequence[Any], climatology: Any, bins: int,
                  epsilon: float) -> dict[str, Any]:
    events = [_event(o) for o in outcomes]
    in_sample = climatology is None
    base = mean(events) if in_sample else _probability(climatology)
    decomposition = murphy(forecasts, events, bins)
    reference = brier([base] * len(events), events)
    return {
        "kind": "binary", "n": len(events), "brier": decomposition["brier"],
        "log_loss": log_loss(forecasts, events, epsilon), "ece": ece(forecasts, events, bins),
        "murphy": decomposition, "reliability": [b.to_dict() for b in reliability(forecasts, events, bins)],
        "climatology": {"base_rate": base, "brier": reference, "in_sample": in_sample},
        "skill": skill_score(decomposition["brier"], reference),
    }


def _score_categorical(forecasts: Sequence[Any], outcomes: Sequence[Any], climatology: Any,
                       epsilon: float) -> dict[str, Any]:
    in_sample = climatology is None
    if in_sample:
        counts: dict[str, int] = {}
        for o in outcomes:
            counts[str(o)] = counts.get(str(o), 0) + 1
        reference_dist = {k: v / len(outcomes) for k, v in counts.items()}
    else:
        reference_dist = _distribution(climatology)
    hits = sum(1 for f, o in zip(forecasts, outcomes) if max(_distribution(f).items(), key=lambda kv: kv[1])[0]
               == str(o))
    value = brier_multiclass(forecasts, outcomes)
    reference = brier_multiclass([reference_dist] * len(outcomes), outcomes)
    return {"kind": "categorical", "n": len(outcomes), "brier": value,
            "log_loss": log_loss_multiclass(forecasts, outcomes, epsilon), "accuracy": hits / len(outcomes),
            "climatology": {"distribution": reference_dist, "brier": reference, "in_sample": in_sample},
            "skill": skill_score(value, reference)}


def _score_ensemble(forecasts: Sequence[Any], outcomes: Sequence[Any], climatology: Any,
                    nominal: float | None) -> dict[str, Any]:
    level = 0.8 if nominal is None else nominal
    tail = (1.0 - level) / 2.0
    ensembles = [[float(m) for m in f if is_number(m)] for f in forecasts]
    observations = [float(o) for o in outcomes]
    if any(not e for e in ensembles):
        raise ValueError("every ensemble needs at least one numeric member")
    in_sample = climatology is None
    reference_sample = observations if in_sample else [float(v) for v in climatology]
    value = crps(ensembles, observations)
    reference = crps([reference_sample] * len(observations), observations)
    intervals = [(quantile(e, tail), quantile(e, 1.0 - tail)) for e in ensembles]
    return {"kind": "ensemble", "n": len(observations), "crps": value,
            "mae_of_median": mean([abs(quantile(e, 0.5) - o) for e, o in zip(ensembles, observations)]),
            "coverage": interval_coverage(intervals, observations, level),
            "climatology": {"crps": reference, "in_sample": in_sample},
            "skill": skill_score(value, reference)}
