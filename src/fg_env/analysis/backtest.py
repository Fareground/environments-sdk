"""Backtests (forecasts from the contract scored against known outcomes) and precision targets.

``backtest`` runs the contract once per historical case, turns its runs into a forecast of the
case's outcome, and scores every forecast with :func:`.scoring.score`. ``precision`` adds runs
in batches until an estimate is as precise as asked, and shows how it converged.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..api import ContractLike
from . import runner
from .accuracy import coverage_verdict
from .draws import parameter_draws, with_draws
from .holdout import Split, case_names, splits
from .scoring import score, skill_score
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
    cases: list[dict[str, Any]]
    scores: dict[str, Any]
    notes: list[str] = field(default_factory=list)
    #: Scores on held-out cases against a climatology from the other cases (``test`` or ``folds``), else ``None``.
    holdout: dict[str, Any] | None = None

    def report(self) -> str:
        s = self.scores
        lines = [f"Backtest of {self.output} in {self.contract}: {len(self.cases)} case(s) × {self.runs} run(s), "
                 f"{self.kind}"]
        if self.kind == "binary":
            clim = s["climatology"]
            lines.append(f"Brier {s['brier']:.4f} (climatology "
                         f"{clim['brier']:.4f}{', in-sample' if clim['in_sample'] else ''}), skill {_pct(s['skill'])}, "
                         f"log loss {s['log_loss']:.4f}, ECE {s['ece']:.4f}")
            m = s["murphy"]
            lines.append(f"Murphy: reliability {m['reliability']:.4f}, resolution {m['resolution']:.4f}, uncertainty "
                         f"{m['uncertainty']:.4f}")
        elif self.kind == "categorical":
            lines.append(f"Brier {s['brier']:.4f}, log loss {s['log_loss']:.4f}, accuracy {s['accuracy']:.0%}, skill "
                         f"{_pct(s['skill'])}")
        else:
            cov = s["coverage"]
            lines.append(f"CRPS {s['crps']:.4g} (climatology {s['climatology']['crps']:.4g}), skill "
                         f"{_pct(s['skill'])}, median abs error {s['mae_of_median']:.4g}, {cov['nominal']:.0%} "
                         f"interval coverage {cov['coverage']:.0%}")
        for case in self.cases:
            lines.append(f"  {case['name']}: forecast {case['forecast_text']}, outcome {case['outcome']}")
        if self.holdout:
            h, metric = self.holdout, self.holdout["metric"]
            o = h["out_of_sample"]
            how = "held-out cases" if h["method"] == "test" else f"{len(h['splits'])}-fold cross-validation"
            lines.append(f"Out of sample ({how}, climatology from the other cases): {metric} {o[metric]:.4g} "
                         f"(reference {o['reference']:.4g}), skill {_pct(o['skill'])} over {o['n']} case(s)")
            for row in h["splits"]:
                lines.append(f"  {row['label']}, held out {', '.join(row['test'])}: {metric} "
                             f"{row['scores'][metric]:.4g}, skill {_pct(row['scores']['skill'])}")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"contract": self.contract, "output": self.output, "kind": self.kind, "runs": self.runs,
                "cases": self.cases, "scores": self.scores, "notes": self.notes, "holdout": self.holdout}


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.0%}"


def _case_kind(outcomes: Sequence[Any], threshold: float | None) -> str:
    if all(isinstance(o, bool) for o in outcomes):
        return "binary"
    if all(is_number(o) for o in outcomes):
        return "binary" if threshold is not None else "ensemble"
    if all(isinstance(o, str) for o in outcomes):
        return "categorical"
    raise ValueError("case outcomes must all be yes/no, all numbers, or all text")


def backtest(contract: ContractLike, cases: Sequence[Mapping[str, Any]], output: str, *, runs: int = 10,
             threshold: float | None = None, climatology: Any = None, arm: str | None = None,
             participants: Any = None, rounds: int | None = None, seed: int = 0, workers: int = 1,
             bins: int = 10, test: Any = None, folds: int | None = None, data_dir: Any = None,
             hosts: Any = None, uncertainty: Any = None) -> BacktestResult:
    """Score the contract's forecasts of ``output`` against each case's known ``outcome``.

    ``cases``: ``[{"inputs": {...}, "outcome": value, "name"?: text, "arm"?: text}]``. Outcome
    types pick the forecast: yes/no → the share of runs where the output is true (or above
    ``threshold`` for a numeric output); numbers → the ensemble of run values (CRPS, coverage;
    with ``threshold`` the numbers become yes/no events); text → the frequency of each output
    value. Every case uses the same seeds.

    Skill compares the forecasts with a climatology, which by default comes from the same cases'
    outcomes (in sample). ``test`` (a share, or a list of case names) or ``folds`` (k-fold) also score
    the held-out cases against a climatology built only from the other cases: out-of-sample skill.
    ``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts``
    answers host requests (feeds, judges) in every run.
    """
    runner.check_positive_int("runs", runs)
    if threshold is not None and not is_number(threshold):
        raise ValueError("threshold must be a finite number")
    if not cases:
        raise ValueError("backtest needs at least one case")
    parsed = runner.as_contract(contract, data_dir)
    measure = runner.resolve_measure(parsed, output)
    for i, case in enumerate(cases):
        if not isinstance(case, Mapping) or "outcome" not in case:
            raise ValueError(f"case {i} needs an 'outcome' (and usually 'inputs')")
    parts = (splits(case_names(cases), test=test, folds=folds, seed=seed) if test is not None or folds is not None
             else [])
    outcomes = [case["outcome"] for case in cases]
    kind = _case_kind(outcomes, threshold)
    if kind == "categorical" and threshold is not None:
        raise ValueError("threshold applies to numeric or yes/no case outcomes, not categorical outcomes")
    seeds = runner.run_seeds(seed, runs)
    cells = [(dict(case.get("inputs") or {}), case.get("arm", arm)) for case in cases]
    jobs = runner.jobs_for(cells, seeds)
    if uncertainty is not None:
        jobs = with_draws(jobs, parameter_draws(parsed, uncertainty, runs, seed))
    grouped = runner.by_cell(jobs, runner.run_jobs(parsed, jobs, participants=participants, rounds=rounds,
                                                   workers=workers, hosts=hosts), len(cases))
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
    nominal = _ENSEMBLE_LEVEL if kind == "ensemble" else None
    scores = score(forecasts, events, kind=kind, climatology=climatology, bins=bins, epsilon=epsilon, nominal=nominal)
    if kind == "ensemble":
        verdict = coverage_verdict(scores["coverage"])
        if verdict:
            notes.append(f"WARNING: {verdict}")
    if climatology is None:
        notes.append("skill is measured against the cases' own outcome frequency (in-sample climatology)")
    held = None
    if parts:
        names = [row["name"] for row in rows]
        held = _held_out(kind, forecasts, events, parts, names, climatology, bins, epsilon, nominal,
                         "test" if test is not None else "folds")
    return BacktestResult(parsed.name, output, kind, runs, rows, scores, notes, held)


#: The score each kind of forecast is judged by (lower is better).
_METRIC = {"binary": "brier", "categorical": "brier", "ensemble": "crps"}


def _climatology(kind: str, events: Sequence[Any]) -> Any:
    """The reference forecast learned from ``events``: a base rate, a category distribution, or a sample."""
    if kind == "binary":
        return sum(1 for e in events if e) / len(events)
    if kind == "categorical":
        counts: dict[str, int] = {}
        for e in events:
            counts[str(e)] = counts.get(str(e), 0) + 1
        return {k: c / len(events) for k, c in counts.items()}
    return [float(e) for e in events]


def _held_out(kind: str, forecasts: Sequence[Any], events: Sequence[Any], parts: Sequence[Split], names: Sequence[str],
              climatology: Any, bins: int, epsilon: float, nominal: float | None, method: str) -> dict[str, Any]:
    """Every split's held-out cases scored against a climatology from its training cases, pooled over splits."""
    metric = _METRIC[kind]
    rows, value, reference, count = [], 0.0, 0.0, 0
    for split in parts:
        learned = climatology if climatology is not None else _climatology(kind, [events[i] for i in split.train])
        scores = score([forecasts[i] for i in split.test], [events[i] for i in split.test], kind=kind,
                       climatology=learned, bins=bins, epsilon=epsilon, nominal=nominal)
        size = len(split.test)
        value += scores[metric] * size
        reference += scores["climatology"][metric] * size
        count += size
        rows.append({"label": split.label, "train": [names[i] for i in split.train],
                     "test": [names[i] for i in split.test], "scores": scores})
    pooled, pooled_reference = value / count, reference / count
    return {"method": method, "metric": metric, "splits": rows,
            "out_of_sample": {"n": count, metric: pooled, "reference": pooled_reference,
                              "skill": skill_score(pooled, pooled_reference)}}


