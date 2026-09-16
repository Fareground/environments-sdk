"""Unknown forecast outputs must never be scored as negative events or new categories."""
import math

import pytest

import fg_env
from fg_env.sdk.analysis.runner import AnalysisError


def contract(output):
    return {"name": "Demand forecast evidence", "clock": {"rounds": 1},
            "types": {"shop": {}}, "entities": {"shop": {"type": "shop"}}, "outputs": {"demand": str(output)}}


@pytest.mark.parametrize("outcome, threshold", [(True, 10), (True, None), ("high", None), (20, None)])
@pytest.mark.parametrize("output", ["null", "$choice([null, 20])", "1e309"])
def test_missing_or_wrongly_typed_outputs_cannot_be_scored(outcome, threshold, output):
    with pytest.raises(AnalysisError, match="launch-week.*forecast output.*missing or invalid"):
        fg_env.backtest(contract(output), [{"name": "launch-week", "outcome": outcome}], "demand",
                        runs=12, seed=7, threshold=threshold)


@pytest.mark.parametrize("threshold", [math.nan, math.inf, -math.inf, True, "10"])
def test_invalid_thresholds_fail_before_running(threshold):
    with pytest.raises(ValueError, match="threshold must be a finite number"):
        fg_env.backtest(contract(20), [{"outcome": True}], "demand", runs=1, threshold=threshold)


def test_categorical_outcomes_cannot_silently_ignore_a_threshold():
    with pytest.raises(ValueError, match="not categorical outcomes"):
        fg_env.backtest(contract("'high'"), [{"outcome": "high"}], "demand", threshold=10, runs=1)


@pytest.mark.parametrize("output, outcome, threshold, expected", [
    (20, True, 10, 1.0), (0, False, 10, 0.0),
    ("true", True, None, 1.0), ("false", False, None, 0.0),
    ("'high'", "high", None, {"high": 1.0}), (20, 20, None, [20.0] * 3),
])
def test_valid_forecast_types_and_legitimate_zero_still_score(output, outcome, threshold, expected):
    result = fg_env.backtest(contract(output), [{"outcome": outcome}], "demand", runs=3, threshold=threshold)
    assert result.cases[0]["forecast"] == expected
    assert result.cases[0]["runs"] == 3
