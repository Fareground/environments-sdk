"""Parameter sweeps: how outputs move across a grid (or a Latin hypercube) of inputs.

Every cell runs on the same seeds (common random numbers), so a difference between cells
comes from the inputs, not from luck. Main effects average each input's levels over the
other inputs; the high-vs-low effect is a paired per-seed difference with a t interval.
"""
from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from ..api import ContractLike
from ..measure import RunResult
from ..seeds import SeedTree
from . import runner
from .stats import Estimate, estimate, fisher_interval, latin_hypercube, levels, mean, spearman

__all__ = ["sweep", "SweepResult", "SweepCell", "parse_param"]

ParamSpec = Union[Sequence[Any], Mapping[str, Any]]

#: Bins used to show a Latin-hypercube input's effect as means over equal-count bins.
_LHS_BINS = 4


@dataclass(frozen=True)
class SweepCell:
    """One combination of swept inputs (and arm), with its runs and per-output estimates."""

    index: int
    inputs: Dict[str, Any]
    arm: Optional[str]
    runs: List[RunResult]
    summary: Dict[str, Estimate]

    def label(self) -> str:
        text = runner.describe_inputs(self.inputs)
        return f"{text} [{self.arm}]" if self.arm else text


@dataclass
class SweepResult:
    contract: str
    design: str
    params: Dict[str, List[Any]]
    arms: List[Optional[str]]
    measures: List[str]
    seeds: List[int]
    cells: List[SweepCell]
    _effects: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict, repr=False)

    @property
    def runs(self) -> List[RunResult]:
        return [r for cell in self.cells for r in cell.runs]

    def main_effects(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """``{output: {input_or_arm: effect}}``.

        Factorial: ``levels`` (value, mean of cell means, cells), ``trend`` (increasing,
        decreasing, flat or mixed across ordered numeric levels), ``spearman`` (level vs cell
        mean), ``range`` and ``high_minus_low`` (paired per seed, averaged over the other
        inputs, with a 95% interval). Latin hypercube: ``spearman`` over every run with a
        95% interval, and means over equal-count bins of the input.
        """
        if not self._effects:
            self._effects = {m: self._effects_for(m) for m in self.measures}
        return self._effects

    def trend(self, param: str, measure: str) -> str:
        return self.main_effects()[measure][param]["trend"]

    def _factors(self) -> Dict[str, List[Any]]:
        factors = dict(self.params)
        if len(self.arms) > 1:
            factors["arm"] = list(self.arms)
        return factors

    def _level_of(self, cell: SweepCell, factor: str) -> Any:
        return cell.arm if factor == "arm" else cell.inputs[factor]

    def _effects_for(self, measure: str) -> Dict[str, Dict[str, Any]]:
        key = ("outputs", measure)
        if self.design == "lhs":
            return {name: self._lhs_effect(name, key) for name in self.params}
        return {name: self._factorial_effect(name, levels_, key) for name, levels_ in self._factors().items()}

    def _factorial_effect(self, factor: str, levels_: List[Any], key: Any) -> Dict[str, Any]:
        rows = []
        per_level_runs: List[List[Optional[float]]] = []
        for level in levels_:
            cells = [c for c in self.cells if self._level_of(c, factor) == level]
            means = [c.summary[key[1]].mean for c in cells if c.summary[key[1]].mean is not None]
            rows.append({"value": level, "mean": mean(means) if means else None, "cells": len(cells)})
            per_level_runs.append(_per_seed_means(cells, key, len(self.seeds)))
        effect: Dict[str, Any] = {"levels": rows}
        ordered = [r for r in rows if r["mean"] is not None]
        numeric_levels = all(isinstance(r["value"], (int, float)) and not isinstance(r["value"], bool) for r in ordered)
        if numeric_levels:
            ordered = sorted(ordered, key=lambda r: r["value"])
        values = [r["mean"] for r in ordered]
        effect["range"] = (max(values) - min(values)) if values else None
        effect["trend"] = _trend(values) if numeric_levels else "unordered"
        effect["spearman"] = spearman([r["value"] for r in ordered], values) if numeric_levels and len(values) >= 3 else None
        if len(per_level_runs) >= 2:
            low_i, high_i = (levels_.index(ordered[0]["value"]), levels_.index(ordered[-1]["value"])) \
                if numeric_levels and ordered else (0, len(levels_) - 1)
            diffs = [h - lo for h, lo in zip(per_level_runs[high_i], per_level_runs[low_i]) if h is not None and lo is not None]
            effect["high_minus_low"] = estimate(diffs).to_dict() if diffs else None
        return effect

    def _lhs_effect(self, param: str, key: Any) -> Dict[str, Any]:
        xs, ys = [], []
        for cell in self.cells:
            for run in cell.runs:
                y = runner.value(run, key)
                if y is not None:
                    xs.append(float(cell.inputs[param]))
                    ys.append(y)
        rho = spearman(xs, ys) if len(xs) >= 3 else None
        interval = fisher_interval(rho, len(xs)) if rho is not None else None
        bins = []
        if xs:
            order = sorted(range(len(xs)), key=lambda i: xs[i])
            count = min(_LHS_BINS, len(order))
            for b in range(count):
                members = order[b * len(order) // count:(b + 1) * len(order) // count]
                if members:
                    bins.append({"low": xs[members[0]], "high": xs[members[-1]], "mean": mean([ys[i] for i in members]),
                                 "n": len(members)})
        trend = _trend([b["mean"] for b in bins]) if len(bins) >= 2 else "flat"
        return {"spearman": rho, "spearman_ci95": list(interval) if interval else None, "bins": bins, "trend": trend,
                "clear": bool(interval and (interval[0] > 0 or interval[1] < 0))}

    def table(self) -> str:
        names = list(self.params) + (["arm"] if len(self.arms) > 1 else [])
        header = names + self.measures
        rows = []
        for cell in self.cells:
            row = [_fmt(cell.inputs[n]) if n != "arm" else str(cell.arm) for n in names]
            row += [cell.summary[m].text(3) if m in cell.summary else "—" for m in self.measures]
            rows.append(row)
        widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(header)]
        lines = ["  ".join(h.ljust(w) for h, w in zip(header, widths))]
        lines += ["  ".join(c.ljust(w) for c, w in zip(row, widths)) for row in rows]
        return "\n".join(lines)

    def report(self) -> str:
        cells, runs = len(self.cells), len(self.seeds)
        lines = [f"Sweep of {self.contract}: {self.design}, {cells} cell(s) × {runs} run(s) on common seeds", "",
                 self.table(), "", "Main effects"]
        for measure, effects in self.main_effects().items():
            for factor, effect in effects.items():
                lines.append(f"  {factor} → {measure}: {_effect_text(effect)}")
        failure = runner.summarize_failures(self.runs)
        if failure:
            lines.append(f"\n{failure}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract": self.contract, "design": self.design, "params": self.params, "arms": self.arms,
            "seeds": self.seeds, "measures": self.measures,
            "cells": [{"inputs": c.inputs, "arm": c.arm, "summary": {k: v.to_dict() for k, v in c.summary.items()},
                       "failed": sum(1 for r in c.runs if r.status == "failed"),
                       "outputs": [r.outputs for r in c.runs]} for c in self.cells],
            "main_effects": self.main_effects(),
        }


def _per_seed_means(cells: Sequence[SweepCell], key: Any, runs: int) -> List[Optional[float]]:
    """For seed i, the mean output over ``cells`` (``None`` if any is missing, keeping pairs honest)."""
    out: List[Optional[float]] = []
    for i in range(runs):
        values = [runner.value(c.runs[i], key) for c in cells]
        out.append(mean([v for v in values if v is not None]) if values and all(v is not None for v in values) else None)
    return out


def _trend(values: Sequence[float]) -> str:
    steps = [b - a for a, b in zip(values, values[1:])]
    if not steps or all(math.isclose(s, 0.0, abs_tol=1e-12) for s in steps):
        return "flat"
    if all(s >= 0 for s in steps):
        return "increasing"
    if all(s <= 0 for s in steps):
        return "decreasing"
    return "mixed"


def _fmt(value: Any) -> str:
    return f"{value:.4g}" if isinstance(value, float) else json.dumps(value, default=str)


def _effect_text(effect: Dict[str, Any]) -> str:
    if "levels" in effect:
        parts = ", ".join(f"{_fmt(r['value'])}: {r['mean']:.4g}" for r in effect["levels"] if r["mean"] is not None)
        text = f"{effect['trend']} ({parts})"
        hml = effect.get("high_minus_low")
        if hml and hml.get("low") is not None:
            text += f"; high − low {hml['mean']:+.4g} (95% CI {hml['low']:+.3g} to {hml['high']:+.3g})"
        return text
    rho = effect.get("spearman")
    if rho is None:
        return "no variation to measure"
    interval = effect.get("spearman_ci95")
    ci = f" (95% CI {interval[0]:+.2f} to {interval[1]:+.2f})" if interval else ""
    return f"{effect['trend']}, rank correlation {rho:+.2f}{ci}{', clear' if effect['clear'] else ''}"


def parse_param(contract: Any, name: str, spec: ParamSpec) -> List[Any]:
    """Levels for one swept input: a list of values, or ``{low, high, steps, log}``."""
    runner.input_spec(contract, name)
    if isinstance(spec, Mapping):
        unknown = set(spec) - {"low", "high", "steps", "log"}
        if unknown:
            raise ValueError(f"param '{name}': unknown field(s) {sorted(unknown)} (use low, high, steps, log)")
        low, high = runner.bounds(contract, name, spec)
        steps = runner.check_positive_int(f"param '{name}' steps", spec.get("steps", 3))
        raw = levels(low, high, steps, bool(spec.get("log", False)))
        values = [runner.coerce_input(contract, name, v) for v in raw]
        return list(dict.fromkeys(values))  # integer rounding can repeat a level
    if isinstance(spec, (str, bytes)) or not isinstance(spec, Sequence) or not spec:
        raise ValueError(f"param '{name}' needs a non-empty list of values or {{low, high, steps}}")
    return list(spec)


def sweep(contract: ContractLike, params: Mapping[str, ParamSpec], *, runs: int = 5,
          outputs: Optional[Sequence[str]] = None, arms: Optional[Sequence[Optional[str]]] = None,
          inputs: Optional[Mapping[str, Any]] = None, design: str = "factorial", samples: Optional[int] = None,
          participants: Any = None, rounds: Optional[int] = None, seed: int = 0, workers: int = 1) -> SweepResult:
    """Run the contract across combinations of inputs.

    ``params``: ``{input: [values]}`` or ``{input: {"low", "high", "steps", "log"}}`` (range
    bounds default to the input's declared ``min``/``max``). ``design="factorial"`` runs every
    combination; ``design="lhs"`` draws ``samples`` Latin-hypercube points over the ranges
    (list params are sampled by stratified index). ``arms`` adds the arm as another factor
    (default: no arm). ``inputs`` fixes other inputs for every cell. ``outputs`` limits the
    measured outputs (default: every numeric or yes/no output).
    """
    runner.check_positive_int("runs", runs)
    if not params:
        raise ValueError("sweep needs at least one param")
    parsed = runner.as_contract(contract)
    arm_list: List[Optional[str]] = list(arms) if arms else [None]
    base = dict(inputs or {})
    overlap = set(base) & set(params)
    if overlap:
        raise ValueError(f"inputs {sorted(overlap)} are both fixed and swept")
    if design == "factorial":
        grid = {name: parse_param(parsed, name, spec) for name, spec in params.items()}
        points = [dict(zip(grid, combo)) for combo in itertools.product(*grid.values())]
    elif design == "lhs":
        count = runner.check_positive_int("samples", samples if samples is not None else 10)
        grid, points = _lhs_points(parsed, params, count, seed)
    else:
        raise ValueError(f"design must be 'factorial' or 'lhs', got {design!r}")
    cells = [({**base, **point}, arm) for arm in arm_list for point in points]
    seeds = runner.run_seeds(seed, runs)
    jobs = runner.jobs_for(cells, seeds)
    results = runner.run_jobs(parsed, jobs, participants=participants, rounds=rounds, workers=workers)
    grouped = runner.by_cell(jobs, results, len(cells))
    measures = list(outputs) if outputs else runner.numeric_measures(parsed, results)
    for name in measures:
        if runner.resolve_measure(parsed, name)[0] != "outputs":
            raise ValueError(f"'{name}' is a metric; sweep measures outputs (declare an output reading $metrics.{name})")
    sweep_cells = []
    for index, ((cell_inputs, arm), cell_runs) in enumerate(zip(cells, grouped)):
        swept = {k: cell_inputs[k] for k in params}
        summary = {m: estimate([v for v in (runner.value(r, ("outputs", m)) for r in cell_runs) if v is not None])
                   for m in measures}
        sweep_cells.append(SweepCell(index, swept, arm, cell_runs, summary))
    return SweepResult(parsed.name, design, grid, arm_list, measures, seeds, sweep_cells)


def _lhs_points(contract: Any, params: Mapping[str, ParamSpec], count: int, seed: int) -> tuple:
    names = list(params)
    rng = SeedTree(seed).rng("lhs")
    unit = latin_hypercube(count, len(names), rng)
    points = []
    for row in unit:
        point = {}
        for name, u in zip(names, row):
            spec = params[name]
            if isinstance(spec, Mapping):
                low, high = runner.bounds(contract, name, spec)
                if spec.get("log"):
                    if low <= 0:
                        raise ValueError(f"param '{name}': log sampling needs a positive low")
                    raw = math.exp(math.log(low) + u * (math.log(high) - math.log(low)))
                else:
                    raw = low + u * (high - low)
                point[name] = runner.coerce_input(contract, name, raw)
            else:
                values = parse_param(contract, name, spec)
                point[name] = values[min(len(values) - 1, int(u * len(values)))]
        points.append(point)
    grid = {name: [p[name] for p in points] for name in names}
    return grid, points
