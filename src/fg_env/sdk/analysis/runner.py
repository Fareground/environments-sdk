"""Running many seeded jobs for an analysis, and reading numbers out of run results.

Every analysis reduces to a list of jobs (inputs, arm, seed) against one contract. They run in
one pool (processes when participants are given by name, threads otherwise), a run that fails
is kept as ``status="failed"`` and never stops the others, and seeds are derived exactly as
:func:`fg_env.experiment` derives them, so run *i* of an analysis equals run *i* of an
experiment with the same base seed (common random numbers everywhere).
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, List, Mapping, Optional, Sequence, Tuple

from ..api import ContractLike, contract_source, load, parse
from ..contract import Contract
from ..measure import RunResult
from ..seeds import SeedTree
from .stats import numeric

__all__ = ["Job", "AnalysisError", "run_seeds", "run_jobs", "resolve_measure", "value", "raw_value", "series",
           "numeric_measures", "check_positive_int", "as_contract", "input_spec", "summarize_failures", "execute_job", "failed_result", "worker_pool",
           "describe_inputs", "jobs_for", "by_cell", "coerce_input", "bounds"]


class AnalysisError(ValueError):
    """An analysis cannot produce a result: every run failed, or a request is impossible."""


@dataclass(frozen=True)
class Job:
    """One run: inputs over the contract defaults, an optional arm, and a seed."""

    inputs: Mapping[str, Any]
    arm: Optional[str]
    seed: int
    tags: Mapping[str, Any] = field(default_factory=dict)


def check_positive_int(name: str, value: Any, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be a whole number ≥ {minimum}, got {value!r}")
    return value


def as_contract(source: ContractLike) -> Contract:
    return source if isinstance(source, Contract) else parse(source)


def run_seeds(seed: int, count: int, start: int = 0) -> List[int]:
    """Seeds for runs ``start … start+count-1`` of base ``seed`` (the same as ``experiment``)."""
    tree = SeedTree(seed)
    return [tree.derive("run", i) for i in range(start, start + count)]


def input_spec(contract: Contract, name: str) -> Any:
    if name not in contract.inputs:
        raise ValueError(f"'{name}' is not a declared input (inputs: {', '.join(contract.inputs) or 'none'})")
    return contract.inputs[name]


def _portable(participants: Any) -> bool:
    if participants is None or isinstance(participants, str):
        return True
    return isinstance(participants, Mapping) and all(isinstance(v, str) for v in participants.values())


def failed_result(job: Job, error: BaseException) -> RunResult:
    return RunResult(status="failed", ended_by=None, rounds=0, seed=job.seed, arm=job.arm, inputs=dict(job.inputs),
                     outputs={}, metrics={}, series={}, error=f"{type(error).__name__}: {error}")


def execute_job(data: Any, job: Job, participants: Any, rounds: Optional[int], events: bool) -> RunResult:
    try:
        result = load(data, inputs=dict(job.inputs), seed=job.seed, arm=job.arm).run(participants, rounds=rounds)
    except Exception as exc:  # reported per run, never fatal to the analysis
        return failed_result(job, exc)
    return result if events else replace(result, events=[])


def _process_job(payload: Tuple[Any, Job, Any, Optional[int], bool]) -> RunResult:
    return execute_job(*payload)


def _probe(contract: Contract, jobs: Sequence[Job], participants: Any) -> None:
    """Fail before running anything on what jobs share: bad inputs, unknown arms, unknown participants."""
    seen = set()
    for job in jobs:
        key = (json.dumps(dict(job.inputs), sort_keys=True, default=str), job.arm)
        if key in seen:
            continue
        seen.add(key)
        env = load(contract, inputs=dict(job.inputs), seed=0, arm=job.arm)
        if len(seen) == 1:
            env.run(participants, rounds=0)  # binds participants: an unknown name raises here


@contextmanager
def worker_pool(workers: int, participants: Any) -> Iterator[Optional[ProcessPoolExecutor]]:
    """One process pool shared by every ``run_jobs`` call of an iterative analysis.

    Starting worker processes costs far more than a small batch of runs, so searches that call
    ``run_jobs`` many times pass this pool along. Yields ``None`` when runs stay in this process.
    """
    check_positive_int("workers", workers)
    if workers > 1 and _portable(participants):
        with ProcessPoolExecutor(max_workers=workers) as pool:
            yield pool
    else:
        yield None


def run_jobs(source: ContractLike, jobs: Sequence[Job], *, participants: Any = None, rounds: Optional[int] = None,
             workers: int = 1, events: bool = False, pool: Optional[ProcessPoolExecutor] = None) -> List[RunResult]:
    """Run every job, in order. ``events=False`` drops event logs to keep large analyses light.

    ``pool`` (from :func:`worker_pool`) reuses running worker processes instead of starting new ones.
    Raises before running when inputs, arms or participants are invalid, and
    :class:`AnalysisError` when every run failed (the first error is quoted).
    """
    check_positive_int("workers", workers)
    if rounds is not None:
        check_positive_int("rounds", rounds)
    contract = as_contract(source)
    if not jobs:
        return []
    _probe(contract, jobs, participants)
    if (pool is not None or workers > 1) and len(jobs) > 1 and _portable(participants):
        data = contract_source(contract)
        payloads = [(data, job, participants, rounds, events) for job in jobs]
        chunk = max(1, len(jobs) // (workers * 4))
        try:
            if pool is not None:
                results = list(pool.map(_process_job, payloads, chunksize=chunk))
            else:
                with ProcessPoolExecutor(max_workers=workers) as own:
                    results = list(own.map(_process_job, payloads, chunksize=chunk))
        except BrokenProcessPool:  # a worker died (out of memory, killed): finish here instead
            results = [execute_job(contract, job, participants, rounds, events) for job in jobs]
    elif workers > 1 and len(jobs) > 1:
        with ThreadPoolExecutor(max_workers=workers) as threads:
            results = list(threads.map(lambda job: execute_job(contract, job, participants, rounds, events), jobs))
    else:
        results = [execute_job(contract, job, participants, rounds, events) for job in jobs]
    if all(r.status == "failed" for r in results):
        raise AnalysisError(f"all {len(results)} run(s) failed; first error: {results[0].error}")
    return results


def resolve_measure(contract: Contract, name: str) -> Tuple[str, str]:
    """``(section, key)`` for a measure name: ``outputs.x``, ``metrics.x``, or a bare name (output first)."""
    section, dot, key = name.partition(".")
    if dot and section in ("outputs", "metrics"):
        names = contract.outputs if section == "outputs" else contract.metrics
        if key in names:
            return section, key
        raise ValueError(f"'{key}' is not a declared {section[:-1]} (declared: {', '.join(names) or 'none'})")
    if name in contract.outputs:
        return "outputs", name
    if name in contract.metrics:
        return "metrics", name
    raise ValueError(f"'{name}' is neither an output nor a metric (outputs: {', '.join(contract.outputs) or 'none'}; "
                     f"metrics: {', '.join(contract.metrics) or 'none'})")


def raw_value(result: RunResult, measure: Tuple[str, str]) -> Any:
    section, key = measure
    return (result.outputs if section == "outputs" else result.metrics).get(key)


def value(result: RunResult, measure: Tuple[str, str]) -> Optional[float]:
    """The measure as a number (yes/no as 1/0); ``None`` for failed runs and non-numeric values."""
    if result.status == "failed":
        return None
    return numeric(raw_value(result, measure))


def series(result: RunResult, name: str) -> List[float]:
    """A metric's per-round history as numbers (non-numeric rounds are skipped)."""
    values = (numeric(v) for v in result.series.get(name, []))
    return [v for v in values if v is not None]


