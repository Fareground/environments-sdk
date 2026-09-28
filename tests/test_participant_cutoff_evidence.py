"""A model's partial turn is observed evidence, not a finished decision."""
from types import SimpleNamespace as NS

import pytest

import fg_env

CASE = {
    'name': 'A partially completed shopping decision', 'clock': {'rounds': 1},
    'types': {'buyer': {'agent': True, 'props': {'units': 0}}},
    'entities': {'shopper': {'type': 'buyer'}},
    'stages': [{'name': 'shopping', 'max_actions': 3}],
    'actions': {'buy': {'by': 'buyer', 'do': ['$actor.units += 1']}},
    'outputs': {'units': {'expr': '$entity(shopper).units', 'primary': True}},
}


class Client:
    def __init__(self, finish):
        self.finish = finish
        self.calls = 0
        self.chat = NS(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        name = 'buy' if self.calls == 1 else 'end_turn' if self.finish else 'buy'
        message = NS(content='', tool_calls=[NS(id=f'call{self.calls}',
                     function=NS(name=name, arguments='{}'))])
        return NS(choices=[NS(message=message, finish_reason='tool_calls')],
                  usage=NS(prompt_tokens=1, completion_tokens=1))


@pytest.mark.parametrize('finish', [False, True])
@pytest.mark.parametrize('audience', ['owner', 'analyst'])
def test_partial_turn_cutoff_is_disclosed_without_discarding_its_outcome(finish, audience):
    client = Client(finish)
    participant = fg_env.participants.openai(client, 'test', max_steps=2)
    result = fg_env.run(CASE, participant, seed=1)
    assert client.calls == 2
    assert result.status == 'completed' and result.outputs['units'] == (1 if finish else 2)
    assert result.stats['out_of_steps'] == (0 if finish else 1)
    report = fg_env.analysis.report(result, audience, contract=CASE, objective='max:units')
    if finish:
        assert result.ok and report.recommendation is not None
        assert report.sections[0].title != 'Execution health'
    else:
        assert not result.ok and 'out_of_steps' in result.degraded
        assert report.recommendation is None
        assert report.sections[0].title == 'Execution health'
        assert 'out_of_steps' in report.markdown
        table = report.sections[0].tables[0]
        assert table.columns[-1] == 'Call-limit cutoffs'
        assert table.rows[0][-1] == '1'
        assert table.rows[0][2] == '1'
        assert result.stats['invalid_calls'] == 0


def test_accuracy_validation_excludes_cutoff_runs_and_discloses_missing_evidence():
    import copy
    contract = copy.deepcopy(CASE)
    contract['inputs'] = {'cutoff': {'type': 'bool', 'default': False}}
    contract['brief'] = {'situation': 'Cutoff: {$inputs.cutoff}'}
    def participant(wake):
        wake.call('buy', {})
        if 'Cutoff: yes' in wake.brief:
            wake.record_usage(out_of_steps=1)
        wake.end()
    cases = [{'name': 'finished', 'inputs': {'cutoff': False}, 'actuals': {'units': 1}},
             {'name': 'cut short', 'inputs': {'cutoff': True}, 'actuals': {'units': 1}}]
    validation = fg_env.analysis.validate(contract, cases, runs=2, participants=participant, baselines=())
    assert validation.measures['units']['overall']['n'] == 1
    assert {row['case'] for row in validation.rows} == {'finished'}
    assert any('2 of 4' in warning and 'degraded' in warning for warning in validation.warnings)
    report = fg_env.analysis.report(validation, contract=contract)
    assert 'degraded' in report.markdown
    healthy = fg_env.run(contract, lambda wake: (wake.call('buy', {}), wake.end()))
    combined = fg_env.analysis.report(healthy, validation=validation, contract=contract)
    assert '2 of 4' in combined.markdown and 'degraded' in combined.markdown


def test_no_accuracy_is_claimed_when_every_validation_run_is_degraded():
    def participant(wake):
        wake.call('buy', {})
        wake.record_usage(out_of_steps=1)
        wake.end()
    validation = fg_env.analysis.validate(CASE, [{'actuals': {'units': 1}}], runs=2,
                                           participants=participant, baselines=())
    assert validation.measures == {} and validation.rows == []
    assert any('2 of 2' in warning for warning in validation.warnings)
    report = fg_env.analysis.report(validation, contract=CASE)
    assert 'degraded' in report.markdown
    assert 'accuracy could not be assessed' in report.markdown
    assert 'Checked on 1 case(s)' in report.markdown


def _cutoff_contract():
    import copy
    contract = copy.deepcopy(CASE)
    contract['inputs'] = {'quantity': {'type': 'number', 'default': 1}}
    contract['actions']['buy']['do'] = ['$actor.units += $inputs.quantity']
    return contract


def test_calibration_leaves_a_cut_off_run_out_of_the_fit_and_records_it():
    calls = 0

    def participant(wake):
        nonlocal calls
        calls += 1
        wake.call('buy', {})
        if calls == 2:  # one participant call-limit cutoff in the whole calibration
            wake.record_usage(out_of_steps=1)
        wake.end()

    result = fg_env.analysis.calibrate(_cutoff_contract(), {'units': 1}, {'quantity': {'low': 1, 'high': 2}},
                                       participants=participant, runs=2, holdout=2, budget=2)
    assert result.degraded_runs == 1 and result.to_dict()['degraded_runs'] == 1
    assert any('1 of' in note and 'out_of_steps' in note for note in result.notes)
    assert result.params == {'quantity': 1} and result.fit == 0 and result.validation['fit'] == 0


def test_calibration_with_only_degraded_runs_says_why_nothing_was_fitted():
    from fg_env.analysis.runner import AnalysisError

    def participant(wake):
        wake.call('buy', {})
        wake.record_usage(out_of_steps=1)
        wake.end()

    with pytest.raises(AnalysisError, match='degraded execution.*out_of_steps'):
        fg_env.analysis.calibrate(_cutoff_contract(), {'units': 1}, {'quantity': {'low': 1, 'high': 2}},
                                  participants=participant, runs=1, holdout=1, budget=2)
