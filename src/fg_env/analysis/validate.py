"""Validation: the contract's forecasts checked against what actually happened, case by case and key by key.

``validate`` runs the contract once per historical case (a quarter, a lot, a day) and compares every measure's
ensemble with the case's actual values — one number, a map keyed per product or category (against a map output),
or a list (against a list output, position by position). It reports what a business needs to trust a forecast:
bias, MAPE, WAPE, RMSE and CRPS overall, per key and on held-out cases; whether 80% and 95% intervals really hold
80% and 95% of the actual values; and whether the simulator beats the simple forecasts anyone could make (the last
value, the mean of earlier values, the value one season back). Warnings say plainly what is wrong.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..api import ContractLike
from ..runtime.measure import RunResult
from . import runner
from .accuracy import (BASELINES, Pair, accuracy, baseline_points, bias_verdict, compare_baseline, coverage_verdict,
                       per_key, point_accuracy)
from .draws import parameter_draws, with_draws
from .holdout import case_names, splits
from .stats import is_number, quantile

__all__ = ["validate", "ValidationResult"]

#: Keys shown one by one in a report (worst first); the rest stay in the structured result.
_KEYS_SHOWN = 5


@dataclass
class ValidationResult:
    contract: str
    runs: int
    levels: List[float]
    cases: List[str]
    #: Per measure: ``{overall, held_out?, keys: {key: accuracy}, baselines: [...], warnings: [...]}``.
    measures: Dict[str, Dict[str, Any]]
    #: One row per case, measure and key: ``{case, measure, key, actual, forecast, low, high, level, error}``.
    rows: List[Dict[str, Any]]
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [f"Validation of {self.contract}: {len(self.cases)} case(s) × {self.runs} run(s)"]
        for name, measure in self.measures.items():
            lines.append(f"{name}: {_accuracy_text(measure['overall'])}")
            if measure.get("held_out"):
                lines.append(f"  held-out cases: {_accuracy_text(measure['held_out'])}")
            for level, coverage in measure["overall"]["coverage"].items():
                lines.append(f"  {float(level):.0%} intervals held {coverage['coverage']:.0%} of actual values "
                             f"(95% CI {coverage['coverage_ci95'][0]:.0%}–{coverage['coverage_ci95'][1]:.0%})")
            for row in measure["baselines"]:
                lines.append(f"  vs {row['label']} on {row['n']} value(s): WAPE {_pct(row['model']['wape'])} vs "
                             f"{_pct(row['reference']['wape'])} — {_baseline_verdict(row)}")
            ranked = sorted(measure["keys"].items(), key=lambda kv: -(kv[1]["wape"] or 0.0))
            if len(ranked) > 1:
                shown = ", ".join(f"{key} {_pct(acc['wape'])}" for key, acc in ranked[:_KEYS_SHOWN])
                lines.append(f"  WAPE by key, worst first: {shown}" + (" …" if len(ranked) > _KEYS_SHOWN else ""))
        lines += [f"WARNING: {text}" for text in self.warnings]
        lines += [f"note: {text}" for text in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"contract": self.contract, "runs": self.runs, "levels": self.levels, "cases": self.cases,
                "measures": self.measures, "rows": self.rows, "warnings": self.warnings, "notes": self.notes}


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _accuracy_text(acc: Mapping[str, Any]) -> str:
    bias = "n/a" if acc["bias"] is None else f"{acc['bias']:+.1%}"
    return (f"WAPE {_pct(acc['wape'])}, MAPE {_pct(acc['mape'])}, bias {bias}, RMSE {acc['rmse']:.4g}, "
            f"CRPS {acc['crps']:.4g} over {acc['n']} value(s)")


def _worse(row: Mapping[str, Any]) -> bool:
    """The simulator's error is above the baseline's on the same values, by enough to show in the report (an exact
    baseline beats any visible error; a tie at the shown precision is not called worse)."""
    model, reference = row["model"]["wape"], row["reference"]["wape"]
    return model is not None and reference is not None and round(model, 3) > round(reference, 3)


def _baseline_verdict(row: Mapping[str, Any]) -> str:
    if _worse(row):
        return f"worse than {row['label']}" + (f" by {abs(row['skill']):.0%}" if row["skill"] is not None else "")
    if row["skill"] is None or row["skill"] == 0:
        return f"no better than {row['label']}"
    return f"better than {row['label']} by {row['skill']:.0%}"


def validate(contract: ContractLike, cases: Sequence[Mapping[str, Any]], *, runs: int = 10,
             levels: Sequence[float] = (0.8, 0.95), season: Optional[int] = None,
             baselines: Sequence[str] = ("last", "mean", "seasonal"), test: Any = None, arm: Optional[str] = None,
             participants: Any = None, rounds: Optional[int] = None, seed: int = 0, workers: int = 1,
             data_dir: Any = None, hosts: Any = None, uncertainty: Any = None) -> ValidationResult:
    """Check the contract's forecasts against each case's ``actuals`` (see the module notes).

    ``cases``: ``[{"name"?, "inputs"?, "arm"?, "actuals": {measure: number | {key: number} | [numbers]}}]`` in time
    order; a measure is an output or a metric (its final value). ``levels`` are the nominal interval coverages checked.
    Baselines forecast each key from earlier cases: ``last``, ``mean`` and ``seasonal`` (the value ``season`` cases
    back; needs ``season``). ``test`` (a share or case names) also scores the held-out cases on their own.
    ``uncertainty`` (a calibration, points or priors: :mod:`.draws`) draws parameters per run, so intervals include
    not knowing them.
    """
    runner.check_positive_int("runs", runs)
    if not cases:
        raise ValueError("validate needs at least one case with actuals")
    for level in levels:
        if not is_number(level) or not 0 < level < 1:
            raise ValueError(f"levels are nominal coverages between 0 and 1, got {level!r}")
    unknown = sorted(set(baselines) - set(BASELINES))
    if unknown:
        raise ValueError(f"unknown baseline(s) {unknown} (use {', '.join(BASELINES)})")
    if season is not None:
        runner.check_positive_int("season", season)
    parsed = runner.as_contract(contract, data_dir)
    names = case_names(cases)
    measures = _measures(parsed, cases, names)
    held = set(splits(names, test=test, seed=seed)[0].test) if test is not None else set()
    seeds = runner.run_seeds(seed, runs)
    jobs = runner.jobs_for([(dict(case.get("inputs") or {}), case.get("arm", arm)) for case in cases], seeds)
    if uncertainty is not None:
        jobs = with_draws(jobs, parameter_draws(parsed, uncertainty, runs, seed))
    grouped = runner.by_cell(jobs, runner.run_jobs(parsed, jobs, participants=participants, rounds=rounds,
                                                   workers=workers, hosts=hosts), len(cases))
    notes: List[str] = []
    result_measures: Dict[str, Dict[str, Any]] = {}
    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for name, measure in measures.items():
        pairs = _pairs(name, measure, cases, names, grouped, notes)
        if not pairs:
            notes.append(f"{name}: no case produced a value, so it is not scored")
            continue
        summary = _summarise(name, pairs, held, levels, baselines, season, notes)
        result_measures[name] = summary
        warnings += [f"{name}: {text}" for text in summary["warnings"]]
        rows += _rows(name, pairs, names, levels)
    if "seasonal" in baselines and season is None:
        notes.append("no season given, so the seasonal naive baseline is left out")
    failed = sum(1 for case_runs in grouped for r in case_runs if r.status == "failed")
    if failed:
        notes.append(f"{failed} run(s) failed and were left out")
    return ValidationResult(parsed.name, runs, [float(level) for level in levels], names, result_measures, rows,
                            warnings, notes)


def _measures(contract: Any, cases: Sequence[Mapping[str, Any]], names: List[str]) -> Dict[str, Tuple[str, str]]:
    found: Dict[str, Tuple[str, str]] = {}
    for name, case in zip(names, cases):
        actuals = case.get("actuals") if isinstance(case, Mapping) else None
        if not isinstance(actuals, Mapping) or not actuals:
            raise ValueError(f"case '{name}' needs 'actuals': {{measure: number, {{key: number}} or [numbers]}}")
        for measure in actuals:
            if measure not in found:
                found[measure] = runner.resolve_measure(contract, measure)
    return found


def _pairs(name: str, measure: Tuple[str, str], cases: Sequence[Mapping[str, Any]], names: List[str],
           grouped: Sequence[Sequence[RunResult]], notes: List[str]) -> List[Pair]:
    pairs: List[Pair] = []
    for index, (case, case_runs) in enumerate(zip(cases, grouped)):
        if name not in case["actuals"]:
            continue
        values = [runner.raw_value(r, measure) for r in case_runs if r.status != "failed"]
        for key, actual in _keyed(case["actuals"][name], f"case '{names[index]}' actual {name}"):
            members = tuple(float(v) for v in (_member(value, key) for value in values) if is_number(v))
            if not members:
                notes.append(f"{names[index]} · {name}{'[' + key + ']' if key else ''}: no run gave a number")
                continue
            pairs.append(Pair(index, key, float(actual), members))
    return pairs


def _keyed(actual: Any, where: str) -> List[Tuple[str, float]]:
    if isinstance(actual, Mapping):
        items = [(str(key), value) for key, value in actual.items()]
    elif isinstance(actual, (list, tuple)):
        items = [(str(position), value) for position, value in enumerate(actual)]
    else:
        items = [("", actual)]
    for key, value in items:
        if not is_number(value):
            raise ValueError(f"{where}{'[' + key + ']' if key else ''} must be a number, got {value!r}")
    return [(key, float(value)) for key, value in items]


def _member(value: Any, key: str) -> Any:
    if key == "":
        return value
    if isinstance(value, Mapping):
        return value.get(key)
    if isinstance(value, (list, tuple)) and key.isdigit() and int(key) < len(value):
        return value[int(key)]
    return None


def _summarise(name: str, pairs: List[Pair], held: set, levels: Sequence[float], baselines: Sequence[str],
               season: Optional[int], notes: List[str]) -> Dict[str, Any]:
    overall = accuracy(pairs, levels)
    summary: Dict[str, Any] = {"overall": overall, "keys": {}, "baselines": [], "warnings": []}
    if held:
        tested = [pair for pair in pairs if pair.case in held]
        summary["held_out"] = accuracy(tested, levels) if tested else None
    grouped = per_key(pairs)
    if len(grouped) > 1:
        summary["keys"] = {key: accuracy(group, levels) for key, group in grouped.items()}
    for coverage in overall["coverage"].values():
        verdict = coverage_verdict(coverage)
        if verdict:
            summary["warnings"].append(verdict)
    bias = bias_verdict(overall)
    if bias:
        summary["warnings"].append(bias)
    for kind in baselines:
        if kind == "seasonal" and season is None:
            continue
        model_points: List[Tuple[float, float]] = []
        reference_points: List[Tuple[float, float]] = []
        for group in grouped.values():
            by_case = {pair.case: pair for pair in group}
            for case, point in baseline_points({c: p.actual for c, p in by_case.items()}, kind, season).items():
                model_points.append((by_case[case].point, by_case[case].actual))
                reference_points.append((point, by_case[case].actual))
        if not model_points:
            notes.append(f"{name}: too few earlier cases for the {kind} baseline")
            continue
        row = compare_baseline(point_accuracy(model_points), point_accuracy(reference_points), kind, season)
        summary["baselines"].append(row)
        if _worse(row):
            summary["warnings"].append(f"the simulator's WAPE {_pct(row['model']['wape'])} is worse than {row['label']} "
                                       f"({_pct(row['reference']['wape'])}) on the same {row['n']} value(s)")
    return summary


def _rows(name: str, pairs: List[Pair], names: List[str], levels: Sequence[float]) -> List[Dict[str, Any]]:
    widest = max(levels)
    tail = (1.0 - widest) / 2.0
    return [{"case": names[pair.case], "measure": name, "key": pair.key, "actual": pair.actual, "forecast": pair.point,
             "low": quantile(list(pair.ensemble), tail), "high": quantile(list(pair.ensemble), 1.0 - tail),
             "level": widest, "error": pair.point - pair.actual} for pair in pairs]
