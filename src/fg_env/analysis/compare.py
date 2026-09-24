"""Comparing results, and chaining one contract's outputs into another's inputs.

``compare`` puts two results side by side: two single runs, or two sets of runs (paired per
seed when both sets ran on the same seeds, otherwise Welch's unequal-variance interval).

``chain`` runs a first contract, estimates each bound output (lower, point, upper), and runs
the second contract at each estimate, so the uncertainty of the first stage carries through
to the second instead of being dropped at the hand-over.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..api import ContractLike
from ..runtime.measure import RunResult, _usable_output
from . import runner
from .drivers import collect_runs
from .stats import Estimate, estimate, mean, numeric, quantile, sd, t_quantile

__all__ = ["compare", "Comparison", "welch", "chain", "ChainResult"]


def welch(a: Sequence[float], b: Sequence[float], level: float = 0.95) -> Dict[str, Any]:
    """Difference of means (b − a) with Welch's unequal-variance t interval."""
    if len(a) < 2 or len(b) < 2:
        raise ValueError("Welch's interval needs at least two values on each side")
    va, vb = sd(a) ** 2 / len(a), sd(b) ** 2 / len(b)
    diff = mean(b) - mean(a)
    se = math.sqrt(va + vb)
    if se == 0:
        return {"difference": diff, "se": 0.0, "df": None, "low": diff, "high": diff}
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    half = t_quantile(1.0 - (1.0 - level) / 2.0, df) * se
    return {"difference": diff, "se": se, "df": df, "low": diff - half, "high": diff + half}


@dataclass
class Comparison:
    labels: Tuple[str, str]
    paired: bool
    outputs: Dict[str, Dict[str, Any]]
    series: Dict[str, Dict[str, Any]]
    notes: List[str] = field(default_factory=list)
    level: float = 0.95

    def report(self) -> str:
        a, b = self.labels
        mode = "paired by seed" if self.paired else "independent runs"
        lines = [f"Comparison {b} − {a} ({mode})"]
        for name, row in self.outputs.items():
            if "counts" in row:
                lines.append(f"  {name}: {a} {row['counts'][a]} | {b} {row['counts'][b]}")
            elif row.get("low") is not None:
                verdict = "clear" if row["clear"] else "within noise"
                lines.append(f"  {name}: {row[a]:.4g} → {row[b]:.4g}, {row['difference']:+.4g} "
                             f"({self.level:.0%} CI {row['low']:+.3g} to {row['high']:+.3g}, {verdict})")
            else:
                rel = f" ({row['relative']:+.0%})" if row.get("relative") is not None else ""
                lines.append(f"  {name}: {row[a]:.4g} → {row[b]:.4g}, {row['difference']:+.4g}{rel}")
        for name, row in self.series.items():
            lines.append(f"  path of {name}: RMSE {row['rmse']:.4g} over {row['rounds']} round(s)")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"labels": list(self.labels), "paired": self.paired, "outputs": self.outputs, "series": self.series,
                "notes": self.notes, "level": self.level}


