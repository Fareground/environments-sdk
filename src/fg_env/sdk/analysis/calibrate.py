"""Calibration: fit inputs so a contract's outputs and metrics match targets.

Targets (``{name: spec}``; ``name`` is an output or metric, or ``series.<metric>``):

* a number — the mean over runs should equal it: ``{"peak_infected": 20}``;
* a list — a metric's per-round path: the mean simulated path should follow it (RMSE);
* ``{"value": v, "stat": "volatility", "of": "last_price"}`` — a named statistic of a metric's
  series (a stylized fact; see :mod:`.facts`), averaged over runs;
* ``{"distribution": [values]}`` — the spread of the output across runs should match a sample
  (Wasserstein distance);
* any spec may add ``"weight"`` and ``"scale"`` (the error that counts as "one unit off").

Cases: ``targets`` may instead be a list of cases, each with its own fixed inputs and targets
(``[{"name": "town A", "inputs": {"population": 900}, "targets": {"peak_infected": 120}}, …]``); one set
of params is fitted to all of them. With cases, ``test`` (a share, or a list of case names) fits on the
other cases and reports the error on the held-out ones, and ``folds`` runs k-fold cross-validation:
each fold is fitted without its cases and scored on them, while the params returned are fitted to
every case.

The objective is the weighted root-mean-square of normalized errors, so a fit of 0.1 means
"about 10% of each target's scale off". The search runs every candidate on the same seeds,
which makes the objective a deterministic function of the inputs; the best fit is then re-run
on held-out seeds that played no part in the search, and that validation is what the report
leads with.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..api import ContractLike
from ..measure import RunResult
from ..seeds import SeedTree
from . import optimize, runner
from .facts import statistic
from .holdout import Split, case_names, splits
from .stats import estimate, is_number, mean, sd, wasserstein

__all__ = ["calibrate", "CalibrationResult", "Target", "parse_targets", "evaluate_targets"]

#: The error scale when a target is 0 and no ``scale`` is given (avoids dividing by zero).
_ZERO_SCALE = 1.0
#: Bootstrap resamples used to measure how noisy the objective is at the best fit.
_NOISE_RESAMPLES = 200
#: Evaluated points within this many objective standard errors of the best are "equally good".
_PLAUSIBLE_SE = 2.0
#: A target making up at least this share of the misfit at the best fit, with at most half the weight, is called out.
_DOMINANT_SHARE = 0.75


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
    #: Names of the cases fitted (empty when targets were one mapping).
    cases: List[str] = field(default_factory=list)
    #: Error on held-out cases (``test``) or across cross-validation folds (``folds``); ``None`` without them.
    holdout: Optional[Dict[str, Any]] = None
    #: Every evaluated point that fits as well as the best within the objective's noise (inputs together), which
    #: ``uncertainty=`` draws from so forecasts carry the parameters' uncertainty.
    plausible: List[Dict[str, Any]] = field(default_factory=list)

    def report(self) -> str:
        v = self.validation
        lines = [f"Calibration of {self.contract} ({self.method}, {self.evaluations} evaluation(s))"]
        if self.cases:
            lines.append(f"Fitted to {len(self.cases)} case(s): {', '.join(self.cases)}")
        lines += ["Best inputs: " + runner.describe_inputs(self.params),
                  f"Fit on search seeds: {self.fit:.4g}   on {v['runs']} held-out seed(s): {v['fit']:.4g}"]
        for detail in v["targets"]:
            lines.append("  " + _target_text(detail))
        lines += _holdout_text(self.holdout)
        lines.append("Parameter uncertainty (evaluated points as good as the best, within noise):")
        for name, u in self.uncertainty.items():
            lines.append(f"  {name}: {runner.describe_inputs({'best': u['best']})}, plausible "
                         f"{u['low']:.4g} to {u['high']:.4g} ({u['points']} point(s))")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"contract": self.contract, "params": self.params, "method": self.method, "fit": self.fit,
                "targets": self.targets, "validation": self.validation, "uncertainty": self.uncertainty,
                "evaluations": self.evaluations, "history": self.history, "notes": self.notes, "cases": self.cases,
                "holdout": self.holdout, "plausible": self.plausible}


def _target_text(detail: Dict[str, Any]) -> str:
    where = f"{detail['case']} · " if "case" in detail else ""
    if detail.get("simulated") is None and "rmse" not in detail and "distance" not in detail:
        return f"{where}{detail['target']}: no simulated value"
    if "rmse" in detail:
        return f"{where}{detail['target']}: path RMSE {detail['rmse']:.4g} over {detail['rounds_compared']} round(s)"
    if "distance" in detail:
        return f"{where}{detail['target']}: distribution distance {detail['distance']:.4g}"
    label = f"{detail['stat']} of {detail['target']}" if detail.get("stat") else detail["target"]
    return f"{where}{label}: simulated {detail['simulated']:.4g} vs target {detail['goal']:.4g} (error {detail['error']:+.1%})"


def _holdout_text(holdout: Optional[Dict[str, Any]]) -> List[str]:
    if not holdout:
        return []
    rows = holdout["splits"]
    if holdout["method"] == "test":
        row = rows[0]
        lines = [f"Out of sample: fit {row['out_of_sample']:.4g} on held-out case(s) {', '.join(row['test'])} "
                 f"(fitted to {', '.join(row['train'])}; {row['in_sample']:.4g} on those)"]
        return lines + ["  " + _target_text(detail) for detail in row["targets"]]
    spread = holdout["out_of_sample_sd"]
    lines = [f"Out of sample ({len(rows)}-fold cross-validation): fit {holdout['out_of_sample']:.4g}"
             + (f" ± {spread:.2g} across folds" if spread is not None else "")
             + f" (in sample {holdout['in_sample']:.4g})"]
    return lines + [f"  {row['label']}, held out {', '.join(row['test'])}: {row['out_of_sample']:.4g}" for row in rows]


def calibrate(contract: ContractLike, targets: Any, params: Mapping[str, Mapping[str, Any]], *,
              runs: int = 5, budget: int = 30, holdout: Optional[int] = None, method: str = "auto",
              inputs: Optional[Mapping[str, Any]] = None, arm: Optional[str] = None, participants: Any = None,
              rounds: Optional[int] = None, seed: int = 0, workers: int = 1, test: Any = None,
              folds: Optional[int] = None, data_dir: Any = None, hosts: Any = None) -> CalibrationResult:
    """Search ``params`` (``{input: {"low", "high", "log"?}}``) so the contract matches ``targets``.

    ``targets``: a mapping of targets, or a list of cases ``{name?, inputs?, arm?, targets}`` (see the
    module notes). ``method``: ``bisection`` (one parameter, one number target, monotone response),
    ``golden`` (one parameter), ``nelder_mead`` or ``cross_entropy`` (several), or ``auto``
    (bisection when it applies and the response brackets the target, else golden for one
    parameter, Nelder–Mead for several). ``budget`` caps distinct evaluated points, each costing
    ``runs`` runs per case; ``holdout`` (default ``runs``) fresh seeds validate the best point.
    With cases, ``test`` returns the fit to the other cases with its error on the held-out ones, and
    ``folds`` adds a cross-validated error to the fit on every case (one extra search per fold).
    ``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts`` answers
    host requests (feeds, judges) in every run.
    """
    runner.check_positive_int("runs", runs)
    runner.check_positive_int("budget", budget, 2)
    held = runner.check_positive_int("holdout", holdout if holdout is not None else runs)
    parsed = runner.as_contract(contract, data_dir)
    if not params:
        raise ValueError("calibrate needs at least one param to fit")
    names = list(params)
    ranges = {n: runner.bounds(parsed, n, params[n]) for n in names}
    logs = {n: bool((params[n] or {}).get("log", False)) for n in names}
    for n in names:
        if logs[n] and ranges[n][0] <= 0:
            raise ValueError(f"param '{n}': log scale needs a positive low")
    cases, tagged = _cases(parsed, targets, inputs, arm, names)
    if not tagged and (test is not None or folds is not None):
        raise ValueError("test and folds hold out cases: pass targets as a list of cases {name, inputs, targets}")
    parts = splits([case.name for case in cases], test=test, folds=folds, seed=seed)
    problem = _Problem(parsed, cases, tagged, names, ranges, logs, participants, rounds, workers, hosts)
    with runner.worker_pool(workers, participants, hosts) as pool:
        if test is not None:
            fitted = _fit(problem.subset(parts[0].train), pool, runs, held, budget, method, seed)
            return replace(fitted, holdout=_held_out(problem, parts, [fitted], pool, runs, held, "test", seed))
        result = _fit(problem, pool, runs, held, budget, method, seed)
        if folds is not None:
            fits = [_fit(problem.subset(split.train), pool, runs, held, budget, method, seed) for split in parts]
            result = replace(result, holdout=_held_out(problem, parts, fits, pool, runs, held, "folds", seed))
        return result


@dataclass(frozen=True)
class _Case:
    name: str
    inputs: Dict[str, Any]
    arm: Optional[str]
    goals: List[Target]


def _cases(contract: Any, targets: Any, inputs: Optional[Mapping[str, Any]], arm: Optional[str],
           fitted: Sequence[str]) -> Tuple[List[_Case], bool]:
    """The cases to fit, and whether they were given as cases (``False`` for one mapping of targets)."""
    if isinstance(targets, Mapping):
        return [_Case("all", dict(inputs or {}), arm, parse_targets(contract, targets))], False
    if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence) or not targets:
        raise ValueError("targets must be a mapping of name → target, or a non-empty list of cases {name?, inputs?, targets}")
    for i, case in enumerate(targets):
        if not isinstance(case, Mapping) or not isinstance(case.get("targets"), Mapping):
            raise ValueError(f"case {i + 1} needs 'targets' (a mapping of name → target) and usually 'inputs'")
        if not isinstance(case.get("inputs") or {}, Mapping):
            raise ValueError(f"case {i + 1}: 'inputs' must be a mapping of input → value")
    out = []
    for name, case in zip(case_names(targets), targets):
        own = dict(case.get("inputs") or {})
        clash = sorted(set(own) & set(fitted))
        if clash:
            raise ValueError(f"case '{name}' fixes {', '.join(clash)}, which calibrate is fitting")
        try:
            goals = parse_targets(contract, case["targets"])
        except ValueError as exc:
            raise ValueError(f"case '{name}': {exc}") from None
        out.append(_Case(name, {**dict(inputs or {}), **own}, case.get("arm", arm), goals))
    return _spread_scaled(out), True


def _spread_scaled(cases: List[_Case]) -> List[_Case]:
    """Cases whose value targets without a ``scale`` are scaled by how much that target varies across the cases,
    when the cases mix several value targets.

    Mixed targets are weighed against each other: an error then counts by how far off it is compared with the target's
    own spread, so a rate near 0.05 and a share near 0.85 weigh alike instead of the smaller one counting hundreds of
    times over. A single target keeps ``|goal|`` (its fit reads as a share off, and no weighting is at stake), as does a
    target given once or one that never varies."""
    goals: Dict[str, List[float]] = {}
    for case in cases:
        for goal in case.goals:
            if goal.kind == "value" and goal.scale is None:
                goals.setdefault(goal.name, []).append(goal.goal)
    spreads = {name: sd(values) for name, values in goals.items() if len(values) > 1 and sd(values) > 0}
    if len(goals) < 2 or not spreads:
        return cases
    return [replace(case, goals=[replace(goal, scale=spreads[goal.name])
                                 if goal.kind == "value" and goal.scale is None and goal.name in spreads else goal
                                 for goal in case.goals]) for case in cases]


@dataclass(frozen=True)
class _Problem:
    """Everything fixed during a calibration: the contract, the cases and the parameter space."""

    contract: Any
    cases: List[_Case]
    tagged: bool
    names: List[str]
    ranges: Dict[str, Tuple[float, float]]
    logs: Dict[str, bool]
    participants: Any
    rounds: Optional[int]
    workers: int
    hosts: Any = None

    @property
    def goals(self) -> List[Target]:
        return [goal for case in self.cases for goal in case.goals]

    def subset(self, indices: Sequence[int]) -> "_Problem":
        return replace(self, cases=[self.cases[i] for i in indices])

    def to_inputs(self, unit: Sequence[float]) -> Dict[str, Any]:
        """A point of the unit cube as input values (log-scaled where asked, rounded for int inputs)."""
        out = {}
        for n, u in zip(self.names, unit):
            low, high = self.ranges[n]
            raw = math.exp(math.log(low) + u * (math.log(high) - math.log(low))) if self.logs[n] else low + u * (high - low)
            out[n] = runner.coerce_input(self.contract, n, raw)
        return out

    def run(self, values: Mapping[str, Any], seeds: Sequence[int], pool: Any) -> List[List[RunResult]]:
        """Every case × every seed at these param values, grouped by case."""
        jobs = runner.jobs_for([({**case.inputs, **values}, case.arm) for case in self.cases], seeds)
        results = runner.run_jobs(self.contract, jobs, participants=self.participants, rounds=self.rounds,
                                  workers=self.workers, pool=pool, hosts=self.hosts)
        return runner.by_cell(jobs, results, len(self.cases))

    def evaluate(self, runs: Sequence[Sequence[RunResult]]) -> Tuple[float, List[Dict[str, Any]]]:
        """Weighted RMS of normalized errors over every case's targets (``inf`` when one has no value)."""
        details: List[Dict[str, Any]] = []
        weighted, total, complete = 0.0, 0.0, True
        for case, case_runs in zip(self.cases, runs):
            _, case_details = evaluate_targets(case.goals, case_runs)
            for goal, detail in zip(case.goals, case_details):
                details.append({**detail, "case": case.name} if self.tagged else detail)
                total += goal.weight
                if detail["error"] is None:
                    complete = False
                else:
                    weighted += goal.weight * detail["error"] ** 2
        return (math.sqrt(weighted / total) if complete else math.inf), details


