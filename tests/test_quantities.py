"""Dimensional diagnostics never silently rescale a model or authenticate authored measurements."""
import pytest

import fg_env
from fg_env.contract.quantity import Quantity
from fg_env.expr import ExprError, evaluate


def q(unit):
    return Quantity.model_validate(unit).model_dump()


def model(expr, output='item'):
    return {'name': 'Production', 'types': {}, 'inputs': {
        'rate': {'default': 60, 'quantity': q('item/hour')},
        'duration': {'default': 30, 'quantity': q('minute')}},
        'outputs': {'produced': {'expr': expr, 'quantity': q(output)}}}


def test_rate_times_duration_requires_explicit_matching_scale():
    bad = model('$inputs.rate * $inputs.duration')
    assert any(i.severity == 'error' and 'scales' in i.message for i in fg_env.check(bad))
    with pytest.raises(fg_env.ContractError):
        fg_env.run(bad)
    good = model('$inputs.rate * $convert($inputs.duration, minute, hour)')
    assert not fg_env.check(good)
    assert fg_env.run(good).outputs['produced'] == 30
    assert not fg_env.check(model('$convert(30, minute, hour) * $inputs.rate'))


@pytest.mark.parametrize('expr', ['$inputs.rate + $inputs.duration', '$inputs.rate < $inputs.duration',
                                 '$inputs.rate if $inputs.duration > $inputs.rate else $inputs.rate'])
def test_incompatible_arithmetic_and_comparisons_are_rejected(expr):
    assert any(i.severity == 'error' and 'dimensions' in i.message for i in fg_env.check(model(expr)))


@pytest.mark.parametrize(('expr', 'expected'), [
    ('$convert(30, minute, hour)', .5), ('$convert(2500, L, m3)', 2.5),
    ('$convert(1, m^3, L)', 1000), ('$convert(25, percent, fraction)', .25),
    ('$convert(0, degC, K)', 273.15), ('$convert(32, degF, degC)', 0),
    ('$convert(18, delta_degF, delta_degC)', 10),
    ('$convert_currency(100, USD, EUR, 0.9, "2026-09-01", "authored synthetic rate")', 90),
])
def test_known_explicit_conversion_answers(expr, expected):
    # Compound unit names containing operators must be quoted as DSL strings.
    expr = expr.replace('m^3', '"m^3"')
    assert evaluate(expr) == pytest.approx(expected)


@pytest.mark.parametrize('expr', [
    '$convert(1, hour, L)', '$convert(1, degC, delta_degC)', '$convert(1, USD, EUR)',
    '$convert(1, unknown_unit, hour)', '$convert(1, "degC/hour", K)',
    '$convert_currency(100, USD, EUR, 0.9, "yesterday", "source")',
    '$convert_currency(100, USD, EUR, 0.9, "2026-09-01", "")',
    '$convert_currency(100, USD, EUR, -0.9, "2026-09-01", "source")',
    '$convert_currency(100, L, EUR, 0.9, "2026-09-01", "source")',
])
def test_undefined_or_unjustified_conversions_fail(expr):
    with pytest.raises(ExprError):
        evaluate(expr)


def test_fraction_and_percent_cannot_be_added_without_conversion():
    contract = {'name': 'Rate', 'types': {}, 'inputs': {
        'a': {'default': .2, 'quantity': q('fraction')}, 'b': {'default': 20, 'quantity': q('percent')}},
        'outputs': {'total': {'expr': '$inputs.a + $inputs.b', 'quantity': q('fraction')}}}
    assert any(i.severity == 'error' for i in fg_env.check(contract))
    contract['outputs']['total']['expr'] = '$inputs.a + $convert($inputs.b, percent, fraction)'
    assert not fg_env.check(contract)
    assert fg_env.run(contract).outputs['total'] == pytest.approx(.4)


