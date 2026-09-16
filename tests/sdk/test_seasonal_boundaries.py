"""Seasonal profiles keep exact slot boundaries across repeated cycles and fitting."""
import math

import pytest

import fg_env
from fg_env.sdk.patterns import timebase


def contract(period, profile, rounds=200):
    return {"name": "Seasonal boundaries", "clock": {"rounds": rounds},
            "types": {"observer": {}}, "entities": {"observer": {"type": "observer"}},
            "patterns": {"season": {"kind": "seasonal", "period": period, "profile": profile}},
            "metrics": {"value": "$pattern.season"}}


@pytest.mark.parametrize("period", [3, 5, 7, 11, 24, 49])
def test_long_running_profiles_match_the_integer_cycle(period):
    result = fg_env.run(contract(period, list(range(period))), seed=1)
    assert result.status == "completed", result.error
    assert result.series["value"] == [t % period for t in range(200)]


def test_fractional_times_keep_the_same_cycle():
    assert [timebase.slot(None, t * 0.25, 1.25, 5) for t in range(200)] == [t % 5 for t in range(200)]


def test_half_hour_profiles_align_before_and_after_the_clock_origin():
    for day in range(-60, 60):
        assert [timebase.slot(None, day * 1440 + half_hour * 30, 720, 24)
                for half_hour in range(24)] == list(range(24))


@pytest.mark.parametrize("period", [5, 49])
def test_a_point_just_before_a_boundary_is_not_rounded_into_the_next_slot(period):
    t = float(period * 8 + 1)
    assert timebase.slot(None, math.nextafter(t, -math.inf), period, period) == 0
    assert timebase.slot(None, t, period, period) == 1
    assert timebase.slot(None, math.nextafter(t, math.inf), period, period) == 1
    assert timebase.slot(None, float(period * 10**12 + 1), period, period) == 1


def test_fitted_profile_and_runtime_use_the_same_slot_assignment():
    c = contract(49, [1] * 49, rounds=196)
    c["inputs"] = {"history": {"type": "table", "default": [
        {"t": t, "y": 10 * (t % 49 + 1)} for t in range(98)]}}
    c["patterns"]["season"]["fit"] = {"data": "$inputs.history", "time": "t", "value": "y"}
    c["metrics"]["value"] = "250 * $pattern.season"
    fitted = fg_env.fit_patterns(c)
    result = fg_env.run(fitted.contract, seed=1, inputs={"parameter_uncertainty": 0})
    assert result.status == "completed", result.error
    assert result.series["value"] == pytest.approx([10 * (t % 49 + 1) for t in range(196)])
