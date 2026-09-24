"""Batches in worker processes: one pool kept for the process, short batches kept here, many short runs per round trip
— and the same runs, failures, budgets and exposures whichever way a batch goes."""
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import fg_env
from fg_env.analysis.runner import run_seeds
from fg_env.api import contract_source
from fg_env.experiments import workers as pools
from fg_env.experiments.experiment import Job, _Batch, _run_chunk, run_job, run_jobs, worker_pool

LEMONADE = Path(__file__).parents[1] / "examples" / "contracts" / "lemonade_stand.json"

#: Runs whose divisor is zero fail on their own at run time; the others complete.
FRAGILE = {
    "name": "Fragile",
    "inputs": {"divisor": {"type": "number", "default": 1}},
    "clock": {"rounds": 3},
    "types": {"thing": {"props": {"v": 0}}},
    "world": {"level": 0},
    "events": [{"do": ["$world.level += $normal(0, 1) / $inputs.divisor"]}],
    "outputs": {"level": {"expr": "$world.level", "type": "number"}},
}


@pytest.fixture
def always_in_workers(monkeypatch):
    """Every batch goes to workers, in chunks of two, however short its runs."""
    monkeypatch.setattr(pools, "chunk_size", lambda jobs, workers, seconds, started: 2)


@pytest.fixture
def fresh_costs(monkeypatch):
    """Nothing measured yet in this process: a pool takes a second to start and no batch has run here waiting for one.
    """
    monkeypatch.setattr(pools._kept, "start_seconds", 1.0)
    monkeypatch.setattr(pools._kept, "job_seconds", {})
    monkeypatch.setattr(pools._kept, "ran_here", {})


def _reference(source, jobs, **options):
    """The batch run one job at a time in this process, as the batch runner did before worker processes."""
    contract = fg_env.parse(source)
    return [run_job(contract, job, options.get("participants"), options.get("rounds"), options.get("events", True),
                    None, options.get("budget"), options.get("exposures", False)) for job in jobs]


def _without_wall_clock(results):
    """Results with the seconds each run's budget measured set aside: wall-clock time differs between any two runs."""
    return [replace(r, budget={**r.budget, "used": {**r.budget["used"], "seconds": None}}) for r in results]


def test_every_worker_count_gives_the_same_runs_budgets_and_exposures_as_one_run_at_a_time(always_in_workers):
    seeds = run_seeds(7, 5)
    jobs = [Job({}, arm, s) for arm in [None, *fg_env.parse(LEMONADE).arms] for s in seeds]
    options = dict(participants="random", rounds=4, budget={"calls": 6, "on_exhaust": "end"}, exposures=True)
    expected = _without_wall_clock(_reference(LEMONADE, jobs, **options))
    assert all(r.status != "failed" for r in expected) and any(r.exposures for r in expected)
    assert any(r.budget["exhausted"] for r in expected)
    for workers in (1, 2, 3):
        assert _without_wall_clock(run_jobs(LEMONADE, jobs, workers=workers, **options)) == expected


def test_a_run_that_fails_on_its_own_fails_the_same_way_in_workers_and_the_rest_complete(always_in_workers):
    jobs = [Job({"divisor": 0 if i % 3 == 0 else 1}, None, s) for i, s in enumerate(run_seeds(3, 6))]
    expected = _reference(FRAGILE, jobs, events=False)
    in_workers = run_jobs(FRAGILE, jobs, workers=2, events=False)
    assert in_workers == expected
    assert [r.status == "failed" for r in in_workers] == [i % 3 == 0 for i in range(6)]


def test_a_batch_too_short_for_workers_runs_here_without_starting_a_pool(fresh_costs):
    pools.shutdown_workers()
    jobs = [Job({}, None, s) for s in run_seeds(1, 12)]
    assert run_jobs(LEMONADE, jobs, participants="random", rounds=2, workers=4) == \
        _reference(LEMONADE, jobs, participants="random", rounds=2)
    assert pools._kept.pools == {}


def test_batches_share_one_kept_pool_until_it_is_shut_down(always_in_workers):
    jobs = [Job({}, None, s) for s in run_seeds(2, 4)]
    with worker_pool(2) as first:
        run_jobs(LEMONADE, jobs, workers=2, pool=first, rounds=2)
        kept = first.executor()
    with worker_pool(2) as second:
        assert second.started and second.executor() is kept
    pools.shutdown_workers()
    assert not pools.Workers(2).started


def test_without_kept_workers_a_block_starts_its_own_pool_and_closes_it(monkeypatch, always_in_workers):
    monkeypatch.setenv(pools.KEEP_WORKERS, "0")
    jobs = [Job({}, None, s) for s in run_seeds(2, 4)]
    with worker_pool(2) as pool:
        assert run_jobs(LEMONADE, jobs, workers=2, pool=pool, rounds=2) == _reference(LEMONADE, jobs, rounds=2)
        own = pool.executor()
        assert own is not pools._kept.pools.get(2)
    with pytest.raises(RuntimeError):
        own.submit(abs, -1)


