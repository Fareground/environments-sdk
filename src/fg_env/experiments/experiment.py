"""Experiments: every arm × N seeded runs, with common random numbers across arms."""
from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeGuard

from ..api import ContractLike, contract_source, default_data_dir, load, located, parse
from ..contract import Contract
from ..errors import ContractError, Issue, RunError
from ..runtime.budget import Budget, is_seconds
from ..runtime.measure import RunResult, usable_output
from ..sampling.seeds import SeedTree
from . import workers as pools
from .arm_inputs import arm_input_overrides, override_message

__all__ = ["experiment", "ExperimentResult", "ArmResult"]

#: Two-sided 95% Student-t quantiles by degrees of freedom (1-30); larger samples use the normal 1.96.
#: Experiments are often a handful of runs, where the normal value would overstate certainty.
_T95 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145,
        2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048,
        2.045, 2.042)
_Z95 = 1.96


def _critical(n: int) -> float:
    return _T95[n - 2] if 2 <= n <= len(_T95) + 1 else _Z95


def _describe(values: list[Any]) -> dict[str, Any]:
    present = [v for v in values if v is not None]
    if not present:
        return {"n": 0}
    if all(isinstance(v, dict) for v in present):
        keys: list[str] = []
        for v in present:
            keys += [k for k in v if k not in keys]
        return {"n": len(present), "keys": {k: _describe([v.get(k) for v in present]) for k in keys}}
    if all(isinstance(v, list) for v in present):
        labelled = [_by_label(v) for v in present]
        if all(row is not None for row in labelled):
            return _describe(labelled)  # a ranking: each label's values, wherever it placed
        if all(_is_number(x) for v in present for x in v):
            width = max(len(v) for v in present)
            return {"n": len(present), "items": [_describe([v[i] if i < len(v) else None for v in present])
                                                 for i in range(width)]}
        return {"n": len(present), "length": _describe([len(v) for v in present])}
    if all(isinstance(v, bool) for v in present):
        return {"n": len(present), "rate": sum(present) / len(present)}
    if all(_is_number(v) for v in present):
        return {"n": len(present), **_moments(present)}
    counts: dict[str, int] = {}
    for v in present:
        key = str(v)
        counts[key] = counts.get(key, 0) + 1
    return {"n": len(present), "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}


def _by_label(rows: list[Any]) -> dict[str, Any] | None:
    """A list of ``[label, value, ...]`` rows (a ranking) as label → value (or values), else None."""
    if not rows or not all(isinstance(row, list) and len(row) >= 2 and isinstance(row[0], str) for row in rows):
        return None
    return {row[0]: row[1] if len(row) == 2 else row[1:] for row in rows}


def _brief(stats: Mapping[str, Any]) -> str:
    """One summarised value, short: a mean, a rate, or the means of a list position by position."""
    if "mean" in stats:
        return f"{stats['mean']:.4g}"
    if "rate" in stats:
        return f"{stats['rate']:.0%}"
    if "items" in stats:
        shown = [_brief(item) for item in stats["items"]]
        return "[" + ", ".join(shown if len(shown) <= 6 else shown[:4] + ["…", shown[-1]]) + "]"
    if stats.get("counts"):
        return next(iter(stats["counts"]))  # the most frequent
    return "—"


def _is_number(value: Any) -> TypeGuard[float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _moments(values: list[float]) -> dict[str, Any]:
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    half = _critical(n) * sd / math.sqrt(n) if n > 1 else 0.0
    return {"mean": mean, "sd": sd, "min": min(values), "max": max(values),
            "ci95": [mean - half, mean + half] if n > 1 else None}


@dataclass
class ArmResult:
    arm: str | None
    runs: list[RunResult]
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Inputs of this arm the experiment's own inputs replaced, in plain words (see
    #: :mod:`fg_env.experiments.arm_inputs`).
    overridden: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[RunResult]:
        return [r for r in self.runs if r.status == "failed"]


@dataclass
class ExperimentResult:
    arms: dict[str, ArmResult]
    seeds: list[int]
    rounds: int | None = None  # explicitly requested experiment window

    def deltas(self, control: str | None = None) -> dict[str, dict[str, dict[str, Any]]]:
        """Paired differences (arm − control) for every numeric or yes/no output.

        Run *i* of every arm shares a seed, so each difference compares like with like; a
        confidence interval that excludes zero means the arm moved the output. Runs that failed
        in either arm, or have no value, are left out of that pair. ``control`` defaults to the
        first arm. With fewer than two usable pairs, ``ci95`` is None and ``clear`` is False:
        the observed difference alone does not estimate sampling uncertainty.
        """
        if not self.arms:
            return {}
        label = control if control is not None else next(iter(self.arms))
        if label not in self.arms:
            raise KeyError(f"no arm '{label}' (arms: {', '.join(self.arms)})")
        base = self.arms[label]
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for name, arm in self.arms.items():
            if name == label:
                continue
            per_output: dict[str, dict[str, Any]] = {}
            for key in base.outputs:
                diffs = []
                for mine, theirs in zip(arm.runs, base.runs):
                    if not usable_output(mine, key) or not usable_output(theirs, key):
                        continue
                    a, b = mine.outputs.get(key), theirs.outputs.get(key)
                    if isinstance(a, bool) and isinstance(b, bool):
                        a, b = int(a), int(b)
                    if _is_number(a) and _is_number(b):
                        diffs.append(a - b)
                if diffs:
                    stats = _moments(diffs)
                    interval = stats["ci95"]
                    clear = interval is not None and (interval[0] > 0 or interval[1] < 0)
                    per_output[key] = {"n": len(diffs), **stats, "clear": clear}
            out[name] = per_output
        return out

    def table(self) -> str:
        """Plain-text comparison of every output across arms."""
        names: list[str] = []
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
                    if stats["n"] < 2:
                        cells.append(f"{label}: {stats['mean']:.4g} (n=1; uncertainty not estimated)")
                    else:
                        cells.append(f"{label}: {stats['mean']:.4g} ± {stats['sd']:.2g} (n={stats['n']})")
                elif "rate" in stats:
                    cells.append(f"{label}: {stats['rate']:.0%} (n={stats['n']})")
                elif "keys" in stats:
                    parts = [f"{k} {_brief(s)}" for k, s in stats["keys"].items()][:6]
                    cells.append(f"{label}: " + (", ".join(parts) or "—"))
                elif "items" in stats:
                    cells.append(f"{label}: {_brief(stats)} (mean per position, n={stats['n']})")
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
                    if d["ci95"] is None:
                        lines.append(f"{name} − {control} · {key}: {d['mean']:+.4g} "
                                     f"(n={d['n']}; uncertainty not estimated)")
                        continue
                    verdict = "clear" if d["clear"] else "within noise"
                    lines.append(f"{name} − {control} · {key}: {d['mean']:+.4g} "
                                 f"(95% CI {d['ci95'][0]:+.3g} to {d['ci95'][1]:+.3g}, n={d['n']}, {verdict})")
        failed = [r for a in self.arms.values() for r in a.failed]
        if failed:
            lines.append(f"failed runs: {len(failed)} (first: {failed[0].error})")
        output_errors = [r for arm in self.arms.values() for r in arm.runs if r.output_issues]
        if output_errors:
            first = output_errors[0].output_issues[0]
            lines.append(f"output issues: {len(output_errors)} run(s) (first: {first['path']}: {first['message']})")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"seeds": self.seeds, "rounds": self.rounds, "deltas": self.deltas() if len(self.arms) > 1 else {},
                "arms": {
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
    arm: str | None
    seed: int
    tags: Mapping[str, Any] = field(default_factory=dict)
    participants: Any = None


def failed_run(job: Job, error: BaseException) -> RunResult:
    """A run that could not complete, kept in the results so the other runs are never lost."""
    return RunResult(status="failed", ended_by=None, rounds=0, seed=job.seed, arm=job.arm, inputs=dict(job.inputs),
                     outputs={}, metrics={}, series={}, error=f"{type(error).__name__}: {error}")


def run_job(source: Any, job: Job, participants: Any = None, rounds: int | None = None, events: bool = True,
            data_dir: Any = None, budget: Mapping[str, Any] | None = None, exposures: bool = False,
            hosts: Any = None, time_limit: float | None = None) -> RunResult:
    """Run one job; a failure comes back as a failed run, never raised. A run that records exposures keeps its
    events whatever ``events`` says: a recording is replayed against them."""
    try:
        env = load(source, inputs=dict(job.inputs), seed=job.seed, arm=job.arm, data_dir=data_dir,
                   exposures=exposures, hosts=hosts, events=events or exposures)
        return env.run(participants, rounds=rounds, budget=budget, time_limit=time_limit)
    except Exception as exc:  # reported per run, never fatal to the batch
        return failed_run(job, exc)


@dataclass(frozen=True)
class _Batch:
    """What every job of a batch sent to worker processes shares."""

    key: str
    data: dict[str, Any]
    folder: str | None
    cwd: str | None
    rounds: int | None
    events: bool
    budget: Mapping[str, Any] | None
    exposures: bool
    time_limit: float | None = None


def _run_chunk(batch: _Batch, chunk: Sequence[tuple[Job, Any]]) -> tuple[list[RunResult], float]:
    """Runs a chunk of ``(job, participants)`` in a worker process, the contract parsed once per worker; the seconds
    the chunk took come back with its results."""
    start = time.perf_counter()
    if batch.cwd is not None:
        with suppress(OSError):  # the folder is gone: runs reading relative data files report it themselves
            os.chdir(batch.cwd)  # a kept worker reads relative data folders where this batch started
    try:
        contract: Any = pools.cached_contract(batch.key, batch.data, batch.folder)
    except Exception:  # each run reports the contract's problem, as a run reading it itself would
        contract = batch.data
    results = [run_job(contract, job, who, batch.rounds, batch.events, batch.folder, batch.budget, batch.exposures,
                       time_limit=batch.time_limit) for job, who in chunk]
    return results, time.perf_counter() - start


def _cwd() -> str | None:
    try:
        return os.getcwd()
    except OSError:
        return None


def _in_workers(contract: Contract, folder: Path | None, jobs: Sequence[Job], assigned: Callable[[Job], Any],
                one: Callable[[Job], RunResult], workers: pools.Workers, rounds: int | None, events: bool,
                budget: Mapping[str, Any] | None, exposures: bool, time_limit: float | None) -> list[RunResult]:
    """Every job through worker processes, or in this process when that is measured to be sooner.

    With no measure of this contract's runs and no workers running, the first job runs here and is timed; the
    rest go to the workers only if they would finish before this process could run them, and time a batch spends
    here for want of running workers counts toward starting them. A pool whose worker died (out of memory, killed)
    is dropped and the batch finishes here."""
    data = contract_source(contract)
    where = str(folder) if folder else None
    key = pools.contract_key(data, where)
    done: list[RunResult] = []
    cost = pools.job_seconds(key)
    started = workers.started
    clock = time.perf_counter()
    if cost is None and not started:
        done.append(one(jobs[0]))
        cost = time.perf_counter() - clock
        pools.record_job_seconds(key, cost)
    rest = jobs[len(done):]
    chunk = pools.chunk_size(len(rest), workers.size, cost, started)
    if chunk == 0:
        finished = done + [one(job) for job in rest]
        if not started:
            pools.record_ran_here(workers.size, time.perf_counter() - clock)
        return finished
    batch = _Batch(key, data, where, _cwd(), rounds, events, budget, exposures, time_limit)
    try:
        results, seconds = pools.run_chunks(workers.executor(), _run_chunk, batch,
                                            [(job, assigned(job)) for job in rest], chunk)
    except BrokenProcessPool:
        workers.discard()
        return done + [one(job) for job in rest]
    pools.record_job_seconds(key, seconds / len(rest))
    return done + results


def _check_workers(workers: Any) -> None:
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError(f"workers must be a whole number ≥ 1, got {workers!r}")


def _check_time_limit(time_limit: Any) -> None:
    """A bad time limit raises before anything runs, rather than failing every run."""
    if time_limit is not None and not is_seconds(time_limit):
        raise ValueError(f"time_limit must be a number of seconds > 0, got {time_limit!r}")


@contextmanager
def worker_pool(workers: int, participants: Any = None, hosts: Any = None) -> Iterator[pools.Workers | None]:
    """Worker processes shared by many :func:`run_jobs` calls: this process's kept pool
    (:mod:`fg_env.experiments.workers`), or with ``FG_ENV_KEEP_WORKERS=0`` a pool of its own closed when the block ends.
    Nothing starts until a batch needs it. Yields ``None`` when runs stay in this process: one worker, participants that
    are callables, or hosts."""
    _check_workers(workers)
    if workers > 1 and hosts is None and _portable(participants):
        handle = pools.Workers(workers)
        try:
            yield handle
        finally:
            handle.close()
    else:
        yield None


def run_jobs(source: ContractLike, jobs: Sequence[Job], *, participants: Any = None,
             participants_for: Callable[[Job], Any] | None = None, rounds: int | None = None, workers: int = 1,
             events: bool = True, pool: pools.Pool | None = None, data_dir: Any = None,
             budget: Mapping[str, Any] | None = None, exposures: bool = False, hosts: Any = None,
             time_limit: float | None = None) -> list[RunResult]:
    """Run every job, in order, returning one result per job.

    Problems the jobs share (bad inputs, an unknown arm, an unknown participant) raise before anything
    runs; a job that fails on its own comes back as a failed run. Participants given by name run in
    worker processes when ``workers > 1`` or a ``pool`` is given (this process's kept pool, in chunks, or right here
    when the batch is too short to pay for workers: :mod:`fg_env.experiments.workers`); callables run in threads.
    ``participants_for(job)`` builds fresh participants per job; a job's own ``participants`` replace
    the batch's for that job; ``events=False`` drops event logs; ``budget`` caps each run on its own
    (every run has the whole budget: :mod:`fg_env.runtime.budget`); ``exposures=True`` records what agents saw in every
    run's ``exposures``, events kept, so each run is a trace to read or replay (:func:`fg_env.analysis.trace`).
    Inputs with a ``source`` read their files from ``data_dir`` (default: the contract file's folder); ``hosts``
    answers the contract's host requests (feeds, judges) and keeps runs in this process (threads). ``time_limit`` is the
    wall-clock seconds each agent's turn may take in every run (as in :meth:`fg_env.Env.run`).
    """
    _check_workers(workers)
    _check_time_limit(time_limit)
    contract = located(source, data_dir) if isinstance(source, Contract) else parse(source, data_dir)
    folder = default_data_dir(contract)
    if not jobs:
        return []

    def assigned(job: Job) -> Any:
        return job.participants if job.participants is not None else participants

    probed: set[tuple[str, str | None]] = set()
    bound = False
    for job in jobs:  # fail fast on what the jobs share
        key = (json.dumps(dict(job.inputs), sort_keys=True, default=str), job.arm)
        if key in probed:
            continue
        probed.add(key)
        try:
            env = load(contract, inputs=dict(job.inputs), seed=0, arm=job.arm, hosts=hosts)
        except RunError:
            # An invariant or construction effect can fail for this input/seed only.
            # The actual job records its failure; schema/input errors still fail fast.
            continue
        if participants_for is None and not bound:
            env.driver.bind(assigned(job))
            bound = True

    def one(job: Job) -> RunResult:
        try:
            who = participants_for(job) if participants_for is not None else assigned(job)
        except Exception as exc:
            return failed_run(job, exc)
        return run_job(contract, job, who, rounds, events, folder, budget, exposures, hosts, time_limit)

    many = len(jobs) > 1
    portable = hosts is None and participants_for is None and all(_portable(assigned(job)) for job in jobs)
    if (pool is not None or workers > 1) and many and portable:
        handle = pool if isinstance(pool, pools.Workers) else pools.Workers(workers, pool)
        try:
            return _in_workers(contract, folder, jobs, assigned, one, handle, rounds, events, budget, exposures,
                               time_limit)
        finally:
            if handle is not pool:
                handle.close()
    if workers > 1 and many:
        with ThreadPoolExecutor(max_workers=workers) as threads:
            return list(threads.map(one, jobs))
    return [one(job) for job in jobs]


def _branched(contract: Contract, jobs: Sequence[Job], branch_at: int, participants: Any,
              participants_for: Callable[[Job], Any] | None, rounds: int | None, workers: int,
              folder: Any, budget: Mapping[str, Any] | None, exposures: bool, hosts: Any = None,
              time_limit: float | None = None) -> list[RunResult]:
    """Each run's first ``branch_at`` rounds played once without an arm, then continued under every job's arm. The
    budget starts with the shared rounds and every continuation carries what they used (a fork keeps the budget)."""
    if isinstance(branch_at, bool) or not isinstance(branch_at, int) or branch_at < 0:
        raise ValueError(f"branch_at must be a whole number of rounds ≥ 0, got {branch_at!r}")
    if rounds is not None and branch_at > rounds:
        raise ValueError(f"branch_at ({branch_at}) is after the {rounds} rounds each run plays")
    groups: dict[Any, list[Job]] = {}
    for job in jobs:
        groups.setdefault(job.tags["run"], []).append(job)
    rest = None if rounds is None else rounds - branch_at

    def one(group: list[Job]) -> list[tuple[Job, RunResult]]:
        first = group[0]
        try:
            shared = load(contract, inputs=dict(first.inputs), seed=first.seed, data_dir=folder, exposures=exposures,
                          hosts=hosts)
            shared.run(participants_for(replace(first, arm=None)) if participants_for else
                       (first.participants if first.participants is not None else participants), rounds=branch_at,
                       budget=budget, time_limit=time_limit)
        except ContractError:
            raise
        except Exception as exc:  # the shared history failed: every arm of this run reports it
            return [(job, failed_run(job, exc)) for job in group]
        done: list[tuple[Job, RunResult]] = []
        for job in group:
            try:
                forked = shared.fork(arm=job.arm)
                who = participants_for(job) if participants_for else \
                    (job.participants if job.participants is not None else participants)
                done.append((job, forked.run(who, rounds=rest, time_limit=time_limit)))
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


def experiment(source: ContractLike, *, runs: int = 10, arms: list[str] | None = None, seed: int = 0,
               inputs: Mapping[str, Any] | None = None, participants: Any = None,
               participants_for: Callable[[int, str | None], Any] | None = None,
               rounds: int | None = None, workers: int = 1, data_dir: Any = None,
               branch_at: int | None = None, budget: Mapping[str, Any] | None = None,
               exposures: bool = False, hosts: Any = None, uncertainty: Any = None,
               time_limit: float | None = None) -> ExperimentResult:
    """Run each arm ``runs`` times. Run *i* uses the same seed in every arm, so differences
    between arms come from the arm, not from luck. ``arms`` defaults to every declared arm
    (or a single baseline run set when none are declared). ``participants_for(i, arm)``
    builds fresh participants per run when they hold state.

    ``branch_at=N`` shares history: run *i* plays its first N rounds once, without an arm, and every
    arm continues from that same state (a fork: the arm's patch and inputs apply from round N + 1, and
    an arm whose patch the state cannot follow raises before the experiment goes on).

    ``budget`` caps each run on its own (:mod:`fg_env.runtime.budget`); with ``branch_at`` the shared rounds are part of
    every arm's run, so they count toward each arm's budget. ``exposures=True`` records what agents saw in every run
    (``result.arms[label].runs[i].exposures``, events kept): each run is a trace to read or replay.
    ``data_dir`` is where inputs with a ``source`` are read (default: the contract file's folder); ``hosts``
    answers the contract's host requests in every run. ``uncertainty`` (a calibration, a list of points or priors;
    :mod:`fg_env.analysis.draws`) draws parameters per run, the same for run *i* in every arm, so the spread of
    outcomes includes not knowing them. ``time_limit`` is the wall-clock seconds each agent's turn may take in every
    run.

    Problems shared by every run (an unknown arm, bad inputs, an unknown participant) raise
    before anything runs. A run that fails on its own is kept with ``status="failed"`` and its
    error, and the rest of the experiment carries on.
    """
    for name, value in (("runs", runs), ("workers", workers)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a whole number ≥ 1, got {value!r}")
    if budget is not None:
        Budget.parse(budget)  # a mistake in the budget raises before anything runs
    _check_time_limit(time_limit)
    contract = parse(source, data_dir)
    folder = default_data_dir(contract)
    labels: list[str | None] = list(arms) if arms is not None else (list(contract.arms) or [None])
    unknown = [a for a in labels if a is not None and a not in contract.arms]
    if unknown:
        raise ContractError([Issue("arms", f"not declared: {', '.join(map(str, unknown))}",
                                   f"declared arms: {', '.join(contract.arms) or 'none'}")])
    if len(set(labels)) != len(labels):
        raise ValueError(f"arms are listed more than once: {labels}")
    tree = SeedTree(seed)
    seeds = [tree.derive("run", i) for i in range(runs)]
    jobs = [Job(dict(inputs or {}), arm, seeds[i], {"run": i}) for arm in labels for i in range(runs)]
    if uncertainty is not None:
        from ..analysis.draws import parameter_draws, with_draws  # analysis runs experiments: imported when used

        jobs = with_draws(jobs, parameter_draws(contract, uncertainty, runs, seed))

    def per_job(job: Job) -> Any:
        assert participants_for is not None
        return participants_for(job.tags["run"], job.arm)

    if branch_at is not None:
        results = _branched(contract, jobs, branch_at, participants, per_job if participants_for else None, rounds,
                            workers, folder, budget, exposures, hosts, time_limit)
    else:
        results = run_jobs(contract, jobs, participants=participants,
                           participants_for=per_job if participants_for else None, rounds=rounds, workers=workers,
                           data_dir=folder, budget=budget, exposures=exposures, hosts=hosts, time_limit=time_limit)
    out: dict[str, ArmResult] = {}
    for arm in labels:
        arm_runs = [r for job, r in zip(jobs, results) if job.arm == arm]
        summary = {name: _describe([r.outputs.get(name) for r in arm_runs if usable_output(r, name)])
                   for name in contract.outputs}
        overridden = [override_message(arm, name, arm_value, given)
                      for name, arm_value, given in arm_input_overrides(contract, arm, inputs or {})] if arm else []
        out[arm or "baseline"] = ArmResult(arm, arm_runs, summary, overridden)
    return ExperimentResult(out, seeds, rounds=rounds)
