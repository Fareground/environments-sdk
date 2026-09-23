"""Agent-authored accumulators need a value before a compound update."""
import pytest

import fg_env


def contract(effects):
    return {'name': 'Invoice subtotal', 'clock': {'rounds': 30}, 'types': {'item': {}},
            'world': {'total': 0}, 'events': [{'at': 20, 'do': effects}],
            'outputs': {'total': '$world.total'}}


def errors(c):
    return [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']


@pytest.mark.parametrize('op', ['+=', '-=', '*=', '/='])
def test_uninitialized_compound_local_is_a_static_error_with_a_repair(op):
    c = contract([f'$subtotal {op} 3', '$world.total = $subtotal'])
    found = errors(c)
    assert len(found) == 1
    assert found[0].path == 'events[0].do[0]'
    assert '$subtotal' in found[0].message and 'initial' in found[0].fix


@pytest.mark.parametrize('op, expected', [('+=', 9), ('-=', 3), ('*=', 18), ('/=', 2)])
def test_initialized_local_retains_arithmetic_semantics(op, expected):
    c = contract(['$subtotal = 6', f'$subtotal {op} 3', '$world.total = $subtotal'])
    assert not errors(c)
    assert fg_env.run(c).outputs['total'] == expected


def test_zero_argument_definition_can_supply_the_original_value():
    c = contract(['$subtotal += 3', '$world.total = $subtotal'])
    c['defs'] = {'subtotal': {'expr': '6'}}
    assert not errors(c)
    assert fg_env.run(c).outputs['total'] == 9


def test_definition_requiring_arguments_does_not_supply_a_bare_value():
    c = contract(['$subtotal += 3'])
    c['defs'] = {'subtotal': {'args': ['n'], 'expr': '$n'}}
    assert errors(c)


def test_delayed_callback_requires_an_available_initial_value():
    c = contract([{'after': 1, 'do': ['$subtotal += 3']}])
    assert errors(c)[0].path == 'events[0].do[0].do[0]'
    c['events'][0]['do'].insert(0, '$subtotal = 6')
    c['events'][0]['do'][1]['do'].append('$world.total = $subtotal')
    assert not errors(c)
    assert fg_env.run(c).outputs['total'] == 9


def test_loop_binding_can_be_updated_without_prior_assignment():
    c = contract([{'each': [1, 2, 3], 'as': 'subtotal',
                   'do': ['$subtotal *= 2', '$world.total += $subtotal']}])
    assert not errors(c)
    assert fg_env.run(c).outputs['total'] == 12


def test_then_branch_does_not_initialize_a_local_in_the_else_branch():
    c = contract([{'if': '$world.total != 0', 'then': ['$subtotal = 6'], 'else': ['$subtotal += 3']}])
    found = errors(c)
    assert len(found) == 1
    assert found[0].path == 'events[0].do[0].else[0]'


@pytest.mark.parametrize('condition,expected', [('$world.total == 0', 9), ('$world.total != 0', 3)])
def test_both_branches_can_update_an_enclosing_local(condition, expected):
    c = contract(['$subtotal = 6', {'if': condition, 'then': ['$subtotal += 3'], 'else': ['$subtotal -= 3']},
                  '$world.total = $subtotal'])
    assert not errors(c)
    assert fg_env.run(c).outputs['total'] == expected
