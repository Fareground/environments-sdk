"""Mechanism predicates have expression semantics even without $ references."""
import copy
import json

import pytest
from test_mech_dynamics import ARENA, MAGES, _script

import fg_env

CONDITIONS = [('false', False), ('1 > 2', False), ('true', True), ('1 < 2', True)]


def production(condition):
    return {'name': 'Production gate', 'clock': {'rounds': 1},
        'types': {'producer': {'agent': True}}, 'entities': {'p': {'type': 'producer'}},
        'mechanisms': {
            'goods': {'kind': 'economy', 'mode': 'inventory', 'who': 'producer', 'items': {'widget': {}}},
            'plant': {'kind': 'economy', 'mode': 'production', 'who': 'producer', 'inventory': 'goods',
                      'recipes': {'make': {'outputs': {'widget': 1}, 'when': condition}}}}}


@pytest.mark.parametrize('condition,allowed', CONDITIONS)
def test_production_requirement_controls_actual_production(condition, allowed):
    env = fg_env.load(production(condition), seed=1)
    accepted = []
    def play(wake):
        accepted.append(wake.call('plant_start', {'recipe': 'make'}).ok)
        wake.end()
    result = env.run(play)
    assert result.status == 'completed', result.error  # refused production degrades the run: the agent never acted
    assert accepted == [allowed]
    assert env.entity('p')['props']['goods'].get('widget', 0) == int(allowed)


@pytest.mark.parametrize('condition,advance', CONDITIONS)
def test_procedure_transition_respects_constant_condition(condition, advance):
    c = {'name': 'Approval gate', 'clock': {'rounds': 3}, 'types': {'item': {}},
         'mechanisms': {'review': {'kind': 'flow', 'mode': 'procedure', 'phases': {
             'pending': {'next': [{'to': 'approved', 'when': condition}]}, 'approved': {}}}}}
    env = fg_env.load(c, seed=1)
    result = env.run()
    assert result.ok, result.error
    assert env.props['review_phase'] == ('approved' if advance else 'pending')


@pytest.mark.parametrize('condition,immune', CONDITIONS)
def test_status_unless_controls_application(condition, immune):
    c = copy.deepcopy(ARENA)
    c['clock'] = {'rounds': 1}
    c['mechanisms']['conditions']['statuses']['curse']['unless'] = condition
    c['events'] = [{'at': 1, 'do': [{'conditions': 'conditions', 'action': 'apply',
                                   'status': 'curse', 'who': '$entity(ann)'}]}]
    env = fg_env.load(c, seed=1)
    result = env.run(rounds=1)
    assert result.ok, result.error
    assert ('curse' in env.entity('ann')['props']['conditions']) is not immune


@pytest.mark.parametrize('condition,interrupt', CONDITIONS)
def test_channel_interrupt_controls_pending_resolution_and_restore(condition, interrupt):
    c = copy.deepcopy(MAGES)
    c['clock'] = {'rounds': 3}
    c['mechanisms']['spells']['actions']['meteor']['interrupt'] = condition
    play = _script({('ann', 1): [('meteor', {'target': 'bob', 'power': 1})]})
    env = fg_env.load(c, seed=1)
    env.run(play, rounds=1)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run(play, rounds=2)
    assert result.ok, result.error
    assert env.entity('bob')['props']['hp'] == (50 if interrupt else 40)
    assert result.to_dict() == restored.run(play, rounds=2).to_dict()


@pytest.mark.parametrize('condition,enabled', CONDITIONS)
def test_demand_filter_and_report_replay_agree(condition, enabled):
    from store_fixtures import store

    from fg_env.report.demand import _replay
    c = store(rounds=1, rate=0, segments={'bulk': {'rate': 3, 'where': condition}})
    env = fg_env.load(c, seed=1)
    result = env.run('idle')
    assert result.ok, result.error
    assert sum(env.entity(i)['props']['shop_expected'] for i in ('a', 'b')) == (6 if enabled else 0)
    if not enabled:
        assert result.outputs['shop_demand'] == 0
    tallies, _, _ = _replay(env.contract, result, ['shop'])
    assert any(key[1] == 'bulk' for key in tallies) is enabled


@pytest.mark.parametrize('condition,enabled', CONDITIONS + [(False, False), (True, True)])
def test_demand_history_condition_controls_recording(condition, enabled):
    from store_fixtures import store
    env = fg_env.load(store(rounds=1, rate=3, record=condition), seed=1)
    result = env.run('idle')
    assert result.ok, result.error
    assert len(env.world.records_store.get('shop_history', [])) == (2 if enabled else 0)
