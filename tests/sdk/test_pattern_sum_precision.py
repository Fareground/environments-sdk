"""Composed net quantities preserve residuals despite offsetting large drivers."""
from decimal import Decimal, localcontext
import itertools
import json

import pytest

import fg_env


def contract(terms, base=0, weights=None):
    names = [f'factor_{i}' for i in range(len(terms))]
    combined = {'kind': 'sum', 'base': base, 'of': names}
    if weights is not None:
        combined['weights'] = weights
    return {'name': 'Net business drivers', 'clock': {'rounds': 3}, 'types': {'account': {}},
            'patterns': {**{name: {'kind': 'trend', 'start': value} for name, value in zip(names, terms)},
                         'net': combined}, 'metrics': {'net': '$pattern.net'},
            'outputs': {'net': '$pattern.net'}}


def reference(terms):
    with localcontext() as context:
        context.prec = 2000
        return float(sum((Decimal.from_float(float(v)) for v in terms), Decimal(0)))


@pytest.mark.parametrize('terms', list(itertools.permutations([1e16, -1e16, 1])))
def test_small_net_survives_every_base_and_operand_position(terms):
    c = contract(terms[1:], base=terms[0])
    result = fg_env.load(c).run()
    assert result.ok and result.outputs['net'] == reference(terms) == 1
    assert result.series['net'] == [1, 1, 1]


@pytest.mark.parametrize('terms', list(set(itertools.permutations([1e308, 1e308, -1e308]))))
def test_finite_net_survives_intermediate_overflow_in_any_order(terms):
    result = fg_env.load(contract(terms)).run()
    assert result.ok and result.outputs['net'] == reference(terms)


@pytest.mark.parametrize('small', [5e-324, 1e-200, -1e-200])
def test_tiny_representable_residual_is_preserved(small):
    result = fg_env.load(contract([small, -1e16], base=1e16)).run()
    assert result.ok and result.outputs['net'] == reference([1e16, small, -1e16]) == small


def test_signed_weights_and_base_are_summed_together():
    result = fg_env.load(contract([5e15, 0.25], base=1e16, weights=[-2, 4])).run()
    assert result.ok and result.outputs['net'] == 1


@pytest.mark.parametrize('terms, base, weights, message', [
    ([1e308], 1e308, None, 'sum is outside the finite numeric range'),
    ([-1e308], -1e308, None, 'sum is outside the finite numeric range'),
    ([1e308, -1e308], 0, [2, 2], 'weighted terms must be finite'),
])
def test_nonrepresentable_sums_and_weighted_terms_have_actionable_errors(terms, base, weights, message):
    c = contract(terms, base, weights)
    c.pop('metrics')
    result = fg_env.load(c).run()
    assert not result.ok
    issue = next(i for i in result.output_issues if i['path'] == 'outputs.net')
    assert message in issue['message'] and 'patterns.net' in issue['message']
    json.dumps(result.to_dict(), allow_nan=False)


def test_corrected_sum_is_bounded_after_cancellation():
    c = contract([-1e16, 1], base=1e16)
    c['patterns']['net'].update(min=0, max=0.5)
    assert fg_env.load(c).run().outputs['net'] == 0.5


def test_keyed_inputs_and_nested_products_preserve_independent_residuals():
    rows = [{'sku': 'a', 'gross': 1e16, 'offset': -1e16, 'net': 1},
            {'sku': 'b', 'gross': 1e16, 'offset': -1e16, 'net': 3}]
    keyed = {'table': '$inputs.rows', 'column': 'sku'}
    c = {'name': 'SKU net drivers', 'clock': {'rounds': 2}, 'types': {'account': {}},
         'inputs': {'rows': {'type': 'table', 'default': rows}},
         'patterns': {'offset': {**keyed, 'kind': 'trend', 'start': '$row.offset'},
                      'small': {**keyed, 'kind': 'trend', 'start': '$row.net'},
                      'net': {**keyed, 'kind': 'sum', 'base': '$row.gross', 'of': ['offset', 'small']},
                      'doubled': {**keyed, 'kind': 'product', 'scale': 2, 'of': ['net']}},
         'outputs': {'values': '$pattern_values("doubled")'}}
    env = fg_env.load(c, seed=7)
    env.run(rounds=1)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok and result.outputs['values'] == {'a': 2, 'b': 6}
    assert restored.run().to_dict() == result.to_dict()
    assert fg_env.decompose(env, 'net', key='a').rows[0]['total'] == 1


def test_input_what_if_recomputes_residual_without_changing_parent_history():
    c = contract([-1e16, 1], base=1e16)
    c['inputs'] = {'residual': {'type': 'number', 'default': 1}}
    c['patterns']['factor_1']['start'] = '$inputs.residual'
    env = fg_env.load(c, seed=5)
    env.run(rounds=1)
    alternative = env.fork(inputs={'residual': 2})
    assert alternative.run().series['net'] == [1, 2, 2]
    assert env.run().series['net'] == [1, 1, 1]
