"""Regenerate the contact centre's half-hourly history from its `truth` arm, then estimate the model from it.

    python examples/contact_centre_history.py            # history.csv and the estimated contract
    python examples/contact_centre_history.py --report   # also print what was estimated

The truth arm plays eight weeks of days (08:00-20:00) with the true parameters and the manager's staffing rule,
three of them with a network outage, and records every half-hour the way a contact centre's reporting exports it:
calls offered, answered, abandoned and answered within 20 seconds, average handle and wait times, agents on duty.
The model is then estimated from the first six weeks only (the last two are kept for validation):

* arrivals — the day-of-week and time-of-day indexes and the base volume, fitted jointly by ``fg_env.analysis.fit_patterns``
  from the days without an outage;
* average handle time — the answered-weighted mean of the half-hourly averages;
* an outage's peak uplift — the first outage half-hour's calls over the fitted forecast, averaged over the outages;
* patience — the simulated method of moments: the patience at which abandonment over the calibration days, each
  replayed with the agents it actually had and pooled on common seeds, equals the recorded abandonment (found by
  bisection, since more patience always means fewer callers giving up). Weighing matters: fitting each day's rate by
  its relative error lets the quietest days' noise count most (``fg_env.analysis.calibrate`` on per-day relative errors lands
  near 270 s here, against a true 160 s, and its pooled check says so); giving each day's calls as the target's
  ``count``, or ``"pool": true``, recovers 160–167 s.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

import fg_env

CONTRACT = Path(__file__).parent / "contracts" / "contact_centre.json"
HISTORY = CONTRACT.parent / "contact_centre" / "history.csv"
FIELDS = ["time", "date", "weekday", "interval", "offered", "answered", "abandoned", "answered_within_20s",
          "avg_handle_sec", "avg_wait_sec", "agents", "outage"]
#: The seed of the first recorded day (each later day adds one).
SEED = 2026
FIRST_DAY = dt.date(2026, 7, 20)
DAYS = 56
#: The history's outages: day → (start, true peak uplift).
OUTAGES = {"2026-07-29": ("09:30", 2.6), "2026-08-12": ("13:00", 3.4), "2026-09-08": ("10:30", 3.0)}
#: Days before this are fitted; the rest are held out for validation (examples/contact_centre_plan.py).
TRAIN_END = "2026-08-31"
#: Recorded days patience is calibrated to: every training day without an outage.
CALIBRATION_STRIDE = 1
#: Seeds each calibration day is replayed on, and the bisection's steps over the patience range (seconds).
CALIBRATION_RUNS, BISECTION_STEPS, PATIENCE_RANGE = 4, 10, (40.0, 600.0)


def day_inputs(day: str) -> Dict[str, Any]:
    """The inputs that replay one recorded day: its date and, on an outage day, the outage."""
    inputs: Dict[str, Any] = {"day": f"{day}T08:00"}
    if day in OUTAGES:
        start, uplift = OUTAGES[day]
        inputs.update(outage_at=f"{day}T{start}", outage_uplift=uplift)
    return inputs


def history_rows(contract: Path = CONTRACT, seed: int = SEED) -> List[Dict[str, str]]:
    """Every half-hour the truth arm records over the history's days, as CSV rows (texts, like the file)."""
    rows = []
    for offset in range(DAYS):
        day = (FIRST_DAY + dt.timedelta(days=offset)).isoformat()
        env = fg_env.load(contract, arm="truth", seed=seed + offset, inputs=day_inputs(day))
        result = env.run()
        if result.status != "completed":
            raise SystemExit(f"the truth run of {day} {result.status}: {result.error}")
        opening = dt.datetime.fromisoformat(f"{day}T08:00")
        for record in env.props["centre_intervals"]:
            start = opening + dt.timedelta(minutes=30 * record["interval"])
            rows.append({"time": start.isoformat(timespec="minutes"), "date": day, "weekday": start.strftime("%a"),
                         "interval": start.strftime("%H:%M"), "offered": str(record["offered"]),
                         "answered": str(record["answered"]), "abandoned": str(record["abandoned"]),
                         "answered_within_20s": str(record["within"]),
                         "avg_handle_sec": f"{record['aht'] or 0:.1f}", "avg_wait_sec": f"{record['asa'] or 0:.1f}",
                         "agents": str(record["staff"]), "outage": str(int(day in OUTAGES))})
    return rows


