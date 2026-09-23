"""Find the cheapest staffing per half-hour that answers 80% of calls within 20 seconds in every half-hour, validate the
model on the two held-out weeks of history, and print the owner report.

    python examples/contact_centre_plan.py            # searches; writes the plan into the arms and contact_centre/plan.json
    python examples/contact_centre_plan.py --report   # validates, plays every arm and prints the owner report

The plan is a 24-value staffing vector searched by ``fg_env.analysis.optimise``: minimise the staffing cost subject to every
half-hour's expected service level reaching 80% (``each centre_service_level_by_interval >= 0.8``) and the day's in
90% of runs, both held with the optimiser's 90% confidence — a day-level service level alone lets a search starve the
quiet half-hours. The search starts from the Erlang C staffing of each half-hour's forecast; every candidate plays the
whole day on the same seeds, with the fitted arrival parameters drawn per run; the finalists are confirmed on new seeds
and the choice checked again on seeds no search saw.

The constraints target each half-hour's expected service level rather than the minimum observed across a noisy day.
The saved result in contact_centre/plan.json records the selected staffing, costs, constraints and seed checks.
After correcting recurring time-slot boundaries, the regenerated search evaluated 1,001 plans across 30,470 runs.
The selected plan passed the constraints on 60 confirmation seeds and again on 40 held-out seeds.
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
from fg_env.analysis.optimise_result import OptimisationResult

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
    forecast = fg_env.analysis.decompose(contract, "calls", data_dir=CONTRACT.parent, inputs={"parameter_uncertainty": 0})
    start = [erlang_c_staff(row["total"], contract["inputs"]["aht_sec"]["default"]) + START_MARGIN
             for row in forecast.rows]
    decisions = {"staffing": {"length": len(start), "low": 2, "high": 2 * max(start), "step": 1, "start": start}}
    return fg_env.analysis.optimise(CONTRACT, decisions, OBJECTIVE, CONSTRAINTS, runs=RUNS, budget=BUDGET,
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
    checked = fg_env.analysis.validate(CONTRACT, validation_cases(), runs=VALIDATION_RUNS, season=7)
    print(checked.report())
    experiment = fg_env.experiment(CONTRACT, arms=["recommended", "current", "outage", "outage_with_callbacks", "callbacks"],
                                   runs=REPORT_RUNS, workers=WORKERS)
    print(fg_env.analysis.report(experiment, contract=CONTRACT, validation=checked, optimisation=result, control="recommended").markdown)


def main() -> None:
    if "--report" in sys.argv:
        report()
    else:
        search()


if __name__ == "__main__":
    main()
