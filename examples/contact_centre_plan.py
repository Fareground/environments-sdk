"""Find the cheapest staffing per half-hour that answers 80% of calls within 20 seconds in every half-hour, validate the
model on the two held-out weeks of history, and print the owner report.

    python examples/contact_centre_plan.py            # searches; writes the plan into the arms and contact_centre/plan.json
    python examples/contact_centre_plan.py --report   # validates, plays every arm and prints the owner report

The plan is a 24-value staffing vector searched by ``fg_env.optimise``: minimise the staffing cost subject to every
half-hour's expected service level reaching 80% (``each centre_service_level_by_interval >= 0.8``) and the day's in
90% of runs, both held with the optimiser's 90% confidence — a day-level service level alone lets a search starve the
quiet half-hours. The search starts from the Erlang C staffing of each half-hour's forecast; every candidate plays the
whole day on the same seeds, with the fitted arrival parameters drawn per run; the finalists are confirmed on new seeds
and the choice checked again on seeds no search saw.

Why not "the worst half-hour keeps 80% in 90% of runs"? A single day's half-hour service level is noisy (40–120 calls),
and the worst of 24 noisy values is far below their average: the Erlang C plan gives every half-hour at least 85% on
average yet its worst half-hour reaches 80% in 13% of runs, and two more agents in every half-hour (every half-hour at
94% on average, about $900 more a day) still only in 57%. That constraint buys service nobody asked for; an expected
level per half-hour, held with confidence, keeps each half-hour at its target at a sane cost.

What the search found (contact_centre/plan.json, 1,018 plans searched before the local search settled): a plan of $8,930
a day, feasible with 90% confidence on its 60 confirmation seeds and again on 40 held-out ones. On 60 further fresh days
it keeps every half-hour's average at 80% or more (23 of the 24 with 90% confidence, the tightest at 80.6%) and the
day's service level at 80% in 98% of them, for $239 a day less than the manager's rule (which runs its tightest
half-hour at 87%). The tightest half-hours are the opening one and 09:00.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

import fg_env
from contact_centre_history import HISTORY, TRAIN_END, by_day, day_inputs
from fg_env.sdk.analysis.optimise_result import OptimisationResult

CONTRACT = Path(__file__).parent / "contracts" / "contact_centre.json"
#: How the recommended plan was found: the optimisation result, kept so the report and tests need not search again.
PLAN = CONTRACT.parent / "contact_centre" / "plan.json"
TARGET, THRESHOLD = 0.8, 20.0
OBJECTIVE = "minimise centre_cost"
#: Every half-hour's expected service level reaches 80%, and the day's in 90% of runs — each with the optimiser's 90%
#: confidence, so the plan is feasible on days the search never saw, not only on the seeds that picked it.
CONSTRAINTS = ["each centre_service_level_by_interval >= 0.8", "centre_service_level >= 0.8 in 90% of runs"]
#: Seeds judging each plan, distinct plans searched, fresh seeds checking the choice, and processes.
RUNS, BUDGET, HOLDOUT_SEEDS, WORKERS = 30, 1200, 40, 8
#: Agents added to every half-hour of the Erlang C plan so the search starts from a plan that meets the constraints
#: and works down (from an infeasible start a local search spends its budget climbing).
START_MARGIN = 1
VALIDATION_RUNS, REPORT_RUNS = 10, 40
#: The arms that play the recommended plan.
PLANNED_ARMS = ("recommended", "outage", "outage_with_callbacks", "callbacks")


def erlang_c_staff(calls: float, aht: float, length: float = 1800.0) -> int:
    """The fewest agents an M/M/c queue needs to answer ``TARGET`` of ``calls`` within ``THRESHOLD`` seconds."""
    load = calls * aht / length
    servers = max(1, math.ceil(load))
    while True:
        if servers > load:
            below = sum(load ** k / math.factorial(k) for k in range(servers))
            top = load ** servers / math.factorial(servers) * servers / (servers - load)
            waits = top / (below + top)
            if 1 - waits * math.exp(-(servers - load) * THRESHOLD / aht) >= TARGET:
                return servers
        servers += 1


def optimise(contract: Dict[str, Any]) -> Any:
    forecast = fg_env.decompose(contract, "calls", data_dir=CONTRACT.parent, inputs={"parameter_uncertainty": 0})
    start = [erlang_c_staff(row["total"], contract["inputs"]["aht_sec"]["default"]) + START_MARGIN
             for row in forecast.rows]
    decisions = {"staffing": {"length": len(start), "low": 2, "high": 2 * max(start), "step": 1, "start": start}}
    return fg_env.optimise(CONTRACT, decisions, OBJECTIVE, CONSTRAINTS, runs=RUNS, budget=BUDGET,
                           holdout_seeds=HOLDOUT_SEEDS, workers=WORKERS, seed=1)


def validation_cases() -> List[Dict[str, Any]]:
    with open(HISTORY, newline="") as handle:
        rows = list(csv.DictReader(handle))
    cases = []
    for day, recorded in sorted(by_day(rows).items()):
        if day < TRAIN_END:
            continue
        offered = [int(row["offered"]) for row in recorded]
        joined = sum(offered)
        cases.append({"name": day, "inputs": {**day_inputs(day), "staffing": [int(row["agents"]) for row in recorded]},
                      "actuals": {"centre_offered": joined,
                                  "centre_service_level": sum(int(row["answered_within_20s"]) for row in recorded) / joined,
                                  "centre_abandon_rate": sum(int(row["abandoned"]) for row in recorded) / joined,
                                  "centre_offered_by_interval": offered}})
    return cases


def search() -> None:
    """Optimise the plan, write it into the planned arms and keep how it was found beside the contract."""
    contract = json.loads(CONTRACT.read_text())
    result = optimise(contract)
    for arm in PLANNED_ARMS:
        contract["arms"][arm]["inputs"]["staffing"] = result.best["staffing"]
    CONTRACT.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n")
    PLAN.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n")
    print(result.summary())


def report() -> None:
    """Validate the model on the held-out weeks, play every arm and print the owner report of the kept plan."""
    result = OptimisationResult(**json.loads(PLAN.read_text()))
    checked = fg_env.validate(CONTRACT, validation_cases(), runs=VALIDATION_RUNS, season=7)
    print(checked.report())
    experiment = fg_env.experiment(CONTRACT, arms=["recommended", "current", "outage", "outage_with_callbacks", "callbacks"],
                                   runs=REPORT_RUNS, workers=WORKERS)
    print(fg_env.report(experiment, contract=CONTRACT, validation=checked, optimisation=result, control="recommended").markdown)


def main() -> None:
    if "--report" in sys.argv:
        report()
    else:
        search()


if __name__ == "__main__":
    main()
