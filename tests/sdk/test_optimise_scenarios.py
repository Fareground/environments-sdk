"""The optimiser on the business study's scenarios: staffing per half hour under a service level, a safety margin per
category under a fill rate (the auto-parts example, across its fitted patterns' uncertainty) and retail prices per
grade — every choice checked on seeds no search saw, against the rule of thumb it would replace."""
import json
from pathlib import Path
from statistics import fmean

import fg_env

FIXTURES = Path(__file__).parent / "fixtures" / "optimise"
CENTRE = str(FIXTURES / "contact_centre" / "contact_centre.json")
PHONES = str(FIXTURES / "iphones" / "iphones.json")
STORE = Path(__file__).parents[2] / "examples" / "contracts" / "auto_parts_store.json"

#: The contact centre manager's rule for the four peak hours (agents per half hour), and its cost.
MANAGER_PLAN, MANAGER_COST = [8, 9, 9, 9, 8, 7, 8, 9], 1210
STAFFING = {"agents": {"length": 8, "low": 2, "high": 14, "step": 1, "start": MANAGER_PLAN}}
#: Each grade's fair eBay price: the fitted wholesale value times the channel's uplift.
FAIR = {"A": 229.0 * 1.31, "B": 194.65 * 1.31, "C": 155.75 * 1.31, "D": 96.2 * 1.31}


def _fresh(contract, inputs, measure, runs=40):
    """``measure`` in every run on seeds the searches never used."""
    return [r.outputs[measure] for r in fg_env.experiment(contract, runs=runs, seed=4242, inputs=inputs)
            .arms["baseline"].runs]


def test_a_staffing_plan_picked_on_four_seeds_misses_its_service_level_on_fresh_seeds_and_is_flagged_as_luck():
    few = fg_env.optimise(CENTRE, STAFFING, "minimise staffing_cost", ["sl >= 0.8 in 100% of runs"], runs=4, budget=24,
                          holdout_seeds=20)
    assert few.holdout["seed_luck"] and "clearly no longer holds" in few.summary()
    cheapest = min((h for h in few.history if h["feasible"]), key=lambda h: h["objectives"][0])
    assert fmean(sl >= 0.8 for sl in _fresh(CENTRE, cheapest["decision"], "sl")) < 0.8


def test_a_staffing_plan_judged_on_enough_seeds_keeps_its_service_level_on_fresh_seeds_for_no_more_than_the_rule():
    chosen = fg_env.optimise(CENTRE, STAFFING, "minimise staffing_cost", ["sl >= 0.8 in 80% of runs"], runs=16,
                             budget=24, workers=2)
    assert chosen.feasible and chosen.estimates["objectives"][0]["value"] <= MANAGER_COST
    assert fmean(sl >= 0.8 for sl in _fresh(CENTRE, chosen.best, "sl")) >= 0.8


def _store_with_a_margin_per_category():
    """The example store with its service policy's safety margin set per category (a map input)."""
    contract = json.loads(STORE.read_text())
    contract["inputs"]["service_z_by_category"] = {"type": "map", "default": {}}
    events = json.dumps(contract["events"])
    assert events.count("$inputs.service_z *") == 1
    contract["events"] = json.loads(events.replace(
        "$inputs.service_z *", "$get($inputs.service_z_by_category, $s.category, $inputs.service_z) *"))
    return contract


def test_a_safety_margin_per_category_keeps_the_fill_rate_with_less_stock_across_the_fitted_uncertainty():
    store, priors = _store_with_a_margin_per_category(), fg_env.fit_patterns(STORE).priors
    common = dict(inputs={"policy": "service"}, data_dir=STORE.parent, uncertainty=priors, rounds=13)
    categories = ["brake_pads", "batteries", "wipers"]
    result = fg_env.optimise(store, {"service_z_by_category": {"keys": categories, "low": 0, "high": 3, "step": 0.5,
                                                               "start": {c: 1.5 for c in categories}}},
                             "minimise average_stock_value", ["fill_rate >= 0.97"], runs=4, budget=16, workers=2,
                             **common)
    assert result.feasible and set(priors) == {"growth_rate", "promo_lift"}

    def fresh(margins):
        runs = fg_env.experiment(store, runs=24, seed=9090, arms=[None], data_dir=STORE.parent, uncertainty=priors,
                                 rounds=13, inputs={"policy": "service", **margins}).arms["baseline"].runs
        return fmean(r.outputs["fill_rate"] for r in runs), fmean(r.outputs["average_stock_value"] for r in runs)

    fill, stock = fresh(result.best)
    _, uniform_stock = fresh({})  # the example's service arm: 1.65 in every category
    assert fill >= 0.97 and stock < 0.9 * uniform_stock


def test_prices_per_grade_stay_in_grade_order_and_beat_listing_at_the_fair_price_on_fresh_seeds():
    bounds = {"low": {"A": 250, "B": 210, "C": 170, "D": 100}, "high": {"A": 700, "B": 600, "C": 480, "D": 300}}
    result = fg_env.optimise(PHONES, {"price": {"keys": list("ABCD"), **bounds, "step": 10, "monotone": "decreasing"}},
                             "maximise profit", runs=8, budget=40)
    prices = result.best["price"]
    assert prices["A"] >= prices["B"] >= prices["C"] >= prices["D"]
    assert all(prices[grade] > 1.2 * FAIR[grade] for grade in FAIR)
    at_fair = {"price": {grade: round(price, -1) for grade, price in FAIR.items()}}
    assert fmean(_fresh(PHONES, result.best, "profit")) > 1.3 * fmean(_fresh(PHONES, at_fair, "profit"))
