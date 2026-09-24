"""Deferred workflow arguments preserve literal business data and entity identity."""
import json

import pytest

import fg_env

DATA = [
    {'$entity': 'supplier'},
    {'$link': ['credit', 'buyer', 'supplier']},
    {'$literal': {'$entity': 'supplier'}},
    {'rows': [{'$entity': 'supplier'}, {'$literal': 42}]},
]


def contract(payload):
    """A purchase pushed on a procedure's stack in round 1 and resolved in round 2, after the supplier changed."""
    params = {'payload': {'type': 'enum', 'values': [payload]},
              'recipient': {'type': 'entity', 'of': 'manager'}}
    settle = ['$world.seen = $params.payload', '$world.amount = $params.recipient.amount']
    return {'name': 'Deferred purchase review', 'clock': {'rounds': 3},
            'types': {'manager': {'agent': True, 'props': {'amount': 100}}},
            'entities': {name: {'type': 'manager'} for name in ('buyer', 'supplier')},
            'world': {'seen': {'type': 'any', 'default': None}, 'amount': 0},
            'outputs': {'seen': '$world.seen', 'amount': '$world.amount'},
            'mechanisms': {'review': {'kind': 'decision', 'mode': 'procedure', 'stack': {
                'who': 'manager', 'silence': 'wait', 'unanswered': 'wait',
                'kinds': {'purchase': {'params': params, 'resolve': settle}}}}},
            'stages': [{'name': 'submit', 'who': "$it.id == 'buyer'", 'actions': ['review_purchase']}],
            'events': [{'at': 1, 'phase': 'end', 'do': ['$entity(supplier).amount = 140']}]}


def participant(payload):
    def play(wake):
        if wake.entity_id == 'buyer' and wake.round == 1 and wake.stage != 'review_stack':
            assert wake.call('review_purchase', {'payload': payload, 'recipient': 'supplier'}).ok
        if wake.round == 2 and wake.stage == 'review_stack':
            assert wake.call('review_pass', {}).ok
        wake.end()
    return play


@pytest.mark.parametrize('payload', DATA)
def test_workflow_literal_arguments_and_live_entities_survive_restore(payload):
    c = contract(payload)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    env = fg_env.load(c, seed=24)
    play = participant(payload)
    assert env.run(play, rounds=1).status == 'running'
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run(play)
    assert result.ok, result.error
    assert result.to_dict() == restored.run(play).to_dict()
    assert result.outputs == {'seen': payload, 'amount': 140}


@pytest.mark.parametrize('payload', [{'$link': ['credit', 'buyer', 'supplier']}, {'$literal': 42}])
def test_old_workflow_snapshots_keep_literal_maps_and_entity_references(payload):
    c = contract(payload)
    env = fg_env.load(c)
    play = participant(payload)
    env.run(play, rounds=1)
    snapshot = json.loads(json.dumps(env.snapshot()))
    held = snapshot['props']['review_stack']['items'][0]
    held.pop('capture_version')
    held['params'] = {'payload': payload, 'recipient': {'$entity': 'supplier'}}
    result = fg_env.Env.restore(c, snapshot).run(play)
    assert result.ok, result.error
    assert result.outputs == {'seen': payload, 'amount': 140}


def test_fork_preserves_literal_data_and_rebinds_entities_to_branch():
    payload = DATA[3]
    c = contract(payload)
    env = fg_env.load(c)
    play = participant(payload)
    env.run(play, rounds=1)
    branch = fg_env.fork(c, env.snapshot(), effects=['$entity(supplier).amount = 200'])
    assert branch.run(play).outputs == {'seen': payload, 'amount': 200}
    assert env.run(play).outputs == {'seen': payload, 'amount': 140}
