"""The auto-parts example: its history is what its truth arm records, the demand patterns fitted from that history
recover the truth, and its policies behave as a store would expect."""
import csv
import importlib.util
import json
import statistics
from pathlib import Path

import pytest

import fg_env

EXAMPLES = Path(__file__).parents[2] / "examples"
CONTRACT = EXAMPLES / "contracts" / "auto_parts_store.json"
HISTORY = EXAMPLES / "contracts" / "auto_parts_store" / "history.csv"


def _generator():
    spec = importlib.util.spec_from_file_location("auto_parts_history", EXAMPLES / "auto_parts_history.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _truth():
    return json.loads(CONTRACT.read_text())["arms"]["truth"]["inputs"]


def test_the_bundled_history_is_exactly_what_the_truth_arm_records():
    generator = _generator()
    with open(HISTORY, newline="") as handle:
        bundled = list(csv.DictReader(handle))
    assert generator.history_rows() == bundled
    assert len(bundled) == 12 * 156
    assert 0.05 < statistics.fmean(int(row["stockout"]) for row in bundled) < 0.2


def test_fitting_the_bundled_history_recovers_the_truth_and_reproduces_the_shipped_estimates():
    fitted = fg_env.fit_patterns(CONTRACT).contract["inputs"]
    shipped = json.loads(CONTRACT.read_text())["inputs"]
    for name in ("growth_rate", "promo_lift", "sales_dispersion", "season_fit", "price_effect_fit", "demand_fit"):
        assert fitted[name]["default"] == pytest.approx(shipped[name]["default"], rel=1e-9), name
    truth = _truth()
    for name in ("growth_rate", "promo_lift"):
        assert abs(fitted[name]["default"] - truth[name]) < 3 * fitted[f"{name}_se"]["default"], name
    assert fitted["sales_dispersion"]["default"] == pytest.approx(truth["sales_dispersion"], rel=0.3)
    for row in fitted["price_effect_fit"]["default"]:
        true = next(r["elasticity"] for r in truth["price_effect_fit"] if r["category"] == row["category"])
        assert abs(row["elasticity"] - true) < 3 * row["elasticity_se"], row["category"]
    for row in fitted["demand_fit"]["default"]:
        true = next(r["scale"] for r in truth["demand_fit"] if r["sku"] == row["sku"])
        assert abs(row["scale"] - true) < 3 * row["scale_se"], row["sku"]
    for row in fitted["season_fit"]["default"]:
        true = next(r["profile"] for r in truth["season_fit"] if r["category"] == row["category"])
        close = sum(abs(a - b) <= 3 * max(error, 0.02) for a, b, error in zip(row["profile"], true, row["profile_se"]))
        assert close >= 11, row["category"]


def test_the_forecast_driven_policy_serves_more_demand_and_earns_more_than_the_lean_rule():
    exp = fg_env.experiment(CONTRACT, arms=["lean", "service"], runs=4, seed=3)
    lean, service = ([run.outputs for run in exp.arms[arm].runs] for arm in ("lean", "service"))
    assert statistics.fmean(o["fill_rate"] for o in service) > statistics.fmean(o["fill_rate"] for o in lean) + 0.05
    assert all(s["profit"] > l["profit"] for s, l in zip(service, lean))  # the same luck in both arms, run by run
    assert statistics.fmean(o["average_stock_value"] for o in service) > statistics.fmean(o["average_stock_value"] for o in lean)


def test_dearer_premium_tiers_move_sales_to_the_value_tier():
    def sold_by_tier(arm):
        env = fg_env.load(CONTRACT, arm=arm, seed=5, inputs={"parameter_uncertainty": 0})
        env.run()
        totals = {"premium": 0, "value": 0}
        for entity in env.entities("sku"):
            totals[entity["props"]["tier"]] += entity["props"]["units_total"]
        return totals

    base, dearer = sold_by_tier("lean"), sold_by_tier("premium_price_up")
    assert dearer["premium"] < base["premium"] and dearer["value"] > base["value"]


def test_the_fitted_demand_can_be_decomposed_and_described():
    parts = fg_env.decompose(CONTRACT, "demand", key="BAT-TOY-V", rounds=3, inputs={"parameter_uncertainty": 0})
    assert [set(row["factors"]) for row in parts.rows] == [{"growth", "season"}] * 3
    first = parts.rows[0]
    assert first["total"] == pytest.approx(first["factors"]["growth"] * first["factors"]["season"]
                                           * next(r["scale"] for r in json.loads(CONTRACT.read_text())["inputs"]["demand_fit"]["default"]
                                                  if r["sku"] == "BAT-TOY-V"))
    text = fg_env.describe(CONTRACT).markdown
    assert "### World patterns" in text and "`$pattern.promo(key)`" in text and "fitted from $inputs.history" in text
