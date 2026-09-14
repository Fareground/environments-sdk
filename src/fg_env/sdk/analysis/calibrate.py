"""Calibration: fit inputs so a contract's outputs and metrics match targets.

Targets (``{name: spec}``; ``name`` is an output or metric, or ``series.<metric>``):

* a number — the mean over runs should equal it: ``{"peak_infected": 20}``;
* a list — a metric's per-round path: the mean simulated path should follow it (RMSE);
* ``{"value": v, "stat": "volatility", "of": "last_price"}`` — a named statistic of a metric's
  series (a stylized fact; see :mod:`.facts`), averaged over runs;
* ``{"distribution": [values]}`` — the spread of the output across runs should match a sample
  (Wasserstein distance);
* any spec may add ``"weight"`` and ``"scale"`` (the error that counts as "one unit off").

The objective is the weighted root-mean-square of normalized errors, so a fit of 0.1 means
"about 10% of each target's scale off". The search runs every candidate on the same seeds,
which makes the objective a deterministic function of the inputs; the best fit is then re-run
on held-out seeds that played no part in the search, and that validation is what the report
leads with.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..api import ContractLike
from ..measure import RunResult
from ..seeds import SeedTree
from . import optimize, runner
from .facts import statistic
from .stats import estimate, is_number, mean, sd, wasserstein

__all__ = ["calibrate", "CalibrationResult", "Target", "parse_targets", "evaluate_targets"]

#: The error scale when a target is 0 and no ``scale`` is given (avoids dividing by zero).
_ZERO_SCALE = 1.0
#: Bootstrap resamples used to measure how noisy the objective is at the best fit.
_NOISE_RESAMPLES = 200
#: Evaluated points within this many objective standard errors of the best are "equally good".
_PLAUSIBLE_SE = 2.0
#: Cross-entropy population per generation, per parameter, and the share kept as elite.
_CE_POPULATION_PER_DIM, _CE_ELITE_SHARE = 6, 0.25


@dataclass(frozen=True)
class Target:
    name: str
    kind: str  # value | series | stat | distribution
    measure: Tuple[str, str]
    goal: Any
    stat: Optional[str] = None
    weight: float = 1.0
    scale: Optional[float] = None


def parse_targets(contract: Any, targets: Mapping[str, Any]) -> List[Target]:
    if not targets:
        raise ValueError("calibrate needs at least one target")
    parsed = []
    for name, spec in targets.items():
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
            parsed.append(Target(name, "stat", measure, float(goal), spec["stat"], weight, scale))
        elif isinstance(spec, Mapping) and "distribution" in spec:
            sample = spec["distribution"]
            if not isinstance(sample, Sequence) or not sample or not all(is_number(v) for v in sample):
                raise ValueError(f"target '{name}': 'distribution' must be a non-empty list of numbers")
            parsed.append(Target(name, "distribution", runner.resolve_measure(contract, lookup),
                                 [float(v) for v in sample], None, weight, scale))
        else:
            goal = spec.get("value") if isinstance(spec, Mapping) else spec
            if isinstance(goal, Sequence) and not isinstance(goal, str):
                if not goal or not all(is_number(v) for v in goal):
                    raise ValueError(f"target '{name}': a series target must be a non-empty list of numbers")
                parsed.append(Target(name, "series", runner.resolve_measure(contract, f"metrics.{lookup}"),
                                     [float(v) for v in goal], None, weight, scale))
            elif is_number(goal) or isinstance(goal, bool):
                parsed.append(Target(name, "value", runner.resolve_measure(contract, lookup), float(goal), None,
                                     weight, scale))
            else:
                raise ValueError(f"target '{name}': give a number, a list, or {{value|stat|distribution}}, got {spec!r}")
    return parsed


def _target_error(target: Target, runs: Sequence[RunResult]) -> Dict[str, Any]:
    """The simulated counterpart of one target and its normalized error (``None`` without data)."""
    ok = [r for r in runs if r.status != "failed"]
    if target.kind == "value":
        values = [v for v in (runner.value(r, target.measure) for r in ok) if v is not None]
        if not values:
            return {"target": target.name, "simulated": None, "error": None}
        simulated = mean(values)
        scale = target.scale or (abs(target.goal) if target.goal else _ZERO_SCALE)
        return {"target": target.name, "goal": target.goal, "simulated": simulated, "sd": sd(values),
                "error": (simulated - target.goal) / scale}
    if target.kind == "stat":
        values = []
        for r in ok:
            path = runner.series(r, target.measure[1])
            try:
                values.append(statistic(target.stat or "", path))
            except (ValueError, ZeroDivisionError):
                continue
        if not values:
            return {"target": target.name, "simulated": None, "error": None}
        simulated = mean(values)
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
    details = [_target_error(t, runs) for t in targets]
    if any(d["error"] is None for d in details):
        return math.inf, details
    total = math.fsum(t.weight for t in targets)
    return math.sqrt(math.fsum(t.weight * d["error"] ** 2 for t, d in zip(targets, details)) / total), details


@dataclass
class CalibrationResult:
    contract: str
    params: Dict[str, Any]
    method: str
    fit: float
    targets: List[Dict[str, Any]]
    validation: Dict[str, Any]
    uncertainty: Dict[str, Dict[str, Any]]
    evaluations: int
    history: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def report(self) -> str:
        v = self.validation
        lines = [f"Calibration of {self.contract} ({self.method}, {self.evaluations} evaluation(s))",
                 "Best inputs: " + runner.describe_inputs(self.params),
                 f"Fit on search seeds: {self.fit:.4g}   on {v['runs']} held-out seed(s): {v['fit']:.4g}"]
        for detail in v["targets"]:
            lines.append("  " + _target_text(detail))
        lines.append("Parameter uncertainty (evaluated points as good as the best, within noise):")
        for name, u in self.uncertainty.items():
            lines.append(f"  {name}: {runner.describe_inputs({'best': u['best']})}, plausible "
                         f"{u['low']:.4g} to {u['high']:.4g} ({u['points']} point(s))")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"contract": self.contract, "params": self.params, "method": self.method, "fit": self.fit,
                "targets": self.targets, "validation": self.validation, "uncertainty": self.uncertainty,
                "evaluations": self.evaluations, "history": self.history, "notes": self.notes}


def _target_text(detail: Dict[str, Any]) -> str:
    if detail.get("simulated") is None and "rmse" not in detail and "distance" not in detail:
        return f"{detail['target']}: no simulated value"
    if "rmse" in detail:
        return f"{detail['target']}: path RMSE {detail['rmse']:.4g} over {detail['rounds_compared']} round(s)"
    if "distance" in detail:
        return f"{detail['target']}: distribution distance {detail['distance']:.4g}"
    label = f"{detail['stat']} of {detail['target']}" if detail.get("stat") else detail["target"]
    return f"{label}: simulated {detail['simulated']:.4g} vs target {detail['goal']:.4g} (error {detail['error']:+.1%})"


def calibrate(contract: ContractLike, targets: Mapping[str, Any], params: Mapping[str, Mapping[str, Any]], *,
              runs: int = 5, budget: int = 30, holdout: Optional[int] = None, method: str = "auto",
              inputs: Optional[Mapping[str, Any]] = None, arm: Optional[str] = None, participants: Any = None,
              rounds: Optional[int] = None, seed: int = 0, workers: int = 1) -> CalibrationResult:
    """Search ``params`` (``{input: {"low", "high", "log"?}}``) so the contract matches ``targets``.

    ``method``: ``bisection`` (one parameter, one number target, monotone response),
    ``golden`` (one parameter), ``nelder_mead`` or ``cross_entropy`` (several), or ``auto``
    (bisection when it applies and the response brackets the target, else golden for one
    parameter, Nelder–Mead for several). ``budget`` caps distinct evaluated points, each costing
    ``runs`` runs; ``holdout`` (default ``runs``) fresh seeds validate the best point.
    """
    runner.check_positive_int("runs", runs)
    runner.check_positive_int("budget", budget, 2)
    held = runner.check_positive_int("holdout", holdout if holdout is not None else runs)
    parsed = runner.as_contract(contract)
    if not params:
        raise ValueError("calibrate needs at least one param to fit")
    names = list(params)
    ranges = {n: runner.bounds(parsed, n, params[n]) for n in names}
    logs = {n: bool((params[n] or {}).get("log", False)) for n in names}
    for n in names:
        if logs[n] and ranges[n][0] <= 0:
            raise ValueError(f"param '{n}': log scale needs a positive low")
    problem = _Problem(parsed, parse_targets(parsed, targets), names, ranges, logs, dict(inputs or {}), arm,
                       participants, rounds, workers)
    with runner.worker_pool(workers, participants) as pool:
        return _fit(problem, pool, runs, held, budget, method, seed)


@dataclass(frozen=True)
class _Problem:
    """Everything fixed during a calibration: the contract, the targets and the parameter space."""

    contract: Any
    goals: List[Target]
    names: List[str]
    ranges: Dict[str, Tuple[float, float]]
    logs: Dict[str, bool]
    fixed: Dict[str, Any]
    arm: Optional[str]
    participants: Any
    rounds: Optional[int]
    workers: int

    def to_inputs(self, unit: Sequence[float]) -> Dict[str, Any]:
        """A point of the unit cube as input values (log-scaled where asked, rounded for int inputs)."""
        out = {}
        for n, u in zip(self.names, unit):
            low, high = self.ranges[n]
            raw = math.exp(math.log(low) + u * (math.log(high) - math.log(low))) if self.logs[n] else low + u * (high - low)
            out[n] = runner.coerce_input(self.contract, n, raw)
        return out

    def run(self, values: Mapping[str, Any], seeds: Sequence[int], pool: Any) -> List[RunResult]:
        jobs = [runner.Job({**self.fixed, **values}, self.arm, s) for s in seeds]
        return runner.run_jobs(self.contract, jobs, participants=self.participants, rounds=self.rounds,
                               workers=self.workers, pool=pool)


def _fit(problem: _Problem, pool: Any, runs: int, held: int, budget: int, method: str, seed: int) -> CalibrationResult:
    search_seeds = runner.run_seeds(seed, runs)

    def objective(unit: Tuple[float, ...]) -> Tuple[float, Any]:
        values = problem.to_inputs(unit)
        results = problem.run(values, search_seeds, pool)
        loss, details = evaluate_targets(problem.goals, results)
        return loss, (values, details, results)

    evaluator = optimize.Evaluator(objective, key=lambda u: tuple(sorted(problem.to_inputs(u).items())), budget=budget)
    chosen = _search(method, problem.names, problem.goals, evaluator, budget, seed)
    best = evaluator.best
    if best is None or not math.isfinite(best[1]):
        raise runner.AnalysisError("no evaluated point produced every target (check the target names and that runs complete)")
    unit, fit, (values, _, best_runs) = best
    notes = []
    single_value = len(problem.names) == 1 and len(problem.goals) == 1 and problem.goals[0].kind == "value"
    if method == "auto" and chosen != "bisection" and single_value:
        notes.append("the response did not bracket the target at the range ends, so bisection was not possible")
    at_edge = [n for n, u in zip(problem.names, unit) if u in (0.0, 1.0)]
    if at_edge:
        notes.append(f"best value at the edge of its range for {', '.join(at_edge)}: the true fit may lie outside it")
    validation_runs = problem.run(values, runner.run_seeds(seed, held, start=runs), pool)
    validation_fit, validation_details = evaluate_targets(problem.goals, validation_runs)
    uncertainty = _uncertainty(problem.names, evaluator, problem.goals, best_runs, fit, SeedTree(seed))
    history = [{"inputs": d[0], "fit": loss} for _, loss, d in evaluator.history]
    return CalibrationResult(problem.contract.name, values, chosen, fit, evaluate_targets(problem.goals, best_runs)[1],
                             {"runs": held, "fit": validation_fit, "targets": validation_details},
                             uncertainty, len(evaluator.history), history, notes)


def _search(method: str, names: List[str], goals: List[Target], evaluator: optimize.Evaluator, budget: int,
            seed: int) -> str:
    dims = len(names)
    iterations = max(1, budget)
    if method in ("auto", "bisection") and dims == 1 and len(goals) == 1 and goals[0].kind == "value":
        target = goals[0]

        def signed(u: float) -> Optional[float]:
            _, details, _ = evaluator.detail((u,))
            simulated = details[0].get("simulated")
            return None if simulated is None else simulated - target.goal

        if optimize.bisection(signed, evaluator, iterations):
            return "bisection"
        if method == "bisection":
            raise runner.AnalysisError("bisection needs the output to cross the target between the range ends; "
                                       "widen the range or use method='golden'")
    elif method == "bisection":
        raise ValueError("bisection fits one param to one number target; use 'golden', 'nelder_mead' or 'cross_entropy'")
    if method in ("auto", "golden", "bisection") and dims == 1:
        optimize.golden_section(evaluator, iterations)
        return "golden"
    if method == "golden":
        raise ValueError("golden-section search fits one param; use 'nelder_mead' or 'cross_entropy'")
    if method in ("auto", "nelder_mead"):
        optimize.nelder_mead(evaluator, dims)
        return "nelder_mead"
    if method == "cross_entropy":
        population = _CE_POPULATION_PER_DIM * dims
        elite = max(2, int(population * _CE_ELITE_SHARE))
        optimize.cross_entropy(evaluator, dims, SeedTree(seed).rng("cross-entropy"), population, elite,
                               generations=max(1, budget // population))
        return "cross_entropy"
    raise ValueError(f"method must be auto, bisection, golden, nelder_mead or cross_entropy, got {method!r}")


def _uncertainty(names: List[str], evaluator: optimize.Evaluator, goals: List[Target], best_runs: List[RunResult],
                 fit: float, tree: SeedTree) -> Dict[str, Dict[str, Any]]:
    """Range of each parameter over evaluated points whose fit is within the objective's noise of the best.

    The noise is the bootstrap standard error of the best point's objective (resampling its runs).
    """
    rng = tree.rng("calibration-noise")
    ok = [r for r in best_runs if r.status != "failed"]
    draws = []
    for _ in range(_NOISE_RESAMPLES if len(ok) > 1 else 0):
        sample = [ok[rng.randrange(len(ok))] for _ in ok]
        loss, _ = evaluate_targets(goals, sample)
        if math.isfinite(loss):
            draws.append(loss)
    noise = estimate(draws).sd if len(draws) > 1 else 0.0
    threshold = fit + _PLAUSIBLE_SE * (noise or 0.0)
    good = [d[0] for _, loss, d in evaluator.history if loss <= threshold]
    best_values = evaluator.best[2][0] if evaluator.best else {}
    out = {}
    for name in names:
        vals = [float(g[name]) for g in good]
        out[name] = {"best": best_values.get(name), "low": min(vals), "high": max(vals), "points": len(vals),
                     "objective_noise": noise}
    return out