def _fit(problem: _Problem, pool: Any, runs: int, held: int, budget: int, method: str, seed: int) -> CalibrationResult:
    search_seeds = runner.run_seeds(seed, runs)

    def objective(unit: Tuple[float, ...]) -> Tuple[float, Any]:
        values = problem.to_inputs(unit)
        results = problem.run(values, search_seeds, pool)
        loss, details = problem.evaluate(results)
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
    validation_fit, validation_details = problem.evaluate(problem.run(values, runner.run_seeds(seed, held, start=runs), pool))
    uncertainty = _uncertainty(problem, evaluator, best_runs, fit, SeedTree(seed))
    noise = next(iter(uncertainty.values()))["objective_noise"] if uncertainty else 0.0
    plausible = [dict(d[0]) for _, loss, d in evaluator.history if loss <= fit + _PLAUSIBLE_SE * noise]
    details = problem.evaluate(best_runs)[1]
    notes += _fit_notes(problem, details, fit, validation_fit, noise, held)
    history = [{"inputs": d[0], "fit": loss} for _, loss, d in evaluator.history]
    return CalibrationResult(problem.contract.name, values, chosen, fit, details,
                             {"runs": held, "fit": validation_fit, "targets": validation_details},
                             uncertainty, len(evaluator.history), history, notes,
                             [case.name for case in problem.cases] if problem.tagged else [], plausible=plausible)


