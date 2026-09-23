"""Values that used to come out silently wrong: each one is now correct or a clear error."""
import pytest

import fg_env


def contract(*do, world=None, props=None, **sections):
    c = {'name': 'Ledger', 'clock': {'rounds': 1},
         'world': {'w': 0, **(world or {})},
         'types': {'p': {'agent': True, 'props': {
             'cash': 10, 'n': {'type': 'int', 'default': 3},
             'status': {'type': 'enum', 'values': ['open', 'closed'], 'default': 'open'}, **(props or {})}}},
         'entities': {'a': {'type': 'p'}},
         'outputs': {'w': '$world.w'}}
    if do:
        c['events'] = [{'do': list(do)}]
    c.update(sections)
    return c


def errors(c):
    return [i for i in fg_env.check(c, rounds=1) if i.severity == 'error']


def output(expr, *do, **world):
    c = contract(*do, world=world)
    c['outputs'] = {'v': expr}
    result = fg_env.run(c, seed=1)
    assert result.ok, (result.error, result.output_issues)
    return result.outputs['v']


# -- map keys -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('expr,expected', [
    ('$get({1: 3, 2: 4}, 2, -1)', 4),
    ('2 in {1: 3, 2: 4}', True),
    ('{1: 3, 2: 4}[2]', 4),
    ("$get({1: 3, 2: 4}, '2', -1)", 4),
    ('$get($tally([1, 2, 2]), 2, 0)', 2),
    ('2 in $tally([1, 2, 2])', True),
    ('$tally([1, 2, 2])[2]', 2),
    ('$get($dict([1, 2], $it, $it * 10), 2, 0)', 20),
    ('$keys({1: 3})', ['1']),
    ('$mode([1, 2, 2])', 2),
    ('$without({1: 3, 2: 4}, 1)', {'2': 4}),
])
def test_map_keys_are_text_and_a_number_finds_its_key_everywhere(expr, expected):
    assert output(expr) == expected


def test_a_map_written_by_number_key_is_read_back_by_it():
    assert output('$world.m[1]', '$world.m[1] = 5', m={'type': 'map', 'default': {}}) == 5


# -- null into typed properties -----------------------------------------------------------------------------------

@pytest.mark.parametrize('rule', ['$entity(a).n = $avg([])', '$entity(a).status = $max([])',
                                  '$entity(a).cash = $min([])', '$world.w = $first([])'])
def test_null_is_refused_by_a_property_that_starts_with_a_value(rule):
    found = errors(contract(rule))
    assert found and 'null' in found[0].message and '"default": null' in found[0].fix + found[0].message


def test_a_property_declared_empty_may_be_emptied_again():
    c = contract('$world.best = 3', '$world.best = $max([])', world={'best': {'type': 'int'}})
    assert not errors(c)
    result = fg_env.run(c, seed=1)
    assert result.ok and fg_env.load(c, seed=1).props['best'] is None


# -- bare words in conditions -------------------------------------------------------------------------------------

@pytest.mark.parametrize('patch,path', [
    (lambda c: c.update(end=[{'when': 'deal'}]), 'end[0].when'),
    (lambda c: c['actions'].__setitem__('go', {'by': 'p', 'when': 'ready'}), 'actions.go.when[0]'),
    (lambda c: c.update(triggers=[{'when': "'yes'", 'do': ['$world.w = 1']}]), 'triggers[0].when'),
    (lambda c: c.update(invariants=['positive']), 'invariants[0]'),
    (lambda c: c['records'].__setitem__('chat', {'visible': 'private'}), 'records.chat.visible'),
    (lambda c: c['records'].__setitem__('chat', {'visible': 'none'}), 'records.chat.visible'),
])
def test_a_bare_word_condition_is_refused(patch, path):
    c = contract(world={'deal': False})
    c.update(actions={'noop': {'by': 'p'}}, records={})
    patch(c)
    found = [i for i in errors(c) if i.path == path]
    assert found, errors(c)
    assert 'always true' in found[0].message


