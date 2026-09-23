"""The used-iPhone reseller: regenerate its histories, compare channels, fork a new-model launch, validate the forecast.

    python examples/phone_reseller_study.py history    # sales and lot histories from the truth arm, then refit
    python examples/phone_reseller_study.py channels   # the base plan against a clearance plan, many seeds
    python examples/phone_reseller_study.py launch     # three weeks in, fork the run: does a new iPhone launch?
    python examples/phone_reseller_study.py validate   # refit on the first nine months, forecast every four weeks
    python examples/phone_reseller_study.py report     # the owner's report: the plan to run, why, and how sure

The truth arm runs a year of daily trade with day-to-day repricing (so the markup's effect can be estimated) and the
2026 launch, recording every item's day per channel and every lot bought — a reseller's marketplace and purchase
exports. The retail and wholesale demand, the markup elasticity and the lead-time spread are fitted from them.

Why the held-out forecasts run low. Refit on the first nine months, the model forecast the last three held-out
four-week periods about 10% low, and its per-channel 80% ranges held 2 of 6 values. Three causes, measured against
the truth arm's own parameters on the same cases:

* Fitting (fixed). A stockout row means demand went unmet, so demand was more than what sold; the fit read it as "at
  least what sold", so a slow line's empty-shelf days added nothing back and each channel's fitted demand came out
  2–4% low. Now read as "more than", the channel totals sit within their standard errors of the truth.
* Ranges (fixed here). Validating with the fitted priors draws only the number parameters, leaving every item's
  demand scale at its estimate, so a channel's total carried none of the sixteen scales' uncertainty. The cases now
  draw every fitted parameter per run (``parameter_uncertainty`` 1). With the scales' and weekday profile's errors
  taken where the profile averages 1 (not from its first day) and stockout days' information counted as censored,
  channel 80% ranges hold 88% of values over every case and 95% ranges 96%.
* The held-out months themselves (not a defect). Forecast with the truth's own parameters, the thirteen cases are
  unbiased (+0.3%), yet the three held-out periods sold about 6% more than even the truth expects — retail 796 against
  738 — which is chance in the generated history. Three periods are thin evidence either way, and the report says so
  as a bias the owner should allow for rather than hide.
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

import fg_env
from fg_env.sdk.analysis.validate import ValidationResult

CONTRACT = Path(__file__).parent / "contracts" / "phone_reseller.json"
FOLDER = CONTRACT.parent / "phone_reseller"
HISTORY_FIELDS = ["time", "item", "segment", "units", "stockout", "stock", "price", "retail_price_effect"]
ORDER_FIELDS = ["time", "item", "placed", "arrived", "qty", "lead_time", "factor"]
SEED = 2025
#: The truth arm's first day, and the day the validation's held-out weeks begin.
TRUTH_START, HELD_OUT = date(2025, 10, 6), date(2026, 7, 6)
WORKERS = 4


def truth_records(seed: int = SEED) -> tuple:
    """Every item-day per channel and every lot the truth arm records, as CSV rows (texts, like the files)."""
    env = fg_env.load(CONTRACT, arm="truth", seed=seed, inputs={"history": [], "orders": []})
    result = env.run()
    if result.status not in ("completed", "ended"):
        raise SystemExit(f"the truth run {result.status}: {result.error}")
    records = env.world.records_store

    def text(value: object) -> str:
        return "" if value is None else str(value)

    return ([{field: text(entry.get(field)) for field in HISTORY_FIELDS} for entry in records["sales_history"]],
            [{field: text(entry.get(field)) for field in ORDER_FIELDS} for entry in records["buying_orders"]])


def history() -> None:
    sales, lots = truth_records()
    for path, fields, rows in ((FOLDER / "history.csv", HISTORY_FIELDS, sales), (FOLDER / "orders.csv", ORDER_FIELDS, lots)):
        with open(path, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    fitted = fg_env.analysis.fit_patterns(CONTRACT)
    CONTRACT.write_text(json.dumps(fitted.contract, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(sales)} item-days and {len(lots)} lots, and the fitted contract\n{fitted.report()}")


def _mean(runs: list, key: str, part: str = "") -> float:
    return statistics.fmean(run.outputs[key][part] if part else run.outputs[key] for run in runs)


def channels(runs: int = 20) -> None:
    exp = fg_env.experiment(CONTRACT, arms=[None, "clearance"], runs=runs, seed=7, workers=WORKERS)
    print(f"{'plan':<11}{'retail sold':>12}{'wholesale':>11}{'returned':>10}{'revenue':>11}{'profit':>10}{'avg stock':>11}")
    for arm, result in exp.arms.items():
        rs = result.runs
        print(f"{arm:<11}{_mean(rs, 'sales_sold_by_segment', 'retail'):>12,.0f}{_mean(rs, 'sales_sold_by_segment', 'wholesale'):>11,.0f}"
              f"{_mean(rs, 'sales_returned'):>10,.0f}{_mean(rs, 'sales_revenue'):>11,.0f}{_mean(rs, 'buying_profit'):>10,.0f}"
              f"{_mean(rs, 'buying_average_stock_value'):>11,.0f}")


def launch(runs: int = 12, fork_day: int = 21) -> None:
    """Fork each run three weeks in: the same luck until then, then with and without the launch."""
    rows = []
    for seed in range(runs):
        env = fg_env.load(CONTRACT, seed=seed)
        env.run("idle", rounds=fork_day)
        launched = env.fork(inputs={"new_launch": True}).run("idle").outputs
        quiet = env.run("idle").outputs
        rows.append({key: launched[key] - quiet[key] for key in ("buying_profit", "sales_revenue", "sales_margin",
                                                               "buying_average_stock_value", "sales_sold")})
    print(f"A new iPhone on 2026-09-18, forked on day {fork_day}, mean change over {runs} runs:")
    for key in rows[0]:
        values = [row[key] for row in rows]
        print(f"  {key}: {statistics.fmean(values):+,.0f} (runs from {min(values):+,.0f} to {max(values):+,.0f})")


def validation_cases(sales: list, lots: list, weeks: int = 4) -> list:
    """One case per four weeks of the history: its opening stock and lots on their way, run the reseller's own plan,
    compare units sold per model and per channel."""
    cases, start = [], TRUTH_START
    while start + timedelta(weeks=weeks) <= TRUTH_START + timedelta(days=364):
        first, end = start.isoformat(), (start + timedelta(weeks=weeks)).isoformat()
        day = (start - TRUTH_START).days + 1
        rows = [row for row in sales if first <= row["time"] < end]
        opening = {row["item"]: row["stock"] for row in rows if row["time"] == first and row["segment"] == "retail"}
        on_the_way: dict = {}
        for lot in lots:
            if lot["placed"] < day < lot["arrived"]:
                on_the_way.setdefault(lot["item"], []).append([lot["arrived"] - day + 1, lot["qty"], lot["placed"] - day + 1,
                                                               lot["lead_time"], lot["factor"]])
        by_model: dict = {}
        by_channel: dict = {}
        for row in rows:
            model = row["item"].split("-")[0]
            by_model[model] = by_model.get(model, 0) + row["units"]
            by_channel[row["segment"]] = by_channel.get(row["segment"], 0) + row["units"]
        cases.append({"name": first, "inputs": {"start": first, "days": 7 * weeks, "opening_stock": opening,
                                                "opening_orders": on_the_way, "markup_wiggle": 0.1,
                                                "new_launch": True, "parameter_uncertainty": 1},
                      "actuals": {"sales_sold_by_group": by_model, "sales_sold_by_segment": by_channel}})
        start += timedelta(weeks=weeks)
    return cases


def validation(runs: int = 20) -> ValidationResult:
    """Refit on the first nine months and forecast every four weeks, every fitted parameter (item demand scales
    included) drawn per run by the contract, so a channel's range carries what the history cannot pin down."""
    loaded = fg_env.load(CONTRACT, seed=0).inputs
    sales, lots = loaded["history"], loaded["orders"]
    fitted = fg_env.analysis.fit_patterns(CONTRACT, inputs={"history": [r for r in sales if r["time"] < HELD_OUT.isoformat()],
                                                   "orders": [r for r in lots if r["time"] < HELD_OUT.isoformat()]})
    cases = validation_cases(sales, lots)
    held_out = [case["name"] for case in cases if case["name"] >= HELD_OUT.isoformat()]
    return fg_env.analysis.validate(fitted.contract, cases, runs=runs, season=13, test=held_out, rounds=28, data_dir=FOLDER.parent,
                           workers=WORKERS)


def validate(runs: int = 20) -> None:
    print(validation(runs).report())


def report(runs: int = 12) -> None:
    """The owner's report: the most profitable buying and pricing plan that serves at least 95% of demand, what drives
    the differences, what the model assumes and how well it forecast held-out weeks."""
    exp = fg_env.experiment(CONTRACT, arms=[None, "clearance", "service"], runs=runs, seed=13, workers=WORKERS)
    print(fg_env.analysis.report(exp, contract=CONTRACT, validation=validation(), objective="max:buying_profit",
                        require={"sales_fill_rate": ">= 0.95"}).markdown)


if __name__ == "__main__":
    {"history": history, "channels": channels, "launch": launch, "validate": validate, "report": report}[
        sys.argv[1] if len(sys.argv) > 1 else "channels"]()
