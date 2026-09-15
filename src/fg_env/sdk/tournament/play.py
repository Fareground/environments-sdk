"""Tournaments: entrants take a contract's seats in rotation on duplicate seeds, then are rated and ranked."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..analysis.runner import AnalysisError, check_positive_int, run_seeds
from ..analysis.stats import estimate
from ..api import ContractLike, default_data_dir, load, parse
from ..experiment import Job, run_jobs, worker_pool
from ..measure import RunResult
from .result import TournamentResult
from .scoring import ScoreSpec, SeatScorer
from .seating import PAIRINGS, Seating, rotations, schedule, swiss_round
from .standings import RATINGS, Ledger, outcomes

__all__ = ["tournament"]


def tournament(contract: ContractLike, entrants: Mapping[str, Any], *, seats: Optional[Sequence[str]] = None,
               pairing: str = "round_robin", games: int = 1, score: ScoreSpec = None, rating: str = "elo",
               swiss_rounds: Optional[int] = None, others: Any = None, inputs: Optional[Mapping[str, Any]] = None,
               arm: Optional[str] = None, rounds: Optional[int] = None, seed: int = 0, workers: int = 1,
               data_dir: Any = None) -> TournamentResult:
    """Play ``entrants`` (``{name: participant}``) against each other in the contract's ``seats``.

    ``seats`` are agent entity ids (default: every agent the contract starts with). ``pairing``:
    ``round_robin`` (every group of entrants, rotated so each sits in every seat equally), ``all_play_all``
    (every group in every seat order) or ``swiss`` (``swiss_rounds`` rounds, default ⌈log₂ entrants⌉, of
    tables drawn by points while avoiding rematches; an entrant left over sits out with a bye worth a win).

    Duplicate deals: every seating plays ``games`` games and game *g* uses the same seed at every table
    and in every rotation (the seeds of :func:`fg_env.experiment`). The world's chance is drawn from the
    seed, so the same cards, dice and events meet each entrant in each seat, and luck cancels out.

    ``score`` ranks a game's seats, higher is better: the winner (default), an output (a map of seat →
    number, or a winner), an expression over ``$outputs``, ``$metrics``, ``$winner``, ``$seat`` and
    ``$seat_name``, or ``fn(result, seat_id)``. Agents without a seat play ``others`` (default: their
    type's policy, else random).

    Standings are ranked by ``rating``: ``elo`` (maximum-likelihood, with 95% intervals) or ``glicko2``;
    both are reported, with win/draw/loss, points and score means. ``evaluation`` adds the Nash average,
    α-Rank and a Schulze vote, which stay meaningful when skill is not transitive; ``returns`` gives every
    entrant's score in every seat, and each standing's ``cost`` its turns, calls, invalid calls, timeouts,
    undone turns and model tokens. A callable entrant is shared by all its games: with ``workers > 1`` those run in threads at once.
    """
    names = _entrant_names(entrants)
    check_positive_int("games", games)
    check_positive_int("workers", workers)
    if rounds is not None:
        check_positive_int("rounds", rounds)
    if pairing not in PAIRINGS:
        raise ValueError(f"pairing must be {', '.join(PAIRINGS)}, got {pairing!r}")
    if rating not in RATINGS:
        raise ValueError(f"rating must be {' or '.join(RATINGS)}, got {rating!r}")
    if swiss_rounds is not None and pairing != "swiss":
        raise ValueError("swiss_rounds applies only to pairing='swiss'")
    folder = default_data_dir(contract, data_dir)
    parsed = parse(contract)
    fixed = dict(inputs or {})
    probe = load(parsed, inputs=fixed, seed=0, arm=arm, data_dir=folder)
    agents = {e.id: e.name for e in probe.world.entities.values() if probe.contract.is_agent(e.entity_type)}
    seat_ids = _seat_ids(seats, agents)
    if len(names) < len(seat_ids):
        raise ValueError(f"{len(seat_ids)} seats need at least {len(seat_ids)} entrants, got {len(names)}; "
                         "pass seats=[...] to seat entrants in fewer of the agents")
    for name in names:
        try:
            probe.driver.bind({seat_ids[0]: entrants[name]})
        except (TypeError, ValueError) as exc:
            raise ValueError(f"entrant '{name}': {exc}") from None
    if others is not None:
        probe.driver.bind({"*": others})
    scorer = SeatScorer(probe.contract, score, seat_ids, agents)
    seeds = run_seeds(seed, games)
    ledger = Ledger(names, seat_ids)
    records: List[Dict[str, Any]] = []
    runs: List[RunResult] = []

    def play(round_index: int, seatings: Sequence[Seating], pool: Any) -> None:
        jobs = [Job(fixed, arm, seeds[g], {"round": round_index, "seating": s, "game": g},
                    _participants(seat_ids, seating, entrants, others))
                for s, seating in enumerate(seatings) for g in range(games)]
        results = run_jobs(parsed, jobs, rounds=rounds, workers=workers, events=False, pool=pool, data_dir=folder)
        batch = [_record(job, result, seat_ids, seatings[job.tags["seating"]], scorer) for job, result in zip(jobs, results)]
        ledger.record_round(batch)
        records.extend(batch)
        runs.extend(results)

    everyone = {**{name: entrants[name] for name in names}, **({"*": others} if others is not None else {})}
    with worker_pool(workers, everyone) as pool:
        if pairing == "swiss":
            total = check_positive_int("swiss_rounds", swiss_rounds) if swiss_rounds is not None \
                else max(1, math.ceil(math.log2(len(names))))
            for round_index in range(total):
                tables, resting = swiss_round(ledger.ranked(), len(seat_ids), ledger.met, ledger.byes)
                for name in resting:
                    ledger.bye(name)
                    records.append({"round": round_index, "bye": name})
                play(round_index, [seating for table in tables for seating in rotations(table)], pool)
        else:
            play(0, schedule(names, len(seat_ids), pairing), pool)
    notes = _notes(records, score)
    return TournamentResult(parsed.name, pairing, rating, scorer.describe(), seat_ids, names, games,
                            ledger.standings(rating), ledger.head_to_head, ledger.returns(),
                            {seat: estimate(points).to_dict() for seat, points in ledger.seat_points.items()},
                            ledger.evaluation(), records, runs, notes)


def _entrant_names(entrants: Mapping[str, Any]) -> List[str]:
    if not isinstance(entrants, Mapping):
        raise ValueError("entrants must be a mapping of name → participant, like {'greedy': 'policy:greedy'}")
    names = list(entrants)
    if len(names) < 2:
        raise ValueError(f"a tournament needs at least two entrants, got {len(names)}")
    for name in names:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"entrant names must be non-empty text, got {name!r}")
    return names


def _seat_ids(seats: Optional[Sequence[str]], agents: Mapping[str, str]) -> List[str]:
    chosen = list(agents) if seats is None else seats
    if isinstance(chosen, str) or not isinstance(chosen, Sequence):
        raise ValueError(f"seats must be a list of agent entity ids, got {seats!r}")
    listed = list(chosen)
    for seat in listed:
        if seat not in agents:
            shown = ", ".join(list(agents)[:20]) or "none"
            raise ValueError(f"seat {seat!r} is not an agent entity the contract starts with (agents: {shown})")
    if len(set(listed)) != len(listed):
        raise ValueError(f"seats are listed more than once: {listed}")
    if len(listed) < 2:
        raise ValueError(f"a tournament needs at least two seats, got {len(listed)}; to compare agents acting alone, "
                         "use fg_env.experiment")
    return listed


def _participants(seat_ids: Sequence[str], seating: Seating, entrants: Mapping[str, Any], others: Any) -> Dict[str, Any]:
    chosen = {seat: entrants[name] for seat, name in zip(seat_ids, seating)}
    return {**chosen, "*": others} if others is not None else chosen


def _record(job: Job, result: RunResult, seat_ids: Sequence[str], seating: Seating, scorer: SeatScorer) -> Dict[str, Any]:
    record = {"round": job.tags["round"], "game": job.tags["game"], "seed": job.seed,
              "seating": dict(zip(seat_ids, seating)), "status": result.status,
              "stats": {seat: result.agent_stats[seat] for seat in seat_ids if seat in result.agent_stats}}
    if result.status == "failed":
        return {**record, "scores": None, "outcomes": None, "note": result.error}
    scores, reason = scorer(result)
    return {**record, "scores": scores, "outcomes": outcomes(scores) if scores else None, "note": reason}


def _notes(records: Sequence[Mapping[str, Any]], score: ScoreSpec) -> List[str]:
    games = [r for r in records if "seating" in r]
    failed = [r for r in games if r["status"] == "failed"]
    unscored = [r for r in games if r["status"] != "failed" and r["scores"] is None]
    if len(failed) == len(games):
        raise AnalysisError(f"all {len(games)} game(s) failed; first error: {failed[0]['note']}")
    notes = []
    if failed:
        notes.append(f"{len(failed)} game(s) failed and were left out (first: {failed[0]['note']})")
    if unscored:
        notes.append(f"{len(unscored)} game(s) could not be scored and were left out (first: {unscored[0]['note']})")
    scored = [r for r in games if r["scores"] is not None]
    if score is None and scored and all(len(set(r["scores"].values())) == 1 for r in scored):
        notes.append("no game named a winner among the seats, so every game counts as a draw; "
                     "pass score= (an output, an expression over $seat, or a function) to rank seats")
    return notes
