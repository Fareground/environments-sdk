"""Experiments: every arm × N seeded runs, with common random numbers across arms."""
from __future__ import annotations

import json
import math
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from .api import ContractLike, contract_source, default_data_dir, load, located, parse
from .arm_inputs import arm_input_overrides, override_message
from .budget import Budget
from .contract import Contract
from .errors import ContractError, Issue
from .measure import RunResult
from .seeds import SeedTree

__all__ = ["experiment", "ExperimentResult", "ArmResult"]

#: Two-sided 95% Student-t quantiles by degrees of freedom (1-30); larger samples use the normal 1.96.
#: Experiments are often a handful of runs, where the normal value would overstate certainty.
_T95 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145,
        2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048,
        2.045, 2.042)
_Z95 = 1.96


def _critical(n: int) -> float:
    return _T95[n - 2] if 2 <= n <= len(_T95) + 1 else _Z95


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
    half = _critical(n) * sd / math.sqrt(n) if n > 1 else 0.0
    return {"mean": mean, "sd": sd, "min": min(values), "max": max(values), "ci95": [mean - half, mean + half]}


@dataclass
class ArmResult:
    arm: Optional[str]
    runs: List[RunResult]
    outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Inputs of this arm the experiment's own inputs replaced, in plain words (see :mod:`fg_env.sdk.arm_inputs`).
    overridden: List[str] = field(default_factory=list)

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
        lines += [f"warning: {message}" for arm in self.arms.values() for message in arm.overridden]
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
            label: {"outputs": arm.outputs, "runs": [r.to_dict(events=bool(r.exposures)) for r in arm.runs]}
            for label, arm in self.arms.items()}}


def _portable(participants: Any) -> bool:
    """Participants given by name (``"random"``, ``"policy:x"``, or a mapping of those) can run in other processes."""
    if participants is None or isinstance(participants, str):
        return True
    return isinstance(participants, Mapping) and all(isinstance(v, str) for v in participants.values())


@dataclass(frozen=True)
class Job:
    """One run: inputs over the contract defaults, an optional arm, a seed, free-form tags, and optionally
    its own participants (used instead of the batch's for this run: a tournament seats different entrants)."""

    inputs: Mapping[str, Any]
    arm: Optional[str]
    seed: int
    tags: Mapping[str, Any] = field(default_factory=dict)
    participants: Any = None


def failed_run(job: Job, error: BaseException) -> RunResult:
    """A run that could not complete, kept in the results so the other runs are never lost."""
    return RunResult(status="failed", ended_by=None, rounds=0, seed=job.seed, arm=job.arm, inputs=dict(job.inputs),
                     outputs={}, metrics={}, series={}, error=f"{type(error).__name__}: {error}")


def run_job(source: Any, job: Job, participants: Any = None, rounds: Optional[int] = None, events: bool = True,
            data_dir: Any = None, budget: Optional[Mapping[str, Any]] = None, exposures: bool = False,
            hosts: Any = None) -> RunResult:
    """Run one job; a failure comes back as a failed run, never raised. A run that records exposures keeps its
    events whatever ``events`` says: a recording is replayed against them."""
    try:
        result = load(source, inputs=dict(job.inputs), seed=job.seed, arm=job.arm, data_dir=data_dir,
                      exposures=exposures, hosts=hosts).run(participants, rounds=rounds, budget=budget)
    except Exception as exc:  # reported per run, never fatal to the batch
        return failed_run(job, exc)
    return result if events or exposures else replace(result, events=[])


def _process_job(payload: tuple) -> RunResult:
    return run_job(*payload)


def _check_workers(workers: Any) -> None:
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError(f"workers must be a whole number ≥ 1, got {workers!r}")


@contextmanager
def worker_pool(workers: int, participants: Any = None, hosts: Any = None) -> Iterator[Optional[ProcessPoolExecutor]]:
    """One process pool shared by many :func:`run_jobs` calls (starting workers costs more than a small
    batch). Yields ``None`` when runs stay in this process: one worker, participants that are callables, or hosts."""
    _check_workers(workers)
    if workers > 1 and hosts is None and _portable(participants):
        with ProcessPoolExecutor(max_workers=workers) as pool:
            yield pool
    else:
        yield None


