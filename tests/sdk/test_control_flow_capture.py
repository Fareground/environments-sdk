"""Capture trimming preserves incoming locals across optional and nested execution."""
import json

import pytest

import fg_env


def contract(body, locals=None):
    return {'name': 'Deferred conditional settlement', 'clock': {'rounds': 4}, 'types': {'item': {}},
            'world': {'total': 0}, 'events': [{'at': 1, 'do': ['$batch = $range(1000)', *(locals or []),
                {'after': 1, 'do': body}]}], 'outputs': {'total': '$world.total'}}


def run(c, expected, *, pruned=True):
    env = fg_env.load(c, seed=15)
    assert env.run(rounds=1).status == 'running'
    assert ('batch' not in env.world.scheduled[0][2]['vars']) == pruned
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.ok, result.error
    assert result.outputs == {'total': expected}
    assert result.to_dict() == restored.run().to_dict()


@pytest.mark.parametrize('condition,expected', [('true', 7), ('false', 3)])
def test_conditional_assignment_keeps_incoming_value_for_unassigned_branch(condition, expected):
    run(contract([{'if': condition, 'then': ['$amount = 7']}, '$world.total += $amount'], ['$amount = 3']), expected)


@pytest.mark.parametrize('items,expected', [([], 3), ([1, 2], 6)])
def test_loop_accumulator_keeps_incoming_value_even_when_loop_is_empty(items, expected):
    run(contract([{'each': '$items', 'as': 'value', 'do': ['$amount += $value']}, '$world.total = $amount'],
                 ['$amount = 3', f'$items = {items}']), expected)


def test_loop_filter_nested_conditional_and_repeat_dependencies():
    run(contract([{'each': '$items', 'as': 'value', 'where': '$value > $cutoff', 'do': [
        {'if': '$value == 3', 'then': ['$amount += $value']}]},
        {'repeat': '$limit', 'while': '$amount < $target', 'do': ['$amount += $increment']},
        '$world.total = $amount'], ['$items = [1, 2, 3]', '$cutoff = 1', '$amount = 1',
                                  '$limit = 4', '$target = 6', '$increment = 1']), 6)


def test_nested_delay_retains_future_reads():
    run(contract([{'after': '$delay', 'do': [{'if': '$enabled', 'then': ['$world.total += $amount']}]}],
                 ['$delay = 1', '$enabled = true', '$amount = 8']), 8)


def test_call_in_nested_condition_retains_implicit_context():
    run(contract([{'if': '$sum($batch) > 0', 'then': ['$world.total = 8']}]), 8, pruned=False)


def test_bare_definition_keeps_full_implicit_scope():
    c = contract([{'if': '$allowed', 'then': ['$world.total = 8']}])
    c['defs'] = {'allowed': {'expr': 'true'}}
    run(c, 8, pruned=False)


def test_other_operation_preserves_scope():
    run(contract([{'if': 'true', 'then': [{'emit': 'settled'}, '$world.total = 8']}]), 8, pruned=False)


def test_conditional_bulk_snapshot_growth_is_linear():
    sizes = []
    for count in (100, 200):
        c = contract([])
        c['events'][0]['do'] = [f'$batch = $range({count})', {'each': '$batch', 'as': 'invoice', 'do': [
            {'after': 1, 'do': [{'if': '$invoice % 2 == 0', 'then': ['$world.total += $invoice']}]}]}]
        env = fg_env.load(c)
        env.run(rounds=1)
        sizes.append(len(json.dumps(env.snapshot())))
        assert env.run().outputs['total'] == sum(i for i in range(count) if i % 2 == 0)
    assert sizes[1] < 2.5 * sizes[0], sizes


def test_single_conditional_object_uses_the_same_capture_rules():
    run(contract({'if': 'true', 'then': '$world.total += $amount'}, ['$amount = 9']), 9)


def test_function_in_assignment_keeps_extension_visible_locals(monkeypatch):
    from fg_env.sdk.expr_calls import FUNCTIONS, FunctionSpec
    name = 'capture_total'
    monkeypatch.setitem(FUNCTIONS, name, FunctionSpec(
        name, lambda call: sum(call.scope.vars['batch']), f'{name}()', 'Reads implicit locals', 0, 0))
    run(contract([{'if': 'true', 'then': [f'$world.total = ${name}()']}]), 499500, pruned=False)
