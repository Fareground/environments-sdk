"""Delayed model aliases evaluate in the executing world."""
import json

import pytest

import fg_env


def contract(nested=False, second_delay=False):
    body = (['$model = $models[0]'] if nested else []) + [
        '$world.seen = [$model.demand, $pattern.price(20)]']
    if second_delay:
        body = [{'after': 1, 'do': body}]
    return {'name': 'Deferred business models', 'clock': {'rounds': 3},
            'types': {'firm': {}}, 'inputs': {'base': {'type': 'number', 'default': 100}},
            'world': {'seen': {'type': 'any', 'default': None}},
            'patterns': {'demand': {'kind': 'trend', 'start': '$inputs.base', 'slope': 10},
                         'price': {'kind': 'elasticity', 'elasticity': -2, 'reference': 10}},
            'events': [{'at': 1, 'do': ['$model = $pattern', '$models = [$model]',
                                       {'after': 1, 'do': body}]}],
            'outputs': {'seen': '$world.seen'}}


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('second_delay', [False, True])
def test_model_alias_is_json_safe_and_evaluates_branch_inputs_and_time(nested, second_delay):
    c = contract(nested, second_delay)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    env = fg_env.load(c, seed=11)
    assert env.run(rounds=1).status == 'running'
    snap = json.loads(json.dumps(env.snapshot()))
    restored = fg_env.Env.restore(c, snap)
    branch = fg_env.fork(c, snap, inputs={'base': 200})
    result = env.run()
    assert result.ok, result.error
    increment = 20 if second_delay else 10
    assert result.outputs == {'seen': [100 + increment, .25]}
    assert restored.run().to_dict() == result.to_dict()
    fork_result = branch.run()
    assert fork_result.ok, fork_result.error
    assert fork_result.outputs == {'seen': [200 + increment, .25]}


def test_in_memory_branch_does_not_read_original_pattern_runtime():
    c = contract()
    env = fg_env.load(c)
    env.run(rounds=1)
    branch = fg_env.fork(c, env.snapshot(), inputs={'base': 200})
    result = branch.run()
    assert result.ok, result.error
    assert result.outputs == {'seen': [210, .25]}
    assert env.run().outputs == {'seen': [110, .25]}


@pytest.mark.parametrize('version', [0, 1, 2, 3, 4, 5])
def test_pattern_view_marker_keeps_literal_meaning(version):
    c = contract()
    payload = {'$view': 'pattern'}
    c['world']['payload'] = {'type': 'any', 'default': payload}
    c['events'][0]['do'] = ['$payload = $world.payload',
                           {'after': 1, 'do': ['$world.seen = $payload']}]
    env = fg_env.load(c)
    env.run(rounds=1)
    snap = json.loads(json.dumps(env.snapshot()))
    item = snap['scheduled'][0][2]
    item['capture_version'] = version
    if version < 2:
        # Before view markers existed, these maps were stored without escaping.
        item['vars']['payload'] = payload
    result = fg_env.Env.restore(c, snap).run()
    assert result.ok, result.error
    assert result.outputs == {'seen': payload}


def test_sampled_value_stays_frozen_while_model_alias_follows_time_after_two_restores():
    c = contract(second_delay=True)
    c['events'][0]['do'].insert(0, '$initial = $pattern.demand')
    c['events'][0]['do'][-1]['do'][0]['do'] = [
        '$world.seen = [$initial, $model.demand]']
    env = fg_env.load(c)
    for _ in range(2):
        assert env.run(rounds=1).status == 'running'
        env = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok, result.error
    assert result.outputs == {'seen': [100, 120]}


def test_model_alias_uses_replacement_contract_in_branch():
    c = contract()
    env = fg_env.load(c)
    env.run(rounds=1)
    branch = fg_env.fork(c, json.loads(json.dumps(env.snapshot())),
                         patch={'patterns': {'demand': {'kind': 'trend', 'start': 300, 'slope': 50}}})
    result = branch.run()
    assert result.ok, result.error
    assert result.outputs == {'seen': [350, .25]}
    assert env.run().outputs == {'seen': [110, .25]}