def test_a_bare_world_name_suggests_the_world_property():
    c = contract(world={'deal': False}, end=[{'when': 'deal'}])
    found = [i for i in errors(c) if i.path == 'end[0].when']
    assert found and '$world.deal' in found[0].fix


@pytest.mark.parametrize('visible', ['all', '$it.author == $viewer.id'])
def test_documented_visibility_still_passes(visible):
    c = contract(records={'chat': {'visible': visible}})
    assert not errors(c)


# -- outputs that raise -------------------------------------------------------------------------------------------

def test_an_output_still_failing_when_the_smoke_run_ends_is_an_error():
    c = contract()
    c['outputs'] = {'ratio': '10 / $world.w', 'empty': '$max([])'}
    found = errors(c)
    assert [i.path for i in found] == ['outputs.ratio'] and 'division by zero' in found[0].message


def test_a_run_whose_output_raised_is_degraded():
    c = contract()
    c['outputs'] = {'ratio': '10 / $world.w', 'empty': '$max([])'}
    result = fg_env.run(c, seed=1)
    assert result.status == 'completed' and not result.ok
    assert 'output_failed' in result.degraded and 'DEGRADED' in result.summary()
    empty = contract()
    empty['outputs'] = {'empty': '$max([])'}
    assert fg_env.run(empty, seed=1).degraded == []


# -- powers -------------------------------------------------------------------------------------------------------

def test_a_power_with_no_real_result_is_an_error():
    with pytest.raises(fg_env.RunError, match='no real result'):
        fg_env.run(contract('$world.w = (-8) ** 0.5'), seed=1)


# -- list -= ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('rule,expected', [
    ('$world.q -= 2', [1, 2, 3]),
    ('$world.q -= [2, 2]', [1, 3]),
    ('$world.q -= 9', [1, 2, 2, 3]),
    ('$world.bag -= {sku: a}', [{'sku': 'b'}, {'sku': 'a'}]),
])
def test_list_minus_removes_one_copy_per_item(rule, expected):
    c = contract(rule, world={'q': {'type': 'list', 'default': [1, 2, 2, 3]},
                              'bag': {'type': 'list', 'default': [{'sku': 'a'}, {'sku': 'b'}, {'sku': 'a'}]}})
    env = fg_env.load(c, seed=1)
    assert env.run('idle').ok
    assert env.props['bag' if 'bag' in rule else 'q'] == expected


# -- event each ---------------------------------------------------------------------------------------------------

def test_an_event_each_over_a_number_is_a_run_error_at_the_event():
    c = contract(world={'n': 3})
    c['events'] = [{'each': '$world.n', 'do': ['$world.w += 1']}]
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(c, seed=1)
    assert 'events[0]' in str(failed.value) and 'type name or a list, got 3' in str(failed.value)
    assert any('type name or a list' in i.message for i in errors(c))


def test_a_policy_each_over_a_number_is_a_run_error():
    c = contract(world={'n': 3})
    c['actions'] = {'noop': {'by': 'p'}}
    c['policies'] = {'bot': {'rules': [{'each': '$world.n', 'do': 'noop'}]}}
    with pytest.raises(fg_env.RunError, match='type name or a list, got 3'):
        fg_env.run(c, {'a': 'bot'}, seed=1)


# -- small typos --------------------------------------------------------------------------------------------------

def test_an_enum_typo_through_entity_is_flagged():
    found = errors(contract('$world.w = 1 if $entity(a).status == opne else 2'))
    assert found and "did you mean 'open'?" in found[0].fix


def test_a_numeric_text_default_warns():
    c = contract(props={'profit': '0'})
    warned = [i for i in fg_env.check(c, rounds=1) if i.path == 'types.p.props.profit']
    assert warned and 'write 0' in warned[0].fix
