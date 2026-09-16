"""Rule changes must account for obligations already scheduled by earlier rules."""
import copy
import json

import pytest

import fg_env


def invoice():
    return {'name': 'Pending invoice', 'clock': {'rounds': 4}, 'types': {'item': {}},
            'inputs': {'fee': {'default': 3, 'type': 'number'}}, 'world': {'total': 0},
            'events': [{'at': 1, 'do': [{'after': 2, 'do': ['$world.total += $inputs.fee']}]}],
            'outputs': {'total': '$world.total'}}


@pytest.mark.parametrize('dependency', ['input', 'definition', 'record', 'type'])
def test_fork_rejects_removed_dependencies_of_pending_effects(dependency):
    c = invoice()
    expression = '$inputs.fee'
    section = 'inputs'
    if dependency == 'definition':
        c['defs'] = {'fee': {'expr': '3'}}
        expression, section = '$fee', 'defs'
    elif dependency == 'record':
        c['records'] = {'orders': {'fields': {'amount': 'number'}}}
        expression, section = '$count($records(orders))', 'records'
    elif dependency == 'type':
        expression, section = '$count(item)', 'types'
    c['events'][0]['do'][0]['do'] = [f'$world.total += {expression}']
    env = fg_env.load(c)
    env.run(rounds=1)
    before = json.dumps(env.snapshot(), sort_keys=True)
    changed = copy.deepcopy(c)
    changed[section] = {} if section != 'types' else {'other': {}}
    changed['events'] = []
    with pytest.raises(fg_env.ContractError, match='pending') as error:
        env.fork(contract=changed)
    assert any('scheduled' in issue.path and issue.fix for issue in error.value.issues)
    assert json.dumps(env.snapshot(), sort_keys=True) == before
    assert env.run().ok


def test_input_intervention_changes_pending_rule_without_changing_frozen_locals():
    c = invoice()
    c['events'][0]['do'] = ['$locked = $inputs.fee',
        {'after': 2, 'do': ['$world.total += $locked + $inputs.fee']}]
    env = fg_env.load(c)
    env.run(rounds=1)
    fork = env.fork(inputs={'fee': 7}, patch={'events': []})
    restored = fg_env.Env.restore(fork.contract, json.loads(json.dumps(fork.snapshot())))
    result = fork.run()
    assert result.outputs['total'] == 10
    assert result.to_dict() == restored.run().to_dict()
    assert env.run().outputs['total'] == 6


def test_dependency_can_be_removed_after_pending_work_finishes():
    c = invoice()
    env = fg_env.load(c)
    env.run(rounds=3)
    changed = {**c, 'inputs': {}, 'events': []}
    assert env.fork(contract=changed).run().outputs['total'] == 3


def test_nested_pending_rule_is_checked_even_after_its_source_event_is_removed():
    c = invoice()
    c['events'][0]['do'][0]['do'] = [
        {'if': '$world.total == 0', 'then': [{'after': 1, 'do': ['$world.total += $inputs.fee']}]}]
    env = fg_env.load(c)
    env.run(rounds=1)
    with pytest.raises(fg_env.ContractError, match='no such input'):
        env.fork(contract={**c, 'events': [], 'inputs': {}})


def test_captured_action_parameters_survive_rewriting_the_action():
    c = invoice()
    c['events'] = []
    c['types'] = {'buyer': {'agent': True}}
    c['entities'] = {'a': {'type': 'buyer'}}
    c['actions'] = {'buy': {'by': 'buyer', 'params': {'quantity': {'type': 'int'}},
        'do': [{'after': 2, 'do': ['$world.total += $params.quantity * $inputs.fee']}]}}
    env = fg_env.load(c)
    def buy(wake):
        assert wake.call('buy', {'quantity': 4}).ok
        wake.end()
    env.run(buy, rounds=1)
    fork = env.fork(patch={'actions': {'buy': {'do': []}}}, inputs={'fee': 5})
    assert fork.run().outputs['total'] == 20


def test_captured_local_can_replace_a_definition_removed_in_the_new_contract():
    c = invoice()
    c['defs'] = {'fee': {'expr': '99'}}
    c['events'][0]['do'] = ['$fee = 4', {'after': 2, 'do': ['$world.total += $fee']}]
    env = fg_env.load(c)
    env.run(rounds=1)
    assert env.fork(contract={**c, 'events': [], 'defs': {}}).run().outputs['total'] == 4
