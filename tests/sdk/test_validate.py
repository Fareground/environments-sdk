"""Validation tells the truth: errors per key and overall, bias, intervals that hold fewer actual values than they
claim, and whether the simulator beats the last value, the earlier mean and the value one season back."""
import pytest

import fg_env

#: Weekly sales per shop: demand scaled by a level, with optional noise.
SHOPS = {
    "name": "Shops", "clock": {"rounds": 1},
    "inputs": {"demand": {"type": "map", "default": {"north": 100, "south": 50}},
               "level": {"type": "number", "default": 1.0, "min": 0, "max": 5},
               "noise": {"type": "number", "default": 0, "min": 0, "max": 50}},
    "types": {"t": {"props": {"v": 0}}},
    "outputs": {
        "by_shop": {"type": "map", "expr": "$dict($keys($inputs.demand), $it, $get($inputs.demand, $it) * $inputs.level + $normal(0, $inputs.noise))"},
        "total": {"type": "number", "expr": "$sum($values($inputs.demand)) * $inputs.level"},
        "path": {"type": "list", "expr": "[$inputs.level, $inputs.level * 2]"}},
}


def _cases(actual_level=1.0, count=6, noise=0):
    """Cases whose actual values follow a known pattern: shop demand alternating over a two-case season."""
    out = []
    for i in range(count):
        demand = {"north": 100 + 10 * (i % 2), "south": 50 + 5 * (i % 2)}
        out.append({"name": f"week {i + 1}", "inputs": {"demand": demand, "noise": noise},
                    "actuals": {"by_shop": {k: v * actual_level for k, v in demand.items()},
                                "total": sum(demand.values()) * actual_level}})
    return out


def test_a_model_that_matches_the_actual_values_has_no_error_and_beats_every_baseline():
    result = fg_env.validate(SHOPS, _cases(), runs=3, season=2)
    total = result.measures["total"]
    assert total["overall"]["wape"] == 0 and total["overall"]["bias"] == 0 and total["overall"]["rmse"] == 0
    assert {row["baseline"]: row["skill"] for row in total["baselines"]} == {"last": 1.0, "mean": 1.0, "seasonal": None}
    assert result.warnings == []
    assert result.report().startswith("Validation of Shops: 6 case(s) × 3 run(s)")


def test_errors_are_reported_per_key_and_overall_with_bias_as_a_share_of_the_actual_total():
    result = fg_env.validate(SHOPS, _cases(actual_level=1 / 1.1), runs=2, baselines=())
    by_shop = result.measures["by_shop"]
    assert set(by_shop["keys"]) == {"north", "south"}
    assert by_shop["overall"]["bias"] == pytest.approx(0.1)
    assert by_shop["keys"]["north"]["wape"] == pytest.approx(0.1)
    assert "by_shop: forecasts run high by 10.0% of the actual total on average (± 0.0%)" in result.warnings
    assert [row["key"] for row in result.rows if row["case"] == "week 1" and row["measure"] == "by_shop"] == ["north", "south"]


def test_consistent_bias_with_spread_is_called_out():
    cases = _cases(actual_level=1 / 1.1, count=8, noise=0)
    for i, case in enumerate(cases):  # actual values scatter around a 10% over-forecast
        case["actuals"]["total"] *= 1 + (0.02 if i % 2 else -0.02)
    result = fg_env.validate(SHOPS, cases, runs=2, baselines=())
    assert any(text.startswith("total: forecasts run high by") for text in result.warnings)


def test_intervals_that_hold_too_few_actual_values_raise_a_loud_warning():
    noisy_actuals = _cases(count=10)
    for i, case in enumerate(noisy_actuals):
        case["actuals"]["by_shop"]["north"] += 30 if i % 2 else -30
    result = fg_env.validate(SHOPS, noisy_actuals, runs=10, levels=(0.8,), baselines=())
    [warning] = [w for w in result.warnings if "intervals held" in w]
    assert warning.startswith("by_shop: 80% intervals held 10 of 20 actual values (50%")
    assert "overconfident" in warning and "uncertainty=" in warning
    assert "WARNING: by_shop: 80% intervals held" in result.report()


def test_the_seasonal_naive_baseline_repeats_the_value_one_season_back_and_can_win():
    cases = _cases(actual_level=1.3)  # the simulator is 30% low; last year's same season is exact
    result = fg_env.validate(SHOPS, cases, runs=2, season=2, baselines=("seasonal",))
    [row] = result.measures["total"]["baselines"]
    assert row["label"] == "seasonal naive (2 back)" and row["n"] == 4 and row["reference"]["wape"] == 0
    assert any("worse than seasonal naive (2 back)" in text for text in result.warnings)


def test_list_actual_values_are_checked_position_by_position_and_held_out_cases_on_their_own():
    cases = [{"name": f"c{i}", "inputs": {"level": i}, "actuals": {"path": [i, 2 * i + 1]}} for i in range(1, 5)]
    result = fg_env.validate(SHOPS, cases, runs=1, baselines=(), test=["c4"])
    path = result.measures["path"]
    assert path["keys"]["0"]["wape"] == 0 and path["keys"]["1"]["mae"] == 1
    assert path["held_out"]["n"] == 2 and path["held_out"]["mae"] == 0.5


@pytest.mark.parametrize("cases, message", [
    ([{"name": "a", "inputs": {}}], "needs 'actuals'"),
    ([{"name": "a", "actuals": {"missing": 1}}], "'missing' is neither an output nor a metric"),
    ([{"name": "a", "actuals": {"total": "lots"}}], "actual total must be a number"),
])
def test_validation_mistakes_say_what_to_pass(cases, message):
    with pytest.raises(ValueError) as excinfo:
        fg_env.validate(SHOPS, cases, runs=1)
    assert message in str(excinfo.value)
