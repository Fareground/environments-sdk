"""Relative demand/category weights preserve mass, scale and conditional shares."""
from decimal import Decimal
import json
import math
import random

import pytest

import fg_env
from fg_env.sdk.expr import Scope, World, evaluate
from fg_env.sdk.stdlib import dists


class DrawWorld(World):
    def __init__(self, seed=17):
        self.rng = random.Random(seed)


def draw(weights, n=1000, seed=17):
    return evaluate('$multinomial($n, $weights)', Scope({'n': n, 'weights': weights}, DrawWorld(seed)))


@pytest.mark.parametrize('scale', [1.0, 1e-300, 1e300, 2.5e307])
@pytest.mark.parametrize('mapping', [False, True])
def test_units_of_relative_weights_do_not_change_seeded_binary_ratio_draws(scale, mapping):
    weights = [4 * scale, 0.0, 2 * scale, scale]
    reference = [4, 0, 2, 1]
    if mapping:
        weights = dict(zip(('a', 'closed', 'b', 'c'), weights))
        reference = dict(zip(('a', 'closed', 'b', 'c'), reference))
    assert draw(weights) == draw(reference)
    values = list(draw(weights).values()) if mapping else draw(weights)
    assert sum(values) == 1000
    assert values[1] == 0


@pytest.mark.parametrize('weights', [[1e308, 1e308, 1e308], [1e-320, 1e-320, 1e-320]])
def test_equal_categories_remain_equal_at_float_scale_limits(weights):
    assert draw(weights) == draw([1, 1, 1])


@pytest.mark.parametrize('weights', [[1e16, 1, 2], [1e16, 2, 1], [1e16, 0, 1, 2]])
def test_small_remaining_categories_use_their_own_conditional_mass(monkeypatch, weights):
    shares = []

    def probe(rng, n, p):
        shares.append(p)
        return n if p == 1 else 0

    monkeypatch.setattr(dists, 'binomial', probe)
    draw(weights, n=1)
    tail = [weight for weight in weights[1:] if weight]
    expected = float(Decimal(tail[0]) / sum(Decimal(w) for w in tail))
    assert shares[1] == pytest.approx(expected, rel=1e-15)
    assert shares[-1] == 1


@pytest.mark.parametrize('weights', [[0, 1e308, 0], [1e-320, 0, 0]])
@pytest.mark.parametrize('n', [0, 1, 10**12])
def test_single_positive_category_and_zero_trials_conserve_counts(weights, n):
    counts = draw(weights, n=n)
    assert sum(counts) == n
    assert all(count == 0 for weight, count in zip(weights, counts) if weight == 0)


def test_empirical_allocation_matches_multinomial_marginals():
    repeats, n = 20000, 30
    scope = Scope({'n': n, 'weights': [1, 2, 3, 0]}, DrawWorld(661))
    totals = [0] * 4
    for _ in range(repeats):
        counts = evaluate('$multinomial($n, $weights)', scope)
        assert sum(counts) == n and counts[3] == 0
        for i, count in enumerate(counts):
            totals[i] += count
    for total, p in zip(totals[:3], (1/6, 2/6, 3/6)):
        expected = n * repeats * p
        sd = math.sqrt(n * repeats * p * (1-p))
        assert abs(total - expected) < 6 * sd


def test_public_run_restore_and_rescaled_what_if_preserve_allocations():
    c = {'name': 'Demand by channel', 'clock': {'rounds': 4},
         'inputs': {'weights': {'type': 'list', 'default': [4, 2, 1]}},
         'world': {'counts': {'type': 'list', 'default': []}},
         'types': {'store': {}}, 'entities': {'s': {'type': 'store'}},
         'events': [{'phase': 'end', 'do': ['$world.counts = $multinomial(1000, $inputs.weights)']}],
         'metrics': {'counts': '$world.counts'}, 'outputs': {'counts': '$world.counts'}}
    env = fg_env.load(c, seed=12)
    env.run(rounds=2)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    rescaled = env.fork(inputs={'weights': [1e308, 5e307, 2.5e307]})
    original = env.run()
    assert original.ok
    assert restored.run().to_dict() == original.to_dict()
    changed = rescaled.run()
    assert changed.ok and changed.series['counts'] == original.series['counts']
    assert changed.outputs == original.outputs