def numeric_measures(contract: Contract, results: Sequence[RunResult]) -> List[str]:
    """Outputs that hold a number or yes/no in at least one completed run, in declaration order."""
    out = []
    for name in contract.outputs:
        if any(value(r, ("outputs", name)) is not None for r in results):
            out.append(name)
    return out


def summarize_failures(results: Sequence[RunResult]) -> Optional[str]:
    failed = [r for r in results if r.status == "failed"]
    if not failed:
        return None
    return f"{len(failed)} of {len(results)} run(s) failed (first: {failed[0].error})"


def describe_inputs(inputs: Mapping[str, Any]) -> str:
    return ", ".join(f"{k}={_short(v)}" for k, v in inputs.items()) or "defaults"


def _short(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    text = json.dumps(v, default=str)
    return text if len(text) <= 40 else text[:37] + "…"


def jobs_for(cells: Sequence[Tuple[Mapping[str, Any], Optional[str]]], seeds: Sequence[int]) -> List[Job]:
    """Every cell × every seed: the common-random-numbers grid."""
    return [Job(dict(inputs), arm, s, {"cell": c, "run": i}) for c, (inputs, arm) in enumerate(cells)
            for i, s in enumerate(seeds)]


def by_cell(jobs: Sequence[Job], results: Sequence[RunResult], cells: int) -> List[List[RunResult]]:
    grouped: List[List[RunResult]] = [[] for _ in range(cells)]
    for job, result in zip(jobs, results):
        grouped[job.tags["cell"]].append(result)
    return grouped


def coerce_input(contract: Contract, name: str, raw: float) -> Any:
    """A sampled number as the input's declared type (whole numbers rounded for ``int``)."""
    spec = input_spec(contract, name)
    if spec.type == "int":
        return int(round(raw))
    return float(raw)


def bounds(contract: Contract, name: str, given: Optional[Mapping[str, Any]] = None) -> Tuple[float, float]:
    """``(low, high)`` from ``given`` (``{"low", "high"}``) or the input's declared ``min``/``max``."""
    spec = input_spec(contract, name)
    if spec.type not in ("number", "int"):
        raise ValueError(f"input '{name}' is {spec.type}; only number and int inputs have a range")
    low = (given or {}).get("low", spec.min)
    high = (given or {}).get("high", spec.max)
    if low is None or high is None:
        raise ValueError(f"input '{name}' needs a range: give {{'low': …, 'high': …}} (it declares no min/max)")
    low, high = float(low), float(high)
    if not low < high:
        raise ValueError(f"input '{name}': low must be below high, got {low} and {high}")
    return low, high
