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


def contract(kind, payload):
    c = {'name': 'Deferred purchase review', 'clock': {'rounds': 3},
         'types': {'manager': {'agent': True, 'props': {'amount': 100}}},
         'entities': {name: {'type': 'manager'} for name in ('buyer', 'supplier')},
         'world': {'seen': {'type': 'any', 'default': None}, 'amount': 0},
         'outputs': {'seen': '$world.seen', 'amount': '$world.amount'}}
    params = {'payload': {'type': 'enum', 'values': [payload]},
              'recipient': {'type': 'entity', 'of': 'manager'}}
    settle = ['$world.seen = $params.payload', '$world.amount = $params.recipient.amount']
    if kind == 'review':
        c['mechanisms'] = {'review': {'kind': 'flow', 'mode': 'procedure', 'stack': {
            'who': 'manager', 'silence': 'wait', 'unanswered': 'wait',
            'kinds': {'purchase': {'params': params, 'resolve': settle}}}}}
        c['stages'] = [{'name': 'submit', 'who': "$it.id == 'buyer'", 'actions': ['review_purchase']}]
    else:
        c['actions'] = {'purchase': {'by': 'manager', 'params': params, 'do': []}}
        c['mechanisms'] = {'job': {'kind': 'conditions', 'mode': 'channeling', 'actions': {
            'purchase': {'rounds': 1, 'resolve': settle, 'on_interrupt': settle}}}}
    c['events'] = [{'at': 1, 'phase': 'end', 'do': ['$entity(supplier).amount = 140']}]
    return c


def participant(kind, payload):
    def play(wake):
        if wake.entity_id == 'buyer' and wake.round == 1 and wake.stage != 'review_stack':
            action = 'review_purchase' if kind == 'review' else 'purchase'
            assert wake.call(action, {'payload': payload, 'recipient': 'supplier'}).ok
        if kind == 'review' and wake.round == 2 and wake.stage == 'review_stack':
            assert wake.call('review_pass', {}).ok
        wake.end()
    return play


@pytest.mark.parametrize('kind', ['review', 'job'])
@pytest.mark.parametrize('payload', DATA)
def test_workflow_literal_arguments_and_live_entities_survive_restore(kind, payload):
    c = contract(kind, payload)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    env = fg_env.load(c, seed=24)
    play = participant(kind, payload)
    assert env.run(play, rounds=1).status == 'running'
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run(play)
    assert result.ok, result.error
    assert result.to_dict() == restored.run(play).to_dict()
    assert result.outputs == {'seen': payload, 'amount': 140}


@pytest.mark.parametrize('payload', DATA)
def test_interrupted_job_receives_unchanged_arguments(payload):
    c = contract('job', payload)
    c['mechanisms']['job']['actions']['purchase']['interrupt'] = '$round == 2'
    env = fg_env.load(c)
    result = env.run(participant('job', payload))
    assert result.ok, result.error
    assert result.outputs == {'seen': payload, 'amount': 140}


@pytest.mark.parametrize('kind', ['review', 'job'])
@pytest.mark.parametrize('payload', [{'$link': ['credit', 'buyer', 'supplier']}, {'$literal': 42}])
def test_old_workflow_snapshots_keep_literal_maps_and_entity_references(kind, payload):
    c = contract(kind, payload)
    env = fg_env.load(c)
    play = participant(kind, payload)
    env.run(play, rounds=1)
    snapshot = json.loads(json.dumps(env.snapshot()))
    if kind == 'review':
        held = snapshot['props']['review_stack']['items'][0]
    else:
        held = next(row for row in snapshot['entities'] if row['id'] == 'buyer')['props']['job']
    held.pop('capture_version')
    held['params'] = {'payload': payload, 'recipient': {'$entity': 'supplier'}}
    result = fg_env.Env.restore(c, snapshot).run(play)
    assert result.ok, result.error
    assert result.outputs == {'seen': payload, 'amount': 140}


@pytest.mark.parametrize('kind', ['review', 'job'])
def test_fork_preserves_literal_data_and_rebinds_entities_to_branch(kind):
    payload = DATA[3]
    c = contract(kind, payload)
    env = fg_env.load(c)
    play = participant(kind, payload)
    env.run(play, rounds=1)
    branch = fg_env.fork(c, env.snapshot(), effects=['$entity(supplier).amount = 200'])
    assert branch.run(play).outputs == {'seen': payload, 'amount': 200}
    assert env.run(play).outputs == {'seen': payload, 'amount': 140}


@pytest.mark.parametrize('payload', DATA)
def test_pending_job_inspection_exposes_logical_arguments(payload):
    c = contract('job', payload)
    c['events'][0]['do'] += [
        '$world.seen = $channeling($entity(buyer)).params.payload',
        '$world.amount = $channeling($entity(buyer)).params.recipient.amount']
    result = fg_env.load(c).run(participant('job', payload), rounds=1)
    assert result.status == 'running', result.error
    assert result.outputs == {'seen': payload, 'amount': 140}
