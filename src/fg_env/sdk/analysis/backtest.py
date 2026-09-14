"""Backtests (forecasts from the contract scored against known outcomes) and precision targets.

``backtest`` runs the contract once per historical case, turns its runs into a forecast of the
case's outcome, and scores every forecast with :func:`.scoring.score`. ``precision`` adds runs
in batches until an estimate is as precise as asked, and shows how it converged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..api import ContractLike
from . import runner
from .scoring import score
from .stats import Estimate, estimate, is_number, normal_quantile, proportion, quantile

__all__ = ["backtest", "BacktestResult", "precision", "PrecisionResult"]

#: Central interval of an ensemble forecast that coverage is checked against.
_ENSEMBLE_LEVEL = 0.8


@dataclass
class BacktestResult:
    contract: str
    output: str
    kind: str
    runs: int
    cases: List[Dict[str, Any]]
    scores: Dict[str, Any]
    notes: List[str] = field(default_factory=list)

    def report(self) -> str:
        s = self.scores
        lines = [f"Backtest of {self.output} in {self.contract}: {len(self.cases)} case(s) × {self.runs} run(s), {self.kind}"]
        if self.kind == "binary":
            clim = s["climatology"]
            lines.append(f"Brier {s['brier']:.4f} (climatology {clim['brier']:.4f}{', in-sample' if clim['in_sample'] else ''}), "
                         f"skill {_pct(s['skill'])}, log loss {s['log_loss']:.4f}, ECE {s['ece']:.4f}")
            m = s["murphy"]
            lines.append(f"Murphy: reliability {m['reliability']:.4f}, resolution {m['resolution']:.4f}, uncertainty {m['uncertainty']:.4f}")
        elif self.kind == "categorical":
            lines.append(f"Brier {s['brier']:.4f}, log loss {s['log_loss']:.4f}, accuracy {s['accuracy']:.0%}, skill {_pct(s['skill'])}")
        else:
            cov = s["coverage"]
            lines.append(f"CRPS {s['crps']:.4g} (climatology {s['climatology']['crps']:.4g}), skill {_pct(s['skill'])}, "
                         f"median abs error {s['mae_of_median']:.4g}, {cov['nominal']:.0%} interval coverage {cov['coverage']:.0%}")
        for case in self.cases:
            lines.append(f"  {case['name']}: forecast {case['forecast_text']}, outcome {case['outcome']}")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"contract": self.contract, "output": self.output, "kind": self.kind, "runs": self.runs,
                "cases": self.cases, "scores": self.scores, "notes": self.notes}


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:+.0%}"


def _case_kind(outcomes: Sequence[Any], threshold: Optional[float]) -> str:
    if all(isinstance(o, bool) for o in outcomes):
        return "binary"
    if all(is_number(o) for o in outcomes):
        return "binary" if threshold is not None else "ensemble"
    if all(isinstance(o, str) for o in outcomes):
        return "categorical"
    raise ValueError("case outcomes must all be yes/no, all numbers, or all text")


def backtest(contract: ContractLike, cases: Sequence[Mapping[str, Any]], output: str, *, runs: int = 10,
             threshold: Optional[float] = None, climatology: Any = None, arm: Optional[str] = None,
             participants: Any = None, rounds: Optional[int] = None, seed: int = 0, workers: int = 1,
             bins: int = 10) -> BacktestResult:
    """Score the contract's forecasts of ``output`` against each case's known ``outcome``.

    ``cases``: ``[{"inputs": {...}, "outcome": value, "name"?: text, "arm"?: text}]``. Outcome
    types pick the forecast: yes/no → the share of runs where the output is true (or above
    ``threshold`` for a numeric output); numbers → the ensemble of run values (CRPS, coverage;
    with ``threshold`` the numbers become yes/no events); text → the frequency of each output
    value. Every case uses the same seeds.
    """
    runner.check_positive_int("runs", runs)
    if not cases:
        raise ValueError("backtest needs at least one case")
    parsed = runner.as_contract(contract)
    measure = runner.resolve_measure(parsed, output)
    for i, case in enumerate(cases):
        if not isinstance(case, Mapping) or "outcome" not in case:
            raise ValueError(f"case {i} needs an 'outcome' (and usually 'inputs')")
    outcomes = [case["outcome"] for case in cases]
    kind = _case_kind(outcomes, threshold)
    seeds = runner.run_seeds(seed, runs)
    cells = [(dict(case.get("inputs") or {}), case.get("arm", arm)) for case in cases]
    jobs = runner.jobs_for(cells, seeds)
    grouped = runner.by_cell(jobs, runner.run_jobs(parsed, jobs, participants=participants, rounds=rounds,
                                                   workers=workers), len(cases))
    forecasts, rows, notes = [], [], []
    for index, (case, case_runs) in enumerate(zip(cases, grouped)):
        raw = [runner.raw_value(r, measure) for r in case_runs if r.status != "failed"]
        name = str(case.get("name", f"case {index + 1}"))
        forecast, text = _forecast(kind, raw, threshold, name)
        forecasts.append(forecast)
        failed = len(case_runs) - len(raw)
        if failed:
            notes.append(f"{name}: {failed} run(s) failed and were left out")
        rows.append({"name": name, "inputs": case.get("inputs") or {}, "outcome": case["outcome"],
                     "forecast": forecast, "forecast_text": text, "runs": len(raw)})
    events = outcomes if kind != "binary" or threshold is None or all(isinstance(o, bool) for o in outcomes) \
        else [o > threshold for o in outcomes]
    epsilon = 1.0 / (2.0 * runs)  # a frequency from `runs` runs cannot resolve probabilities finer than this
    scores = score(forecasts, events, kind=kind, climatology=climatology, bins=bins, epsilon=epsilon,
                   nominal=_ENSEMBLE_LEVEL if kind == "ensemble" else None)
    if climatology is None:
        notes.append("skill is measured against the cases' own outcome frequency (in-sample climatology)")
    return BacktestResult(parsed.name, output, kind, runs, rows, scores, notes)


def _forecast(kind: str, raw: List[Any], threshold: Optional[float], name: str) -> tuple:
    if not raw:
        raise runner.AnalysisError(f"{name}: every run failed, so there is no forecast")
    if kind == "binary":
        if threshold is not None:
            flags = [is_number(v) and v > threshold for v in raw]
        elif all(isinstance(v, bool) for v in raw):
            flags = list(raw)
        else:
            raise ValueError("the output is not yes/no: pass threshold= to forecast 'output > threshold'")
        p = sum(flags) / len(flags)
        return p, f"{p:.0%}"
    if kind == "categorical":
        counts: Dict[str, int] = {}
        for v in raw:
            counts[str(v)] = counts.get(str(v), 0) + 1
        dist = {k: c / len(raw) for k, c in sorted(counts.items(), key=lambda kv: -kv[1])}
        return dist, ", ".join(f"{k} {p:.0%}" for k, p in list(dist.items())[:3])
    values = [float(v) for v in raw if is_number(v)]
    if not values:
        raise runner.AnalysisError(f"{name}: the output is never a number, so there is no ensemble forecast")
    tail = (1 - _ENSEMBLE_LEVEL) / 2
    return values, f"median {quantile(values, 0.5):.4g} ({_ENSEMBLE_LEVEL:.0%}: {quantile(values, tail):.4g}–{quantile(values, 1 - tail):.4g})"


@dataclass
class PrecisionResult:
    contract: str
    output: str
    converged: bool
    runs: int
    estimate: Estimate
    target_se: float
    trace: List[Dict[str, Any]]
    runs_needed: Optional[int]

    def report(self) -> str:
        state = "reached" if self.converged else "not reached"
        lines = [f"Precision of {self.output} in {self.contract}: target standard error {self.target_se:.4g} {state} "
                 f"after {self.runs} run(s)", f"Estimate: {self.estimate.text()}"]
        if not self.converged and self.runs_needed:
            lines.append(f"About {self.runs_needed} run(s) would be needed at the current spread.")
        lines.append("Convergence: " + "; ".join(f"{t['runs']}: {t['mean']:.4g} ± {t['se']:.3g}" for t in self.trace
                                                 if t["mean"] is not None))
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"contract": self.contract, "output": self.output, "converged": self.converged, "runs": self.runs,
                "estimate": self.estimate.to_dict(), "target_se": self.target_se, "trace": self.trace,
                "runs_needed": self.runs_needed}


def precision(contract: ContractLike, output: str, *, target_se: Optional[float] = None,
              relative_se: Optional[float] = None, max_runs: int = 100, batch: int = 5, min_runs: Optional[int] = None,
              inputs: Optional[Mapping[str, Any]] = None, arm: Optional[str] = None, participants: Any = None,
              rounds: Optional[int] = None, seed: int = 0, workers: int = 1, level: float = 0.95) -> PrecisionResult:
    """Add runs ``batch`` at a time until the standard error of ``output``'s mean is small enough.

    Give ``target_se`` (in output units) or ``relative_se`` (a share of |mean|). A yes/no output
    is a proportion: its standard error is the Wilson interval's half-width over z, which never
    reaches 0 by luck at 0% or 100%. At least ``min_runs`` (default two batches) run before the
    target can be declared met, so a few identical early runs cannot end the search.
    """
    if (target_se is None) == (relative_se is None):
        raise ValueError("give exactly one of target_se or relative_se")
    goal = target_se if target_se is not None else relative_se
    if not is_number(goal) or goal <= 0:
        raise ValueError(f"the precision target must be a positive number, got {goal!r}")
    runner.check_positive_int("batch", batch)
    runner.check_positive_int("max_runs", max_runs)
    floor = runner.check_positive_int("min_runs", min_runs if min_runs is not None else min(max_runs, 2 * batch))
    parsed = runner.as_contract(contract)
    measure = runner.resolve_measure(parsed, output)
    z = normal_quantile(1.0 - (1.0 - level) / 2.0)
    values: List[Any] = []
    trace: List[Dict[str, Any]] = []
    done = 0
    current = estimate([], level)
    required = float(goal)
    with runner.worker_pool(workers, participants) as pool:
        while done < max_runs:
            size = min(batch, max_runs - done)
            jobs = [runner.Job(dict(inputs or {}), arm, s) for s in runner.run_seeds(seed, size, start=done)]
            results = runner.run_jobs(parsed, jobs, participants=participants, rounds=rounds, workers=workers, pool=pool)
            values += [runner.raw_value(r, measure) for r in results if r.status != "failed"]
            done += size
            current = _estimate(values, level, z)
            required = float(goal) if target_se is not None else float(goal) * abs(current.mean or 0.0)
            trace.append({"runs": done, "n": current.n, "mean": current.mean, "se": current.se,
                          "low": current.low, "high": current.high})
            if done >= floor and current.se is not None and required > 0 and current.se <= required:
                return PrecisionResult(parsed.name, output, True, done, current, required, trace, None)
    needed = None
    if current.se is not None and required > 0 and current.n:
        needed = int(math.ceil(current.n * (current.se / required) ** 2))
    return PrecisionResult(parsed.name, output, False, done, current, required, trace, needed)


def _estimate(values: List[Any], level: float, z: float) -> Estimate:
    if values and all(isinstance(v, bool) for v in values):
        p = proportion(values, level)
        assert p.low is not None and p.high is not None
        return Estimate(p.n, p.mean, p.sd, (p.high - p.low) / (2 * z), p.low, p.high, level)
    numbers = [float(v) for v in values if is_number(v)]  # missing or text values cannot enter a mean
    return estimate(numbers, level) if numbers else Estimate(0, None, level=level)
