"""Experiments: every arm × N seeded runs, with common random numbers across arms."""
from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from .api import ContractLike, load, parse
from .errors import ContractError, Issue
from .measure import RunResult
from .seeds import SeedTree

__all__ = ["experiment", "ExperimentResult", "ArmResult"]

#: Two-sided 95% normal quantile, used for every confidence interval reported here.
_Z95 = 1.96


def _describe(values: List[Any]) -> Dict[str, Any]:
    present = [v for v in values if v is not None]
    if not present:
        return {"n": 0}
    if all(isinstance(v, dict) for v in present):
        keys: List[str] = []
        for v in present:
            keys += [k for k in v if k not in keys]
        return {"n": len(present), "keys": {k: _describe([v.get(k) for v in present]) for k in keys}}
    if all(isinstance(v, list) for v in present):
        return {"n": len(present), "length": _describe([len(v) for v in present])}
    if all(isinstance(v, bool) for v in present):
        return {"n": len(present), "rate": sum(present) / len(present)}
    if all(_is_number(v) for v in present):
        return {"n": len(present), **_moments(present)}
    counts: Dict[str, int] = {}
    for v in present:
        key = str(v)
        counts[key] = counts.get(key, 0) + 1
    return {"n": len(present), "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _moments(values: List[float]) -> Dict[str, Any]:
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    half = _Z95 * sd / math.sqrt(n) if n > 1 else 0.0
    return {"mean": mean, "sd": sd, "min": min(values), "max": max(values), "ci95": [mean - half, mean + half]}


@dataclass
class ArmResult:
    arm: Optional[str]
    runs: List[RunResult]
    outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def failed(self) -> List[RunResult]:
        return [r for r in self.runs if r.status == "failed"]


@dataclass
class ExperimentResult:
    arms: Dict[str, ArmResult]
    seeds: List[int]

    def deltas(self, control: Optional[str] = None) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Paired differences (arm − control) for every numeric or yes/no output.

        Run *i* of every arm shares a seed, so each difference compares like with like; a
        confidence interval that excludes zero means the arm moved the output. Runs that failed
        in either arm, or have no value, are left out of that pair. ``control`` defaults to the
        first arm.
        """
        if not self.arms:
            return {}
        label = control if control is not None else next(iter(self.arms))
        if label not in self.arms:
            raise KeyError(f"no arm '{label}' (arms: {', '.join(self.arms)})")
        base = self.arms[label]
        out: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for name, arm in self.arms.items():
            if name == label:
                continue
            per_output: Dict[str, Dict[str, Any]] = {}
            for key in base.outputs:
                diffs = []
                for mine, theirs in zip(arm.runs, base.runs):
                    if mine.status == "failed" or theirs.status == "failed":
                        continue
                    a, b = mine.outputs.get(key), theirs.outputs.get(key)
                    if isinstance(a, bool) and isinstance(b, bool):
                        a, b = int(a), int(b)
                    if _is_number(a) and _is_number(b):
                        diffs.append(a - b)
                if diffs:
                    stats = _moments(diffs)
                    low, high = stats["ci95"]
                    per_output[key] = {"n": len(diffs), **stats, "clear": low > 0 or high < 0}
            out[name] = per_output
        return out

    def table(self) -> str:
        """Plain-text comparison of every output across arms."""
        names: List[str] = []
        for arm in self.arms.values():
            for key in arm.outputs:
                if key not in names:
                    names.append(key)
        lines = []
        for key in names:
            cells = []
            for label, arm in self.arms.items():
                stats = arm.outputs.get(key, {})
                if "mean" in stats:
                    cells.append(f"{label}: {stats['mean']:.4g} ± {stats['sd']:.2g} (n={stats['n']})")
                elif "rate" in stats:
                    cells.append(f"{label}: {stats['rate']:.0%} (n={stats['n']})")
                elif "keys" in stats:
                    parts = [f"{k} {s['mean']:.3g}" for k, s in stats["keys"].items() if "mean" in s][:6]
                    cells.append(f"{label}: " + (", ".join(parts) or f"{len(stats['keys'])} keys"))
                elif "length" in stats:
                    cells.append(f"{label}: lists of ~{stats['length'].get('mean', 0):.3g} items")
                elif "counts" in stats:
                    top = ", ".join(f"{k}×{v}" for k, v in list(stats["counts"].items())[:3])
                    cells.append(f"{label}: {top}")
                else:
                    cells.append(f"{label}: —")
            lines.append(f"{key}: " + " | ".join(cells))
        if len(self.arms) > 1:
            control = next(iter(self.arms))
            for name, outputs in self.deltas(control).items():
                for key, d in outputs.items():
                    verdict = "clear" if d["clear"] else "within noise"
                    lines.append(f"{name} − {control} · {key}: {d['mean']:+.4g} "
                                 f"(95% CI {d['ci95'][0]:+.3g} to {d['ci95'][1]:+.3g}, n={d['n']}, {verdict})")
        failed = [r for a in self.arms.values() for r in a.failed]
        if failed:
            lines.append(f"failed runs: {len(failed)} (first: {failed[0].error})")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"seeds": self.seeds, "deltas": self.deltas() if len(self.arms) > 1 else {}, "arms": {
            label: {"outputs": arm.outputs, "runs": [r.to_dict(events=False) for r in arm.runs]}
            for label, arm in self.arms.items()}}


def _portable(participants: Any) -> bool:
    """Participants given by name (``"random"``, ``"policy:x"``, or a mapping of those) can run in other processes."""
    if participants is None or isinstance(participants, str):
        return True
    return isinstance(participants, Mapping) and all(isinstance(v, str) for v in participants.values())


def _failed(seed: int, arm: Optional[str], inputs: Optional[Mapping[str, Any]], error: BaseException) -> RunResult:
    """A run that could not complete, kept in the results so the other runs are never lost."""
    return RunResult(status="failed", ended_by=None, rounds=0, seed=seed, arm=arm, inputs=dict(inputs or {}),
                     outputs={}, metrics={}, series={}, error=f"{type(error).__name__}: {error}")


def _run_job(payload: tuple) -> RunResult:
    data, inputs, seed, arm, participants, rounds = payload
    try:
        return load(data, inputs=inputs, seed=seed, arm=arm).run(participants, rounds=rounds)
    except Exception as exc:  # reported per run, never fatal to the experiment
        return _failed(seed, arm, inputs, exc)


def experiment(source: ContractLike, *, runs: int = 10, arms: Optional[List[str]] = None, seed: int = 0,
               inputs: Optional[Mapping[str, Any]] = None, participants: Any = None,
               participants_for: Optional[Callable[[int, Optional[str]], Any]] = None,
               rounds: Optional[int] = None, workers: int = 1) -> ExperimentResult:
    """Run each arm ``runs`` times. Run *i* uses the same seed in every arm, so differences
    between arms come from the arm, not from luck. ``arms`` defaults to every declared arm
    (or a single baseline run set when none are declared). ``participants_for(i, arm)``
    builds fresh participants per run when they hold state.

    Problems shared by every run (an unknown arm, bad inputs, an unknown participant) raise
    before anything runs. A run that fails on its own is kept with ``status="failed"`` and its
    error, and the rest of the experiment carries on.
    """
    for name, value in (("runs", runs), ("workers", workers)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a whole number ≥ 1, got {value!r}")
    contract = parse(source)
    labels: List[Optional[str]] = list(arms) if arms is not None else (list(contract.arms) or [None])
    unknown = [a for a in labels if a is not None and a not in contract.arms]
    if unknown:
        raise ContractError([Issue("arms", f"not declared: {', '.join(map(str, unknown))}",
                                   f"declared arms: {', '.join(contract.arms) or 'none'}")])
    if len(set(labels)) != len(labels):
        raise ValueError(f"arms are listed more than once: {labels}")
    for arm in labels:  # fail fast on what every run shares
        probe = load(contract, inputs=inputs, seed=0, arm=arm)
        if participants_for is None:
            probe._bind(participants)
    tree = SeedTree(seed)
    seeds = [tree.derive("run", i) for i in range(runs)]

    def one(job: tuple) -> RunResult:
        arm, index = job
        try:
            env = load(contract, inputs=inputs, seed=seeds[index], arm=arm)
            who = participants_for(index, arm) if participants_for is not None else participants
            return env.run(who, rounds=rounds)
        except Exception as exc:
            return _failed(seeds[index], arm, inputs, exc)

    jobs = [(arm, i) for arm in labels for i in range(runs)]
    if workers > 1 and participants_for is None and _portable(participants):
        # Runs are CPU-bound: separate processes use every core.
        data = contract.model_dump(by_alias=True, exclude_unset=True)
        payloads = [(data, inputs, seeds[i], arm, participants, rounds) for arm, i in jobs]
        try:
            with ProcessPoolExecutor(max_workers=workers) as processes:
                results = list(processes.map(_run_job, payloads))
        except BrokenProcessPool:  # a worker died (out of memory, killed): finish in this process instead
            results = [one(job) for job in jobs]
    elif workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, jobs))
    else:
        results = [one(job) for job in jobs]
    out: Dict[str, ArmResult] = {}
    for arm in labels:
        arm_runs = [r for (a, _), r in zip(jobs, results) if a == arm]
        summary = {name: _describe([r.outputs.get(name) for r in arm_runs if r.status != "failed"])
                   for name in contract.outputs}
        out[arm or "baseline"] = ArmResult(arm, arm_runs, summary)
    return ExperimentResult(out, seeds)
