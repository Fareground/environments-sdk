"""Agents must not receive success signals or statistics for invalid declared outputs."""
import json

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.experiments.experiment import ArmResult, ExperimentResult


def contract(kind='type'):
    c = {'name': 'Sales reporting', 'clock': {'rounds': 1}, 'world': {'sales': 3, 'visits': 0},
         'types': {'store': {}}, 'entities': {'s': {'type': 'store'}},
         'outputs': {'units': {'expr': '$world.sales / 2', 'type': 'int'},
                     'sales': '$world.sales'}}
    if kind == 'division':
        c['outputs']['units'] = '$world.sales / $world.visits'
    elif kind == 'valid':
        c['world']['sales'] = 4
    elif kind == 'nullable':
        c['outputs']['units'] = 'null'
    elif kind == 'runtime':
        c['events'] = [{'phase': 'end', 'do': ['$world.sales = $world.sales / $world.visits']}]
    return c


@pytest.mark.parametrize('kind', ['type', 'division'])
def test_python_result_already_exposes_output_failure_and_repair_hint(kind):
    result = fg_env.load(contract(kind)).run()
    assert result.status == 'completed' and not result.ok
    assert result.output_issues[0]['path'] == 'outputs.units'
    assert result.output_issues[0]['fix']
    assert 'diagnostic: outputs.units:' in result.summary()
    assert [found['code'] for found in result.diagnostics] == ['output_failed']


@pytest.mark.parametrize('kind', ['type', 'division', 'runtime'])
@pytest.mark.parametrize('command', ['run', 'experiment'])
@pytest.mark.parametrize('as_json', [False, True])
def test_commands_signal_unusable_results_while_printing_diagnostics(tmp_path, capsys, kind, command, as_json):
    path = tmp_path / 'scenario.json'
    path.write_text(json.dumps(contract(kind)))
    args = [command, str(path)] + (['--runs', '2'] if command == 'experiment' else [])
    args += ['--json'] if as_json else []
    assert main(args) == 2
    output = capsys.readouterr().out
    if as_json:
        payload = json.loads(output)
        results = [payload] if command == 'run' else payload['arms']['baseline']['runs']
        assert all(r['status'] == 'failed' if kind == 'runtime' else r['output_issues'] for r in results)
    else:
        assert ('failed' if kind == 'runtime' else 'outputs.units') in output


@pytest.mark.parametrize('kind', ['valid', 'nullable'])
@pytest.mark.parametrize('command', ['run', 'experiment'])
def test_valid_and_explicitly_nullable_outputs_keep_success_exit(tmp_path, capsys, kind, command):
    path = tmp_path / 'scenario.json'
    path.write_text(json.dumps(contract(kind)))
    args = [command, str(path)] + (['--runs', '2'] if command == 'experiment' else [])
    assert main(args) == 0
    assert 'output issue' not in capsys.readouterr().out


def test_experiment_omits_only_invalid_output_from_summary_and_retains_evidence():
    result = fg_env.experiment(contract(), runs=3)
    arm = result.arms['baseline']
    assert arm.outputs['units'] == {'n': 0}
    assert arm.outputs['sales']['n'] == 3 and arm.outputs['sales']['mean'] == 3
    assert all(r.outputs['units'] == 1.5 and r.output_issues for r in arm.runs)
    assert 'output issues: 3 run(s)' in result.table()
    assert 'outputs.units' in result.table()
    assert result.to_dict()['arms']['baseline']['runs'][0]['output_issues']


def test_paired_deltas_exclude_invalid_pairs_without_discarding_other_outputs():
    good = fg_env.load(contract('valid'), seed=1).run()
    bad = fg_env.load(contract(), seed=1).run()
    # The invalid output is a real number, so numeric filtering alone is insufficient.
    result = ExperimentResult({'control': ArmResult(None, [good, good], {'units': {}, 'sales': {}}),
                               'treatment': ArmResult(None, [bad, good], {'units': {}, 'sales': {}})}, [1, 2])
    deltas = result.deltas()['treatment']
    assert deltas['units']['n'] == 1 and deltas['units']['mean'] == 0
    assert deltas['sales']['n'] == 2 and deltas['sales']['mean'] == -.5


def test_all_invalid_arm_pairs_produce_no_spurious_effect_estimate():
    c = contract()
    c['arms'] = {'control': {'patch': {'world': {'sales': 4}}},
                 'treatment': {'patch': {'world': {'sales': 3}}}}
    result = fg_env.experiment(c, runs=2, arms=['control', 'treatment'])
    assert result.arms['control'].outputs['units']['mean'] == 2
    assert result.arms['treatment'].outputs['units'] == {'n': 0}
    assert 'units' not in result.deltas('control')['treatment']
    assert result.deltas('control')['treatment']['sales']['mean'] == -1
    assert 'output issues: 2 run(s)' in result.table()
