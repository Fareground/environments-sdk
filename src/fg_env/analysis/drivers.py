"""Drivers: which inputs, metrics, outputs, actions and endings separate one outcome from another.

Each run is labelled by whether the focus outcome happened. Every feature is reduced to high/low
(indicators as they are, numbers split at their median), and its effect is the lift
``P(outcome | high) − P(outcome | low)`` — one comparable unit for every feature. Testing many
features finds a strong-looking one by chance, so significance uses a max-T permutation test:
the labels are shuffled many times and a feature counts only when its lift beats the *largest*
lift any feature reaches on shuffled labels (family-wise error control).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..measure import RunResult, _usable_output
from ..seeds import SeedTree
from .stats import is_number, numeric

__all__ = ["drivers", "DriversResult", "Driver", "collect_runs"]

FEATURE_GROUPS = ("inputs", "metrics", "outputs", "actions", "end")


@dataclass(frozen=True)
class Driver:
    feature: str
    kind: str  # indicator | numeric
    lift: float
    p_value: float
    high_rate: float
    low_rate: float
    n_high: int
    n_low: int
    split: Optional[float] = None

    @property
    def description(self) -> str:
        return f"{self.feature} ≥ {self.split:.4g}" if self.kind == "numeric" else self.feature

    def to_dict(self) -> Dict[str, Any]:
        return {"feature": self.feature, "kind": self.kind, "description": self.description, "lift": self.lift,
                "p_value": self.p_value, "high_rate": self.high_rate, "low_rate": self.low_rate,
                "n_high": self.n_high, "n_low": self.n_low, "split": self.split}


@dataclass
class DriversResult:
    output: str
    focus: str
    n: int
    base_rate: float
    drivers: List[Driver]
    tested: int
    permutations: int
    alpha: float
    notes: List[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [f"Drivers of {self.focus} ({self.output}): {self.n} run(s), base rate {self.base_rate:.0%}, "
                 f"{self.tested} feature(s) tested, {self.permutations} permutations"]
        if not self.drivers:
            lines.append(f"No feature separates the outcomes beyond chance (family-wise α = {self.alpha}).")
        for d in self.drivers:
            lines.append(f"  {d.description}: outcome in {d.high_rate:.0%} of {d.n_high} run(s) vs {d.low_rate:.0%} of "
                         f"{d.n_low} (lift {d.lift:+.0%}, p = {d.p_value:.3f})")
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"output": self.output, "focus": self.focus, "n": self.n, "base_rate": self.base_rate,
                "drivers": [d.to_dict() for d in self.drivers], "tested": self.tested,
                "permutations": self.permutations, "alpha": self.alpha, "notes": self.notes}


def collect_runs(source: Any) -> List[RunResult]:
    """Runs from a list of results, an experiment (every arm), a sweep, or any object with ``runs``."""
    if isinstance(source, RunResult):
        return [source]
    arms = getattr(source, "arms", None)
    if isinstance(arms, Mapping):
        return [r for arm in arms.values() for r in arm.runs]
    runs = getattr(source, "runs", None)
    if runs is not None and not callable(runs):
        return list(runs)
    if isinstance(source, Sequence):
        if not all(isinstance(r, RunResult) for r in source):
            raise TypeError("drivers needs RunResults, an experiment, or a sweep")
        return list(source)
    raise TypeError("drivers needs RunResults, an experiment, or a sweep")


def _labels(values: List[Any], focus: Any, threshold: Optional[float]) -> Tuple[List[int], str]:
    if threshold is not None:
        return [1 if is_number(v) and v > threshold else 0 for v in values], f"above {threshold:g}"
    if focus is not None:
        return [1 if v == focus or str(v) == str(focus) else 0 for v in values], f"= {focus}"
    if all(isinstance(v, bool) for v in values):
        return [1 if v else 0 for v in values], "true"
    if all(is_number(v) for v in values):
        mid = median(values)
        return [1 if v > mid else 0 for v in values], f"above the median ({mid:.4g})"
    counts: Dict[str, int] = {}
    for v in values:
        counts[str(v)] = counts.get(str(v), 0) + 1
    mode = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return [1 if str(v) == mode else 0 for v in values], f"= {mode} (most common)"


def _flat(prefix: str, values: Mapping[str, Any], row: Dict[str, float]) -> None:
    for key, v in values.items():
        if isinstance(v, bool) or is_number(v):
            row[f"{prefix}.{key}"] = float(v)
        elif isinstance(v, str) and len(v) <= 80:
            row[f"{prefix}.{key}={v}"] = 1.0


def _features(run: RunResult, output: str, groups: Sequence[str]) -> Dict[str, float]:
    row: Dict[str, float] = {}
    if "inputs" in groups:
        _flat("inputs", run.inputs, row)
        if run.arm is not None:
            row[f"arm={run.arm}"] = 1.0
    if "metrics" in groups:
        _flat("metrics", {k: v for k, v in run.metrics.items() if k != output}, row)
        for name, path in run.series.items():
            nums = [numeric(v) for v in path]
            if path and all(n is not None for n in nums) and name != output:
                row[f"metrics.{name}.peak"] = max(n for n in nums if n is not None)
    if "outputs" in groups:
        _flat("outputs", {k: v for k, v in run.outputs.items() if k != output and _usable_output(run, k)}, row)
    if "actions" in groups:
        for event in run.events:
            data = event.get("data") or {}
            if event.get("kind") == "action" and data.get("success", True):
                key = f"actions.{data.get('action')}"
                row[key] = row.get(key, 0.0) + 1.0
    if "end" in groups:
        if run.ended_by is not None:
            row[f"ended_by={run.ended_by}"] = 1.0
        if run.winner is not None:
            row[f"winner={json.dumps(run.winner, default=str)}"] = 1.0
    return row


def _binarize(column: List[Optional[float]]) -> Optional[Tuple[List[int], str, Optional[float]]]:
    present = [v for v in column if v is not None]
    if not present:
        return None
    if all(v in (0.0, 1.0) for v in present):
        flags, kind, split = [1 if v == 1.0 else 0 for v in column], "indicator", None
    else:
        split = median(present)
        flags, kind = [1 if v is not None and v >= split else 0 for v in column], "numeric"
    if len(set(flags)) < 2:
        return None
    return flags, kind, split


def _lift(high: Sequence[int], labels: Sequence[int], total: int) -> float:
    n1 = len(high)
    n0 = len(labels) - n1
    s1 = sum(labels[i] for i in high)
    return s1 / n1 - (total - s1) / n0


def drivers(runs: Any, output: str, *, focus: Any = None, threshold: Optional[float] = None,
            include: Sequence[str] = FEATURE_GROUPS, permutations: int = 500, alpha: float = 0.05, top: int = 10,
            seed: int = 0) -> DriversResult:
    """Features that separate runs where ``output`` hits the focus from runs where it does not.

    Focus: yes/no outputs → true; numbers → above the median (or ``threshold``); text → the most
    common value (or ``focus``). ``include`` picks feature groups: inputs (and arm), metrics
    (final value and peak), other outputs, actions (successful count, from event logs), end
    (how the run ended, winner). Deterministic for a given ``seed``.
    """
    unknown = set(include) - set(FEATURE_GROUPS)
    if unknown:
        raise ValueError(f"unknown feature group(s) {sorted(unknown)} (use {', '.join(FEATURE_GROUPS)})")
    if permutations < 1 or not 0 < alpha < 1:
        raise ValueError("permutations must be ≥ 1 and alpha between 0 and 1")
    completed = [r for r in collect_runs(runs) if r.status != "failed"]
    rows = [(r, r.outputs[output] if output in r.outputs else r.metrics.get(output)) for r in completed
            if output not in r.outputs or _usable_output(r, output)]
    rows = [(r, v) for r, v in rows if v is not None]
    if len(rows) < 4:
        raise ValueError(f"drivers needs at least 4 completed runs with a value for '{output}', got {len(rows)}")
    labels, focus_text = _labels([v for _, v in rows], focus, threshold)
    total = sum(labels)
    notes = []
    if total in (0, len(labels)):
        return DriversResult(output, focus_text, len(labels), total / len(labels), [], 0, 0, alpha,
                             ["the outcome is the same in every run, so nothing can separate it"])
    matrix = [_features(r, output, include) for r, _ in rows]
    names = sorted({k for row in matrix for k in row})
    target = [numeric(v) for _, v in rows]
    restated = [n for n in names if not n.startswith(("inputs.", "arm="))
                and all(row.get(n) is not None and row.get(n) == t for row, t in zip(matrix, target))]
    if restated:
        notes.append(f"left out {', '.join(restated)}: equal to the outcome itself in every run")
    columns = {}
    for name in names:
        if name in restated:
            continue
        binned = _binarize([row.get(name) for row in matrix])
        if binned is not None:
            flags, kind, split = binned
            columns[name] = ([i for i, f in enumerate(flags) if f], kind, split)
    if "actions" in include and not any(r.events for r, _ in rows):
        notes.append("no event logs in these runs, so actions were not tested")
    observed = {name: _lift(high, labels, total) for name, (high, _, _) in columns.items()}
    rng = SeedTree(seed).rng("drivers")
    shuffled = list(labels)
    null_max = []
    for _ in range(permutations):
        rng.shuffle(shuffled)
        null_max.append(max((abs(_lift(high, shuffled, total)) for high, _, _ in columns.values()), default=0.0))
    found = []
    for name, lift in observed.items():
        p = (1 + sum(1 for m in null_max if m >= abs(lift) - 1e-12)) / (1 + permutations)
        if p <= alpha:
            high, kind, split = columns[name]
            n1 = len(high)
            s1 = sum(labels[i] for i in high)
            found.append(Driver(name, kind, lift, p, s1 / n1, (total - s1) / (len(labels) - n1), n1,
                                len(labels) - n1, split))
    found.sort(key=lambda d: (-abs(d.lift), d.p_value, d.feature))
    return DriversResult(output, focus_text, len(labels), total / len(labels), found[:top], len(columns),
                         permutations, alpha, notes)
