"""Random patterns and draws: correct statistics, fixed by seed, name and key, never shifting other draws."""
import math
import statistics

import pytest
from patterns_helpers import series, world

import fg_env


def _corr(xs, ys):
    return statistics.correlation(xs, ys)


def test_a_random_walk_without_noise_moves_by_its_drift_and_stays_within_bounds():
    got = series({"w": {"kind": "random_walk", "start": 0, "drift": 1, "sd": 0, "max": 3}}, {"w": "$pattern.w"},
                 rounds=6)
    assert got["w"] == [0, 1, 2, 3, 3, 3]


def test_a_geometric_walk_compounds_its_drift():
    got = series({"w": {"kind": "random_walk", "start": 100, "drift": 0.1, "sd": 0, "form": "multiply"}},
                 {"w": "$pattern.w"}, rounds=3)
    assert got["w"] == pytest.approx([100, 100 * math.exp(0.1), 100 * math.exp(0.2)])


def test_mean_reversion_closes_the_gap_exponentially_and_its_spread_matches_theory():
    calm = series({"m": {"kind": "mean_reversion", "mean": 10, "rate": 0.5, "start": 20}}, {"m": "$pattern.m"},
                  rounds=4)
    assert calm["m"] == pytest.approx([10 + 10 * math.exp(-0.5 * t) for t in range(4)])
    long = series({"m": {"kind": "mean_reversion", "mean": 0, "rate": 0.2, "sd": 1}}, {"m": "$pattern.m"}, rounds=4000)
    assert statistics.pstdev(long["m"][200:]) == pytest.approx(1 / math.sqrt(0.4), rel=0.12)


def test_autoregression_carries_its_coefficients_of_the_last_steps():
    got = series({"a": {"kind": "autoregressive", "coefficients": [0.5], "mean": 10, "sd": 0, "start": 18}},
                 {"a": "$pattern.a"}, rounds=4)
    assert got["a"] == [18, 14, 12, 11]


def test_volatility_clusters_and_its_long_run_variance_matches_garch():
    omega, alpha, beta = 0.0001, 0.1, 0.85
    got = series({"r": {"kind": "volatility", "omega": omega, "alpha": alpha, "beta": beta}}, {"r": "$pattern.r"},
                 rounds=6000)
    squares = [r * r for r in got["r"][100:]]
    assert statistics.fmean(squares) == pytest.approx(omega / (1 - alpha - beta), rel=0.25)
    assert _corr(squares[:-1], squares[1:]) > 0.05


def test_regimes_switch_by_their_transition_probabilities_and_give_their_values():
    got = series({"e": {"kind": "regimes", "states": {"boom": {"g": 2}, "bust": {"g": -1}},
                        "transitions": {"boom": {"bust": 1}, "bust": {"boom": 1}}}},
                 {"g": "$pattern.e.g"}, rounds=4)
    assert got["g"] == [2, -1, 2, -1]
    sticky = series({"e": {"kind": "regimes", "states": {"on": 1, "off": 0},
                           "transitions": {"on": {"off": 0.1}, "off": {"on": 0.3}}}}, {"e": "$pattern.e"}, rounds=5000)
    assert statistics.fmean(sticky["e"]) == pytest.approx(0.75, abs=0.05)


def test_scheduled_shocks_last_then_end_and_random_ones_respect_gap_and_limit():
    got = series({"s": {"kind": "shocks", "at": [2], "size": 3, "lasts": 2}}, {"s": "$pattern.s"}, rounds=6)
    assert got["s"] == [0, 0, 3, 3, 0, 0]
    fading = series({"s": {"kind": "shocks", "at": [1], "size": -0.5, "half_life": 1, "form": "multiply"}},
                    {"s": "$pattern.s"}, rounds=4)
    assert fading["s"] == pytest.approx([1, 0.5, 0.75, 0.875])
    spaced = series({"s": {"kind": "shocks", "chance": 1, "gap": 2, "limit": 3, "size": 1}}, {"s": "$pattern.s"},
                    rounds=12)
    assert spaced["s"] == [1, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0]


def test_noise_is_fresh_each_round_and_independent_across_keys():
    measures = {"a": "$pattern.n('a')", "b": "$pattern.n('b')"}
    got = series({"n": {"kind": "noise", "sd": 2}}, measures, rounds=3000)
    assert statistics.fmean(got["a"]) == pytest.approx(0, abs=0.15)
    assert statistics.pstdev(got["a"]) == pytest.approx(2, rel=0.06)
    assert abs(_corr(got["a"], got["b"])) < 0.06
    assert abs(_corr(got["a"][:-1], got["a"][1:])) < 0.06


