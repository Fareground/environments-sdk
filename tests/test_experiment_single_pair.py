"""One usable pair is an observation, not an estimated confidence interval."""
import copy
import math

import pytest

import fg_env
from fg_env.experiments import ArmResult, ExperimentResult, _describe


def contract():
    return {'name': 'Promotion comparison', 'clock': {'rounds': 1},
            'types': {'store': {}}, 'entities': {'s': {'type': 'store'}},
            'world': {'sales': 100}, 'outputs': {'sales': '$world.sales'},
            'arms': {'control': {}, 'promo': {'patch': {'world': {'sales': 110}}}}}


def test_single_run_keeps_observed_effect_without_claiming_confidence():
    result = fg_env.experiment(contract(), runs=1, arms=['control', 'promo'])
    delta = result.deltas('control')['promo']['sales']
    assert delta['mean'] == 10 and delta['n'] == 1
    assert delta['ci95'] is None and delta['clear'] is False
    assert result.arms['control'].outputs['sales']['ci95'] is None
    assert result.to_dict()['deltas']['promo']['sales']['ci95'] is None
    table = result.table()
    assert 'uncertainty not estimated' in table
    assert '95% CI' not in table and 'clear' not in table and 'within noise' not in table


@pytest.mark.parametrize('bad', ['missing', 'failed', 'invalid'])
def test_one_remaining_usable_pair_has_no_interval_even_when_more_runs_were_requested(bad):
    result = fg_env.experiment(contract(), runs=3, arms=['control', 'promo'])
    for run in result.arms['promo'].runs[1:]:
        if bad == 'missing':
            run.outputs['sales'] = None
        elif bad == 'failed':
            run.status = 'failed'
        else:
            run.output_issues = [{'path': 'outputs.sales', 'message': 'invalid value'}]
    delta = result.deltas()['promo']['sales']
    assert delta['n'] == 1 and delta['mean'] == 10
    assert delta['ci95'] is None and not delta['clear']
    assert 'uncertainty not estimated' in result.table()


@pytest.mark.parametrize('difference', [-10, 0, 10])
def test_direction_does_not_make_one_pair_conclusive(difference):
    c = contract()
    c['arms']['promo']['patch']['world']['sales'] = 100 + difference
    delta = fg_env.experiment(c, runs=1, arms=['control', 'promo']).deltas()['promo']['sales']
    assert delta['mean'] == difference and not delta['clear'] and delta['ci95'] is None


def test_nested_numeric_summaries_also_mark_single_observation_uncertainty_unavailable():
    summary = _describe([{'north': 10, 'south': 20}])
    assert summary['keys']['north']['ci95'] is None
    assert summary['keys']['south']['ci95'] is None
    assert _describe([[1, 2, 3]])['items'][0]['ci95'] is None
    assert _describe([]) == {'n': 0}


def test_two_or_more_pairs_retain_the_existing_student_interval():
    first = fg_env.load(contract(), seed=1).run()
    control = [copy.deepcopy(first) for _ in range(4)]
    treatment = [copy.deepcopy(first) for _ in range(4)]
    for difference, run in zip((1, 2, 3, 4), treatment):
        run.outputs['sales'] += difference
    result = ExperimentResult({'control': ArmResult(None, control, {'sales': {}}),
                               'promo': ArmResult(None, treatment, {'sales': {}})}, [1, 2, 3, 4])
    delta = result.deltas()['promo']['sales']
    half = 3.182 * math.sqrt(5/3) / 2
    assert delta['ci95'] == pytest.approx([2.5-half, 2.5+half])
    assert delta['clear']


def test_single_boolean_pair_is_not_reported_as_conclusive():
    c = contract()
    c['outputs']['sales'] = '$world.sales > 105'
    delta = fg_env.experiment(c, runs=1, arms=['control', 'promo']).deltas()['promo']['sales']
    assert delta['mean'] == 1 and delta['ci95'] is None and not delta['clear']
