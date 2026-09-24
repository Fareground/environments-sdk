"""Worker processes for batches of runs.

Starting a worker process costs more than many short runs (a fresh interpreter imports the SDK), so batches share
worker pools for the life of the process: started on first use, kept for the next batch, closed at exit or by
:func:`shutdown_workers`. ``FG_ENV_KEEP_WORKERS=0`` gives every batch (or ``worker_pool`` block) its own pool
instead, closed when it ends.

Where a batch runs is decided by what its runs are measured to cost, never by what they return: a job gets the same
result in this process as in a worker. A batch that would finish before workers could start stays in this process
until the time batches have spent here would have paid for starting them (many short batches then start the pool once
instead of each staying slow); the rest go to workers in chunks, each carrying enough run time to be worth its round
trip, and each worker parses a contract once however many chunks of it arrive.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Union

from ..api import parse
from ..contract import Contract

__all__ = ["KEEP_WORKERS", "Workers", "Pool", "shutdown_workers", "chunk_size", "contract_key", "cached_contract",
           "job_seconds", "record_job_seconds", "record_ran_here", "run_chunks"]

#: Environment variable: ``0`` gives every batch its own worker processes instead of keeping pools for the process.
KEEP_WORKERS = "FG_ENV_KEEP_WORKERS"
#: Wall seconds assumed to start a pool until one has been timed in this process (each worker imports the SDK).
_ASSUMED_START_SECONDS = 1.0
#: Wall seconds a chunk's round trip adds to its runs: pickling both ways, the queue, waking a worker.
_ROUND_TRIP_SECONDS = 0.002
#: A chunk carries about this much run time, so its round trip is a small share of it …
_CHUNK_SECONDS = 0.1
#: … but a batch is cut into at least this many chunks per worker, so faster workers take on more of it.
_CHUNKS_PER_WORKER = 4
#: Parsed contracts a worker keeps, least recently used dropped first.
_CACHED_CONTRACTS = 8
#: Weight of the newest measurement in a running estimate of a cost.
_SMOOTHING = 0.5

Pool = Union["Workers", ProcessPoolExecutor]


class _Kept:
    """The pools kept for this process, by size, and what batches have been measured to cost here."""

    def __init__(self) -> None:
        #: Re-entrant: a pool's start is timed by callbacks that may run at once, in the thread starting it.
        self.lock = threading.RLock()
        self.pools: dict[int, ProcessPoolExecutor] = {}
        self.start_seconds = _ASSUMED_START_SECONDS
        self.job_seconds: dict[str, float] = {}
        #: Seconds batches ran here, by pool size, because that pool was not running (reset when one starts).
        self.ran_here: dict[int, float] = {}


_kept = _Kept()


def _smoothed(old: float | None, new: float) -> float:
    return new if old is None else (1 - _SMOOTHING) * old + _SMOOTHING * new


#: Seconds between a worker's checks that the process that started it is still alive.
_PARENT_CHECK_SECONDS = 1.0


def _exit_with(parent: int) -> None:
    """Run in each new worker: exit once ``parent`` is gone. A parent killed outright (SIGKILL, a stopped test run)
    never shuts its pool down, and an idle worker waits on its queue forever: every worker holds that queue's writing
    end too, so it never reads an end of file."""
    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(_PARENT_CHECK_SECONDS)
        os._exit(0)

    threading.Thread(target=watch, name="fg-env-parent-watch", daemon=True).start()


def _ready() -> None:
    """Sent to each new worker: returns once the worker has imported the SDK, which times a pool's start."""


def _new_pool(size: int) -> ProcessPoolExecutor:
    """A started pool of ``size`` workers; the caller holds ``_kept.lock`` (its start is timed once every worker is up).
    """
    _kept.ran_here.pop(size, None)
    pool = ProcessPoolExecutor(max_workers=size, initializer=_exit_with, initargs=(os.getpid(),))
    started = time.perf_counter()
    waiting = [pool.submit(_ready) for _ in range(size)]
    remaining = [len(waiting)]

    def arrived(_: Any) -> None:
        with _kept.lock:
            remaining[0] -= 1
            if remaining[0] == 0:
                _kept.start_seconds = _smoothed(_kept.start_seconds, time.perf_counter() - started)

    for future in waiting:
        future.add_done_callback(arrived)
    return pool


def _keeping() -> bool:
    return os.environ.get(KEEP_WORKERS, "1") != "0"


