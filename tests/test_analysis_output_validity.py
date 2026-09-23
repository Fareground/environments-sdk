"""Invalid declared outputs cannot become forecasts, drivers or recommendations."""
import pytest

import fg_env
from fg_env.analysis import runner
from fg_env.analysis.drivers import _features
from fg_env.analysis.goals import Measure
from fg_env.report.evidence import Option
from fg_env.report.noise import paired
from fg_env.report.queue import QueueView


def contract():
    return {'name': 'Sales scenario', 'clock': {'rounds': 1},
            'types': {'store': {}}, 'entities': {'s': {'type': 'store'}},
            'inputs': {'sales': {'type': 'int', 'default': 6}},
            'outputs': {'units': {'expr': '$inputs.sales / 2', 'type': 'int'}, 'sales': '$inputs.sales'},
            'metrics': {'sales': '$inputs.sales'}}


def result(sales=7, seed=1):
    return fg_env.load(contract(), inputs={'sales': sales}, seed=seed).run()


def test_named_output_access_excludes_error_without_masking_healthy_outputs_or_metrics():
    bad = result()
    assert runner.raw_value(bad, ('outputs', 'units')) is None
    assert runner.value(bad, ('outputs', 'units')) is None
    assert runner.value(bad, ('outputs', 'sales')) == 7
    assert runner.value(bad, ('metrics', 'sales')) == 7
    assert 'outputs.units' in runner.summarize_failures([bad])


def test_sweep_summary_and_report_cannot_recommend_the_invalid_higher_number():
    sweep = fg_env.analysis.sweep(contract(), {'sales': [6, 7]}, runs=2)
    assert sweep.cells[0].summary['units'].mean == 3
    assert sweep.cells[1].summary['units'].n == 0
    assert sweep.cells[1].summary['sales'].mean == 7
    assert 'output issues' in sweep.report()
    report = fg_env.analysis.report(sweep, objective='max:units').markdown
    assert 'Choose sales=6.' in report
    assert 'Choose sales=7.' not in report
    assert 'outputs.units' in report and 'Invalid values were excluded' in report
    assert 'No risk stands out' not in report


def test_experiment_report_uses_valid_raw_observations_too():
    c = contract()
    c['arms'] = {'control': {'inputs': {'sales': 6}}, 'promo': {'inputs': {'sales': 7}}}
    experiment = fg_env.experiment(c, runs=2, arms=['control', 'promo'])
    report = fg_env.analysis.report(experiment, objective='max:units').markdown
    assert 'Choose control.' in report
    assert 'Choose promo.' not in report
    assert 'outputs.units' in report


@pytest.mark.parametrize('objective', ['maximise units', 'maximise $outputs.units * 2'])
def test_optimizer_does_not_choose_a_candidate_using_rejected_values(objective):
    optimum = fg_env.analysis.optimise(contract(), {'sales': [6, 7]}, objective, runs=2, holdout_seeds=0)
    assert optimum.best == {'sales': 6}


def test_expression_measures_do_not_consume_invalid_output_sets():
    bad = result()
    assert Measure('units', ('outputs', 'units')).per_run([bad]) == [None]
    assert Measure('sales', ('outputs', 'sales')).per_run([bad]) == [7]
    assert Measure('$outputs.units + $outputs.sales').per_run([bad]) == [None]
    assert Measure('$outputs.sales * 2').per_run([bad]) == [14]
    assert Measure('$inputs.sales * 2').per_run([bad]) == [14]
    assert Measure('$metrics.sales * 2').per_run([bad]) == [14]


def test_keyed_constraints_treat_invalid_and_missing_values_as_unavailable():
    bad = result()
    bad.outputs['units'] = {'a': 1}
    assert Measure('units', ('outputs', 'units')).per_run_keyed([bad]) == [None]
    good = result(6)
    good.outputs['units'] = None
    assert Measure('units', ('outputs', 'units')).per_run_keyed([good]) == [None]


