"""Responses, observations, memory and composition give the values their definitions say."""
import math
import statistics

import pytest
from patterns_helpers import series, world

import fg_env


def _one(patterns, expr, **options):
    return series(patterns, {"v": expr}, rounds=1, **options)["v"][0]


def test_constant_and_linear_elasticity_scale_demand_by_price():
    patterns = {"c": {"kind": "elasticity", "elasticity": -2, "reference": 10},
                "l": {"kind": "elasticity", "elasticity": -2, "reference": 10, "form": "linear"}}
    assert _one(patterns, "$pattern.c(20)") == pytest.approx(0.25)
    assert _one(patterns, "$pattern.l(12)") == pytest.approx(0.6)
    assert _one(patterns, "$pattern.l(30)") == 0


def test_cross_price_substitution_answers_every_items_price():
    patterns = {"x": {"kind": "cross_price", "keys": ["a", "b", "c"], "reference": 10, "own": -2, "cross": 0.5,
                      "groups": {"a": "pads", "b": "pads", "c": "oil"}}}
    prices = "{a: 20, b: 10, c: 5}"
    assert _one(patterns, f"$pattern.x({prices}, 'a')") == pytest.approx(0.25 * 0.5 ** 0.0)
    assert _one(patterns, f"$pattern.x({prices}, 'b')") == pytest.approx(2 ** 0.5)
    assert _one(patterns, f"$pattern.x({prices}, 'c')") == pytest.approx(0.5 ** -2)


def test_saturation_thresholds_learning_and_network_effects_follow_their_curves():
    patterns = {"h": {"kind": "saturation", "half": 2, "limit": 1},
                "e": {"kind": "saturation", "form": "exponential", "scale": 1, "limit": 2, "base": 1},
                "t": {"kind": "threshold", "at": 5, "below": 1, "above": 3},
                "s": {"kind": "threshold", "at": 5, "below": 1, "above": 3, "width": 2},
                "k": {"kind": "learning_curve", "first": 100, "rate": 0.8},
                "n": {"kind": "network", "strength": 0.1, "exponent": 2}}
    assert _one(patterns, "$pattern.h(2)") == pytest.approx(0.5)
    assert _one(patterns, "$pattern.e(1)") == pytest.approx(1 + 2 * (1 - math.exp(-1)))
    assert (_one(patterns, "$pattern.t(4.9)"), _one(patterns, "$pattern.t(5)")) == (1, 3)
    assert _one(patterns, "$pattern.s(5)") == pytest.approx(2)
    assert _one(patterns, "$pattern.k(4)") == pytest.approx(64)
    assert _one(patterns, "$pattern.n(10)") == pytest.approx(11)


def test_hazards_give_the_chance_of_leaving_now_given_age():
    patterns = {"w": {"kind": "hazard", "form": "weibull", "shape": 1, "scale": 10},
                "r": {"kind": "hazard", "form": "weibull", "shape": 2, "scale": 10},
                "t": {"kind": "hazard", "form": "table", "values": [0.5, 0.2, 0.1]}}
    assert _one(patterns, "$pattern.w(3)") == pytest.approx(1 - math.exp(-0.1))
    assert _one(patterns, "$pattern.r(10)") > _one(patterns, "$pattern.r(1)")
    assert (_one(patterns, "$pattern.t(1)"), _one(patterns, "$pattern.t(9)")) == (0.2, 0.1)


def _counts(dist, mean, **extra):
    contract = world({"c": {"kind": "counts", "dist": dist, **extra}},
                     metrics={"c": f"$map($range(4000), $pattern.c({mean}, $it))"}, rounds=1)
    return fg_env.run(contract, "idle", seed=9).series["c"][0]


def test_poisson_counts_have_variance_equal_to_their_mean():
    values = _counts("poisson", 4)
    assert statistics.fmean(values) == pytest.approx(4, rel=0.04)
    assert statistics.pvariance(values) == pytest.approx(4, rel=0.08)


def test_negative_binomial_counts_are_overdispersed_by_their_dispersion():
    values = _counts("negative_binomial", 6, dispersion=2)
    assert statistics.fmean(values) == pytest.approx(6, rel=0.05)
    assert statistics.pvariance(values) == pytest.approx(6 + 36 / 2, rel=0.12)


def test_a_larger_expected_count_never_draws_fewer_for_the_same_key_and_round():
    got = series({"c": {"kind": "counts", "dispersion": 3}},
                 {"low": "$map($range(300), $pattern.c(4, $it))", "high": "$map($range(300), $pattern.c(6, $it))"},
                 rounds=2)
    for low, high in zip(got["low"], got["high"]):
        assert all(a <= b for a, b in zip(low, high))


def test_measurement_error_is_biased_and_spread_as_declared():
    contract = world({"m": {"kind": "measurement", "sd": 0.1, "bias": 0.05}},
                     metrics={"m": "$map($range(4000), $pattern.m(200, $it))"}, rounds=1)
    values = fg_env.run(contract, "idle", seed=1).series["m"][0]
    assert statistics.fmean(values) == pytest.approx(210, rel=0.01)
    assert statistics.pstdev(values) == pytest.approx(20, rel=0.06)