def _fit_notes(problem: _Problem, details: Sequence[Dict[str, Any]], fit: float, fresh: float, noise: float,
               held: int) -> List[str]:
    """Plain warnings about a fit: one target making up most of the misfit, and a best point that was seed luck."""
    notes: List[str] = []
    misfit: Dict[str, float] = {}
    weight: Dict[str, float] = {}
    for goal, detail in zip(problem.goals, details):
        if detail.get("error") is not None:
            misfit[goal.name] = misfit.get(goal.name, 0.0) + goal.weight * detail["error"] ** 2
        weight[goal.name] = weight.get(goal.name, 0.0) + goal.weight
    total, total_weight = math.fsum(misfit.values()), math.fsum(weight.values())
    if len(weight) > 1 and total > 0:
        name = max(misfit, key=lambda key: misfit[key])
        share, weight_share = misfit[name] / total, weight[name] / total_weight
        if share >= _DOMINANT_SHARE and weight_share <= 0.5:
            notes.append(f"{name} carries {share:.0%} of the misfit left at the best fit (with {weight_share:.0%} of "
                         "the weight): the other targets' errors count for more per unit, so the search traded it away; "
                         "give it more `weight` or a smaller `scale`, or leave `scale` out so mixed targets are scaled "
                         "by their spread")
    if noise > 0 and math.isfinite(fresh) and fresh > fit + _PLAUSIBLE_SE * noise:
        notes.append(f"on {held} fresh seed(s) the fit is {fresh:.4g} against {fit:.4g} on the search seeds (more than "
                     f"{_PLAUSIBLE_SE:g}× the objective's noise of {noise:.2g}): the best point is partly the luck of "
                     "those seeds; use more `runs` and trust the fresh-seed fit")
    return notes