def test_a_pool_whose_workers_died_finishes_the_batch_here_and_the_next_batch_starts_a_new_one(always_in_workers):
    jobs = [Job({}, None, s) for s in run_seeds(5, 4)]
    handle = pools.Workers(2)
    broken = handle.executor()
    broken.submit(abs, -1).result()
    for process in list(broken._processes.values()):
        process.kill()
        process.join()
    assert run_jobs(LEMONADE, jobs, workers=2, pool=handle, rounds=2) == _reference(LEMONADE, jobs, rounds=2)
    assert handle.executor() is not broken
    assert run_jobs(LEMONADE, jobs, workers=2, pool=handle, rounds=2) == _reference(LEMONADE, jobs, rounds=2)


def test_many_short_runs_share_a_round_trip_but_every_worker_gets_several_chunks():
    assert pools.chunk_size(1000, 4, 0.0001, started=True) == 1000 // 16
    assert pools.chunk_size(40, 4, 0.5, started=True) == 1
    assert pools.chunk_size(40, 4, None, started=True) == 2


def test_a_batch_goes_to_workers_only_when_they_would_finish_it_sooner(fresh_costs):
    assert pools.chunk_size(10, 8, 0.001, started=False) == 0
    assert pools.chunk_size(2, 8, 0.0005, started=True) == 0
    assert pools.chunk_size(100, 8, 0.5, started=False) >= 1


def test_short_batches_kept_here_start_the_workers_once_their_time_would_have_paid_for_them(fresh_costs):
    assert pools.chunk_size(4, 4, 0.2, started=False) == 0
    pools.record_ran_here(4, 0.3)
    assert pools.chunk_size(4, 4, 0.2, started=False) == 0
    pools.record_ran_here(4, 0.6)
    assert pools.chunk_size(4, 4, 0.2, started=False) == 1
    assert pools.chunk_size(4, 8, 0.2, started=False) == 0  # a pool of another size has not been waited for


def test_a_worker_parses_a_contract_once_however_many_chunks_of_it_arrive(monkeypatch):
    parsed = []
    real = pools.parse
    monkeypatch.setattr(pools, "parse", lambda data, folder: parsed.append(folder) or real(data, folder))
    data = contract_source(real(FRAGILE))
    batch = _Batch(pools.contract_key(data, None), data, None, None, None, False, None, False)
    jobs = [(Job({}, None, s), None) for s in run_seeds(4, 4)]
    first, _ = _run_chunk(batch, jobs[:2])
    second, _ = _run_chunk(batch, jobs[2:])
    assert len(parsed) == 1 and first + second == _reference(FRAGILE, [job for job, _ in jobs], events=False)


def test_a_kept_worker_reads_a_relative_data_folder_where_each_batch_started(tmp_path, monkeypatch, always_in_workers):
    store = Path(__file__).parent / "fixtures" / "store" / "store.json"
    contract = json.loads(store.read_text())
    jobs = [Job({}, None, s) for s in run_seeds(6, 4)]
    for place in ("one", "two"):
        shutil.copytree(store.parent / "data", tmp_path / place / "shared" / "data")
    monkeypatch.chdir(tmp_path / "one")
    first = run_jobs(contract, jobs, workers=2, data_dir="shared", events=False)
    monkeypatch.chdir(tmp_path / "two")
    shutil.rmtree(tmp_path / "one")  # a worker still reading where the first batch started would find nothing
    second = run_jobs(contract, jobs, workers=2, data_dir="shared", events=False)
    assert all(r.status == "completed" for r in first + second)
    assert second == first


_KILLED_PARENT = """
import multiprocessing, os, signal
from fg_env.experiments.workers import Workers
Workers(2).executor().submit(os.getpid).result()
print(" ".join(str(child.pid) for child in multiprocessing.active_children()), flush=True)
os.kill(os.getpid(), signal.SIGKILL)
"""


def test_workers_exit_when_the_process_that_started_them_is_killed():
    """A killed parent (a test run stopped with SIGKILL) leaves no idle worker processes behind."""
    import os
    import subprocess
    import sys
    import time

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    parent = subprocess.Popen([sys.executable, "-c", _KILLED_PARENT], stdout=subprocess.PIPE, text=True, env=env)
    with parent:  # the workers inherit its output pipe, so only the first line is read
        pids = [int(pid) for pid in parent.stdout.readline().split()]
        parent.wait(timeout=120)
    assert pids, "the parent started no workers"

    def alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    deadline = time.monotonic() + 30
    while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.2)
    left = [pid for pid in pids if alive(pid)]
    for pid in left:
        os.kill(pid, 9)
    assert not left, f"worker processes {left} outlived their killed parent"
