"""Delayed business rules keep live relationship references, including across snapshots."""
import json

import pytest

import fg_env
from fg_env.expr import compile_expr


def contract(body, *, symmetric=False):
    return {'name': 'Delayed supplier settlement', 'clock': {'rounds': 3},
            'types': {'firm': {}},
            'entities': {'buyer': {'type': 'firm'}, 'supplier': {'type': 'firm'}},
            'relations': {'credit': {'symmetric': symmetric, 'props': {'balance': 100}}},
            'links': [{'relation': 'credit', 'from': 'buyer', 'to': 'supplier'}],
            'world': {'seen': {'type': 'any', 'default': 0}},
            'events': [{'at': 1, 'do': ['$account = $link(buyer, supplier, credit)',
                {'after': 1, 'do': body}, '$account.balance = 140']}],
            'outputs': {'seen': '$world.seen', 'balance': '$link(buyer, supplier, credit).balance'}}


def run_restored(c):
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    env = fg_env.load(c, seed=12)
    assert env.run(rounds=1).status == 'running'
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.to_dict() == restored.run().to_dict()
    assert result.ok, result.error
    return result


@pytest.mark.parametrize('symmetric', [False, True])
def test_delayed_relationship_reads_current_state_and_settles_balance(symmetric):
    c = contract(['$world.seen = $account.balance', '$account.balance -= 20'], symmetric=symmetric)
    if symmetric:
        c['events'][0]['do'][0] = '$account = $link(supplier, buyer, credit)'
    assert run_restored(c).outputs == {'seen': 140, 'balance': 120}


def test_nested_delays_and_collections_keep_relationship_identity():
    c = contract([{'after': 1, 'do': [
        {'each': '$accounts', 'as': 'line', 'do': ['$line.balance -= 20']},
        '$world.seen = $accounts[0].target.id == supplier']}])
    c['events'][0]['do'].insert(1, '$accounts = [$account]')
    assert run_restored(c).outputs == {'seen': True, 'balance': 120}


def test_removed_relationship_fails_instead_of_reading_a_stale_balance():
    c = contract(['$world.seen = $account.balance'])
    c['events'][0]['do'].append({'unlink': 'credit', 'from': 'buyer', 'to': 'supplier'})
    c['outputs'] = {'seen': '$world.seen'}
    env = fg_env.load(c)
    env.run(rounds=1)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    for candidate in (env, restored):
        result = candidate.run()
        assert result.status == 'failed'
        assert 'no longer exists' in result.error


def test_refused_scheduling_action_leaves_relationship_and_queue_unchanged():
    c = contract([])
    c.pop('events')
    c['types']['firm']['agent'] = True
    c['actions'] = {'settle': {'by': 'firm', 'do': [
        '$account = $link(buyer, supplier, credit)',
        {'after': 1, 'do': ['$account.balance -= 20']},
        {'fail': 'Not authorized'}]}}
    env = fg_env.load(c)

    def play(wake):
        assert not wake.call('settle').ok
        assert not env.world.scheduled
        wake.end()

    result = env.run(play)
    assert result.status == 'completed' and result.degraded == ['agents_never_acted'], result.error
    assert result.outputs['balance'] == 100


@pytest.mark.parametrize('data', [
    {'$entity': 'buyer'}, {'$link': ['credit', 'buyer', 'supplier']},
    {'$literal': {'$link': ['credit', 'buyer', 'supplier']}},
])
def test_marker_shaped_business_data_remains_literal(data):
    c = contract(['$world.seen = $payload'])
    c['world']['payload'] = {'default': data, 'type': 'any'}
    c['events'][0]['do'].insert(1, '$payload = $world.payload')
    assert run_restored(c).outputs['seen'] == data


def test_old_plain_link_captures_remain_plain_after_restore():
    c = contract(['$world.seen = $account.balance'])
    env = fg_env.load(c)
    env.run(rounds=1)
    snapshot = json.loads(json.dumps(env.snapshot()))
    snapshot['scheduled'][0][2].pop('capture_version')
    snapshot['scheduled'][0][2]['vars']['account'] = {
        'source': 'buyer', 'target': 'supplier', 'kind': 'credit', 'value': 1, 'balance': 100}
    assert fg_env.Env.restore(c, snapshot).run().outputs == {'seen': 100, 'balance': 140}


@pytest.mark.parametrize('data', [{'$link': ['credit', 'buyer', 'supplier']}, {'$literal': 42}])
def test_old_literal_captures_do_not_become_new_reference_tags(data):
    c = contract(['$world.seen = $account'])
    env = fg_env.load(c)
    env.run(rounds=1)
    snapshot = json.loads(json.dumps(env.snapshot()))
    snapshot['scheduled'][0][2].pop('capture_version')
    snapshot['scheduled'][0][2]['vars']['account'] = data
    assert fg_env.Env.restore(c, snapshot).run().outputs['seen'] == data


def test_a_refused_delayed_settlement_fails_the_run_and_rolls_back_link_mutations():
    c = contract(['$account.balance -= 20', {'fail': 'Settlement cancelled'}])
    env = fg_env.load(c, seed=12)
    result = env.run()
    assert result.status == 'failed' and 'Settlement cancelled World logic cannot be refused' in result.error
    assert compile_expr(c['outputs']['balance'])(env.world.evaluation.scope()) == 140


def test_forked_relationship_references_belong_to_the_fork():
    c = contract(['$world.seen = $account.balance', '$account.balance -= 20'])
    env = fg_env.load(c)
    env.run(rounds=1)
    branch = fg_env.fork(c, env.snapshot(), effects=['$link(buyer, supplier, credit).balance = 200'])
    assert branch.run().outputs == {'seen': 200, 'balance': 180}
    assert env.run().outputs == {'seen': 140, 'balance': 120}