def _held_out(problem: _Problem, parts: Sequence[Split], fits: Sequence[CalibrationResult], pool: Any, runs: int,
              held: int, method: str, seed: int) -> Dict[str, Any]:
    """Each split's fit scored on its held-out cases, with fresh seeds."""
    seeds = runner.run_seeds(seed, held, start=runs)
    rows = []
    for split, fitted in zip(parts, fits):
        scored = problem.subset(split.test)
        loss, details = scored.evaluate(scored.run(fitted.params, seeds, pool))
        rows.append({"label": split.label, "train": [problem.cases[i].name for i in split.train],
                     "test": [problem.cases[i].name for i in split.test], "params": fitted.params,
                     "in_sample": fitted.validation["fit"], "out_of_sample": loss, "targets": details})
    finite = [row["out_of_sample"] for row in rows if math.isfinite(row["out_of_sample"])]
    return {"method": method, "splits": rows, "out_of_sample": mean(finite) if len(finite) == len(rows) else math.inf,
            "out_of_sample_sd": sd(finite) if len(finite) > 1 else None,
            "in_sample": mean([row["in_sample"] for row in rows])}


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
        population, elite, generations = optimize.cross_entropy_sizes(dims, budget)
        optimize.cross_entropy(evaluator, dims, SeedTree(seed).rng("cross-entropy"), population, elite, generations)
        return "cross_entropy"
    raise ValueError(f"method must be auto, bisection, golden, nelder_mead or cross_entropy, got {method!r}")


