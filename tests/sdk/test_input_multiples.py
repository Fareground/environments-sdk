"""Numeric increments enforce data requirements independently of UI hints."""
import pytest
from fg_env.sdk.contract import InputSpec, Contract
from fg_env.sdk.inputs import check_value, resolve_inputs
from fg_env.sdk.errors import InputError


@pytest.mark.parametrize('value', [0, .01, .3, .1 + .2, -1.25, 1e300])
def test_cent_values_and_float_arithmetic_noise_are_accepted(value):
    assert check_value('number', value, InputSpec(multiple_of=.01)) is None


@pytest.mark.parametrize('value', [.001, 1.005, -.125, .300001])
def test_fractional_cent_inputs_are_rejected(value):
    assert 'multiple of 0.01' in check_value('number', value, InputSpec(multiple_of=.01))


def test_numeric_constraint_is_not_a_ui_step_or_min_offset():
    assert check_value('number', .125, InputSpec(step=.01)) is None
    spec = InputSpec(type='int', min=1, multiple_of=6)
    assert check_value('int', 6, spec) is None
    assert check_value('int', 7, spec)
    assert check_value('number', 5e-324, InputSpec(multiple_of=1e-323))


@pytest.mark.parametrize('multiple', [0, -1, float('inf'), float('nan')])
def test_invalid_increments_are_rejected(multiple):
    with pytest.raises(ValueError):
        InputSpec(multiple_of=multiple)


def test_non_numeric_constraint_is_rejected():
    with pytest.raises(ValueError, match='numeric'):
        InputSpec(type='text', multiple_of=.01)


def test_nested_default_and_supplied_values_follow_the_same_rule():
    contract = Contract.model_validate({'name': 'Input validation', 'types': {}, 'inputs': {'rows': {'type': 'table', 'fields': {
        'prices': {'type': 'list', 'items': {'type': 'number', 'multiple_of': .01}}
    }, 'default': [{'prices': [1.25]}]}}})
    assert resolve_inputs(contract)['rows'][0]['prices'] == [1.25]
    with pytest.raises(InputError, match="row 0 field 'prices' item 0 must be a multiple"):
        resolve_inputs(contract, {'rows': [{'prices': [.001]}]})
    contract.inputs['rows'].default = [{'prices': [.001]}]
    with pytest.raises(InputError, match='multiple'):
        resolve_inputs(contract)
