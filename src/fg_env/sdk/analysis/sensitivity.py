"""Which inputs matter for an output, ranked, with intervals.

Three methods, all on common seeds:

* ``oat`` — one at a time around a baseline: the elasticity (% change in the output per % change
  in the input) from a central difference, per seed, with a t interval across seeds.
* ``morris`` — elementary-effects screening over the whole range: ``trajectories`` random
  one-step-at-a-time paths on a ``levels``-point grid. μ* (mean absolute effect, in output units
  per full input range) ranks influence; σ (spread of effects) flags non-linearity or interaction.
* ``sobol`` — a first-order index from a Latin hypercube: the share of output variance explained
  by each input alone (a correlation ratio), with a bootstrap interval. "Sobol-lite": it does not
  estimate interaction (total-order) indices.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ..api import ContractLike
from ..seeds import SeedTree
from . import runner
from .stats import correlation_ratio, estimate, latin_hypercube, mean, quantile, sd

__all__ = ["sensitivity", "SensitivityResult"]

#: Bootstrap resamples for the first-order index interval.
_BOOTSTRAP = 200

InputRanges = Union[Sequence[str], Mapping[str, Optional[Mapping[str, Any]]]]


@dataclass
class SensitivityResult:
    contract: str
    output: str
    method: str
    runs: int
    ranking: List[Dict[str, Any]]
    details: Dict[str, Any] = field(default_factory=dict)

    def report(self) -> str:
        unit = {"oat": "elasticity", "morris": "μ* (output change per full range)", "sobol": "first-order share"}[self.method]
        lines = [f"Sensitivity of {self.output} in {self.contract}: {self.method}, {self.runs} run(s) per point", f"Ranked by {unit}:"]
        for rank, row in enumerate(self.ranking, 1):
            value = row["value"]
            text = "—" if value is None else f"{value:+.4g}" if self.method == "oat" else f"{value:.4g}"
            interval = f" [{row['low']:.3g}, {row['high']:.3g}]" if row.get("low") is not None else ""
            extra = row.get("note") or (f", σ {row['sigma']:.3g}" if row.get("sigma") is not None else "")
            lines.append(f"  {rank}. {row['input']}: {text}{interval}{extra}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"contract": self.contract, "output": self.output, "method": self.method, "runs": self.runs,
                "ranking": self.ranking, "details": self.details}


def _ranges(contract: Any, inputs: InputRanges) -> Dict[str, Optional[Mapping[str, Any]]]:
    if isinstance(inputs, Mapping):
        return dict(inputs)
    if isinstance(inputs, str) or not inputs:
        raise ValueError("inputs must be a non-empty list of input names or {name: {low, high}}")
    return {name: None for name in inputs}


def sensitivity(contract: ContractLike, inputs: InputRanges, output: str, *, method: str = "oat", runs: int = 5,
                baseline: Optional[Mapping[str, Any]] = None, delta: float = 0.1, trajectories: int = 6,
                levels: int = 4, samples: int = 20, arm: Optional[str] = None, participants: Any = None,
                rounds: Optional[int] = None, seed: int = 0, workers: int = 1, level: float = 0.95,
                data_dir: Any = None, hosts: Any = None) -> SensitivityResult:
    """Rank ``inputs`` by their influence on ``output`` (an output or a metric's final value).

    ``inputs``: names, or ``{name: {"low", "high"}}`` (ranges default to declared min/max; OAT
    only needs them to keep perturbations inside the allowed range). ``baseline`` overrides the
    contract defaults as the OAT centre and the fixed values for the other inputs. ``data_dir`` is where
    inputs with a ``source`` are read (default: the contract file's folder); ``hosts`` answers host requests.
    """
    runner.check_positive_int("runs", runs)
    parsed = runner.as_contract(contract, data_dir)
    measure = runner.resolve_measure(parsed, output)
    ranges = _ranges(parsed, inputs)
    for name in ranges:
        spec = runner.input_spec(parsed, name)
        if spec.type not in ("number", "int"):
            raise ValueError(f"input '{name}' is {spec.type}; sensitivity needs number or int inputs")
    common = dict(arm=arm, participants=participants, rounds=rounds, seed=seed, workers=workers, level=level,
                  hosts=hosts)
    fixed = dict(baseline or {})
    if method == "oat":
        if not 0 < delta < 1:
            raise ValueError(f"delta must be between 0 and 1 (a share of the baseline), got {delta}")
        ranking, details = _oat(parsed, ranges, measure, runs, fixed, delta, **common)
    elif method == "morris":
        runner.check_positive_int("trajectories", trajectories, 2)
        runner.check_positive_int("levels", levels, 2)
        ranking, details = _morris(parsed, ranges, measure, runs, fixed, trajectories, levels, **common)
    elif method == "sobol":
        runner.check_positive_int("samples", samples, 4)
        ranking, details = _sobol(parsed, ranges, measure, runs, fixed, samples, **common)
    else:
        raise ValueError(f"method must be 'oat', 'morris' or 'sobol', got {method!r}")
    return SensitivityResult(parsed.name, output, method, runs, ranking, details)


def _run_points(contract: Any, points: Sequence[Mapping[str, Any]], measure: Tuple[str, str], runs: int, *,
                arm: Optional[str], participants: Any, rounds: Optional[int], seed: int, workers: int, hosts: Any
                ) -> List[List[Optional[float]]]:
    """Output values per point (outer) per seed (inner), common seeds across points."""
    seeds = runner.run_seeds(seed, runs)
    jobs = runner.jobs_for([(p, arm) for p in points], seeds)
    results = runner.run_jobs(contract, jobs, participants=participants, rounds=rounds, workers=workers, hosts=hosts)
    return [[runner.value(r, measure) for r in cell] for cell in runner.by_cell(jobs, results, len(points))]


def _default(contract: Any, name: str, fixed: Mapping[str, Any]) -> float:
    value = fixed.get(name, runner.input_spec(contract, name).default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"input '{name}' has no numeric baseline; pass baseline={{'{name}': …}}")
    return float(value)


def _oat(contract: Any, ranges: Mapping[str, Any], measure: Tuple[str, str], runs: int, fixed: Mapping[str, Any],
         delta: float, *, level: float, **common: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    points: List[Dict[str, Any]] = [dict(fixed)]
    plan = {}
    for name, given in ranges.items():
        centre = _default(contract, name, fixed)
        spec = runner.input_spec(contract, name)
        low_bound = (given or {}).get("low", spec.min)
        high_bound = (given or {}).get("high", spec.max)
        step = abs(centre) * delta if centre != 0 else delta * ((high_bound - low_bound) if low_bound is not None and high_bound is not None else 1.0)
        down, up = centre - step, centre + step
        down = max(down, low_bound) if low_bound is not None else down
        up = min(up, high_bound) if high_bound is not None else up
        down_v, up_v = runner.coerce_input(contract, name, down), runner.coerce_input(contract, name, up)
        if down_v == up_v and spec.type == "int":  # rounding collapsed the step: move one whole unit if allowed
            bumped = runner.coerce_input(contract, name, centre + 1)
            up_v = bumped if high_bound is None or bumped <= high_bound else up_v
        plan[name] = (centre, down_v, up_v, len(points))
        points += [{**fixed, name: down_v}, {**fixed, name: up_v}]
    values = _run_points(contract, points, measure, runs, **common)
    base = [v for v in values[0] if v is not None]
    base_mean = mean(base) if base else None
    ranking: List[Dict[str, Any]] = []
    for name, (centre, down_v, up_v, index) in plan.items():
        if up_v == down_v:
            ranking.append({"input": name, "value": None, "note": " (range too narrow to perturb)"})
            continue
        slopes = [(u - d) / (up_v - down_v) for d, u in zip(values[index], values[index + 1]) if d is not None and u is not None]
        slope = estimate(slopes, level)
        row: Dict[str, Any] = {"input": name, "baseline": centre, "down": down_v, "up": up_v,
                               "slope": slope.to_dict()}
        if base_mean and centre != 0 and slope.mean is not None:
            factor = centre / base_mean
            row.update(value=slope.mean * factor,
                       low=None if slope.low is None else min(slope.low * factor, slope.high * factor),
                       high=None if slope.high is None else max(slope.low * factor, slope.high * factor))
        else:
            row.update(value=None, note=" (elasticity undefined at a zero baseline; see slope)")
        ranking.append(row)
    ranking.sort(key=lambda r: -abs(r["value"]) if r.get("value") is not None else math.inf)
    return ranking, {"baseline_output": base_mean, "delta": delta}


def _morris(contract: Any, ranges: Mapping[str, Any], measure: Tuple[str, str], runs: int, fixed: Mapping[str, Any],
            trajectories: int, grid_levels: int, *, level: float, seed: int, **common: Any
            ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    names = list(ranges)
    bounds = {n: runner.bounds(contract, n, ranges[n]) for n in names}
    step = grid_levels / (2.0 * (grid_levels - 1))
    starts = [i / (grid_levels - 1) for i in range(grid_levels) if i / (grid_levels - 1) + step <= 1.0 + 1e-12]
    rng = SeedTree(seed).rng("morris")
    paths = []
    for _ in range(trajectories):
        unit = {n: rng.choice(starts) for n in names}
        order = list(names)
        rng.shuffle(order)
        path = [dict(unit)]
        for name in order:
            unit = {**unit, name: unit[name] + step}
            path.append(dict(unit))
        paths.append((order, path))

    def actual(unit: Mapping[str, float]) -> Dict[str, Any]:
        return {**fixed, **{n: runner.coerce_input(contract, n, bounds[n][0] + u * (bounds[n][1] - bounds[n][0]))
                            for n, u in unit.items()}}

    points = [actual(u) for _, path in paths for u in path]
    values = _run_points(contract, points, measure, runs, seed=seed, **common)
    means = [mean([v for v in cell if v is not None]) if any(v is not None for v in cell) else None for cell in values]
    effects: Dict[str, List[float]] = {n: [] for n in names}
    cursor = 0
    for order, path in paths:
        for k, name in enumerate(order):
            a, b = cursor + k, cursor + k + 1
            moved = (points[b][name] - points[a][name]) / (bounds[name][1] - bounds[name][0])
            if means[a] is not None and means[b] is not None and moved != 0:
                effects[name].append((means[b] - means[a]) / moved)
        cursor += len(path)
    ranking: List[Dict[str, Any]] = []
    for name in names:
        absolute = estimate([abs(e) for e in effects[name]], level)
        ranking.append({"input": name, "value": absolute.mean, "low": absolute.low, "high": absolute.high,
                        "mu": mean(effects[name]) if effects[name] else None,
                        "sigma": sd(effects[name]) if effects[name] else None, "effects": len(effects[name])})
    ranking.sort(key=lambda r: -(r["value"] or 0.0))
    return ranking, {"trajectories": trajectories, "levels": grid_levels, "step": step, "bounds": bounds}


def _sobol(contract: Any, ranges: Mapping[str, Any], measure: Tuple[str, str], runs: int, fixed: Mapping[str, Any],
           samples: int, *, level: float, seed: int, **common: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    names = list(ranges)
    bounds = {n: runner.bounds(contract, n, ranges[n]) for n in names}
    tree = SeedTree(seed)
    unit = latin_hypercube(samples, len(names), tree.rng("sobol"))
    points = [{**fixed, **{n: runner.coerce_input(contract, n, bounds[n][0] + u * (bounds[n][1] - bounds[n][0]))
                           for n, u in zip(names, row)}} for row in unit]
    values = _run_points(contract, points, measure, runs, seed=seed, **common)
    rows = [(p, mean([v for v in cell if v is not None])) for p, cell in zip(points, values) if any(v is not None for v in cell)]
    bins = max(2, int(math.sqrt(len(rows))))
    rng = tree.rng("sobol-bootstrap")
    ranking: List[Dict[str, Any]] = []
    for name in names:
        xs = [float(p[name]) for p, _ in rows]
        ys = [y for _, y in rows]
        index = correlation_ratio(xs, ys, bins, adjusted=True) if len(rows) > bins else None
        interval = _bootstrap(xs, ys, bins, rng, level) if index is not None else None
        ranking.append({"input": name, "value": index, "low": interval[0] if interval else None,
                        "high": interval[1] if interval else None})
    ranking.sort(key=lambda r: -(r["value"] or 0.0))
    return ranking, {"samples": samples, "bins": bins, "bounds": bounds}


def _bootstrap(xs: Sequence[float], ys: Sequence[float], bins: int, rng: random.Random,
               level: float) -> Optional[Tuple[float, float]]:
    n = len(xs)
    draws = []
    for _ in range(_BOOTSTRAP):
        picks = [rng.randrange(n) for _ in range(n)]
        value = correlation_ratio([xs[i] for i in picks], [ys[i] for i in picks], bins, adjusted=True)
        if value is not None:
            draws.append(value)
    if not draws:
        return None
    tail = (1.0 - level) / 2.0
    return quantile(draws, tail), quantile(draws, 1.0 - tail)