def _forecast(kind: str, raw: list[Any], threshold: float | None, name: str) -> tuple:
    if not raw:
        raise runner.AnalysisError(f"{name}: every run failed, so there is no forecast")
    expected = "a finite number" if kind == "ensemble" or threshold is not None else \
        "yes/no" if kind == "binary" else "text"
    valid = is_number if expected == "a finite number" else \
        (lambda value: isinstance(value, bool)) if kind == "binary" else (lambda value: isinstance(value, str))
    invalid = [value for value in raw if not valid(value)]
    if invalid:
        raise runner.AnalysisError(
            f"{name}: {len(invalid)} of {len(raw)} forecast output(s) are missing or invalid; "
            f"expected {expected}, got {invalid[0]!r}. Every successful run needs a valid forecast output.")
    if kind == "binary":
        if threshold is not None:
            flags = [v > threshold for v in raw]
        else:
            flags = list(raw)
        p = sum(flags) / len(flags)
        return p, f"{p:.0%}"
    if kind == "categorical":
        counts: dict[str, int] = {}
        for v in raw:
            counts[str(v)] = counts.get(str(v), 0) + 1
        dist = {k: c / len(raw) for k, c in sorted(counts.items(), key=lambda kv: -kv[1])}
        return dist, ", ".join(f"{k} {p:.0%}" for k, p in list(dist.items())[:3])
    values = [float(v) for v in raw]
    tail = (1 - _ENSEMBLE_LEVEL) / 2
    return (values,
            f"median {quantile(values, 0.5):.4g} ({_ENSEMBLE_LEVEL:.0%}: "
            f"{quantile(values, tail):.4g}–{quantile(values, 1 - tail):.4g})")


