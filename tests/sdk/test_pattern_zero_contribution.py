"""A zero business driver has an observable counterfactual contribution, not NaN."""
import json

import pytest

import fg_env
from fg_env.sdk.errors import ContractError


def contract(values, scale=10):
    patterns = {name: {'kind': 'trend', 'start': value} for name, value in values.items()}
    patterns['demand'] = {'kind': 'product', 'scale': scale, 'of': list(values)}
    return {'name': 'Demand drivers', 'clock': {'rounds': 2}, 'types': {'store': {}},
            'patterns': patterns, 'outputs': {'demand': '$pattern.demand'}}


@pytest.mark.parametrize('values, scale, expected', [
    ({'availability': 0, 'lift': 2}, 10, {'availability': -20, 'lift': 0}),
    ({'lift': 2, 'availability': 0}, 10, {'lift': 0, 'availability': -20}),
    ({'availability': 0}, 10, {'availability': -10}),
    ({'availability': 0, 'lift': 2}, 0, {'availability': 0, 'lift': 0}),
    ({'availability': 0, 'lift': 0}, 10, {'availability': 0, 'lift': 0}),
    ({'availability': 0, 'lift': 2}, -10, {'availability': 20, 'lift': 0}),
    ({'availability': -0.0, 'lift': -2}, 10, {'availability': 20, 'lift': 0}),
    ({'availability': 0.5, 'lift': 2}, 10, {'availability': -10, 'lift': 5}),
])
def test_contributions_match_removing_each_factor(values, scale, expected):
    c = contract(values, scale)
    parts = fg_env.analysis.decompose(c, 'demand')
    for row in parts.rows:
        assert row['adds'] == expected
        assert row['total'] == fg_env.load(c).run().outputs['demand']
        # Independent scenario counterfactual: neutralize exactly one factor.
        for name, effect in row['adds'].items():
            alternative = contract({**values, name: 1}, scale)
            without = fg_env.load(alternative).run().outputs['demand']
            assert row['total'] - without == effect
    assert json.loads(json.dumps(parts.to_dict(), allow_nan=False)) == parts.to_dict()


def test_factor_can_switch_to_zero_between_rounds():
    c = contract({'availability': 1, 'lift': 2})
    c['patterns']['availability']['slope'] = -1
    parts = fg_env.analysis.decompose(c, 'demand')
    assert [row['total'] for row in parts.rows] == [20, 0]
    assert [row['adds']['availability'] for row in parts.rows] == [0, -20]


def test_live_and_restored_explanations_match_and_do_not_mutate_run():
    c = contract({'availability': 0, 'lift': 2})
    env = fg_env.load(c, seed=9)
    env.run(rounds=1)
    before = json.loads(json.dumps(env.snapshot()))
    restored = fg_env.Env.restore(c, before)
    assert fg_env.analysis.decompose(env, 'demand').to_dict() == fg_env.analysis.decompose(restored, 'demand').to_dict()
    assert env.snapshot() == before
    assert fg_env.analysis.decompose(env, 'demand').rows[0]['adds']['availability'] == -20


def test_unrepresentable_counterfactual_is_an_actionable_error_not_infinite_json():
    c = contract({'availability': 0, 'lift': 1e308}, scale=10)
    assert fg_env.load(c).run().outputs['demand'] == 0
    with pytest.raises(ContractError, match='contribution is outside the finite numeric range'):
        fg_env.analysis.decompose(c, 'demand')


@pytest.mark.parametrize('values, bounds, expected', [
    ({'availability': 1, 'lift': 2}, {'max': 15}, {'availability': 0, 'lift': 5}),
    ({'availability': 0.5, 'lift': 2}, {'min': 15}, {'availability': -5, 'lift': 0}),
    ({'availability': 0, 'lift': 2}, {'max': 5}, {'availability': -5, 'lift': 0}),
    ({'availability': 0, 'lift': 2}, {'min': 5}, {'availability': -15, 'lift': 0}),
    ({'availability': 0, 'lift': 2}, {'min': 5, 'max': 15}, {'availability': -10, 'lift': 0}),
])
def test_product_contributions_apply_the_same_bounds_as_the_simulated_counterfactual(values, bounds, expected):
    c = contract(values)
    c['patterns']['demand'].update(bounds)
    row = fg_env.analysis.decompose(c, 'demand').rows[0]
    assert row['adds'] == expected
    for name, effect in expected.items():
        alternative = contract({**values, name: 1})
        alternative['patterns']['demand'].update(bounds)
        assert row['total'] - fg_env.load(alternative).run().outputs['demand'] == effect


def test_product_scale_and_bounds_can_come_from_inputs():
    c = contract({'availability': 0, 'lift': 2}, scale='$inputs.base')
    c['inputs'] = {'base': {'type': 'number', 'default': 10},
                   'capacity': {'type': 'number', 'default': 5}}
    c['patterns']['demand']['max'] = '$inputs.capacity'
    assert fg_env.analysis.decompose(c, 'demand').rows[0]['adds']['availability'] == -5
    assert fg_env.analysis.decompose(c, 'demand', inputs={'base': 20, 'capacity': 12}).rows[0]['adds']['availability'] == -12


def test_keyed_nested_products_keep_each_regions_scale_bounds_and_factors():
    rows = [{'region': 'north', 'available': 0, 'base': 10, 'capacity': 5},
            {'region': 'south', 'available': 1, 'base': 20, 'capacity': 30}]
    keyed = {'table': '$inputs.regions', 'column': 'region'}
    c = {'name': 'Regional campaign', 'clock': {'rounds': 1}, 'types': {'store': {}},
         'inputs': {'regions': {'type': 'table', 'default': rows}},
         'patterns': {
             'availability': {**keyed, 'kind': 'trend', 'start': '$row.available'},
             'lift': {'kind': 'trend', 'start': 2},
             'demand': {**keyed, 'kind': 'product', 'scale': '$row.base', 'max': '$row.capacity',
                        'of': ['availability', 'lift']},
             'margin': {**keyed, 'kind': 'product', 'scale': 3, 'of': ['demand']}}}
    env = fg_env.load(c)
    env.run()
    for key, total, effects in [('north', 0, {'availability': -5, 'lift': 0}),
                                ('south', 30, {'availability': 0, 'lift': 10})]:
        row = fg_env.analysis.decompose(env, 'demand', key=key).rows[0]
        assert row['total'] == total and row['adds'] == effects
        margin = fg_env.analysis.decompose(env, 'margin', key=key).rows[0]
        assert margin['total'] == total * 3
        assert margin['adds']['demand'] == total * 3 - 3
        json.dumps(margin, allow_nan=False)
