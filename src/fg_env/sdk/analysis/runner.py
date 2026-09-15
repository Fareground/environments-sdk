"""Running many seeded jobs for an analysis, and reading numbers out of run results.

Every analysis reduces to a list of jobs (inputs, arm, seed) against one contract. They run in
one pool (processes when participants are given by name, threads otherwise), a run that fails
is kept as ``status="failed"`` and never stops the others, and seeds are derived exactly as
:func:`fg_env.experiment` derives them, so run *i* of an analysis equals run *i* of an
experiment with the same base seed (common random numbers everywhere).
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from ..api import ContractLike, DataDir, located, parse
from ..contract import Contract
from ..experiment import Job, failed_run, run_job, worker_pool
from ..experiment import run_jobs as _run_jobs
from ..measure import RunResult
from ..seeds import SeedTree
from .stats import numeric

__all__ = ["Job", "AnalysisError", "run_seeds", "run_jobs", "resolve_measure", "value", "raw_value", "series",
           "numeric_measures", "check_positive_int", "as_contract", "input_spec", "summarize_failures", "execute_job", "failed_result", "worker_pool",
           "describe_inputs", "jobs_for", "by_cell", "coerce_input", "bounds"]


class AnalysisError(ValueError):
    """An analysis cannot produce a result: every run failed, or a request is impossible."""


def check_positive_int(name: str, value: Any, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be a whole number ≥ {minimum}, got {value!r}")
    return value


def as_contract(source: ContractLike, data_dir: DataDir = None) -> Contract:
    """The parsed contract, reading its data files from ``data_dir`` (default: the contract file's folder)."""
    return located(source, data_dir) if isinstance(source, Contract) else parse(source, data_dir)


def run_seeds(seed: int, count: int, start: int = 0) -> List[int]:
    """Seeds for runs ``start … start+count-1`` of base ``seed`` (the same as ``experiment``)."""
    tree = SeedTree(seed)
    return [tree.derive("run", i) for i in range(start, start + count)]


def input_spec(contract: Contract, name: str) -> Any:
    if name not in contract.inputs:
        raise ValueError(f"'{name}' is not a declared input (inputs: {', '.join(contract.inputs) or 'none'})")
    return contract.inputs[name]


def run_jobs(source: ContractLike, jobs: Sequence[Job], *, participants: Any = None, rounds: Optional[int] = None,
             workers: int = 1, events: bool = False, pool: Optional[ProcessPoolExecutor] = None,
             hosts: Any = None) -> List[RunResult]:
    """:func:`fg_env.sdk.experiment.run_jobs` for analyses: event logs dropped by default, and
    :class:`AnalysisError` when every run failed (the first error is quoted)."""
    check_positive_int("workers", workers)
    if rounds is not None:
        check_positive_int("rounds", rounds)
    results = _run_jobs(as_contract(source), jobs, participants=participants, rounds=rounds, workers=workers,
                        events=events, pool=pool, hosts=hosts)
    if results and all(r.status == "failed" for r in results):
        raise AnalysisError(f"all {len(results)} run(s) failed; first error: {results[0].error}")
    return results

#: Names analyses already use.
execute_job = run_job
failed_result = failed_run


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
