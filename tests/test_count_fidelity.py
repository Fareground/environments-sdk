"""Demand-count fidelity beyond toy volumes, including stockout-conditioned fitting."""
import math

import pytest

import fg_env
from fg_env.patterns.numeric import truncated_mean
from fg_env.patterns.observe import count_quantile


@pytest.mark.parametrize("mean,dispersion,expected", [
    (1000, None, [927, 1000, 1074]),
    (5000, None, [4836, 5000, 5165]),
    (10000, 1, [100, 6931, 46054]),
    (10000, 0.5, [1, 4549, 66350]),
    (10000, 10, [4127, 9669, 18788]),
])
def test_quantiles_match_independent_distribution_references(mean, dispersion, expected):
    # Reference values independently checked with scipy.stats; scipy is not a dependency.
    assert [count_quantile(u, mean, dispersion) for u in [0.01, 0.5, 0.99]] == expected


@pytest.mark.parametrize("mean", [1, 100, 5000, 5001, 10000, 1000000])
def test_geometric_special_case_retains_its_exact_heavy_tail(mean):
    for u in [0.001, 0.1, 0.5, 0.9, 0.999]:
        expected = math.ceil(math.log1p(-u) / -math.log1p(1 / mean)) - 1
        assert count_quantile(u, mean, 1) == expected


@pytest.mark.parametrize("dispersion", [None, 0.1, 1, 10, 1000])
def test_shared_draws_stay_ordered_as_volume_crosses_numerical_boundaries(dispersion):
    for u in [0.001, 0.01, 0.5, 0.9, 0.999]:
        values = [count_quantile(u, mean, dispersion) for mean in [700, 745, 750, 1000, 4999, 5000, 5001, 10000]]
        assert values == sorted(values)


def test_large_skewed_tail_keeps_integer_quantile_precision():
    assert count_quantile(0.9, 1000000, 0.1) == 2661546
    assert count_quantile(0.999999, 1000000, 0.1) == 94570275


@pytest.mark.parametrize("mean,dispersion,floor,expected", [
    (1000, None, 1000, 1025.0188023514744),
    (5000, None, 5100, 5131.826287970149),
    (5000, 10000, 5000, 5068.991546682751),
])
def test_stockout_conditioned_mean_recovers_mass_above_the_observed_limit(mean, dispersion, floor, expected):
    assert truncated_mean(mean, dispersion, floor) == pytest.approx(expected, rel=1e-9)


def test_public_count_patterns_do_not_collapse_high_volume_demand_and_replay():
    contract = {"name": "High-volume arrivals", "clock": {"rounds": 3}, "types": {"item": {}},
                "patterns": {"arrivals": {"kind": "counts", "dist": "poisson"}},
                "metrics": {"arrivals": "$map($range(30), $pattern.arrivals(1000, $it))"}}
    env = fg_env.load(contract, seed=19)
    env.run(rounds=1)
    snapshot = env.snapshot()
    result = env.run()
    restored = fg_env.Env.restore(contract, snapshot).run()
    assert result.series == restored.series
    draws = [v for row in result.series["arrivals"] for v in row]
    assert len(set(draws)) > 10
    assert all(800 < v < 1200 for v in draws)