def test_absolute_temperature_difference_is_an_interval_not_an_absolute_temperature():
    contract = {'name': 'Temperature', 'types': {}, 'inputs': {
        'a': {'default': 30, 'quantity': q('degC')}, 'b': {'default': 20, 'quantity': q('degC')}},
        'outputs': {'delta': {'expr': '$inputs.a - $inputs.b', 'quantity': q('delta_degC')}}}
    assert not fg_env.check(contract)
    assert fg_env.run(contract).outputs['delta'] == 10
    contract['outputs']['delta']['quantity'] = q('degC')
    assert any(i.severity == 'error' for i in fg_env.check(contract))
    contract['outputs']['delta']['expr'] = '$inputs.a + $inputs.b'
    assert any('absolute quantities' in i.message for i in fg_env.check(contract))


def test_custom_units_are_explicit_and_unmodeled_expressions_are_unchecked():
    custom = {'dimensions': {'widgets': 1}, 'scale': 10}
    assert evaluate('$convert(3, {dimensions: {widgets: 1}, scale: 10}, {dimensions: {widgets: 1}})') == 30
    contract = {'name': 'Custom', 'types': {}, 'world': {'stock': 3},
                'outputs': {'stock': {'expr': '$world.stock', 'quantity': custom}}}
    issues = fg_env.check(contract)
    assert any(i.severity == 'warning' and 'unchecked' in i.message for i in issues)


def test_legacy_display_labels_are_not_implicitly_converted():
    contract = model('$inputs.rate * $inputs.duration')
    for spec in [*contract['inputs'].values(), *contract['outputs'].values()]:
        spec.pop('quantity')
        spec['unit'] = 'unregistered display label'
    assert fg_env.run(contract).outputs['produced'] == 1800


def test_canonical_temperature_can_be_used_in_physical_products_after_conversion():
    contract = {'name': 'Thermal law', 'types': {}, 'inputs': {
        'temperature': {'default': 0, 'quantity': q('degC')}}, 'outputs': {
        'thermal': {'expr': '$convert($inputs.temperature, degC, K) ** 4', 'quantity': q('K^4')}}}
    assert not fg_env.check(contract)
    assert fg_env.run(contract).outputs['thermal'] == pytest.approx(273.15 ** 4)
    contract['outputs']['thermal']['expr'] = '$inputs.temperature ** 4'
    assert any(i.severity == 'error' for i in fg_env.check(contract))


def test_output_references_and_sampled_expression_do_not_escape_dimensional_checks():
    contract = model('$inputs.rate * $convert($inputs.duration, minute, hour)')
    contract['outputs']['wrong'] = {'expr': '$outputs.produced + $inputs.duration', 'quantity': q('item')}
    assert any(i.path == 'outputs.wrong.quantity' and i.severity == 'error' for i in fg_env.check(contract))
    del contract['outputs']['wrong']
    contract['outputs']['produced']['series'] = '$inputs.rate * $inputs.duration'
    assert any(i.path == 'outputs.produced.series.quantity' and i.severity == 'error' for i in fg_env.check(contract))


def test_invalid_static_unit_is_error_and_bad_arity_does_not_crash_checker():
    for expr in ['$convert($inputs.duration, nonsense, hour)', '$convert()']:
        assert any(i.severity == 'error' for i in fg_env.check(model(expr)))


def test_guided_quantity_example_is_executable_and_schema_accepts_structured_metadata():
    import json
    import re

    import jsonschema
    example = json.loads(re.search(r'```json\n(.*?)\n```', fg_env.guide('quantities'), re.S)[1])
    jsonschema.validate(example, fg_env.schema())
    assert not fg_env.check(example)
    assert fg_env.run(example).outputs['produced'] == pytest.approx(30)


def test_unrestricted_input_cannot_claim_a_verified_numeric_quantity():
    contract = model('$inputs.rate')
    contract['inputs']['rate'].update(type='any', default='not a number')
    assert any(i.path == 'inputs.rate.quantity' and i.severity == 'error' for i in fg_env.check(contract))
