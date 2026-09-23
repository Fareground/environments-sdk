"""Finite products retain their scale across underflow, overflow and zero factors."""
from decimal import Decimal, localcontext
import itertools
import json
import math

import pytest

import fg_env


def contract(values, scale=1):
    names = [f'factor_{i}' for i in range(len(values))]
    return {'name': 'Composed scale factors', 'clock': {'rounds': 3}, 'types': {'store': {}},
            'patterns': {**{n: {'kind': 'trend', 'start': v} for n, v in zip(names, values)},
                         'net': {'kind': 'product', 'scale': scale, 'of': names}},
            'metrics': {'net': '$pattern.net'}, 'outputs': {'net': '$pattern.net'}}


def reference(values, scale=1):
    with localcontext() as ctx:
        ctx.prec = 2000
        total = Decimal.from_float(float(scale))
        for value in values:
            total *= Decimal.from_float(float(value))
        return float(total)


BALANCED = [*set(itertools.permutations([1e-200, 1e-200, 1e200, 1e200])),
            *set(itertools.permutations([1e308, 1e308, 1e-308, 1e-308]))]


@pytest.mark.parametrize('values', BALANCED)
def test_balanced_products_preserve_the_finite_net_across_factor_order(values):
    c = contract(values)
    result = fg_env.load(c).run()
    assert result.ok, result.summary()
    expected = reference(values)
    assert result.outputs['net'] == pytest.approx(expected, rel=2e-15, abs=0)
    assert result.series['net'] == [result.outputs['net']] * 3


@pytest.mark.parametrize('values, scale', [
    ([1e-200, 1e200, 1e200], 1e-200),
    ([1e-308, 1e-308, 1e308], 1e308),
    ([1e-308, 1e-4, 1e308, 1e4], 1),
    ([-1e-200, 1e-200, 1e200, 1e200], 1),
    ([1e-200, 1e-200, 1e200, 1e200], -1),
    ([1e-200, 1e-200], -1),
    ([5e-324, 1e308, 1e16], 1),
])
def test_scale_signs_and_subnormal_intermediates_match_independent_arithmetic(values, scale):
    result = fg_env.load(contract(values, scale)).run()
    assert result.ok
    actual = result.outputs['net']
    expected = reference(values, scale)
    assert actual == pytest.approx(expected, rel=2e-15, abs=0)
    assert math.copysign(1, actual) == math.copysign(1, expected)


@pytest.mark.parametrize('values', [
    [0, 1e308, 1e308], [1e308, 0, 1e308], [1e308, 1e308, 0],
    [-1e308, 1e308, 0],
])
def test_true_zero_wins_regardless_of_intermediate_magnitude(values):
    result = fg_env.load(contract(values)).run()
    assert result.ok and result.outputs['net'] == 0
    assert math.copysign(1, result.outputs['net']) == math.copysign(1, reference(values))
    json.dumps(result.to_dict(), allow_nan=False)


@pytest.mark.parametrize('values', [[1e308, 1e308], [-1e308, 1e308]])
def test_truly_overflowing_products_remain_explicit_errors(values):
    c = contract(values)
    c.pop('metrics')
    result = fg_env.load(c).run()
    assert not result.ok and result.outputs['net'] is None
    assert 'patterns.net' in result.output_issues[0]['message']
    json.dumps(result.to_dict(), allow_nan=False)


def test_decomposition_recovers_counterfactuals_when_the_true_total_underflows():
    c = contract([1e-200, 1e-200])
    row = fg_env.analysis.decompose(c, 'net').rows[0]
    assert row['total'] == 0
    assert row['adds'] == {'factor_0': -1e-200, 'factor_1': -1e-200}


def test_extreme_but_finite_decomposition_matches_neutralized_scenarios():
    values = [1e-200, 1e-200, 1e200, 1e200]
    c = contract(values)
    row = fg_env.analysis.decompose(c, 'net').rows[0]
    for i in range(len(values)):
        neutral = [*values[:i], 1, *values[i+1:]]
        expected = row['total'] - fg_env.load(contract(neutral)).run().outputs['net']
        assert row['adds'][f'factor_{i}'] == pytest.approx(expected, rel=2e-15, abs=0)
    json.dumps(row, allow_nan=False)


def test_pattern_products_restore_and_recompute_changed_input_scales():
    c = contract([1e-200, 1e-200, 1e200, 1e200], scale='$inputs.scale')
    c['inputs'] = {'scale': {'type': 'number', 'default': 1}}
    env = fg_env.load(c, seed=3)
    env.run(rounds=1)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    alternative = env.fork(inputs={'scale': 2})
    expected = reference([1e-200, 1e-200, 1e200, 1e200])
    assert alternative.run().series['net'] == pytest.approx([expected, 2 * expected, 2 * expected])
    original = env.run()
    assert original.series['net'] == pytest.approx([expected] * 3)
    assert restored.run().to_dict() == original.to_dict()



def test_signed_zero_scale_survives_large_factors():
    result = fg_env.load(contract([1e308, 1e308], scale=-0.0)).run()
    assert result.ok and result.outputs['net'] == 0
    assert math.copysign(1, result.outputs['net']) == -1


def test_zero_product_does_not_hide_an_invalid_operand():
    c = contract([1], scale=0)
    c.pop('metrics')
    c['inputs'] = {'value': {'type': 'number', 'default': 1}}
    c['patterns']['factor_0']['start'] = '$inputs.value / 0'
    result = fg_env.load(c).run()
    assert not result.ok and 'division by zero' in result.output_issues[0]['message']