def run_jobs(source: ContractLike, jobs: Sequence[Job], *, participants: Any = None,
             participants_for: Optional[Callable[[Job], Any]] = None, rounds: Optional[int] = None, workers: int = 1,
             events: bool = True, pool: Optional[ProcessPoolExecutor] = None, data_dir: Any = None,
             budget: Optional[Mapping[str, Any]] = None, exposures: bool = False, hosts: Any = None) -> List[RunResult]:
    """Run every job, in order, returning one result per job.

    Problems the jobs share (bad inputs, an unknown arm, an unknown participant) raise before anything
    runs; a job that fails on its own comes back as a failed run. Participants given by name run in
    worker processes when ``workers > 1`` or a ``pool`` is given; callables run in threads.
    ``participants_for(job)`` builds fresh participants per job; a job's own ``participants`` replace
    the batch's for that job; ``events=False`` drops event logs; ``budget`` caps each run on its own
    (every run has the whole budget: :mod:`fg_env.sdk.budget`); ``exposures=True`` records what agents saw in every
    run's ``exposures``, events kept, so each run is a trace to read or replay (:func:`fg_env.trace`).
    Inputs with a ``source`` read their files from ``data_dir`` (default: the contract file's folder); ``hosts``
    answers the contract's host requests (feeds, judges) and keeps runs in this process (threads).
    """
    _check_workers(workers)
    contract = located(source, data_dir) if isinstance(source, Contract) else parse(source, data_dir)
    folder = default_data_dir(contract)
    if not jobs:
        return []

    def assigned(job: Job) -> Any:
        return job.participants if job.participants is not None else participants

    probed: Set[Tuple[str, Optional[str]]] = set()
    for job in jobs:  # fail fast on what the jobs share
        key = (json.dumps(dict(job.inputs), sort_keys=True, default=str), job.arm)
        if key in probed:
            continue
        probed.add(key)
        env = load(contract, inputs=dict(job.inputs), seed=0, arm=job.arm, hosts=hosts)
        if participants_for is None and len(probed) == 1:
            env.driver.bind(assigned(job))

    def one(job: Job) -> RunResult:
        try:
            who = participants_for(job) if participants_for is not None else assigned(job)
        except Exception as exc:
            return failed_run(job, exc)
        return run_job(contract, job, who, rounds, events, folder, budget, exposures, hosts)

    many = len(jobs) > 1
    portable = hosts is None and participants_for is None and all(_portable(assigned(job)) for job in jobs)
    if (pool is not None or workers > 1) and many and portable:
        data = contract_source(contract)
        payloads = [(data, job, assigned(job), rounds, events, str(folder) if folder else None, budget, exposures)
                    for job in jobs]
        chunk = max(1, len(jobs) // (workers * 4))
        try:
            if pool is not None:
                return list(pool.map(_process_job, payloads, chunksize=chunk))
            with ProcessPoolExecutor(max_workers=workers) as own:
                return list(own.map(_process_job, payloads, chunksize=chunk))
        except BrokenProcessPool:  # a worker died (out of memory, killed): finish in this process instead
            return [one(job) for job in jobs]
    if workers > 1 and many:
        with ThreadPoolExecutor(max_workers=workers) as threads:
            return list(threads.map(one, jobs))
    return [one(job) for job in jobs]


def _branched(contract: Contract, jobs: Sequence[Job], branch_at: int, participants: Any,
              participants_for: Optional[Callable[[Job], Any]], rounds: Optional[int], workers: int,
              folder: Any, budget: Optional[Mapping[str, Any]], exposures: bool, hosts: Any = None) -> List[RunResult]:
    """Each run's first ``branch_at`` rounds played once without an arm, then continued under every job's arm. The
    budget starts with the shared rounds and every continuation carries what they used (a fork keeps the budget)."""
    if isinstance(branch_at, bool) or not isinstance(branch_at, int) or branch_at < 0:
        raise ValueError(f"branch_at must be a whole number of rounds ≥ 0, got {branch_at!r}")
    if rounds is not None and branch_at > rounds:
        raise ValueError(f"branch_at ({branch_at}) is after the {rounds} rounds each run plays")
    groups: Dict[Any, List[Job]] = {}
    for job in jobs:
        groups.setdefault(job.tags["run"], []).append(job)
    rest = None if rounds is None else rounds - branch_at

    def one(group: List[Job]) -> List[Tuple[Job, RunResult]]:
        first = group[0]
        try:
            shared = load(contract, inputs=dict(first.inputs), seed=first.seed, data_dir=folder, exposures=exposures,
                          hosts=hosts)
            shared.run(participants_for(replace(first, arm=None)) if participants_for else
                       (first.participants if first.participants is not None else participants), rounds=branch_at,
                       budget=budget)
        except ContractError:
            raise
        except Exception as exc:  # the shared history failed: every arm of this run reports it
            return [(job, failed_run(job, exc)) for job in group]
        done: List[Tuple[Job, RunResult]] = []
        for job in group:
            try:
                forked = shared.fork(arm=job.arm)
                who = participants_for(job) if participants_for else \
                    (job.participants if job.participants is not None else participants)
                done.append((job, forked.run(who, rounds=rest)))
            except ContractError:
                raise
            except Exception as exc:
                done.append((job, failed_run(job, exc)))
        return done

    if workers > 1 and len(groups) > 1:
        with ThreadPoolExecutor(max_workers=workers) as threads:
            pairs = [pair for chunk in threads.map(one, groups.values()) for pair in chunk]
    else:
        pairs = [pair for group in groups.values() for pair in one(group)]
    found = {id(job): result for job, result in pairs}
    return [found[id(job)] for job in jobs]


def experiment(source: ContractLike, *, runs: int = 10, arms: Optional[List[str]] = None, seed: int = 0,
               inputs: Optional[Mapping[str, Any]] = None, participants: Any = None,
               participants_for: Optional[Callable[[int, Optional[str]], Any]] = None,
               rounds: Optional[int] = None, workers: int = 1, data_dir: Any = None,
               branch_at: Optional[int] = None, budget: Optional[Mapping[str, Any]] = None,
               exposures: bool = False, hosts: Any = None) -> ExperimentResult:
    """Run each arm ``runs`` times. Run *i* uses the same seed in every arm, so differences
    between arms come from the arm, not from luck. ``arms`` defaults to every declared arm
    (or a single baseline run set when none are declared). ``participants_for(i, arm)``
    builds fresh participants per run when they hold state.

    ``branch_at=N`` shares history: run *i* plays its first N rounds once, without an arm, and every
    arm continues from that same state (a fork: the arm's patch and inputs apply from round N + 1, and
    an arm whose patch the state cannot follow raises before the experiment goes on).

    ``budget`` caps each run on its own (:mod:`fg_env.sdk.budget`); with ``branch_at`` the shared rounds are part of
    every arm's run, so they count toward each arm's budget. ``exposures=True`` records what agents saw in every run
    (``result.arms[label].runs[i].exposures``, events kept): each run is a trace to read or replay.
    ``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts``
    answers the contract's host requests in every run.

    Problems shared by every run (an unknown arm, bad inputs, an unknown participant) raise
    before anything runs. A run that fails on its own is kept with ``status="failed"`` and its
    error, and the rest of the experiment carries on.
    """
    for name, value in (("runs", runs), ("workers", workers)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a whole number ≥ 1, got {value!r}")
    if budget is not None:
        Budget.parse(budget)  # a mistake in the budget raises before anything runs
    contract = parse(source, data_dir)
    folder = default_data_dir(contract)
    labels: List[Optional[str]] = list(arms) if arms is not None else (list(contract.arms) or [None])
    unknown = [a for a in labels if a is not None and a not in contract.arms]
    if unknown:
        raise ContractError([Issue("arms", f"not declared: {', '.join(map(str, unknown))}",
                                   f"declared arms: {', '.join(contract.arms) or 'none'}")])
    if len(set(labels)) != len(labels):
        raise ValueError(f"arms are listed more than once: {labels}")
    tree = SeedTree(seed)
    seeds = [tree.derive("run", i) for i in range(runs)]
    jobs = [Job(dict(inputs or {}), arm, seeds[i], {"run": i}) for arm in labels for i in range(runs)]

    def per_job(job: Job) -> Any:
        assert participants_for is not None
        return participants_for(job.tags["run"], job.arm)

    if branch_at is not None:
        results = _branched(contract, jobs, branch_at, participants, per_job if participants_for else None, rounds,
                            workers, folder, budget, exposures, hosts)
    else:
        results = run_jobs(contract, jobs, participants=participants,
                           participants_for=per_job if participants_for else None, rounds=rounds, workers=workers,
                           data_dir=folder, budget=budget, exposures=exposures, hosts=hosts)
    out: Dict[str, ArmResult] = {}
    for arm in labels:
        arm_runs = [r for job, r in zip(jobs, results) if job.arm == arm]
        summary = {name: _describe([r.outputs.get(name) for r in arm_runs if r.status != "failed"])
                   for name in contract.outputs}
        overridden = [override_message(arm, name, arm_value, given)
                      for name, arm_value, given in arm_input_overrides(contract, arm, inputs or {})] if arm else []
        out[arm or "baseline"] = ArmResult(arm, arm_runs, summary, overridden)
    return ExperimentResult(out, seeds)
