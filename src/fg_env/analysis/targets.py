"""Calibration targets: what a contract's runs should match, how far off they are, and matching cases together.

Targets (``{name: spec}``; ``name`` is an output or metric, or ``series.<metric>``):

* a number — the mean over runs should equal it: ``{"peak_infected": 20}``;
* a list — a metric's per-round path: the mean simulated path should follow it (RMSE);
* ``{"value": v, "stat": "volatility", "of": "last_price"}`` — a named statistic of a metric's
  series (a stylized fact; see :mod:`.facts`), averaged over runs;
* ``{"distribution": [values]}`` — the spread of the output across runs should match a sample
  (Wasserstein distance);
* any spec may add ``"weight"`` and ``"scale"`` (the error that counts as "one unit off").

A rate recorded case by case (a day's abandonment, a store's fill rate) may add:

* ``"count"`` — the trials behind it (calls offered, units demanded). Its error then counts in standard errors of a rate
  with that many trials at the cases' pooled rate: a likelihood-style weight, so a quiet day's noisy rate weighs as
  little as its data does. Scaled by its own value instead, the smallest (and noisiest) rates count most.
* ``"pool": true`` — the cases are matched together: the mean of their simulated values against the mean of their
  recorded ones (weighted by ``count`` when given), one error instead of one per case. When the response is not linear
  in what differs between cases (abandonment climbs steeply as a day gets busier) and each replayed case is only
  approximately that day, per-case errors pull a parameter away from the level the cases share; the pooled level does
  not.

:func:`pooled_checks` compares a per-case fit with the pooled level of the same target, and says when they disagree.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..runtime.measure import RunResult
from . import runner
from .facts import statistic
from .stats import is_number, mean, sd, wasserstein

__all__ = ["Target", "parse_targets", "target_error", "evaluate_targets", "count_scaled", "pooled_error",
           "pooled_checks"]

#: The error scale when a target is 0 and no ``scale`` is given (avoids dividing by zero).
_ZERO_SCALE = 1.0
#: A per-case fit disagrees with the pooled level when they differ by more than this many standard errors of that gap.
_DISAGREE_SE = 2.0


@dataclass(frozen=True)
class Target:
    name: str
    kind: str  # value | series | stat | distribution
    measure: Tuple[str, str]
    goal: Any
    stat: Optional[str] = None
    weight: float = 1.0
    scale: Optional[float] = None
    #: Trials behind a recorded rate (weights its error by its data).
    count: Optional[float] = None
    #: Matched together with the same target of the other cases.
    pool: bool = False


def parse_targets(contract: Any, targets: Mapping[str, Any]) -> List[Target]:
    if not targets:
        raise ValueError("calibrate needs at least one target")
    return [_target(contract, name, spec) for name, spec in targets.items()]


def _target(contract: Any, name: str, spec: Any) -> Target:
    lookup = name[len("series."):] if name.startswith("series.") else name
    options: Mapping[str, Any] = spec if isinstance(spec, Mapping) else {}
    weight = float(options.get("weight", 1.0))
    scale = options.get("scale")
    if weight <= 0 or (scale is not None and (not is_number(scale) or scale <= 0)):
        raise ValueError(f"target '{name}': weight and scale must be positive numbers")
    if isinstance(spec, Mapping) and "stat" in spec:
        of = spec.get("of", lookup)
        measure = runner.resolve_measure(contract, f"metrics.{of}")
        statistic(spec["stat"], [1.0, 2.0, 3.0, 4.0, 5.0])  # an unknown statistic name fails now
        goal = spec.get("value")
        if not is_number(goal):
            raise ValueError(f"target '{name}': a statistic target needs a numeric 'value'")
        target = Target(name, "stat", measure, float(goal), spec["stat"], weight, scale)
    elif isinstance(spec, Mapping) and "distribution" in spec:
        sample = spec["distribution"]
        if not isinstance(sample, Sequence) or not sample or not all(is_number(v) for v in sample):
            raise ValueError(f"target '{name}': 'distribution' must be a non-empty list of numbers")
        target = Target(name, "distribution", runner.resolve_measure(contract, lookup), [float(v) for v in sample],
                        None, weight, scale)
    else:
        goal = spec.get("value") if isinstance(spec, Mapping) else spec
        if isinstance(goal, Sequence) and not isinstance(goal, str):
            if not goal or not all(is_number(v) for v in goal):
                raise ValueError(f"target '{name}': a series target must be a non-empty list of numbers")
            target = Target(name, "series", runner.resolve_measure(contract, f"metrics.{lookup}"),
                            [float(v) for v in goal], None, weight, scale)
        elif is_number(goal) or isinstance(goal, bool):
            target = Target(name, "value", runner.resolve_measure(contract, lookup), float(goal), None, weight, scale)
        else:
            raise ValueError(f"target '{name}': give a number, a list, or {{value|stat|distribution}}, got {spec!r}")
    return _counted(target, options)


def _counted(target: Target, options: Mapping[str, Any]) -> Target:
    """The target with its ``count`` and ``pool`` options, checked."""
    count, pool = options.get("count"), options.get("pool", False)
    if count is None and not pool:
        return target
    if target.kind != "value":
        raise ValueError(f"target '{target.name}': count and pool apply to a number target")
    if not isinstance(pool, bool):
        raise ValueError(f"target '{target.name}': pool must be true or false, got {pool!r}")
    if count is not None:
        if isinstance(count, bool) or not is_number(count) or count <= 0:
            raise ValueError(f"target '{target.name}': count must be a positive number of trials, got {count!r}")
        if not 0 <= target.goal <= 1:
            raise ValueError(f"target '{target.name}': count weighs a rate, so its value must lie between 0 and 1")
    return replace(target, count=None if count is None else float(count), pool=pool)


def target_error(target: Target, runs: Sequence[RunResult]) -> Dict[str, Any]:
    """The simulated counterpart of one target and its normalized error (``None`` without data)."""
    ok = [r for r in runs if r.status != "failed"]
    if target.kind == "value":
        values = [v for v in (runner.value(r, target.measure) for r in ok) if v is not None]
        if not values:
            return {"target": target.name, "simulated": None, "error": None}
        simulated = mean(values)
        scale = target.scale or (abs(target.goal) if target.goal else _ZERO_SCALE)
        return {"target": target.name, "goal": target.goal, "simulated": simulated, "sd": sd(values),
                "n": len(values), "error": (simulated - target.goal) / scale}
    if target.kind == "stat":
        found = []
        for r in ok:
            path = runner.series(r, target.measure[1])
            try:
                found.append(statistic(target.stat or "", path))
            except (ValueError, ZeroDivisionError):
                continue
        if not found:
            return {"target": target.name, "simulated": None, "error": None}
        simulated = mean(found)
        scale = target.scale or (abs(target.goal) if target.goal else _ZERO_SCALE)
        return {"target": target.name, "stat": target.stat, "goal": target.goal, "simulated": simulated,
                "error": (simulated - target.goal) / scale}
    if target.kind == "series":
        paths = [runner.series(r, target.measure[1]) for r in ok]
        length = min([len(target.goal)] + [len(p) for p in paths]) if paths else 0
        if length == 0:
            return {"target": target.name, "simulated": None, "error": None}
        average = [mean([p[t] for p in paths]) for t in range(length)]
        rmse = math.sqrt(mean([(a - g) ** 2 for a, g in zip(average, target.goal[:length])]))
        scale = target.scale or (sd(target.goal) or mean([abs(g) for g in target.goal]) or _ZERO_SCALE)
        return {"target": target.name, "rmse": rmse, "rounds_compared": length, "simulated": average,
                "error": rmse / scale}
    values = [v for v in (runner.value(r, target.measure) for r in ok) if v is not None]
    if not values:
        return {"target": target.name, "simulated": None, "error": None}
    distance = wasserstein(values, target.goal)
    scale = target.scale or (sd(target.goal) or abs(mean(target.goal)) or _ZERO_SCALE)
    return {"target": target.name, "distance": distance, "simulated_mean": mean(values), "error": distance / scale}


def evaluate_targets(targets: Sequence[Target], runs: Sequence[RunResult]) -> Tuple[float, List[Dict[str, Any]]]:
    """Weighted RMS of normalized errors (``inf`` when a target has no simulated value)."""
    details = [target_error(t, runs) for t in targets]
    if any(d["error"] is None for d in details):
        return math.inf, details
    total = math.fsum(t.weight for t in targets)
    return math.sqrt(math.fsum(t.weight * d["error"] ** 2 for t, d in zip(targets, details)) / total), details


def _rate_error(rate: float, count: float) -> float:
    """The standard error of a rate over ``count`` trials (one trial's worth when the rate is 0 or 1)."""
    spread = rate * (1 - rate)
    return math.sqrt(spread / count) if spread > 0 else 1.0 / count


def _pooled_goals(goal_lists: Sequence[Sequence[Target]]) -> Dict[str, float]:
    """Each counted target's recorded rate pooled over the cases (weighted by count)."""
    sums: Dict[str, List[float]] = {}
    for goals in goal_lists:
        for goal in goals:
            if goal.count is not None:
                total = sums.setdefault(goal.name, [0.0, 0.0])
                total[0] += goal.count * goal.goal
                total[1] += goal.count
    return {name: weighted / count for name, (weighted, count) in sums.items()}


def count_scaled(goal_lists: Sequence[Sequence[Target]]) -> List[List[Target]]:
    """Per-case targets with a ``count`` and no ``scale``, scaled by a rate's standard error at the pooled rate."""
    rates = _pooled_goals(goal_lists)
    return [[replace(goal, scale=_rate_error(rates[goal.name], goal.count))
             if goal.count is not None and goal.scale is None and not goal.pool else goal for goal in goals]
            for goals in goal_lists]


def pooled_error(pairs: Sequence[Tuple[Target, Mapping[str, Any]]]) -> Dict[str, Any]:
    """One target over every case: the weighted means of its simulated and recorded values, the gap's standard error
    (the runs' noise and, with counts, the recorded rates'), and the normalized error."""
    first = pairs[0][0]
    weights = [goal.count or 1.0 for goal, _ in pairs]
    total = math.fsum(weights)
    goal = math.fsum(w * g.goal for w, (g, _) in zip(weights, pairs)) / total
    row: Dict[str, Any] = {"target": first.name, "case": "pooled", "cases": len(pairs), "goal": goal}
    if any(detail.get("simulated") is None for _, detail in pairs):
        return {**row, "simulated": None, "error": None, "se": None}
    simulated = math.fsum(w * d["simulated"] for w, (_, d) in zip(weights, pairs)) / total
    noise = math.fsum((w * d.get("sd", 0.0) / math.sqrt(d.get("n", 1))) ** 2 for w, (_, d) in zip(weights, pairs))
    counted = all(g.count is not None for g, _ in pairs)
    recorded = _rate_error(goal, total) if counted else 0.0
    se = math.sqrt(noise / total ** 2 + recorded ** 2)
    scale = first.scale if first.scale and not first.count else (recorded if counted else abs(goal) or _ZERO_SCALE)
    return {**row, "simulated": simulated, "se": se, "error": (simulated - goal) / scale}


def pooled_checks(cases: Sequence[Sequence[Target]], details: Sequence[Mapping[str, Any]],
                  history: Sequence[Tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]]) -> List[Dict[str, Any]]:
    """For every number target fitted case by case in more than one case: its pooled level at the best fit, whether that
    disagrees with the recorded pooled level beyond noise, and the evaluated inputs that match the pooled level best.

    ``details`` are the best fit's per-case rows in case and target order; ``history`` every evaluated point's
    ``(inputs, rows)`` in the same order."""
    order = [(i, goal) for i, goals in enumerate(cases) for goal in goals]
    names = [name for name in dict.fromkeys(goal.name for _, goal in order)
             if sum(1 for _, g in order if g.name == name and g.kind == "value" and not g.pool) > 1]
    checks = []
    for name in names:
        at = [k for k, (_, goal) in enumerate(order) if goal.name == name]

        def pooled(rows: Sequence[Mapping[str, Any]], at: List[int] = at) -> Dict[str, Any]:
            return pooled_error([(order[k][1], rows[k]) for k in at])

        best = pooled(details)
        if best["simulated"] is None:
            continue
        gap = best["simulated"] - best["goal"]
        disagrees = bool(best["se"]) and abs(gap) > _DISAGREE_SE * best["se"]
        scored = [(abs(p["simulated"] - p["goal"]), dict(inputs)) for inputs, rows in history
                  for p in [pooled(rows)] if p["simulated"] is not None]
        closest = min(scored, key=lambda s: s[0])[1] if scored else None
        checks.append({"target": name, "cases": len(at), "goal": best["goal"], "simulated": best["simulated"],
                       "se": best["se"], "disagrees": disagrees, "closest_inputs": closest})
    return checks
