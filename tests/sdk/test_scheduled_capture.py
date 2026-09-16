"""Delayed assignments retain needed state without copying unused enclosing batches."""
import json

import fg_env


def batch(size):
    return {'name': 'Delayed invoices', 'clock': {'rounds': 3}, 'types': {'item': {}}, 'world': {'total': 0},
            'events': [{'at': 1, 'do': [f'$batch = $range({size})',
                {'each': '$batch', 'as': 'invoice', 'do': [{'after': 1, 'do': ['$world.total += $invoice']}]}]}],
            'outputs': {'total': '$world.total'}}


def test_unused_enclosing_batch_does_not_make_snapshot_growth_quadratic():
    sizes = []
    for count in (100, 200):
        c = batch(count)
        env = fg_env.load(c, seed=1)
        assert env.run(rounds=1).status == 'running'
        snapshot = json.dumps(env.snapshot())
        sizes.append(len(snapshot))
        resumed = fg_env.Env.restore(c, json.loads(snapshot))
        result = env.run()
        assert result.outputs['total'] == count * (count - 1) // 2
        assert result.to_dict() == resumed.run().to_dict()
    assert sizes[1] < 2.5 * sizes[0], sizes


def test_compound_locals_and_index_references_survive_capture_and_restore():
    c = {'name': 'Indexed delivery', 'clock': {'rounds': 3}, 'types': {'item': {}},
         'world': {'items': [0, 0], 'result': 0},
         'events': [{'at': 1, 'do': ['$index = 1', '$amount = 4', '$extra = 2',
            {'after': 1, 'do': ['$amount += $extra', '$world.items[$index] += $amount', '$world.result = $amount']},
            '$amount = 99', '$index = 0']}],
         'outputs': {'items': '$world.items', 'result': '$world.result'}}
    env = fg_env.load(c, seed=1)
    env.run(rounds=1)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok, result.error
    assert result.outputs == {'items': [0, 6], 'result': 6}
    assert result.to_dict() == restored.run().to_dict()


def test_local_shadowing_in_callback_does_not_need_the_previous_large_value():
    c = batch(100)
    c['events'][0]['do'][1]['do'][0]['do'] = ['$batch = 2', '$world.total += $batch + $invoice']
    env = fg_env.load(c, seed=1)
    env.run(rounds=1)
    assert all('batch' not in item['vars'] for _, _, item in env.world.scheduled)
    result = env.run()
    assert result.ok, result.error
    assert result.outputs['total'] == 5150


def test_function_callbacks_keep_full_scope_and_frozen_values():
    c = batch(4)
    c['events'][0]['do'][1]['do'][0]['do'] = ['$world.total += $sum($batch) + $invoice']
    env = fg_env.load(c, seed=1)
    env.run(rounds=1)
    assert all(item['vars']['batch'] == [0, 1, 2, 3] for _, _, item in env.world.scheduled)
    result = env.run()
    assert result.ok, result.error
    assert result.outputs['total'] == 30


def test_nested_condition_callbacks_omit_unused_scope():
    c = batch(4)
    c['events'][0]['do'][1]['do'][0]['do'] = [{'if': '$invoice > 1', 'then': ['$world.total += $invoice']}]
    env = fg_env.load(c, seed=1)
    env.run(rounds=1)
    assert all('batch' not in item['vars'] for _, _, item in env.world.scheduled)
    assert env.run().outputs['total'] == 5


def test_extension_function_with_implicit_local_reads_keeps_context(monkeypatch):
    from fg_env.sdk.expr_calls import FUNCTIONS, FunctionSpec
    name = 'scheduled_capture_probe'
    monkeypatch.setitem(FUNCTIONS, name, FunctionSpec(
        name, lambda call: sum(call.scope.vars['batch']) + call.scope.vars['invoice'],
        f'{name}()', 'Reads implicit scheduling context', 0, 0))
    c = batch(4)
    c['events'][0]['do'][1]['do'][0]['do'] = [f'$world.total += ${name}()']
    result = fg_env.run(c)
    assert result.ok, result.error
    assert result.outputs['total'] == 30


def test_bare_contract_definitions_keep_full_context():
    c = batch(4)
    c['defs'] = {'increment': {'expr': '2'}}
    c['events'][0]['do'][1]['do'][0]['do'] = ['$world.total += $increment + $invoice']
    env = fg_env.load(c)
    env.run(rounds=1)
    assert all('batch' in item['vars'] for _, _, item in env.world.scheduled)
    assert env.run().outputs['total'] == 14


def test_nested_schedules_keep_values_through_both_delays():
    c = batch(4)
    c['events'][0]['do'][1]['do'][0]['do'] = [{'after': 1, 'do': ['$world.total += $invoice']}]
    env = fg_env.load(c, seed=1)
    env.run(rounds=2)
    restored = fg_env.Env.restore(c, env.snapshot())
    result = env.run()
    assert result.outputs['total'] == 6
    assert result.to_dict() == restored.run().to_dict()


def test_continuous_delays_retain_their_timing_and_values():
    c = batch(4)
    c['clock'] = {'mode': 'continuous', 'horizon': 5}
    c['events'][0]['do'][1]['do'][0]['after'] = 1.5
    result = fg_env.run(c, seed=1)
    assert result.ok, result.error
    assert result.outputs['total'] == 6


def test_refused_parent_action_leaves_no_scheduled_work():
    c = {'name': 'Refused shipment', 'clock': {'rounds': 3},
         'types': {'worker': {'agent': True}}, 'entities': {'a': {'type': 'worker'}},
         'world': {'total': 0}, 'actions': {'ship': {'by': 'worker',
            'do': ['$quantity = 7', {'after': 1, 'do': ['$world.total += $quantity']}, {'fail': 'Capacity unavailable'}]}},
         'outputs': {'total': '$world.total'}}
    env = fg_env.load(c, seed=1)
    def play(w):
        if w.round == 1:
            assert not w.call('ship').ok
            assert env.world.scheduled == []
        w.end()
    result = env.run(play)
    assert result.ok, result.error
    assert result.outputs['total'] == 0


def test_entity_reference_stays_live_while_local_amount_is_frozen():
    c = {'name': 'Recipient changes', 'clock': {'rounds': 3},
         'types': {'recipient': {'props': {'received': 0}}}, 'entities': {'a': {'type': 'recipient'}},
         'world': {'total': 0}, 'events': [{'at': 1, 'do': ['$recipient = $entity(a)', '$amount = 3',
             {'after': 2, 'do': ['$recipient.received += $amount']}, '$recipient.received = 5', '$amount = 90']}],
         'outputs': {'received': '$entity(a).received'}}
    env = fg_env.load(c, seed=1)
    env.run(rounds=1)
    restored = fg_env.Env.restore(c, env.snapshot())
    result = env.run()
    assert result.ok, result.error
    assert result.outputs['received'] == 8
    assert result.to_dict() == restored.run().to_dict()


def test_restore_accepts_older_snapshots_with_extra_captured_bindings():
    c = batch(4)
    env = fg_env.load(c, seed=1)
    env.run(rounds=1)
    snapshot = json.loads(json.dumps(env.snapshot()))
    for index, entry in enumerate(snapshot['scheduled']):
        entry[2]['vars'].update(batch=[0, 1, 2, 3], i=index)
    restored = fg_env.Env.restore(c, snapshot)
    assert env.run().to_dict() == restored.run().to_dict()
