"""Compare the auto-parts store's reorder policies, optimise a service level per category, and validate the forecast.

    python examples/auto_parts_policies.py compare    # the lean rule against service levels, a year, many seeds
    python examples/auto_parts_policies.py optimise   # the least stock that keeps 97% of demand served, per category
    python examples/auto_parts_policies.py validate   # refit on 2023–24, forecast every quarter, check the intervals
    python examples/auto_parts_policies.py report     # the owner's report: the policy to run, why, and how sure

Every run draws the fitted parameters around their estimates (the contract's `parameter_uncertainty`), so a policy is
judged across what the history cannot pin down, and every policy in a comparison sees the same customers.
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

import fg_env
from fg_env.sdk.analysis.optimise_result import OptimisationResult
from fg_env.sdk.analysis.validate import ValidationResult

CONTRACT = Path(__file__).parent / "contracts" / "auto_parts_store.json"
CATEGORIES = ["brake_pads", "batteries", "wipers"]
#: The first week of every quarter in the bundled history (2023-01-02 … 2025-12-22); the last four are held out.
QUARTERS = ["2023-01-02", "2023-04-03", "2023-07-03", "2023-10-02", "2024-01-01", "2024-04-01", "2024-07-01",
            "2024-09-30", "2024-12-30", "2025-03-31", "2025-06-30", "2025-09-29"]
HELD_OUT = QUARTERS[8:]
WORKERS = 4


def compare(runs: int = 20) -> None:
    arms = {"lean": {"policy": "lean"}, **{f"service {level:.0%}": {"policy": "service", "service_level": level}
                                           for level in (0.8, 0.9, 0.95, 0.99)}}
    print(f"{'policy':<14}{'fill':>7}{'lost units':>12}{'revenue':>11}{'profit':>11}{'avg stock':>11}{'costs':>9}")
    for label, inputs in arms.items():
        runs_ = fg_env.experiment(CONTRACT, arms=[None], runs=runs, seed=11, inputs=inputs, workers=WORKERS).arms["baseline"].runs
        mean = {key: statistics.fmean(run.outputs[key] for run in runs_) for key in
                ("shop_fill_rate", "shop_lost", "shop_revenue", "reorder_profit", "reorder_average_stock_value",
                 "reorder_total_cost")}
        print(f"{label:<14}{mean['shop_fill_rate']:>7.1%}{mean['shop_lost']:>12,.0f}{mean['shop_revenue']:>11,.0f}"
              f"{mean['reorder_profit']:>11,.0f}{mean['reorder_average_stock_value']:>11,.0f}{mean['reorder_total_cost']:>9,.0f}")


def optimise() -> OptimisationResult:
    decisions = {"service_level_by_category": {"keys": CATEGORIES, "low": 0.5, "high": 0.99, "step": 0.05,
                                               "start": {category: 0.95 for category in CATEGORIES}}}
    result = fg_env.optimise(CONTRACT, decisions, "minimise reorder_average_stock_value", ["shop_fill_rate >= 0.97"],
                             inputs={"policy": "service", "weeks": 26}, runs=16, budget=40, holdout_seeds=32,
                             workers=WORKERS)
    print(result.summary())
    print(fg_env.report(result, contract=CONTRACT).markdown)
    return result


def _on_the_way(orders: list, week: int) -> dict:
    """Orders placed before ``week`` (1 = the history's first) that arrive after it, in weeks counted from ``week``."""
    out: dict = {}
    for order in orders:
        if order["placed"] < week < order["arrived"]:
            out.setdefault(order["item"], []).append([order["arrived"] - week + 1, order["qty"], order["placed"] - week + 1,
                                                      order["lead_time"], order["factor"]])
    return out


def validation_cases(history: list, orders: list) -> list:
    """One case per quarter: start there with the stock on hand and the orders on their way then, run the store's own
    rule, and compare the units sold per category with what the history says."""
    cases = []
    for index, (start, end) in enumerate(zip(QUARTERS, QUARTERS[1:] + ["2026-01-01"])):
        rows = [row for row in history if start <= row["time"] < end]
        opening = {row["item"]: row["stock"] for row in rows if row["time"] == start}
        sold: dict = {}
        for row in rows:
            category = row["item"].split("-")[0]
            sold[category] = sold.get(category, 0) + row["units"]
        names = {"PAD": "brake_pads", "BAT": "batteries", "WIP": "wipers"}
        cases.append({"name": start, "inputs": {"start": start, "weeks": 13, "opening_stock": opening, "policy": "lean",
                                                "opening_orders": _on_the_way(orders, 13 * index + 1), "lean_cover": 1.2},
                      "actuals": {"shop_sold_by_group": {names[key]: value for key, value in sold.items()},
                                  "shop_sold": sum(sold.values())}})
    return cases


def validation(runs: int = 20, drawn_by_contract: bool = False) -> ValidationResult:
    """Refit on 2023–24 and validate every quarter. The fitted number parameters are drawn per run from their priors
    (``uncertainty=``) with per-key parameters at their estimates, or, with ``drawn_by_contract``, every fitted
    parameter is drawn by the contract itself (``parameter_uncertainty`` 1)."""
    loaded = fg_env.load(CONTRACT, seed=0).inputs
    history, orders = loaded["history"], loaded["orders"]
    fitted = fg_env.fit_patterns(CONTRACT, inputs={"history": [row for row in history if row["time"] < HELD_OUT[0]],
                                                   "orders": [row for row in orders if row["time"] < HELD_OUT[0]]})
    cases = validation_cases(history, orders)
    for case in cases:
        case["inputs"]["parameter_uncertainty"] = 1 if drawn_by_contract else 0
    return fg_env.validate(fitted.contract, cases, uncertainty=None if drawn_by_contract else fitted.priors, runs=runs,
                           season=4, test=HELD_OUT, rounds=13, data_dir=CONTRACT.parent, workers=WORKERS)


def validate(runs: int = 20) -> None:
    for label, drawn in (("fitted priors (uncertainty=), per-key parameters at their estimates", False),
                         ("every fitted parameter drawn by the contract (parameter_uncertainty 1)", True)):
        print(f"\n== {label}\n{validation(runs, drawn).report()}")


def report(runs: int = 12) -> None:
    """The owner's report: the most profitable policy that serves at least 95% of demand, what drives the difference,
    what the model assumes and how well it forecast held-out quarters."""
    exp = fg_env.experiment(CONTRACT, arms=["lean", "service"], runs=runs, seed=21, workers=WORKERS)
    print(fg_env.report(exp, contract=CONTRACT, validation=validation(), objective="max:reorder_profit",
                        require={"shop_fill_rate": ">= 0.95"}).markdown)


if __name__ == "__main__":
    {"compare": compare, "optimise": optimise, "validate": validate, "report": report}[
        sys.argv[1] if len(sys.argv) > 1 else "compare"]()