def _uncertainty(problem: _Problem, evaluator: optimize.Evaluator, best_runs: Sequence[Sequence[RunResult]], fit: float,
                 tree: SeedTree) -> Dict[str, Dict[str, Any]]:
    """Range of each parameter over evaluated points whose fit is within the objective's noise of the best.

    The noise is the bootstrap standard error of the best point's objective (resampling each case's runs).
    """
    rng = tree.rng("calibration-noise")
    ok = [[r for r in runs if r.status != "failed"] for runs in best_runs]
    draws = []
    for _ in range(_NOISE_RESAMPLES if all(len(runs) > 1 for runs in ok) else 0):
        sample = [[runs[rng.randrange(len(runs))] for _ in runs] for runs in ok]
        loss, _ = problem.evaluate(sample)
        if math.isfinite(loss):
            draws.append(loss)
    noise = estimate(draws).sd if len(draws) > 1 else 0.0
    threshold = fit + _PLAUSIBLE_SE * (noise or 0.0)
    good = [d[0] for _, loss, d in evaluator.history if loss <= threshold]
    best_values = evaluator.best[2][0] if evaluator.best else {}
    out = {}
    for name in problem.names:
        vals = [float(g[name]) for g in good]
        out[name] = {"best": best_values.get(name), "low": min(vals), "high": max(vals), "points": len(vals),
                     "objective_noise": noise}
    return out