def compare(a: Any, b: Any, *, labels: Tuple[str, str] = ("a", "b"), level: float = 0.95) -> Comparison:
    """Compare results, matching shared unique seeds and excluding invalid outputs per pair.

    Unmatched seeds are omitted when shared seeds exist; disjoint samples use an
    independent comparison. Notes identify exclusions and numeric rows give sample sizes.
    """
    if labels[0] == labels[1]:
        raise ValueError("the two labels must differ")
    reserved = {"difference", "relative", "n_a", "n_b", "low", "high", "se", "df", "clear", "counts"}
    if any(label in reserved for label in labels):
        raise ValueError("comparison labels must not use reserved result fields such as difference, counts or n_a")
    if not 0 < level < 1:
        raise ValueError("level must be between 0 and 1")
    raw_a, raw_b = collect_runs(a), collect_runs(b)
    runs_a = [r for r in raw_a if r.status != "failed"]
    runs_b = [r for r in raw_b if r.status != "failed"]
    if not runs_a or not runs_b:
        raise ValueError("each side needs at least one completed run")
    la, lb = labels
    notes: List[str] = []
    for label, raw, runs in ((la, raw_a, runs_a), (lb, raw_b, runs_b)):
        if len(raw) != len(runs):
            notes.append(f"{label}: excluded {len(raw) - len(runs)} failed run(s)")
        if len({r.seed for r in runs}) != len(runs):
            raise ValueError(f"{label}: duplicate seeds cannot identify independent runs or unique pairs; use distinct seeds")
    by_b = {r.seed: r for r in runs_b}
    common = [r for r in runs_a if r.seed in by_b]
    paired = bool(common)
    if paired:
        omitted = len(runs_a) + len(runs_b) - 2 * len(common)
        if omitted:
            notes.append(f"paired by shared seeds: excluded {omitted} unmatched run(s)")
        runs_a, runs_b = common, [by_b[r.seed] for r in common]
    outputs: Dict[str, Dict[str, Any]] = {}
    names_b = {key for run in runs_b for key in run.outputs}
    names = dict.fromkeys(key for run in runs_a for key in run.outputs if key in names_b)
    for name in names:
        valid_a = [r for r in runs_a if _usable_output(r, name) and r.outputs.get(name) is not None]
        valid_b = [r for r in runs_b if _usable_output(r, name) and r.outputs.get(name) is not None]
        if paired:
            usable_b = {r.seed: r for r in valid_b}
            valid_a = [r for r in valid_a if r.seed in usable_b]
            valid_b = [usable_b[r.seed] for r in valid_a]
        if len(valid_a) != len(runs_a) or len(valid_b) != len(runs_b):
            notes.append(f"{name}: excluded missing or invalid observations; using "
                         f"{len(valid_a)}/{len(runs_a)} {la} and {len(valid_b)}/{len(runs_b)} {lb} run(s)"
                         + (" in matched pairs" if paired else ""))
        if not valid_a or not valid_b:
            continue
        xa = [numeric(r.outputs[name]) for r in valid_a]
        xb = [numeric(r.outputs[name]) for r in valid_b]
        if all(v is not None for v in xa + xb):
            single = len(valid_a) == len(valid_b) == 1
            outputs[name] = _numeric_row(name, xa, xb, la, lb, paired, single, level, notes)
        elif all(isinstance(r.outputs[name], str) for r in valid_a + valid_b):
            outputs[name] = {"counts": {la: _counts(valid_a, name), lb: _counts(valid_b, name)}}
    series: Dict[str, Dict[str, Any]] = {}
    for name in [k for k in runs_a[0].series if k in runs_b[0].series]:
        pa, pb = _mean_path(runs_a, name), _mean_path(runs_b, name)
        length = min(len(pa), len(pb))
        if length:
            series[name] = {"rmse": math.sqrt(mean([(x - y) ** 2 for x, y in zip(pa, pb)])), "rounds": length}
    return Comparison(labels, paired, outputs, series, notes, level)


def _numeric_row(name: str, xa: List[Any], xb: List[Any], la: str, lb: str, paired: bool, single: bool,
                 level: float, notes: List[str]) -> Dict[str, Any]:
    ma, mb = mean(xa), mean(xb)
    row: Dict[str, Any] = {la: ma, lb: mb, "n_a": len(xa), "n_b": len(xb), "difference": mb - ma, "relative": (mb - ma) / abs(ma) if ma else None}
    if single:
        return row
    if paired:
        diff = estimate([y - x for x, y in zip(xa, xb)], level)
        row.update(low=diff.low, high=diff.high, se=diff.se)
    elif len(xa) >= 2 and len(xb) >= 2:
        w = welch(xa, xb, level)
        row.update(low=w["low"], high=w["high"], se=w["se"], df=w["df"])
    else:
        notes.append(f"{name}: one side has a single run, so no interval")
        return row
    row["clear"] = row["low"] is not None and (row["low"] > 0 or row["high"] < 0)
    return row


