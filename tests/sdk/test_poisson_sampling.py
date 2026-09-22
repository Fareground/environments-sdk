"""Public Poisson paths retain count skew, replay and numerical precision."""
import math
import random
import statistics
from decimal import Decimal, localcontext

import pytest

import fg_env
from fg_env.sdk.poisson import _log_mass, sample_poisson


@pytest.mark.parametrize('mean,count', [(600, 0), (600, 10), (600, 599), (600, 650),
                                       (5000, 4999), (5000, 5300)])
def test_acceptance_mass_matches_independent_high_precision_factorials(mean, count):
    with localcontext() as ctx:
        ctx.prec = 70
        expected = Decimal(count) * Decimal(mean).ln() - Decimal(mean) - Decimal(math.factorial(count)).ln()
    assert _log_mass(count, mean) == pytest.approx(float(expected), abs=1e-11)


def test_large_mean_mass_avoids_catastrophic_cancellation():
    # Independent high-precision expansion at the mode, where deviance is exactly zero.
    mean = 10**12
    expected = -0.5 * math.log(2 * math.pi * mean) - 1 / (12 * mean)
    assert _log_mass(mean, mean) == pytest.approx(expected, abs=1e-13)
    assert _log_mass(mean + 1, mean) - _log_mass(mean, mean) == pytest.approx(-math.log1p(1 / mean), abs=3e-15)


def test_large_poisson_preserves_skewness_not_just_mean_and_variance():
    rng = random.Random(927)
    n, mean = 250000, 600
    values = [sample_poisson(rng, mean) for _ in range(n)]
    average = statistics.fmean(values)
    variance = statistics.fmean((x - average)**2 for x in values)
    skewness = statistics.fmean((x - average)**3 for x in values) / variance**1.5
    assert abs(average - mean) < 5 * math.sqrt(mean / n)
    assert abs(variance - mean) < 5 * math.sqrt((mean + 2 * mean**2) / n)
    assert abs(skewness - 1 / math.sqrt(mean)) < 5 * math.sqrt(6 / n)


@pytest.mark.parametrize('mean', [0, 4, 10, 20])
def test_small_means_preserve_legacy_samples_and_rng_state(mean):
    rng, legacy = random.Random(17), random.Random(17)
    for _ in range(20):
        count, product = 0, legacy.random()
        while product > math.exp(-mean):
            count += 1
            product *= legacy.random()
        assert sample_poisson(rng, mean) == count
    assert rng.getstate() == legacy.getstate()


@pytest.mark.parametrize('mean', [20.001, 21, 30, 50, 100, 250, 500, 501, 5000, 10**6, 10**12, 2**52])
def test_mean_and_variance_across_supported_scales(mean):
    rng = random.Random(43)
    n = 20000
    residuals = [(sample_poisson(rng, mean) - mean) / math.sqrt(mean) for _ in range(n)]
    assert abs(statistics.fmean(residuals)) < 6 / math.sqrt(n)
    assert abs(statistics.pvariance(residuals) - 1) < 6 * math.sqrt((2 + 1 / mean) / n)


def test_uniform_endpoints_do_not_divide_by_zero_or_take_log_zero():
    class EndpointRandom:
        def __init__(self):
            self.values = iter([0, 0.5, 0.51, 0])
        def random(self):
            return next(self.values)
    assert sample_poisson(EndpointRandom(), 600) >= 0


@pytest.mark.parametrize('mean', [-1, float('inf'), float('nan'), 2**52 + 1, 10**400])
def test_unrepresentable_or_invalid_mean_fails_explicitly(mean):
    with pytest.raises(ValueError, match='Poisson mean'):
        sample_poisson(random.Random(1), mean)


def test_both_public_paths_replay_and_population_draws_are_stable_by_key():
    c = {'name': 'Poisson demand', 'types': {'item': {}}, 'clock': {'rounds': 3},
         'patterns': {'population': {'kind': 'draw', 'dist': 'poisson', 'mean': 600, 'keys': '$range(100)'}},
         'metrics': {'population': "$pattern_values('population')", 'fresh': '$map($range(100), $poisson(600))'}}
    env = fg_env.load(c, seed=19)
    env.run(rounds=1)
    resumed = fg_env.Env.restore(c, env.snapshot())
    result = env.run()
    assert result.ok, result.error
    assert result.to_dict() == resumed.run().to_dict()
    assert result.series['population'][0] == result.series['population'][2]
    assert result.series['fresh'][0] != result.series['fresh'][2]


@pytest.mark.parametrize('pattern', [False, True])
def test_large_mean_failure_has_public_authored_context(pattern):
    c = {'name': 'Invalid demand', 'types': {'item': {}}, 'clock': {'rounds': 1}, 'world': {'draw': 0},
         'patterns': {'population': {'kind': 'draw', 'dist': 'poisson', 'mean': 2**53}},
         'events': [{'do': '$world.draw = ' + ('$pattern.population' if pattern else '$poisson(9007199254740992)')}]}
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(c, seed=1)
    result = failed.value.result
    assert result.status == 'failed'
    assert 'Poisson mean must be' in result.error
    assert 'events[0]' in result.error or 'patterns.population' in result.error


@pytest.mark.parametrize('mean,cuts', [(21, [12, 20, 30]), (100, [80, 99, 120])])
def test_medium_count_tails_match_independent_probability_sums(mean, cuts):
    rng = random.Random(710)
    n = 80000
    samples = [sample_poisson(rng, mean) for _ in range(n)]
    for cut in cuts:
        probability = math.fsum(math.exp(-mean) * mean**k / math.factorial(k) for k in range(cut + 1))
        observed = sum(value <= cut for value in samples) / n
        tolerance = 5 * math.sqrt(probability * (1 - probability) / n)
        assert abs(observed - probability) < tolerance