def test_censoring_caps_what_is_seen_and_keeps_what_was_lost():
    patterns = {"sold": {"kind": "censored"}}
    assert _one(patterns, "$pattern.sold(8, 5)") == {"value": 5, "lost": 3, "censored": True}
    assert _one(patterns, "$pattern.sold(4, 5)") == {"value": 4, "lost": 0, "censored": False}


def test_missing_readings_are_null_with_their_chance():
    assert _one({"m": {"kind": "missing", "chance": 1}}, "$pattern.m(3)") is None
    assert _one({"m": {"kind": "missing", "chance": 0}}, "$pattern.m(3)") == 3


def _memory(patterns, measure, rounds, schedule):
    """A world whose `x` follows ``schedule`` (one value per round, set by a start event), measuring ``measure``."""
    contract = world(patterns, metrics={"v": measure}, rounds=rounds, world={"x": 0.0},
                     inputs={"plan": {"type": "list", "default": schedule}},
                     events=[{"phase": "start", "do": ["$world.x = $inputs.plan[$round - 1]"]}])
    return fg_env.run(contract, "idle", seed=1).series["v"]


def test_carryover_adds_past_inputs_fading_by_retain_and_lags_them():
    assert _memory({"a": {"kind": "carryover", "input": "$world.x", "retain": 0.5}}, "$pattern.a", 3, [10, 10, 10]) == \
        [10, 15, 17.5]
    assert _memory({"a": {"kind": "carryover", "input": "$world.x", "retain": 0, "lag": 1}}, "$pattern.a", 4,
                   [5, 7, 9, 11]) == [0, 5, 7, 9]
    assert _memory({"a": {"kind": "carryover", "input": "$world.x", "half_life": 1, "form": "average", "start": 10}},
                   "$pattern.a", 2, [20, 20]) == pytest.approx([15, 17.5])


def test_a_promotion_lifts_demand_while_it_runs_then_dips_while_its_memory_fades():
    got = _memory({"p": {"kind": "promotion", "input": "$world.x", "lift": 1, "dip": 0.5, "retain": 0.5}},
                  "$pattern.p", 5, [0, 0.5, 0, 0, 0])
    assert got == pytest.approx([1, 1.5, 0.875, 0.9375, 0.96875])


def test_reference_prices_reward_prices_below_memory_and_punish_those_above_more():
    got = _memory({"r": {"kind": "reference_price", "input": "$world.x", "retain": 0.5, "gain": 1, "loss": 2}},
                  "$pattern.r", 3, [10, 8, 12])
    # remembered: 10, then 9 after paying 8; 12 is a third above 9
    assert got == pytest.approx([1, 1.2, 1 - 2 * 3 / 9])


def test_fatigue_wears_response_down_with_repeated_exposure_and_recovers_when_it_stops():
    got = _memory({"f": {"kind": "habit", "form": "fatigue", "input": "$world.x", "strength": 1, "retain": 0.5}},
                  "$pattern.f", 4, [1, 1, 0, 0])
    assert got == pytest.approx([1 / 2, 1 / 2.5, 1 / 1.75, 1 / 1.375])


def test_memory_patterns_keep_their_state_per_entity_key():
    contract = world({"a": {"kind": "carryover", "keys": "shop", "input": "$it.spend", "retain": 0.5}},
                     types={"shop": {"props": {"spend": 0.0}}},
                     entities={"s1": {"type": "shop", "props": {"spend": 2}},
                               "s2": {"type": "shop", "props": {"spend": 4}}},
                     metrics={"a": "$pattern_values('a')"}, rounds=2)
    assert fg_env.run(contract, "idle", seed=1).series["a"] == [{"s1": 2, "s2": 4}, {"s1": 3, "s2": 6}]


def test_a_product_combines_factors_under_their_own_keys_and_a_sum_weighs_them():
    patterns = {
        "season": {"kind": "seasonal", "period": 2, "table": "$inputs.cats", "column": "cat",
                   "profile": "$row.profile"},
        "trend": {"kind": "trend", "start": 1, "slope": 0.5},
        "demand": {"kind": "product", "table": "$inputs.skus", "column": "sku", "scale": "$row.base",
                   "of": ["trend", {"pattern": "season", "key": "$row.cat"}]},
        "mix": {"kind": "sum", "of": ["trend", "trend2"], "weights": [2, -1], "base": 1},
        "trend2": {"kind": "trend", "start": 0, "slope": 1},
    }
    inputs = {"cats": {"type": "table", "default": [{"cat": "pads", "profile": [1, 2]}]},
              "skus": {"type": "table", "default": [{"sku": "p1", "cat": "pads", "base": 10}]}}
    got = series(patterns, {"d": "$pattern.demand('p1')", "m": "$pattern.mix"}, rounds=3, inputs=inputs)
    assert got["d"] == pytest.approx([10 * 1 * 1, 10 * 1.5 * 2, 10 * 2 * 1])
    assert got["m"] == pytest.approx([3, 3, 3])
