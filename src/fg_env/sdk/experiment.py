"""Experiments: every arm × N seeded runs, with common random numbers across arms."""
from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from .api import ContractLike, load, parse
from .measure import RunResult
from .seeds import SeedTree

__all__ = ["experiment", "ExperimentResult", "ArmResult"]


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
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        n = len(present)
        mean = sum(present) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in present) / (n - 1)) if n > 1 else 0.0
        return {"n": n, "mean": mean, "sd": sd, "min": min(present), "max": max(present),
                "ci95": [mean - 1.96 * sd / math.sqrt(n), mean + 1.96 * sd / math.sqrt(n)] if n > 1 else [mean, mean]}
    counts: Dict[str, int] = {}
    for v in present:
        key = str(v)
        counts[key] = counts.get(key, 0) + 1
    return {"n": len(present), "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}


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
        failed = sum(len(a.failed) for a in self.arms.values())
        if failed:
            lines.append(f"failed runs: {failed}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"seeds": self.seeds, "arms": {
            label: {"outputs": arm.outputs, "runs": [r.to_dict(events=False) for r in arm.runs]}
            for label, arm in self.arms.items()}}


def _portable(participants: Any) -> bool:
    """Participants given by name (``"random"``, ``"policy:x"``, or a mapping of those) can run in other processes."""
    if participants is None or isinstance(participants, str):
        return True
    return isinstance(participants, Mapping) and all(isinstance(v, str) for v in participants.values())


def _run_job(payload: tuple) -> RunResult:
    data, inputs, seed, arm, participants, rounds = payload
    return load(data, inputs=inputs, seed=seed, arm=arm).run(participants, rounds=rounds)


def experiment(source: ContractLike, *, runs: int = 10, arms: Optional[List[str]] = None, seed: int = 0,
               inputs: Optional[Mapping[str, Any]] = None, participants: Any = None,
               participants_for: Optional[Callable[[int, Optional[str]], Any]] = None,
               rounds: Optional[int] = None, workers: int = 1) -> ExperimentResult:
    """Run each arm ``runs`` times. Run *i* uses the same seed in every arm, so differences
    between arms come from the arm, not from luck. ``arms`` defaults to every declared arm
    (or a single baseline run set when none are declared). ``participants_for(i, arm)``
    builds fresh participants per run when they hold state.
    """
    contract = parse(source)
    labels: List[Optional[str]] = list(arms) if arms is not None else (list(contract.arms) or [None])
    tree = SeedTree(seed)
    seeds = [tree.derive("run", i) for i in range(runs)]

    def one(job: tuple) -> RunResult:
        arm, index = job
        env = load(contract, inputs=inputs, seed=seeds[index], arm=arm)
        who = participants_for(index, arm) if participants_for is not None else participants
        return env.run(who, rounds=rounds)

    jobs = [(arm, i) for arm in labels for i in range(runs)]
    if workers > 1 and participants_for is None and _portable(participants):
        # Runs are CPU-bound: separate processes use every core.
        data = contract.model_dump(by_alias=True, exclude_unset=True)
        payloads = [(data, inputs, seeds[i], arm, participants, rounds) for arm, i in jobs]
        with ProcessPoolExecutor(max_workers=workers) as processes:
            results = list(processes.map(_run_job, payloads))
    elif workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, jobs))
    else:
        results = [one(job) for job in jobs]
    out: Dict[str, ArmResult] = {}
    for arm in labels:
        arm_runs = [r for (a, _), r in zip(jobs, results) if a == arm]
        names = list(contract.outputs)
        summary = {name: _describe([r.outputs.get(name) for r in arm_runs if r.status != "failed"]) for name in names}
        out[arm or "baseline"] = ArmResult(arm, arm_runs, summary)
    return ExperimentResult(out, seeds)