def test_drivers_require_usable_outcome_values_and_filter_invalid_predictors():
    runs = [result(6, seed=i) for i in range(4)] + [result(7, seed=i+4) for i in range(4)]
    found = fg_env.analysis.drivers(runs, 'units', permutations=10)
    assert found.n == 4
    with pytest.raises(ValueError, match='at least 4'):
        fg_env.analysis.drivers(runs[1:], 'units', permutations=10)
    features = _features(runs[-1], 'sales', ('outputs',))
    assert 'outputs.units' not in features


def test_report_pairing_uses_only_valid_shared_output_values():
    a = Option('a', '', [result(6, 1), result(6, 2)])
    b = Option('b', '', [result(8, 1), result(7, 2)])
    assert b.values('units') == [4]
    comparison = paired(a, b, 'units')
    assert comparison.n == 1 and comparison.low is None and comparison.high is None


def test_queue_report_excludes_invalid_structured_outputs():
    bad, good = result(6, 1), result(6, 2)
    for run, value in ((bad, 99), (good, 2)):
        run.outputs['q_staff_by_interval'] = [value]
        run.outputs['q_offered_by_interval'] = [value]
    bad.output_issues = [{'path': f'outputs.q_{name}_by_interval', 'message': 'wrong type'}
                         for name in ('staff', 'offered')]
    option = Option('option', '', [bad, good])
    queue = QueueView('q', {}, {}, 1)
    assert queue.staff(option) == [2]
    assert queue._per_interval(option, 'offered_by_interval') == [[2.0]]


@pytest.mark.parametrize('expression, expected', [
    ('$outputs["sales"] * 2', 14),
    ('$get($outputs, "sales", 0)', 7),
    ('$get($outputs, "missing", 11)', 11),
    ('$outputs.sales if $inputs.sales > 6 else $outputs.units', 7),
    ('$sum($values($pick_keys($outputs, ["sales"])))', 7),
    ('$sum($values($without($outputs, ["units"])))', 7),
    ('$len($keys($outputs))', 2),
    ('$len($outputs)', 2),
    ('$sum($map($filter($keys($outputs), $it == "sales"), $get($outputs, $it)))', 7),
])
def test_output_formulas_can_select_healthy_values(expression, expected):
    bad = result()
    original = dict(bad.outputs)
    assert Measure(expression).per_run([bad]) == [expected]
    assert bad.outputs == original


@pytest.mark.parametrize('expression', [
    '$outputs["units"]',
    '$pow($outputs.units, 2)',
    '$len($text($outputs))',
    '$get($outputs, "units", 0)',
    '$outputs.units if $inputs.sales > 6 else $outputs.sales',
    '$sum($values($outputs))',
    '$len($items($outputs))',
    '$sum($values($merge($outputs, {other: 1})))',
    '$sum($values($pick_keys($outputs, ["sales", "units"])))',
    '$sum($map($keys($outputs), $get($outputs, $it)))',
    '1 if $outputs == {units: 3.5, sales: 7} else 0',
    '1 if {units: 3.5, sales: 7} != $outputs else 0',
])
def test_output_formulas_cannot_impute_or_aggregate_rejected_values(expression):
    assert Measure(expression).per_run([result()]) == [None]


@pytest.mark.parametrize('expression', ['$outputs', '[$outputs]', '{wrapped: $outputs}'])
def test_returned_output_containers_are_checked_before_keyed_conversion(expression):
    assert Measure(expression).per_run_keyed([result()]) == [None]


@pytest.mark.parametrize('expression, message', [
    ('$outputs.saless', 'no field'),
    ('$outputs.sales / 0', 'division by zero'),
])
def test_unrelated_output_errors_do_not_hide_formula_authoring_errors(expression, message):
    with pytest.raises(ValueError, match=message):
        Measure(expression).per_run([result()])


def test_healthy_formula_optimizer_can_use_run_with_an_unrelated_output_error():
    optimum = fg_env.analysis.optimise(contract(), {'sales': [6, 7]}, 'maximise $outputs.sales * 2',
                             runs=2, holdout_seeds=0)
    assert optimum.best == {'sales': 7}


def test_healthy_keyed_projection_remains_available():
    assert Measure('$pick_keys($outputs, ["sales"])').per_run_keyed([result()]) == [{'sales': 7}]
