"""run_jobs: one batch runner for experiments and analyses; the same seeds give the same runs in process and in workers.
"""
from pathlib import Path

import fg_env
from fg_env.analysis.runner import run_seeds
from fg_env.experiments.experiment import Job, run_jobs

EXAMPLE = Path(__file__).parents[1] / "examples" / "contracts" / "lemonade_stand.json"


def test_run_jobs_matches_experiment_in_process_threads_and_workers():
    arms = list(fg_env.load(EXAMPLE, seed=0).contract.arms)
    arm = arms[0] if arms else None
    jobs = [Job({}, arm, s) for s in run_seeds(4, 3)]
    serial = run_jobs(EXAMPLE, jobs, participants="random", rounds=3)
    processes = run_jobs(EXAMPLE, jobs, participants="random", rounds=3, workers=2)
    threads = run_jobs(EXAMPLE, jobs, participants_for=lambda job: "random", rounds=3, workers=2)
    experiment = fg_env.experiment(EXAMPLE, runs=3, seed=4, participants="random", rounds=3,
                                   arms=[arm] if arm else None)
    runs = next(iter(experiment.arms.values())).runs
    assert all(r.status != "failed" for r in serial), [r.error for r in serial]
    assert ([r.outputs for r in serial] == [r.outputs for r in processes] == [r.outputs for r in threads]
            == [r.outputs for r in runs])


def test_a_job_that_fails_on_its_own_is_kept_and_events_can_be_dropped():
    jobs = [Job({}, None, 1), Job({}, None, 2)]
    results = run_jobs(EXAMPLE, jobs, participants_for=lambda job: 1 / 0 if job.seed == 2 else "random", rounds=2,
                       events=False)
    assert results[0].status != "failed" and results[0].events == []
    assert results[1].status == "failed" and "ZeroDivisionError" in results[1].error
