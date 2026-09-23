"""Entity-type hints follow local scope and alternative execution paths."""
import pytest

import fg_env


def contract(effects):
    return {'name': 'Supplier selection', 'clock': {'rounds': 3},
            'types': {'supplier': {'props': {'capacity': 10}}, 'campaign': {'props': {'reach': 20}}},
            'world': {'total': 0}, 'events': [{'at': 1, 'do': effects}],
            'outputs': {'total': '$world.total'}}


def check_and_run(effects, expected):
    c = contract(effects)
    errors = [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    assert not errors
    result = fg_env.run(c, seed=11)
    assert result.ok, result.error
    assert result.outputs == {'total': expected}


@pytest.mark.parametrize('condition', ['true', 'false'])
def test_branch_rebinding_does_not_change_sibling_type(condition):
    check_and_run([{'create': 'supplier', 'as': 'selected'},
                   {'if': condition,
                    'then': [{'create': 'campaign', 'as': 'selected'}, '$world.total = $selected.reach'],
                    'else': ['$world.total = $selected.capacity']}], 20 if condition == 'true' else 10)


def test_delayed_rebinding_does_not_change_present_local_type():
    check_and_run([{'create': 'supplier', 'as': 'selected'},
                   {'after': 1, 'do': [{'create': 'campaign', 'as': 'selected'},
                                      '$world.total += $selected.reach']},
                   '$world.total = $selected.capacity'], 30)


@pytest.mark.parametrize('probability', [0, 1])
def test_chance_alternatives_do_not_share_rebound_types(probability):
    check_and_run([{'create': 'supplier', 'as': 'selected'},
                   {'chance': [{'p': probability, 'do': [{'create': 'campaign', 'as': 'selected'},
                                                        '$world.total = $selected.reach']},
                               {'p': 1-probability, 'do': ['$world.total = $selected.capacity']}]}],
                  20 if probability else 10)


def test_plain_assignment_clears_stale_entity_type():
    check_and_run([{'create': 'supplier', 'as': 'selected'},
                   '$selected = {reach: 7}', '$world.total = $selected.reach'], 7)


def test_entity_alias_preserves_useful_property_diagnostics():
    c = contract([{'create': 'supplier', 'as': 'selected'}, '$alias = $selected',
                  '$world.total = $alias.capcity'])
    errors = [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    assert len(errors) == 1
    assert errors[0].path == 'events[0].do[2]'
    assert 'capacity' in errors[0].fix


@pytest.mark.parametrize('condition', ['true', 'false'])
def test_after_branch_union_keeps_both_possible_entity_types(condition):
    check_and_run([{'if': condition, 'then': [{'create': 'supplier', 'as': 'selected'}],
                    'else': [{'create': 'campaign', 'as': 'selected'}]},
                   f'$world.total = $selected.capacity if {condition} else $selected.reach'],
                  10 if condition == 'true' else 20)


@pytest.mark.parametrize('condition', ['true', 'false'])
def test_unknown_alternative_does_not_inherit_other_branch_entity_hint(condition):
    check_and_run([{'create': 'supplier', 'as': 'selected'},
                   {'if': condition, 'then': ['$selected = {reach: 7}']},
                   f'$world.total = $selected.reach if {condition} else $selected.capacity'],
                  7 if condition == 'true' else 10)


def test_skipped_repeat_preserves_incoming_type_as_a_possibility():
    check_and_run([{'create': 'supplier', 'as': 'selected'},
                   {'repeat': 1, 'while': 'false', 'do': [{'create': 'campaign', 'as': 'selected'}]},
                   '$world.total = $selected.capacity'], 10)


@pytest.mark.parametrize('items', [[], [1]])
def test_loop_rebinding_joins_incoming_and_body_types(items):
    check_and_run([{'create': 'supplier', 'as': 'selected'},
                   {'each': items, 'do': [{'create': 'campaign', 'as': 'selected'}]},
                   f'$world.total = $selected.reach if {bool(items).__str__().lower()} else $selected.capacity'],
                  20 if items else 10)


def test_loop_binding_shadow_does_not_change_outer_type():
    check_and_run([{'create': 'supplier', 'as': 'selected'},
                   {'each': [1], 'as': 'selected', 'do': ['$world.total += $selected']},
                   '$world.total += $selected.capacity'], 11)


def test_named_chance_binding_does_not_inherit_previous_entity_type():
    check_and_run([{'create': 'supplier', 'as': 'selected'},
                   {'chance': 'offer', 'outcomes': [{'reach': 7}], 'as': 'selected',
                    'do': ['$world.total = $selected.reach']}], 7)


def test_typo_after_alternative_entity_types_is_still_reported():
    c = contract([{'if': 'true', 'then': [{'create': 'supplier', 'as': 'selected'}],
                   'else': [{'create': 'campaign', 'as': 'selected'}]},
                  '$world.total = $selected.capcity'])
    errors = [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    assert len(errors) == 1
    assert 'capacity' in errors[0].fix


@pytest.mark.parametrize('condition', ['true', 'false'])
def test_alternative_property_enums_do_not_use_arbitrary_type_order(condition):
    c = contract([{'if': condition, 'then': [{'create': 'supplier', 'as': 'selected'}],
                   'else': [{'create': 'campaign', 'as': 'selected'}]},
                  '$world.total = 1 if $selected.status == north else 2'])
    c['types']['supplier']['props']['status'] = {'type': 'text', 'values': ['north'], 'default': 'north'}
    c['types']['campaign']['props']['status'] = {'type': 'text', 'values': ['south'], 'default': 'south'}
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    result = fg_env.run(c)
    assert result.ok, result.error
    assert result.outputs == {'total': 1 if condition == 'true' else 2}


def test_union_of_known_enums_still_rejects_impossible_value():
    c = contract([{'if': 'true', 'then': [{'create': 'supplier', 'as': 'selected'}],
                   'else': [{'create': 'campaign', 'as': 'selected'}]},
                  '$world.total = 1 if $selected.status == east else 2'])
    for kind, value in [('supplier', 'north'), ('campaign', 'south')]:
        c['types'][kind]['props']['status'] = {'type': 'text', 'values': [value], 'default': value}
    errors = [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    assert len(errors) == 1
    assert 'north' in errors[0].message and 'south' in errors[0].message


def test_unknown_property_alternative_does_not_borrow_a_restricted_enum():
    c = contract([{'if': 'false', 'then': [{'create': 'supplier', 'as': 'selected'}],
                   'else': [{'create': 'campaign', 'as': 'selected'}]},
                  '$world.total = 1 if $selected.status == south else 2'])
    c['types']['supplier']['props']['status'] = {'type': 'text', 'values': ['north'], 'default': 'north'}
    c['types']['campaign']['props']['status'] = 'south'
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    assert fg_env.run(c).outputs == {'total': 1}


def test_fixed_repeat_retains_type_hint_for_guaranteed_assignment():
    c = contract([{'repeat': 1, 'do': [{'create': 'supplier', 'as': 'selected'}]},
                  '$world.total = $selected.capcity'])
    errors = [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    assert len(errors) == 1
    assert 'capacity' in errors[0].fix
