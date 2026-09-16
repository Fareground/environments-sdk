"""Observed invalid outputs and failed perturbations must fail an author's behavior gate."""
import json

import pytest

import fg_env
from fg_env.__main__ import main


def contract(expr='$inputs.sales / 2'):
    return {'name': 'Demand units', 'clock': {'rounds': 1},
            'types': {'store': {}}, 'entities': {'s': {'type': 'store'}},
            'inputs': {'sales': {'type': 'int', 'default': 6, 'min': 0, 'max': 12}},
            'outputs': {'units': {'expr': expr, 'type': 'int'}, 'sales': '$inputs.sales'}}


@pytest.mark.parametrize('runs', [1, 2])
@pytest.mark.parametrize('expr', ['$inputs.sales / 2', '$inputs.sales / 0'])
def test_baseline_output_errors_fail_the_gate_with_reproducible_evidence(runs, expr):
    report = fg_env.behavior_checks(contract(expr), inputs={'sales': 7}, runs=runs, seed=20)
    assert not report.ok
    error = next(f for f in report.findings if f.code == 'output_issue')
    assert error.severity == 'error' and error.subject == 'outputs.units'
    assert len(set(error.evidence['seeds'])) == runs
    for seed in error.evidence['seeds']:
        replay = fg_env.load(contract(expr), inputs={'sales': 7}, seed=seed).run()
        assert replay.output_issues[0]['message'] in error.evidence['messages']
    assert error.evidence['messages']
    assert ('output_never_varies', 'outputs.units') not in report.codes()


@pytest.mark.parametrize('workers', [1, 2])
def test_valid_baseline_does_not_hide_output_errors_in_varied_inputs(workers):
    c = contract()
    assert fg_env.load(c).run().ok
    report = fg_env.behavior_checks(c, runs=2, seed=30, workers=workers)
    assert not report.ok and report.tested_inputs == ['sales']
    errors = [f for f in report.findings if f.code == 'output_issue']
    assert len(errors) == 2
    assert [f.evidence['inputs'] for f in errors] == [{'sales': 3}, {'sales': 9}]
    for error in errors:
        assert len(set(error.evidence['seeds'])) == 2
        assert error.severity == 'error'
        assert 'inputs.sales=' in error.message
        for seed in error.evidence['seeds']:
            replay = fg_env.load(c, inputs=error.evidence['inputs'], seed=seed).run()
            assert replay.output_issues[0]['path'] == error.subject
            assert replay.output_issues[0]['message'] in error.evidence['messages']
    assert ('input_has_no_effect', 'inputs.sales') not in report.codes()


def test_failed_input_variant_fails_gate_and_identifies_seed():
    c = contract('$inputs.sales')
    c['invariants'] = [{'expr': '$inputs.sales >= 6', 'why': 'Staff cannot cover this demand.'}]
    report = fg_env.behavior_checks(c, runs=2, seed=40)
    assert not report.ok
    error = next(f for f in report.findings if f.code == 'input_breaks_runs')
    assert error.severity == 'error' and error.evidence['value'] == 3
    assert len(set(error.evidence['seeds'])) == 2
    assert 'Staff cannot cover this demand' in error.message


def test_invalid_baseline_is_not_evidence_that_an_input_has_no_effect():
    c = contract('$inputs.sales / 0')
    c['outputs'].pop('sales')
    report = fg_env.behavior_checks(c, runs=2)
    assert not report.ok
    assert ('input_has_no_effect', 'inputs.sales') not in report.codes()


def test_valid_but_unused_input_is_still_an_advisory_warning():
    report = fg_env.behavior_checks(contract('2'), runs=2)
    # The healthy sales output varies, so the input still has an observable effect.
    assert report.ok
    c = contract('2')
    c['outputs'].pop('sales')
    report = fg_env.behavior_checks(c, runs=2)
    assert report.ok
    assert ('input_has_no_effect', 'inputs.sales') in report.codes()
    assert all(f.severity == 'warning' for f in report.findings)


@pytest.mark.parametrize('inputs', [{'sales': 7}, None])
@pytest.mark.parametrize('as_json', [False, True])
def test_checks_command_returns_failure_for_baseline_and_perturbed_output_errors(tmp_path, capsys, inputs, as_json):
    path = tmp_path / 'demand.json'
    path.write_text(json.dumps(contract()))
    args = ['checks', str(path), '--runs', '2']
    if inputs:
        args += ['--input', 'sales=7']
    if as_json:
        args += ['--json']
    assert main(args) == 1
    output = capsys.readouterr().out
    if as_json:
        payload = json.loads(output)
        assert not payload['ok']
        assert any(f['code'] == 'output_issue' and f['severity'] == 'error' for f in payload['findings'])
    else:
        assert 'error: outputs.units:' in output


def test_checks_command_keeps_zero_exit_for_valid_deterministic_scenarios(tmp_path, capsys):
    path = tmp_path / 'demand.json'
    path.write_text(json.dumps(contract('$inputs.sales')))
    assert main(['checks', str(path), '--runs', '2', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['ok']