class Workers:
    """Worker processes for one or more batches: this process's kept pool of ``size`` workers, a pool of its own when
    pools are not kept, or ``executor`` (a caller's pool, used as it is). Nothing starts until a batch needs it."""

    def __init__(self, size: int, executor: ProcessPoolExecutor | None = None):
        self.size = size
        self._given = executor
        self._own: ProcessPoolExecutor | None = None
        self._keep = executor is None and _keeping()

    @property
    def started(self) -> bool:
        if self._given is not None:
            return True
        if self._keep:
            with _kept.lock:
                return self.size in _kept.pools
        return self._own is not None

    def executor(self) -> ProcessPoolExecutor:
        if self._given is not None:
            return self._given
        if not self._keep:
            if self._own is None:
                with _kept.lock:
                    self._own = _new_pool(self.size)
            return self._own
        with _kept.lock:
            if self.size not in _kept.pools:
                _kept.pools[self.size] = _new_pool(self.size)
            return _kept.pools[self.size]

    def discard(self) -> None:
        """Drop a pool whose workers died, so the next batch starts a new one (a caller's own pool is left to it)."""
        if self._given is not None:
            return
        if self._keep:
            with _kept.lock:
                broken = _kept.pools.pop(self.size, None)
        else:
            broken, self._own = self._own, None
        if broken is not None:
            broken.shutdown(wait=False, cancel_futures=True)

    def close(self) -> None:
        """Close a pool of its own; a kept pool stays for the next batch."""
        if self._own is not None:
            self._own.shutdown()
            self._own = None


def shutdown_workers() -> None:
    """Close every worker pool kept for this process (a later batch starts a new one). Runs at exit."""
    with _kept.lock:
        pools = list(_kept.pools.values())
        _kept.pools.clear()
    for pool in pools:
        pool.shutdown()


atexit.register(shutdown_workers)


def chunk_size(jobs: int, workers: int, seconds_per_job: float | None, started: bool) -> int:
    """Jobs per chunk sent to workers, or ``0`` when the batch would finish sooner in this process.

    Starting workers is charged only what batches have not already spent here waiting for it: once runs kept in this
    process add up to a pool's start, the next batch that can use workers starts them. Without a measured cost the
    batch goes to the workers (only ever asked of workers already running)."""
    most = max(1, jobs // (workers * _CHUNKS_PER_WORKER))
    if seconds_per_job is None:
        return most
    chunk = max(1, min(most, math.ceil(_CHUNK_SECONDS / max(seconds_per_job, 1e-9))))
    here = jobs * seconds_per_job
    with _kept.lock:
        start = 0.0 if started else max(0.0, _kept.start_seconds - _kept.ran_here.get(workers, 0.0))
    there = (here + math.ceil(jobs / chunk) * _ROUND_TRIP_SECONDS) / min(workers, jobs) + start
    return chunk if there < here else 0


def contract_key(data: Mapping[str, Any], folder: str | None) -> str:
    """Names a contract as written together with the folder its data files are read from."""
    text = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(f"{folder}\0{text}".encode()).hexdigest()


_parsed: OrderedDict[str, Contract] = OrderedDict()


def cached_contract(key: str, data: Mapping[str, Any], folder: str | None) -> Contract:
    """The parsed contract for ``key``, parsed on this process's first request for it."""
    found = _parsed.get(key)
    if found is None:
        found = parse(data, folder)
        _parsed[key] = found
        while len(_parsed) > _CACHED_CONTRACTS:
            _parsed.popitem(last=False)
    else:
        _parsed.move_to_end(key)
    return found


def job_seconds(key: str) -> float | None:
    """Seconds one job of this contract has been measured to take in this process's batches (``None``: not yet)."""
    with _kept.lock:
        return _kept.job_seconds.get(key)


def record_job_seconds(key: str, seconds: float) -> None:
    with _kept.lock:
        _kept.job_seconds[key] = _smoothed(_kept.job_seconds.get(key), seconds)


def record_ran_here(workers: int, seconds: float) -> None:
    """A batch that could have used a pool of ``workers`` ran here for ``seconds`` because that pool was not running."""
    with _kept.lock:
        _kept.ran_here[workers] = _kept.ran_here.get(workers, 0.0) + seconds


def run_chunks(pool: ProcessPoolExecutor, work: Callable[[Any, Sequence[Any]], tuple[list[Any], float]], shared: Any,
               items: Sequence[Any], chunk: int) -> tuple[list[Any], float]:
    """``work(shared, chunk)`` on every ``chunk`` items in workers: the results in order, and the seconds the
    workers reported. A failure cancels the chunks not yet started and is raised."""
    futures = [pool.submit(work, shared, items[start:start + chunk]) for start in range(0, len(items), chunk)]
    try:
        done = [future.result() for future in futures]
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    return [result for results, _ in done for result in results], sum(seconds for _, seconds in done)