def by_day(rows: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    days: Dict[str, List[Dict[str, str]]] = {}
    for row in rows:
        days.setdefault(row["date"], []).append(row)
    return days


def average_handle_time(rows: List[Dict[str, str]]) -> float:
    training = [row for row in rows if row["date"] < TRAIN_END and int(row["answered"]) > 0]
    answered = sum(int(row["answered"]) for row in training)
    return sum(float(row["avg_handle_sec"]) * int(row["answered"]) for row in training) / answered


def outage_uplift(contract: Dict[str, Any], rows: List[Dict[str, str]]) -> float:
    """Mean over the training outages of (the first outage half-hour's calls ÷ the fitted forecast) − 1."""
    ratios = []
    for day, (start, _) in OUTAGES.items():
        if day >= TRAIN_END:
            continue
        index = (int(start[:2]) - 8) * 2 + int(start[3:]) // 30
        forecast = fg_env.analysis.decompose(contract, "calls", rounds=[index + 1], data_dir=CONTRACT.parent,
                                    inputs={"day": f"{day}T08:00", "parameter_uncertainty": 0}).rows[0]["total"]
        offered = int(by_day(rows)[day][index]["offered"])
        ratios.append(offered / forecast - 1)
    return statistics.fmean(ratios)


def calibration_days(rows: List[Dict[str, str]]) -> List[str]:
    return [day for day in sorted(by_day(rows)) if day < TRAIN_END and day not in OUTAGES][::CALIBRATION_STRIDE]


def patience(contract: Dict[str, Any], rows: List[Dict[str, str]]) -> float:
    """The patience whose simulated abandonment over the calibration days matches the recorded (see the module)."""
    days = by_day(rows)
    chosen = calibration_days(rows)
    recorded = (sum(int(row["abandoned"]) for day in chosen for row in days[day])
                / sum(int(row["offered"]) for day in chosen for row in days[day]))

    def simulated(seconds: float) -> float:
        abandoned = offered = 0
        for day in chosen:
            inputs = {**day_inputs(day), "staffing": [int(row["agents"]) for row in days[day]],
                      "parameter_uncertainty": 0, "patience_sec": seconds}
            for run in range(CALIBRATION_RUNS):
                outputs = fg_env.run(contract, seed=run, inputs=inputs, data_dir=CONTRACT.parent).outputs
                abandoned += outputs["centre_abandoned"]
                offered += outputs["centre_offered"]
        return abandoned / offered

    low, high = PATIENCE_RANGE
    for _ in range(BISECTION_STEPS):
        middle = (low * high) ** 0.5
        low, high = (middle, high) if simulated(middle) > recorded else (low, middle)
    return (low * high) ** 0.5


def estimate(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """The contract estimated from ``rows`` (already written to the history file)."""
    fitted = fg_env.analysis.fit_patterns(CONTRACT)
    contract = fitted.contract
    inputs = contract["inputs"]
    inputs["aht_sec"]["default"] = round(average_handle_time(rows), 1)
    uplift = round(outage_uplift(contract, rows), 3)
    inputs["outage_uplift_estimate"]["default"] = uplift
    for arm in ("outage", "outage_with_callbacks"):
        contract["arms"][arm]["inputs"]["outage_uplift"] = uplift
    inputs["patience_sec"]["default"] = round(patience(contract, rows), 1)
    contract["_fit_report"] = fitted.report()
    return contract


def main() -> None:
    rows = history_rows()
    with open(HISTORY, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    contract = estimate(rows)
    fit_report = contract.pop("_fit_report")
    CONTRACT.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} half-hours to {HISTORY} and the estimated contract to {CONTRACT}")
    if "--report" in sys.argv:
        print(fit_report)
        print({name: contract["inputs"][name]["default"] for name in ("aht_sec", "patience_sec", "outage_uplift_estimate")})


if __name__ == "__main__":
    main()
