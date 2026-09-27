"""Scheduling and summaries follow living entities, in original creation order."""
import fg_env
from fg_env.runtime.end_state import end_state

CASE = {
    'name': 'Interleaved actors', 'clock': {'rounds': 1},
    'types': {'worker': {'agent': True}, 'specialist': {'extends': 'worker'}, 'item': {}},
    'entities': {'a': {'type': 'specialist'}, 'box': {'type': 'item'},
                 'b': {'type': 'worker'}, 'c': {'type': 'specialist'}},
    'actions': {'work': {'by': 'worker', 'do': []}},
    'stages': [{'name': 'work'}],
}


def _actors(env):
    return [e.id for e in env.schedule.eligible(env.contract.stages[0], ordered=False)]


def test_living_order_survives_removal_rollback_creation_and_copy():
    env = fg_env.load(CASE)
    assert _actors(env) == ['a', 'b', 'c']
    mark = env.world.journal.mark()
    env.world.remove(env.world.entities['a'])
    env.world.add(env.world.new_entity('worker', 'd', None, {}, None, 'test'), 'test')
    assert _actors(env) == ['b', 'c', 'd']
    clone = env.copy()
    assert _actors(clone) == ['b', 'c', 'd']
    env.world.journal.rollback(mark)
    assert _actors(env) == ['a', 'b', 'c']
    assert _actors(clone) == ['b', 'c', 'd']
    state = end_state(clone.contract, clone.world)
    assert state['types']['worker']['alive'] == 2
    assert [e['id'] for e in state['types']['worker']['entities']] == ['b', 'd']
    assert state['types']['specialist']['alive'] == 1


def test_normal_binding_scheduling_and_summary_do_not_scan_entity_history():
    env = fg_env.load(CASE)
    env.world.remove(env.world.entities['a'])
    class LookupOnly(dict):
        def __iter__(self):
            raise AssertionError('Full entity history was scanned')

        def values(self):
            raise AssertionError('Full entity history was scanned')
    env.world.entities = LookupOnly(env.world.entities)
    env.driver.bind({'*': lambda wake: wake.end()})
    env.driver.bind({'worker': lambda wake: wake.end(), 'b': lambda wake: wake.end()})
    assert _actors(env) == ['b', 'c']
    state = end_state(env.contract, env.world)
    assert state['types']['specialist']['entities'][0]['id'] == 'c'
