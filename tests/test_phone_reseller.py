"""The used-iPhone reseller example: its histories are what its truth arm records, demand and lead times fitted from
them recover the truth, a launch cuts every older model's value, and the channel and launch what-ifs move money the
way a reseller would expect while value and stock stay conserved."""
import copy
import csv
import importlib.util
import json
import statistics
from pathlib import Path

import pytest

import fg_env

pytestmark = pytest.mark.slow  # statistical or engine-behaviour: `make test-fast` leaves it out

EXAMPLES = Path(__file__).parents[1] / "examples"
CONTRACT = EXAMPLES / "contracts" / "phone_reseller.json"
FOLDER = CONTRACT.parent / "phone_reseller"


def _study():
    spec = importlib.util.spec_from_file_location("phone_reseller_study", EXAMPLES / "phone_reseller_study.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _truth():
    return json.loads(CONTRACT.read_text())["arms"]["truth"]["inputs"]


def test_the_bundled_sales_and_lot_histories_are_exactly_what_the_truth_arm_records():
    sales, lots = _study().truth_records()
    with open(FOLDER / "history.csv", newline="") as handle:
        assert sales == list(csv.DictReader(handle))
    with open(FOLDER / "orders.csv", newline="") as handle:
        assert lots == list(csv.DictReader(handle))
    assert {row["segment"] for row in sales} == {"retail", "wholesale"} and len(sales) == 16 * 2 * 364
    assert all(row["retail_price_effect"] for row in sales if row["segment"] == "retail")


def test_retail_and_wholesale_demand_and_lead_times_fitted_from_the_histories_recover_the_truth():
    fitted = fg_env.analysis.fit_patterns(CONTRACT).contract["inputs"]
    truth = _truth()
    elasticity, error = (fitted["retail_price_effect_elasticity"]["default"],
                         fitted["retail_price_effect_elasticity_se"]["default"])
    assert abs(elasticity - truth["retail_price_effect_elasticity"]) < 3 * error
    # the counts pattern `sales` shares its name with the demand mechanism, so it loads as `sales_pattern`
    assert fitted["sales_pattern_dispersion"]["default"] == pytest.approx(truth["sales_dispersion"], rel=0.3)
    assert fitted["bulk_dispersion"]["default"] == pytest.approx(truth["bulk_dispersion"], rel=0.3)
    assert fitted["lead_noise_sd"]["default"] == pytest.approx(truth["lead_noise_sd"], abs=0.04)
    assert abs(fitted["lead_noise_mean"]["default"]) < 0.05
    for table in ("retail_demand_fit", "wholesale_demand_fit"):
        true = {row["item"]: row["scale"] for row in truth[table]}
        close = [abs(row["scale"] - true[row["item"]]) < 3 * row["scale_se"] for row in fitted[table]["default"]]
        assert sum(close) >= 15, table  # sixteen item lines, at 3 standard errors
    profile, errors = fitted["weekday_profile"]["default"], fitted["weekday_profile_se"]["default"]
    assert sum(abs(a - b) <= 3 * max(e, 0.03) for a, b, e in zip(profile, truth["weekday_profile"], errors)) >= 6


def test_each_channels_fitted_demand_adds_up_to_the_truth_despite_stockouts_that_sold_nothing():
    """Stockout rows say demand was more than what sold; read as 'at least what sold', a slow line's empty-shelf days
    added nothing back and every channel's demand came out a few percent low (the held-out forecasts ran low with it).
    """
    fitted = fg_env.analysis.fit_patterns(CONTRACT).contract["inputs"]
    truth = _truth()
    for table in ("retail_demand_fit", "wholesale_demand_fit"):
        rows = fitted[table]["default"]
        total, true = sum(row["scale"] for row in rows), sum(row["scale"] for row in truth[table])
        error = sum(row["scale_se"] ** 2 for row in rows) ** 0.5
        assert abs(total - true) < 2 * error and abs(total / true - 1) < 0.03, table


def test_a_new_launch_cuts_every_older_models_value_over_three_weeks():
    env = fg_env.load(CONTRACT, seed=1, inputs={"new_launch": True, "parameter_uncertainty": 0})
    runtime = env.world.patterns
    before = runtime.evaluate("value", "15-B", [], 46, "test")   # 2026-09-17, the day before the launch
    during = runtime.evaluate("value", "15-B", [], 57, "test")   # half way through its three weeks
    after = runtime.evaluate("value", "15-B", [], 78, "test")    # once it has passed through
    ageing = runtime.evaluate("age_decay", "15", [], 78, "test") / runtime.evaluate("age_decay", "15", [], 46, "test")
    assert after / before == pytest.approx(0.93 * ageing, rel=1e-6)
    assert after < during < before


def test_forking_a_run_into_a_new_launch_loses_revenue_and_margin_on_the_same_customers():
    env = fg_env.load(CONTRACT, seed=3)
    env.run("idle", rounds=21)
    launched = env.fork(inputs={"new_launch": True}).run("idle").outputs
    quiet = env.run("idle").outputs
    assert launched["sales_demand"] == quiet["sales_demand"]  # demand here answers the markup, not the value
    assert launched["sales_revenue"] < quiet["sales_revenue"] - 5000
    assert launched["sales_margin"] < quiet["sales_margin"] and launched["buying_profit"] < quiet["buying_profit"]


def test_clearance_pricing_sells_more_at_retail_but_earns_less_with_value_and_stock_conserved():
    contract = copy.deepcopy(json.loads(CONTRACT.read_text()))
    contract["invariants"] = ["$conserved('money')", "$stock_conserved('sales')"]
    exp = fg_env.experiment(contract, arms=[None, "clearance"], runs=4, seed=5, data_dir=CONTRACT.parent)
    base, clearance = ([run.outputs for run in exp.arms[arm].runs] for arm in ("baseline", "clearance"))
    assert all(run.status == "completed" for arm in exp.arms.values() for run in arm.runs)
    retail = [(c["sales_sold_by_segment"]["retail"], b["sales_sold_by_segment"]["retail"])
              for c, b in zip(clearance, base)]
    assert all(c > 1.1 * b for c, b in retail)
    assert statistics.fmean(c["buying_profit"] for c in clearance) < statistics.fmean(b["buying_profit"] for b in base)
    assert all(c["sales_returned"] >= b["sales_returned"] * 0.8 for c, b in zip(clearance, base))