def test_weather_follows_its_seasonal_normal_with_persistent_departures():
    got = series({"t": {"kind": "weather", "mean": 18, "amplitude": 9, "peak": 0.55, "persistence": 0.7, "sd": 3}},
                 {"t": "$pattern.t"}, rounds=52 * 60, clock={"unit": "week", "start": "2000-01-03"})
    temps = got["t"]
    assert statistics.fmean(temps) == pytest.approx(18, abs=0.4)
    normals = series({"n": {"kind": "seasonal", "amplitude": 9, "peak": 0.55, "form": "add"}}, {"n": "$pattern.n"},
                     rounds=52 * 60, clock={"unit": "week", "start": "2000-01-03"})["n"]
    anomalies = [t - 18 - n for t, n in zip(temps, normals)]
    assert statistics.pstdev(anomalies) == pytest.approx(3, rel=0.08)
    assert _corr(anomalies[:-1], anomalies[1:]) == pytest.approx(0.7, abs=0.05)


def test_draws_are_fixed_for_a_run_per_key_and_follow_their_distribution():
    keys = "$range(1500)"
    contract = world({"d": {"kind": "draw", "keys": keys, "dist": "normal", "mean": 5, "sd": 2}},
                     metrics={"d": "$pattern_values('d')"}, rounds=2)
    result = fg_env.run(contract, "idle", seed=4)
    first, second = result.series["d"]
    assert first == second
    values = list(first.values())
    assert (statistics.fmean(values) == pytest.approx(5, abs=0.2) and statistics.pstdev(values)
            == pytest.approx(2, rel=0.08))


def test_correlated_draws_per_key_carry_their_covariance():
    contract = world({"t": {"kind": "draw", "keys": "$range(2000)", "dist": "mvnormal", "means": [0, 1],
                            "cov": [[1, 0.6], [0.6, 1]], "names": ["x", "y"]}},
                     metrics={"t": "$pattern_values('t')"}, rounds=1)
    values = list(fg_env.run(contract, "idle", seed=2).series["t"][0].values())
    assert _corr([v["x"] for v in values], [v["y"] for v in values]) == pytest.approx(0.6, abs=0.05)
    assert statistics.fmean(v["y"] for v in values) == pytest.approx(1, abs=0.06)


def test_segments_split_keys_by_share_and_give_their_values():
    contract = world({"s": {"kind": "segments", "keys": "$range(3000)", "segments": {
        "bargain": {"share": 3, "values": {"elasticity": -2.4}},
        "loyal": {"share": 1, "values": {"elasticity": -0.8}}}}}, metrics={"s": "$pattern_values('s')"}, rounds=1)
    values = list(fg_env.run(contract, "idle", seed=3).series["s"][0].values())
    bargain = [v for v in values if v["segment"] == "bargain"]
    assert len(bargain) / len(values) == pytest.approx(0.75, abs=0.03)
    assert {v["elasticity"] for v in bargain} == {-2.4}


def test_the_same_seed_repeats_every_random_path_and_another_seed_changes_it():
    patterns = {"w": {"kind": "random_walk", "sd": 1}}
    one = series(patterns, {"w": "$pattern.w"}, rounds=5, seed=7)
    assert one == series(patterns, {"w": "$pattern.w"}, rounds=5, seed=7)
    assert one != series(patterns, {"w": "$pattern.w"}, rounds=5, seed=8)


def test_adding_a_pattern_or_a_draw_elsewhere_never_shifts_another_patterns_path():
    base = {"w": {"kind": "random_walk", "sd": 1}}
    more = {**base, "extra": {"kind": "noise"}, "d": {"kind": "draw", "dist": "uniform", "low": 0, "high": 1}}
    measures = {"w": "$pattern.w"}
    alone = series(base, measures, rounds=6, seed=3)
    crowded = series(more, {**measures, "e": "$pattern.extra + $pattern.d + $uniform(0, 1)"}, rounds=6, seed=3)
    assert crowded["w"] == alone["w"]


def test_arms_share_random_paths_so_policies_compare_on_the_same_luck():
    contract = world({"w": {"kind": "random_walk", "sd": 1, "start": "$inputs.start"}},
                     metrics={"w": "$pattern.w"}, rounds=5,
                     inputs={"start": {"type": "number", "default": 0}}, arms={"high": {"inputs": {"start": 10}}})
    base = fg_env.run(contract, "idle", seed=5).series["w"]
    high = fg_env.load(contract, seed=5, arm="high").run("idle").series["w"]
    assert [h - b for h, b in zip(high, base)] == pytest.approx([10] * 5)


def test_a_key_on_an_unkeyed_random_pattern_gives_each_item_its_own_draws():
    got = series({"n": {"kind": "noise"}}, {"a": "$pattern.n", "b": "$pattern.n('x')", "c": "$pattern.n('x')"},
                 rounds=3)
    assert got["b"] == got["c"] != got["a"]
