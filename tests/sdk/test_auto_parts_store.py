"""The auto-parts example: it is built on the demand and replenishment modes, its history is what its truth arm records,
the demand patterns fitted from that history recover the truth, and its policies behave as a store would expect."""
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


def test_the_store_is_demand_and_replenishment_modes_reading_patterns_with_no_hand_written_rules():
    contract = json.loads(CONTRACT.read_text())
    assert {name: (use["kind"], use["mode"]) for name, use in contract["mechanisms"].items()} == {
        "shop": ("economy", "demand"), "reorder": ("economy", "replenishment")}
    assert not {"events", "metrics", "outputs", "records"} & set(contract)
    outputs = fg_env.load(CONTRACT, seed=1, inputs={"weeks": 2}).run().outputs
    assert {"shop_fill_rate", "shop_fill_rate_by_group", "reorder_profit", "reorder_average_stock_value"} <= set(outputs)


def test_the_bundled_sales_and_purchase_order_histories_are_exactly_what_the_truth_arm_records():
    history, orders = _generator().truth_records()
    with open(HISTORY, newline="") as handle:
        assert history == list(csv.DictReader(handle))
    with open(HISTORY.parent / "orders.csv", newline="") as handle:
        assert orders == list(csv.DictReader(handle))
    assert len(history) == 12 * 156 and len(orders) > 500
    assert 0.05 < statistics.fmean(int(row["stockout"]) for row in history) < 0.2


def test_fitting_the_bundled_history_recovers_the_truth_and_reproduces_the_shipped_estimates():
    fitted = fg_env.analysis.fit_patterns(CONTRACT).contract["inputs"]
    shipped = json.loads(CONTRACT.read_text())["inputs"]
    for name in ("growth_rate", "promo_lift", "sales_dispersion", "season_fit", "price_effect_fit", "demand_fit"):
        # The fit is iterative and float sums differ slightly between Python versions (3.12 made sum() exact),
        # so the refit must match the shipped estimates to a tight tolerance, not bit for bit.
        assert _close(fitted[name]["default"], shipped[name]["default"], 1e-6), name
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


def test_the_service_level_policy_serves_more_demand_and_earns_more_than_the_lean_rule():
    exp = fg_env.experiment(CONTRACT, arms=["lean", "service"], runs=4, seed=3)
    lean, service = ([run.outputs for run in exp.arms[arm].runs] for arm in ("lean", "service"))
    assert statistics.fmean(o["shop_fill_rate"] for o in service) > statistics.fmean(o["shop_fill_rate"] for o in lean) + 0.05
    assert all(s["reorder_profit"] > l["reorder_profit"] for s, l in zip(service, lean))  # the same luck in both arms
    assert statistics.fmean(o["reorder_average_stock_value"] for o in service) > \
        statistics.fmean(o["reorder_average_stock_value"] for o in lean)
    assert [o["shop_demand"] for o in service] != [] and all(
        s["shop_fill_rate_by_group"]["batteries"] >= l["shop_fill_rate_by_group"]["batteries"] for s, l in zip(service, lean))


def test_the_owner_report_recommends_the_service_policy_when_95_percent_of_demand_must_be_served():
    exp = fg_env.experiment(CONTRACT, arms=["lean", "service"], runs=4, seed=3, rounds=26)
    text = fg_env.analysis.report(exp, contract=CONTRACT, objective="max:reorder_profit",
                         require={"shop_fill_rate": ">= 0.95"}).markdown
    recommendation = text.split("## Recommendation", 1)[1].split("##", 1)[0]
    assert "Choose order up to the expected demand" in recommendation
    assert "the store's current rule" in text.split("## Risks", 1)[1].split("##", 1)[0]  # the lean rule misses 95%
    assert "parameters are estimated from the data; the analyst report lists them" in text and "lead_noise_sd" not in text


def test_dearer_premium_tiers_move_sales_to_the_value_tier():
    def sold_by_tier(arm):
        env = fg_env.load(CONTRACT, arm=arm, seed=5, inputs={"parameter_uncertainty": 0})
        env.run()
        totals = {"premium": 0, "value": 0}
        for entity in env.entities("sku"):
            totals[entity["props"]["tier"]] += entity["props"]["shop_sold_total"]
        return totals

    base, dearer = sold_by_tier("lean"), sold_by_tier("premium_price_up")
    assert dearer["premium"] < base["premium"] and dearer["value"] > base["value"]


def test_the_fitted_demand_can_be_decomposed_and_described():
    parts = fg_env.analysis.decompose(CONTRACT, "demand", key="BAT-TOY-V", rounds=3, inputs={"parameter_uncertainty": 0})
    assert [set(row["factors"]) for row in parts.rows] == [{"growth", "season"}] * 3
    first = parts.rows[0]
    assert first["total"] == pytest.approx(first["factors"]["growth"] * first["factors"]["season"]
                                           * next(r["scale"] for r in json.loads(CONTRACT.read_text())["inputs"]["demand_fit"]["default"]
                                                  if r["sku"] == "BAT-TOY-V"))
    text = fg_env.analysis.describe(CONTRACT).markdown
    assert "### World patterns" in text and "`$pattern.promo(key)`" in text and "fitted from $inputs.history" in text

def _close(a, b, rel):
    """Nested lists and maps equal in shape and keys, numbers within ``rel`` of each other."""
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(_close(a[k], b[k], rel) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(_close(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)):
        return abs(a - b) <= rel * max(abs(a), abs(b), 1e-12)
    return a == b
