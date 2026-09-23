"""Playing an evaluation: each scenario, mode and seed run twice — focal in the drawn seats, then the baseline."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..analysis.runner import AnalysisError, check_positive_int, run_seeds
from ..budget import Budget
from ..experiment import Job, run_jobs, worker_pool
from ..measure import RunResult
from ..seeds import SeedTree
from ..tournament.scoring import ScoreSpec
from .result import COST_FIELDS, EvaluationResult, summarize
from .suite import Scenario, scenarios

__all__ = ["evaluate"]


def evaluate(suite: Any, *, focal: Any, background: Any = None, baseline: Any = None, seats: Any = None,
             score: ScoreSpec = None, modes: Optional[Mapping[str, float]] = None,
             inputs: Optional[Mapping[str, Any]] = None, arm: Optional[str] = None, runs: int = 10,
             rounds: Optional[int] = None, budget: Optional[Mapping[str, Any]] = None, seed: int = 0,
             workers: int = 1, exposures: bool = False) -> EvaluationResult:
    """How ``focal`` does among ``background`` agents, compared with ``baseline`` in the same seats on the same seeds.

    ``suite`` is a contract, a list of scenarios, or a suite file (see :mod:`fg_env.sdk.evaluate.suite`); the other
    arguments are defaults for scenarios that leave them out. ``seats`` are the agents the focal participant may
    take (ids, or a type; default every starting agent). ``modes`` maps a mode name to the share of those seats the
    focal participant takes (``{"resident": 0.75, "visitor": 0.25}``; default ``{"all": 1.0}``); which seats is
    drawn from the seed, so every candidate evaluated with the same seed meets the same draw. ``background`` plays
    every other agent (default: its type's policy, else random); ``baseline`` plays the focal seats in the paired
    runs (default: the background). ``score`` scores each seat as in :func:`fg_env.rl.tournament`: by default the
    returns the contract's ``game`` section declares, else the winner; or an output, an expression over ``$seat``,
    or ``fn(result, seat)``.

    Run *i* of every scenario uses the seeds of :func:`fg_env.experiment`. A focal run's score is the mean over its
    focal seats (per focal agent), and its difference is that minus the baseline run's score over the same seats.
    ``budget`` caps each run on its own; ``exposures=True`` records what agents saw in every run. ``results`` keeps
    every run: pair *i* is ``results[2i]`` (focal) and ``results[2i + 1]`` (baseline). Callable participants are
    shared by all their runs; with ``workers > 1`` runs go to threads, or to processes when every participant is
    given by name.
    """
    check_positive_int("runs", runs)
    check_positive_int("workers", workers)
    if rounds is not None:
        check_positive_int("rounds", rounds)
    if focal is None:
        raise ValueError("focal is the participant being evaluated; give one (a callable, 'random', 'policy:<name>')")
    if budget is not None:
        Budget.parse(budget)
    defaults = {"seats": seats, "score": score, "background": background, "baseline": baseline, "modes": modes,
                "inputs": inputs, "arm": arm}
    cases = scenarios(suite, defaults, focal)
    seeds = run_seeds(seed, runs)
    named = {str(i): p for i, p in enumerate([focal] + [p for c in cases for p in (c.background, c.baseline)])
             if p is not None}
    pairs: List[Dict[str, Any]] = []
    played: List[RunResult] = []
    with worker_pool(workers, named) as pool:
        for case in cases:
            jobs = _jobs(case, focal, seeds, seed)
            results = run_jobs(case.contract, jobs, rounds=rounds, workers=workers, events=False, pool=pool,
                               data_dir=case.data_dir, budget=budget, exposures=exposures)
            played.extend(results)
            for start in range(0, len(jobs), 2):
                pairs.append(_pair(case, jobs[start], results[start], results[start + 1]))
    scored = [pair for pair in pairs if pair["difference"] is not None]
    if not scored:
        raise AnalysisError(f"no run pair could be scored; first problem: {pairs[0]['note']}")
    return summarize(cases, pairs, focal=_label(focal), runs=runs, seed=seed, results=played)


def _jobs(case: Scenario, focal: Any, seeds: Sequence[int], seed: int) -> List[Job]:
    jobs = []
    for mode in case.modes:
        count = case.focal_seats(mode)
        for run, run_seed in enumerate(seeds):
            drawn = SeedTree(seed).rng("evaluate", case.name, mode, run).sample(range(len(case.seats)), count)
            chosen = tuple(case.seats[i] for i in sorted(drawn))
            tags = {"mode": mode, "run": run, "seats": chosen}
            jobs.append(Job(case.inputs, case.arm, run_seed, tags, _seating(case, chosen, focal)))
            jobs.append(Job(case.inputs, case.arm, run_seed, tags, _seating(case, chosen, case.stand_in)))
    return jobs


def _seating(case: Scenario, chosen: Tuple[str, ...], player: Any) -> Dict[str, Any]:
    spec: Dict[str, Any] = {"*": case.background} if case.background is not None else {}
    for seat in case.seats:
        occupant = player if seat in chosen else case.background
        if occupant is not None:
            spec[seat] = occupant
    return spec


def _pair(case: Scenario, job: Job, focal: RunResult, baseline: RunResult) -> Dict[str, Any]:
    chosen: Tuple[str, ...] = job.tags["seats"]
    record: Dict[str, Any] = {"scenario": case.name, "mode": job.tags["mode"], "run": job.tags["run"], "seed": job.seed,
                              "seats": list(chosen), "status": {"focal": focal.status, "baseline": baseline.status},
                              "cost": {"focal": _bill(focal, chosen), "baseline": _bill(baseline, chosen)}}
    scores, notes = [], []
    for side, result in (("focal", focal), ("baseline", baseline)):
        if result.status == "failed":
            notes.append(f"the {side} run failed: {result.error}")
            continue
        seat_scores, reason = case.scorer(result)
        if seat_scores is None:
            notes.append(f"the {side} run could not be scored: {reason}")
            continue
        scores.append({seat: seat_scores[seat] for seat in chosen})
    if len(scores) < 2:
        return {**record, "focal": None, "baseline": None, "difference": None, "seat_scores": None,
                "note": "; ".join(notes)}
    mine, theirs = (sum(side.values()) / len(side) for side in scores)
    return {**record, "focal": mine, "baseline": theirs, "difference": mine - theirs, "seat_scores": scores[0],
            "note": ""}


def _bill(result: RunResult, seats: Sequence[str]) -> Dict[str, int]:
    return {key: sum(result.agent_stats.get(seat, {}).get(key, 0) for seat in seats) for key in COST_FIELDS}


def _label(participant: Any) -> str:
    if isinstance(participant, str):
        return participant
    return getattr(participant, "__name__", None) or repr(participant)
