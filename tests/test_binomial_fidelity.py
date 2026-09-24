"""Count distributions preserve binomial asymmetry and stable large-n probabilities."""
import json
import math
import random
from decimal import Decimal, localcontext

import pytest

import fg_env
from fg_env.sampling.binomial import _log_ratio, sample_large_binomial
from fg_env.stdlib.dists import binomial


def test_large_conversion_counts_preserve_binomial_skew():
    n, p, count = 100100, 0.01, 400000
    rng = random.Random(1961)
    mean, sd = n * p, math.sqrt(n * p * (1 - p))
    first = second = third = 0.0
    for _ in range(count):
        z = (binomial(rng, n, p) - mean) / sd
        first += z
        second += z * z
        third += z * z * z
    center = first / count
    variance = second / count - center * center
    skew = (third / count - 3 * center * second / count + 2 * center**3) / variance**1.5
    assert abs(center) < 0.01
    assert abs(variance - 1) < 0.015
    assert abs(skew - (1 - 2 * p) / sd) < 0.015


@pytest.mark.parametrize('n,p', [(100100, .01), (10**12, .5), (10**12, .001), (10**12, 1e-8)])
@pytest.mark.parametrize('offset', [-100, 1, 1000])
def test_acceptance_ratio_matches_decimal_probability_recurrence(n, p, offset):
    mode = math.floor((n + 1) * p)
    candidate = mode + offset
    with localcontext() as context:
        context.prec = 60
        probability = Decimal.from_float(p)
        odds = probability / (1 - probability)
        ratio = Decimal(1)
        for k in range(min(mode, candidate), max(mode, candidate)):
            ratio *= Decimal(n - k) / Decimal(k + 1) * odds
        expected = float(ratio.ln()) * (1 if offset > 0 else -1)
    assert _log_ratio(candidate, mode, n, p) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize('n,p', [(0, .3), (100, 0), (100, 1), (20, .3), (2000, .5), (10000, .9)])
def test_existing_small_count_seed_sequences_are_preserved(n, p):
    def legacy(rng):
        if n == 0 or p == 0:
            return 0
        if p == 1:
            return n
        q = min(p, 1 - p)
        log_miss = math.log1p(-q)
        successes = position = 0
        while True:
            position += int(math.log(1 - rng.random()) / log_miss) + 1
            if position > n:
                return successes if p <= .5 else n - successes
            successes += 1
    actual, old = random.Random(17), random.Random(17)
    assert [binomial(actual, n, p) for _ in range(20)] == [legacy(old) for _ in range(20)]
    assert actual.getstate() == old.getstate()


def test_zero_uniform_endpoints_do_not_divide_or_log_zero():
    class Draws:
        def __init__(self):
            self.draws = iter([0.0, .5, 0.0])
        def random(self):
            return next(self.draws)
    assert sample_large_binomial(Draws(), 100100, .01) == 1001


@pytest.mark.parametrize('n,p', [(100100, .01), (100100, .99), (10**12, .5), (10**12, .999)])
def test_large_draws_stay_in_support_and_replay(n, p):
    a, b = random.Random(71), random.Random(71)
    values = [binomial(a, n, p) for _ in range(1000)]
    assert values == [binomial(b, n, p) for _ in range(1000)]
    assert all(type(value) is int and 0 <= value <= n for value in values)


def test_public_binomial_and_multinomial_restore_exactly_and_conserve_counts():
    c = {'name': 'Campaign counts', 'clock': {'rounds': 4}, 'types': {'market': {}},
         'world': {'count': 0, 'segments': [0, 0, 0]},
         'events': [{'do': ['$world.count = $binomial(100100, 0.01)',
                            '$world.segments = $multinomial(100100, [1, 2, 3])']}],
         'outputs': {'count': '$world.count', 'segments': '$world.segments'}}
    env = fg_env.load(c, seed=12)
    env.run(rounds=2)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok, result.error
    assert sum(result.outputs['segments']) == 100100
    assert result.to_dict() == restored.run().to_dict()


@pytest.mark.parametrize('n', [10, 10**12])
@pytest.mark.parametrize('p', [5e-324, 1e-310])
def test_tiny_valid_conversion_probability_does_not_overflow(n, p):
    assert binomial(random.Random(12), n, p) == 0
    c = {'name': 'Rare conversion', 'clock': {'rounds': 1}, 'types': {'market': {}},
         'outputs': {'count': f'$binomial({n}, {p!r})'}}
    result = fg_env.run(c, seed=12)
    assert result.ok, result.error
    assert result.outputs == {'count': 0}
