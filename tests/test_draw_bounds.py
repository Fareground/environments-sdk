"""Authored and input-driven draw bounds have the same clear validation."""
import json

import pytest

import fg_env


def contract(dist, low, high, peak=None, integer=False, dynamic=False):
    params = {'low': low, 'high': high}
    if peak is not None:
        params['peak'] = peak
    spec = {'kind': 'pattern', 'mode': 'draw', 'dist': dist, 'integer': integer,
            **{key: f'$inputs.{key}' if dynamic else value for key, value in params.items()}}
    return {'name': 'Uncertain lead time', 'clock': {'rounds': 3}, 'types': {'firm': {}},
            'inputs': {key: {'type': 'number', 'default': value} for key, value in params.items()},
            'mechanisms': {'lead_time': spec}, 'metrics': {'value': '$pattern.lead_time'}}


@pytest.mark.parametrize('low,high,peak', [(0, 1, 2), (0, 1, -1), (2, 2, 3)])
def test_literal_triangular_peak_outside_bounds_is_a_static_error(low, high, peak):
    errors = [i for i in fg_env.check(contract('triangular', low, high, peak), rounds=0) if i.severity == 'error']
    assert errors
    assert any('peak' in i.message for i in errors)


@pytest.mark.parametrize('low,high,peak', [(0, 1, 2), (0, 1, -1), (2, 2, 3), (5, 1, 3)])
def test_dynamic_triangular_bounds_fail_with_pattern_context(low, high, peak):
    result = fg_env.load(contract('triangular', low, high, peak, dynamic=True), seed=11).run()
    assert not result.ok
    assert 'lead_time' in result.error
    assert 'low' in result.error and 'peak' in result.error and 'high' in result.error
    assert 'math domain' not in result.error


@pytest.mark.parametrize('low,high', [(1.2, 1.8), (-1.8, -1.2)])
def test_literal_integer_uniform_needs_at_least_one_integer(low, high):
    errors = [i for i in fg_env.check(contract('uniform', low, high, integer=True), rounds=0) if i.severity == 'error']
    assert any('whole number' in i.message for i in errors)


@pytest.mark.parametrize('low,high', [(1.2, 1.8), (-1.8, -1.2)])
def test_dynamic_integer_uniform_explains_empty_integer_range(low, high):
    result = fg_env.load(contract('uniform', low, high, integer=True, dynamic=True)).run()
    assert not result.ok
    assert 'lead_time' in result.error and 'whole number' in result.error
    assert 'randrange' not in result.error


@pytest.mark.parametrize('low,high,peak', [(0, 1, 0), (0, 1, .5), (0, 1, 1), (2, 2, 2)])
def test_valid_triangular_values_stay_bounded_and_restore_exactly(low, high, peak):
    c = contract('triangular', low, high, peak, dynamic=True)
    env = fg_env.load(c, seed=11)
    env.run(rounds=1)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok, result.error
    assert all(low <= value <= high for value in result.series['value'])
    assert restored.run().to_dict() == result.to_dict()


@pytest.mark.parametrize('low,high,expected', [(.2, 1.8, 1), (-1.8, -.2, -1), (2, 2, 2)])
@pytest.mark.parametrize('dynamic', [False, True])
def test_integer_uniform_valid_support_and_replay(low, high, expected, dynamic):
    c = contract('uniform', low, high, integer=True, dynamic=dynamic)
    result = fg_env.load(c, seed=11).run()
    assert result.ok, result.error
    assert result.series['value'] == [expected] * 3
    assert result.to_dict() == fg_env.load(c, seed=11).run().to_dict()


@pytest.mark.parametrize('field', ['low', 'high', 'peak'])
@pytest.mark.parametrize('invalid', [float('inf'), float('-inf'), float('nan')])
def test_nonfinite_literal_triangle_parameters_report_finite_number(field, invalid):
    c = contract('triangular', 0, 1, .5)
    c['mechanisms']['lead_time'][field] = invalid
    errors = [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    assert any('finite number' in i.message for i in errors)
