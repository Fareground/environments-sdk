"""The optimiser on the business study's scenarios: staffing per half hour under a service level, a safety margin per
category under a fill rate (the auto-parts example, across its fitted patterns' uncertainty) and retail prices per
grade — every choice checked on seeds no search saw, against the rule of thumb it would replace."""
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


def test_every_run_cannot_be_shown_with_confidence_so_a_plan_picked_on_four_seeds_stays_borderline_and_luck_is_flagged():
    few = fg_env.analysis.optimise(CENTRE, STAFFING, "minimise staffing_cost", ["sl >= 0.8 in 100% of runs"], runs=4, budget=24,
                          holdout_seeds=20)
    assert few.verdict == "borderline" and not few.feasible and "Best decision, borderline" in few.summary()
    assert few.estimates["runs"] == 16  # the confirmation grew to four times the runs trying to settle it
    assert any("asks for every run" in note for note in few.notes)
    assert few.holdout["seed_luck"] and "clearly no longer holds" in few.summary()


def test_a_plan_picked_on_four_seeds_is_confirmed_on_more_seeds_until_its_share_of_runs_holds_with_confidence():
    few = fg_env.analysis.optimise(CENTRE, STAFFING, "minimise staffing_cost", ["sl >= 0.8 in 80% of runs"], runs=4, budget=24,
                          holdout_seeds=20)
    assert few.verdict == "feasible" and few.estimates["runs"] > 4
    assert fmean(sl >= 0.8 for sl in _fresh(CENTRE, few.best, "sl")) >= 0.8


def test_a_staffing_plan_judged_on_enough_seeds_keeps_its_service_level_on_fresh_seeds_for_no_more_than_the_rule():
    chosen = fg_env.analysis.optimise(CENTRE, STAFFING, "minimise staffing_cost", ["sl >= 0.8 in 80% of runs"], runs=16,
                             budget=24, workers=2)
    assert chosen.feasible and chosen.estimates["objectives"][0]["value"] <= MANAGER_COST
    assert fmean(sl >= 0.8 for sl in _fresh(CENTRE, chosen.best, "sl")) >= 0.8


def test_a_service_level_per_category_keeps_the_fill_rate_with_less_stock_across_the_fitted_uncertainty():
    priors = fg_env.analysis.fit_patterns(STORE).priors
    common = dict(data_dir=STORE.parent, uncertainty=priors, rounds=13)
    categories = ["brake_pads", "batteries", "wipers"]
    # the store's replenishment reads its service level per category from this map input. Eight runs: a fill rate held
    # with 90% confidence on four runs needs about four standard errors of room (a t-quantile on 3 degrees of freedom,
    # with the search's margin), so a plan confident on four runs keeps 86% of the uniform stock where one on eight keeps
    # 77% — both confident on 24 fresh seeds (lower 90% bounds 98.6% and 98.0% against 97.5%)
    result = fg_env.analysis.optimise(STORE, {"service_level_by_category": {"keys": categories, "low": 0.5, "high": 0.99, "step": 0.05,
                                                                   "start": {c: 0.95 for c in categories}}},
                             "minimise reorder_average_stock_value", ["shop_fill_rate >= 0.975"], runs=8, budget=16,
                             workers=2, inputs={"policy": "service", "parameter_uncertainty": 0}, **common)
    assert result.feasible and {"growth_rate", "promo_lift", "lead_noise_mean"} <= set(priors)

    def fresh(levels):
        runs = fg_env.experiment(STORE, runs=24, seed=9090, arms=[None], **common,
                                 inputs={"policy": "service", "parameter_uncertainty": 0, **levels}).arms["baseline"].runs
        return fmean(r.outputs["shop_fill_rate"] for r in runs), fmean(r.outputs["reorder_average_stock_value"] for r in runs)

    fill, stock = fresh(result.best)
    _, uniform_stock = fresh({})  # the example's service arm: a 95% service level in every category
    assert fill >= 0.965 and stock < 0.8 * uniform_stock


def test_prices_per_grade_stay_in_grade_order_and_beat_listing_at_the_fair_price_on_fresh_seeds():
    bounds = {"low": {"A": 250, "B": 210, "C": 170, "D": 100}, "high": {"A": 700, "B": 600, "C": 480, "D": 300}}
    result = fg_env.analysis.optimise(PHONES, {"price": {"keys": list("ABCD"), **bounds, "step": 10, "monotone": "decreasing"}},
                             "maximise profit", runs=8, budget=40)
    prices = result.best["price"]
    assert prices["A"] >= prices["B"] >= prices["C"] >= prices["D"]
    assert all(prices[grade] > 1.2 * FAIR[grade] for grade in FAIR)
    at_fair = {"price": {grade: round(price, -1) for grade, price in FAIR.items()}}
    assert fmean(_fresh(PHONES, result.best, "profit")) > 1.3 * fmean(_fresh(PHONES, at_fair, "profit"))
