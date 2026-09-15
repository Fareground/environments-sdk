"""Find the cheapest staffing per half-hour that answers 80% of calls within 20 seconds in every half-hour, validate the
model on the two held-out weeks of history, and print the owner report.

    python examples/contact_centre_plan.py            # searches; writes the plan into the arms and contact_centre/plan.json
    python examples/contact_centre_plan.py --report   # validates, plays every arm and prints the owner report

The plan is a 24-value staffing vector searched by ``fg_env.optimise``: minimise the staffing cost subject to the day's
service level reaching 80% in 90% of runs and, per half-hour, at most two half-hours below 80% in an average run — a
day-level service level alone lets a search starve the quiet half-hours. The search starts from the Erlang C staffing
of each half-hour's forecast; every candidate plays the whole day on the same seeds, with the fitted arrival parameters
drawn per run, and the choice is checked again on seeds the search never saw (the optimiser flags a plan that only won
by seed luck).

Why not "the worst half-hour keeps 80% in 90% of runs"? A single day's half-hour service level is noisy (40–120 calls),
and the worst of 24 noisy values is far below their average: the Erlang C plan gives every half-hour at least 85% on
average yet its worst half-hour reaches 80% in 13% of runs, and two more agents in every half-hour (every half-hour at
94% on average, about $900 more a day) still only in 57%. That constraint buys service nobody asked for; the one above
keeps each half-hour near its target at a sane cost.

What the search found (contact_centre/plan.json): a plan of about $9,600 a day that keeps the day's service level at 80%
in every run and has 1.9 half-hours below target in an average run on the confirmation seeds — within the need of two,
but above the 1.5 the search was held to, so the optimiser reports it as the closest decision rather than a feasible
one. Held at 2, the search returned a $9,460 plan at 2.7 on fresh seeds: its cheapest finalists sit on whatever bound it
is given, and seeds that flattered them do not repeat.
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
#: The search asks for at most 1.5 half-hours below target where the plan needs 2: the cheapest plan found on some seeds
#: is the one those seeds flattered, so a search held exactly at the need picks plans that miss it on fresh seeds.
CONSTRAINTS = ["centre_service_level >= 0.8 in 90% of runs", "mean of centre_intervals_below_target <= 1.5"]
#: Seeds judging each plan, distinct plans searched, fresh seeds checking the choice, and processes.
RUNS, BUDGET, HOLDOUT_SEEDS, WORKERS = 30, 300, 40, 8
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