@dataclass
class PrecisionResult:
    contract: str
    output: str
    converged: bool
    runs: int
    estimate: Estimate
    target_se: float
    trace: list[dict[str, Any]]
    runs_needed: int | None

    def report(self) -> str:
        state = "reached" if self.converged else "not reached"
        lines = [f"Precision of {self.output} in {self.contract}: target standard error {self.target_se:.4g} {state} "
                 f"after {self.runs} run(s)", f"Estimate: {self.estimate.text()}"]
        if not self.converged and self.runs_needed:
            lines.append(f"About {self.runs_needed} run(s) would be needed at the current spread.")
        lines.append("Convergence: " + "; ".join(f"{t['runs']}: {t['mean']:.4g} ± {t['se']:.3g}" for t in self.trace
                                                 if t["mean"] is not None))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"contract": self.contract, "output": self.output, "converged": self.converged, "runs": self.runs,
                "estimate": self.estimate.to_dict(), "target_se": self.target_se, "trace": self.trace,
                "runs_needed": self.runs_needed}


def precision(contract: ContractLike, output: str, *, target_se: float | None = None,
              relative_se: float | None = None, max_runs: int = 100, batch: int = 5, min_runs: int | None = None,
              inputs: Mapping[str, Any] | None = None, arm: str | None = None, participants: Any = None,
              rounds: int | None = None, seed: int = 0, workers: int = 1, level: float = 0.95,
              data_dir: Any = None, hosts: Any = None) -> PrecisionResult:
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
    parsed = runner.as_contract(contract, data_dir)
    measure = runner.resolve_measure(parsed, output)
    z = normal_quantile(1.0 - (1.0 - level) / 2.0)
    values: list[Any] = []
    trace: list[dict[str, Any]] = []
    done = 0
    current = estimate([], level)
    required = float(goal)
    with runner.worker_pool(workers, participants, hosts) as pool:
        while done < max_runs:
            size = min(batch, max_runs - done)
            jobs = [runner.Job(dict(inputs or {}), arm, s) for s in runner.run_seeds(seed, size, start=done)]
            results = runner.run_jobs(parsed, jobs, participants=participants, rounds=rounds, workers=workers,
                                      pool=pool, hosts=hosts)
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


def _estimate(values: list[Any], level: float, z: float) -> Estimate:
    if values and all(isinstance(v, bool) for v in values):
        p = proportion(values, level)
        assert p.low is not None and p.high is not None
        return Estimate(p.n, p.mean, p.sd, (p.high - p.low) / (2 * z), p.low, p.high, level)
    numbers = [float(v) for v in values if is_number(v)]  # missing or text values cannot enter a mean
    return estimate(numbers, level) if numbers else Estimate(0, None, level=level)
