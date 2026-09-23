"""Selecting a business-state target evaluates each index once."""
import json

import pytest

import fg_env


def grid_contract(body):
    return {'name': 'Demand allocation', 'clock': {'rounds': 3}, 'types': {'firm': {}},
            'world': {'grid': [[10, 20], [30, 40]], 'next': 0},
            'events': [{'at': 2, 'do': body + ['$world.next = $randint(0, 1000000)']}],
            'outputs': {'grid': '$world.grid', 'next': '$world.next'}}


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('op', ['=', '+=', '-='])
def test_inline_random_target_matches_explicit_single_evaluation(seed, op):
    inline = grid_contract([f'$world.grid[$randint(0, 1)][$randint(0, 1)] {op} $randint(1, 9)'])
    # Assignment evaluates its value before resolving its target.
    explicit = grid_contract(['$amount = $randint(1, 9)', '$chosen = $randint(0, 1)', '$slot = $randint(0, 1)',
                              f'$world.grid[$chosen][$slot] {op} $amount'])
    a, b = fg_env.run(inline, seed=seed), fg_env.run(explicit, seed=seed)
    assert a.ok and b.ok, (a.error, b.error)
    assert a.outputs == b.outputs


def entity_contract(target):
    return {'name': 'Selected supplier', 'clock': {'rounds': 3},
            'types': {'firm': {'props': {'cash': 100}}},
            'entities': {'buyer': {'type': 'firm'}, 'supplier': {'type': 'firm'}},
            'events': [{'at': 1, 'do': ['$firms = [$entity(buyer), $entity(supplier)]',
                                      {'after': 1, 'do': [f'{target}.cash += 20']}]}],
            'outputs': {'cash': '$map(firm, $it.cash)'}}


@pytest.mark.parametrize('target', ['$firms[1]', '$filter(firm, true)[1]'])
def test_selected_entity_updates_directly_across_snapshot_and_fork(target):
    c = entity_contract(target)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    env = fg_env.load(c)
    env.run(rounds=1)
    snap = json.loads(json.dumps(env.snapshot()))
    restored = fg_env.Env.restore(c, snap)
    branch = fg_env.fork(c, snap, effects=['$entity(supplier).cash = 200'])
    assert branch.run().outputs == {'cash': [100, 220]}
    result = env.run()
    assert result.ok, result.error
    assert result.outputs == {'cash': [100, 120]}
    assert result.to_dict() == restored.run().to_dict()


def test_selected_link_updates_live_relation():
    c = entity_contract('$firms[1]')
    c['relations'] = {'credit': {'props': {'balance': 100}}}
    c['links'] = [{'relation': 'credit', 'from': 'buyer', 'to': 'supplier'}]
    c['events'][0]['do'] = ['$accounts = $links(buyer, credit)', '$accounts[0].balance -= 20']
    c['outputs'] = {'balance': '$link(buyer, supplier, credit).balance'}
    result = fg_env.run(c)
    assert result.ok, result.error
    assert result.outputs == {'balance': 80}


def test_refused_selected_entity_update_rolls_back():
    c = entity_contract('$firms[1]')
    c.pop('events')
    c['types']['firm']['agent'] = True
    c['actions'] = {'transfer': {'by': 'firm', 'do': [
        '$firms = [$entity(buyer), $entity(supplier)]', '$firms[1].cash += 20', {'fail': 'Not approved'}]}}
    env = fg_env.load(c)
    def play(wake):
        assert not wake.call('transfer').ok
        wake.end()
    result = env.run(play)
    assert result.status == 'completed' and result.degraded == ['agents_never_acted'], result.error
    assert result.outputs == {'cash': [100, 100]}


def test_entity_selected_from_map_updates_directly():
    c = entity_contract('$firms[1]')
    c['events'][0]['do'] = ['$by_id = $dict(firm, $it.id, $it)', '$by_id[supplier].cash += 20']
    result = fg_env.run(c)
    assert result.ok, result.error
    assert result.outputs == {'cash': [100, 120]}


def test_indexed_plain_local_data_is_not_mistaken_for_world_state():
    c = grid_contract(['$local_data = [{cash: 10}]', '$local_data[0].cash += 20'])
    env = fg_env.load(c)
    result = env.run()
    assert result.status == 'failed'
    assert "can only assign to an entity's property" in result.error
    assert env.props['grid'] == [[10, 20], [30, 40]]


def test_readonly_input_root_remains_rejected():
    c = grid_contract(['$inputs.data[0].cash += 20'])
    c['inputs'] = {'data': {'type': 'table', 'default': [{'cash': 10}]}}
    issues = [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    assert any('$inputs is read-only' in i.message for i in issues)
