"""What each pattern adds, told for an owner: a calendar pattern at its highest and lowest slot, a promotion by the
rounds it is on, anything else over the horizon — and read at the fitted estimates, not one run's draw of them."""
import json
from pathlib import Path

import pytest

import fg_env
from fg_env.report.pattern_effects import Effects, Factor, clauses

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"
FACTORS = {"season": Factor("season", "the time of year", "calendar"), "promo": Factor("promo", "promotions", "switch"),
           "trend": Factor("trend", "the trend", "overall"), "flat": Factor("flat", "price changes", "overall")}


def test_a_calendar_pattern_is_told_at_its_peak_and_trough_and_a_promotion_by_the_distinct_rounds_it_is_on():
    effects = Effects()
    for round_, (month, season, promo) in enumerate([("January", 1.5, 1.0), ("February", 1.0, 1.3), ("March", 0.5, 0.9)], 1):
        for _item in range(4):  # four items in every round: a promotion still ran for one round, not four
            effects.add(round_, 10.0 * season * promo, {"season": season, "promo": promo, "trend": 1.02, "flat": 1.001},
                        {"season": (month, f"in {month}")})
    told = clauses(effects, FACTORS, "week", "3 weeks")
    assert told["season"] == "the time of year adds 50% in January and takes away 50% in March"
    assert told["promo"] == ("promotions add 30% over the 1 week they raise it and take away 10% over the 1 week they "
                             "lower it")
    assert told["trend"] == "the trend adds 2% over the 3 weeks"
    assert "flat" not in told  # a tenth of a percent is not worth naming
    assert list(told)[0] == "season"  # strongest first


def test_reading_a_contract_at_its_estimates_ignores_the_draws_its_standard_errors_would_make():
    contract = EXAMPLES / "phone_reseller.json"
    drawn = fg_env.analysis.decompose(contract, "wholesale_demand", key="17-A", rounds=[1], seed=4).rows[0]
    at_estimates = fg_env.analysis.decompose(contract, "wholesale_demand", key="17-A", rounds=[1], seed=4, estimates=True).rows[0]
    fixed = fg_env.analysis.decompose(contract, "wholesale_demand", key="17-A", rounds=[1],
                             inputs={"parameter_uncertainty": 0}).rows[0]
    assert at_estimates["total"] == pytest.approx(fixed["total"], rel=1e-12)
    assert drawn["total"] != pytest.approx(fixed["total"], rel=1e-6)


def test_an_owner_report_of_a_demand_says_what_each_pattern_adds_per_category():
    contract = EXAMPLES / "auto_parts_store.json"
    exp = fg_env.experiment(contract, arms=["service"], runs=1, seed=5, inputs={"weeks": 26})
    lines = next(s for s in fg_env.analysis.report(exp, contract=contract).sections if s.title == "What drives it").lines
    assert any(line.startswith("Demand over the 26 weeks is about") for line in lines)
    categories = [line for line in lines if line.split(" (")[0] in ("Batteries", "Brake pads", "Wipers")]
    assert len(categories) == 3 and all("the time of year adds" in line for line in categories)
    assert any("promotions add" in line and "weeks they raise it" in line for line in categories)
    assert json.dumps(lines).count("shop_") == 0
