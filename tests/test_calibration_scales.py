"""Calibrating one parameter to cases with mixed targets weighs a small rate and a large share alike: each target's
errors count against how much that target varies across the cases, so the fit no longer trades the share away."""
import pytest

import fg_env

#: Service level fits its goals best at p = 0.75, abandonment at p = 0.5: the two targets pull p different ways.
CENTRE = {"name": "Toy centre", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
          "inputs": {"p": {"type": "number", "default": 0.5, "min": 0, "max": 1},
                     "day": {"type": "number", "default": 0, "min": -1, "max": 1}},
          "outputs": {"sl": {"type": "number", "expr": "0.70 + 0.20 * $inputs.p + 0.03 * $inputs.day"},
                      "abandon": {"type": "number", "expr": "0.03 + 0.04 * $inputs.p + 0.01 * $inputs.day"}}}
DAYS = [-1, -0.5, 0, 0.5, 1]


def _days(scale_by_goal=False):
    cases = []
    for i, day in enumerate(DAYS):
        sl, abandon = 0.85 + 0.03 * day, 0.05 + 0.01 * day
        targets = {"sl": sl, "abandon": abandon}
        if scale_by_goal:
            targets = {"sl": {"value": sl, "scale": sl}, "abandon": {"value": abandon, "scale": abandon}}
        cases.append({"name": f"day {i}", "inputs": {"day": day}, "targets": targets})
    return cases


def _fit(cases):
    return fg_env.analysis.calibrate(CENTRE, cases, {"p": {"low": 0, "high": 1}}, runs=1, budget=40, method="golden").params["p"]


def test_mixed_targets_are_weighed_by_their_spread_so_the_share_is_not_traded_for_the_rate():
    p = _fit(_days())
    assert 0.6 < p < 0.75  # between abandonment's best (0.5) and service level's (0.75)
    assert abs(0.20 * p - 0.15) < 0.03  # service level within 3 points


def test_scaling_by_the_goal_lets_the_small_rate_dominate_as_the_study_found():
    p = _fit(_days(scale_by_goal=True))
    assert p == pytest.approx(0.5, abs=0.05)
    assert abs(0.20 * p - 0.15) > 0.04  # service level more than 4 points off


def test_a_single_target_keeps_its_fit_as_a_share_off():
    cases = [{"name": f"day {i}", "inputs": {"day": day}, "targets": {"sl": 0.85 + 0.03 * day}}
             for i, day in enumerate(DAYS)]
    result = fg_env.analysis.calibrate(CENTRE, cases, {"p": {"low": 0, "high": 1}}, runs=1, budget=40, method="golden")
    assert result.params["p"] == pytest.approx(0.75, abs=0.01)
    [detail] = [d for d in result.validation["targets"] if d["case"] == "day 2"]
    assert detail["error"] == pytest.approx((0.70 + 0.20 * result.params["p"] - 0.85) / 0.85, rel=1e-6)