def _counts(runs: Sequence[RunResult], name: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in runs:
        key = str(r.outputs.get(name))
        out[key] = out.get(key, 0) + 1
    return out


def _mean_path(runs: Sequence[RunResult], name: str) -> List[float]:
    paths = [runner.series(r, name) for r in runs]
    length = min(len(p) for p in paths) if paths else 0
    return [mean([p[t] for p in paths]) for t in range(length)]


@dataclass
class ChainResult:
    first: str
    second: str
    runs: int
    level: float
    bindings: Dict[str, Dict[str, Any]]
    scenarios: Dict[str, Dict[str, Any]]
    envelope: Dict[str, Dict[str, float]]
    notes: List[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [f"Chain {self.first} → {self.second}: {self.runs} run(s) per stage, {self.level:.0%} range"]
        for name, b in self.bindings.items():
            lines.append(f"  {name} ← {b['output']}: {b['low']:.4g} / {b['point']:.4g} / {b['high']:.4g}")
        for label, scenario in self.scenarios.items():
            parts = ", ".join(f"{k} {Estimate(**v).text(3)}" for k, v in list(scenario["summary"].items())[:6])
            lines.append(f"  {label}: {parts}")
        for name, env in self.envelope.items():
            lines.append(f"  {name} spans {env['low']:.4g} to {env['high']:.4g} across scenarios")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"first": self.first, "second": self.second, "runs": self.runs, "level": self.level,
                "bindings": self.bindings, "scenarios": self.scenarios, "envelope": self.envelope, "notes": self.notes}


def chain(first: ContractLike, second: ContractLike, bind: Mapping[str, str], *, runs: int = 10, level: float = 0.9,
          uncertainty: bool = True, first_inputs: Optional[Mapping[str, Any]] = None,
          second_inputs: Optional[Mapping[str, Any]] = None, participants: Any = None,
          second_participants: Any = None, rounds: Optional[int] = None, second_rounds: Optional[int] = None,
          seed: int = 0, workers: int = 1, data_dir: Any = None, second_data_dir: Any = None,
          hosts: Any = None) -> ChainResult:
    """Run ``first``, bind its outputs into ``second``'s inputs (``{second_input: first_output}``), run ``second``.

    Each bound output is summarised by its mean (point) and the central ``level`` range of its
    run values (lower, upper); yes/no outputs become the share of runs. ``second`` runs at the
    point estimates and, with ``uncertainty``, at all-lower and all-upper bindings too. Values
    outside a bound input's declared range are clamped, with a note. ``data_dir`` / ``second_data_dir`` are where
    each contract's inputs with a ``source`` are read (default: each contract file's folder); ``hosts`` answers host
    requests in the runs of both.
    """
    runner.check_positive_int("runs", runs)
    if not bind:
        raise ValueError("chain needs at least one binding {second_input: first_output}")
    if not 0 < level < 1:
        raise ValueError(f"level must be between 0 and 1, got {level}")
    one, two = runner.as_contract(first, data_dir), runner.as_contract(second, second_data_dir)
    seeds = runner.run_seeds(seed, runs)
    measures = {target: runner.resolve_measure(one, source) for target, source in bind.items()}
    for target in bind:
        spec = runner.input_spec(two, target)
        if spec.type not in ("number", "int", "bool"):
            raise ValueError(f"input '{target}' of the second contract is {spec.type}; only number, int and bool can be bound")
    first_runs = runner.run_jobs(one, [runner.Job(dict(first_inputs or {}), None, s) for s in seeds],
                                 participants=participants, rounds=rounds, workers=workers, hosts=hosts)
    tail = (1.0 - level) / 2.0
    bindings: Dict[str, Dict[str, Any]] = {}
    for target, measure in measures.items():
        values = [v for v in (runner.value(r, measure) for r in first_runs) if v is not None]
        if not values:
            raise runner.AnalysisError(f"the first contract never produced a number for '{bind[target]}'")
        bindings[target] = {"output": bind[target], "low": quantile(values, tail), "point": mean(values),
                            "high": quantile(values, 1.0 - tail), "n": len(values)}
    labels = ("low", "point", "high") if uncertainty else ("point",)
    notes: List[str] = []
    cells = [({**dict(second_inputs or {}), **{t: _bound(two, t, b[label], notes) for t, b in bindings.items()}}, None)
             for label in labels]
    jobs = runner.jobs_for(cells, seeds)
    grouped = runner.by_cell(jobs, runner.run_jobs(two, jobs, participants=second_participants, rounds=second_rounds,
                                                   workers=workers, hosts=hosts), len(cells))
    measured = runner.numeric_measures(two, [r for cell in grouped for r in cell])
    scenarios: Dict[str, Dict[str, Any]] = {}
    for label, (inputs, _), cell in zip(labels, cells, grouped):
        summary = {m: estimate([v for v in (runner.value(r, ("outputs", m)) for r in cell) if v is not None]).to_dict()
                   for m in measured}
        scenarios[label] = {"inputs": {t: inputs[t] for t in bind}, "summary": summary,
                            "failed": sum(1 for r in cell if r.status == "failed")}
    envelope = {}
    for m in measured:
        means = [s["summary"][m]["mean"] for s in scenarios.values() if s["summary"][m]["mean"] is not None]
        if means:
            envelope[m] = {"low": min(means), "high": max(means)}
    return ChainResult(one.name, two.name, runs, level, bindings, scenarios, envelope, sorted(set(notes)))


def _bound(contract: Any, name: str, raw: float, notes: List[str]) -> Any:
    spec = contract.inputs[name]
    if spec.type == "bool":
        return raw >= 0.5
    value = raw
    if spec.min is not None and value < spec.min:
        notes.append(f"{name}: {raw:.4g} is below its minimum {spec.min:g}; clamped")
        value = spec.min
    if spec.max is not None and value > spec.max:
        notes.append(f"{name}: {raw:.4g} is above its maximum {spec.max:g}; clamped")
        value = spec.max
    return int(round(value)) if spec.type == "int" else float(value)
