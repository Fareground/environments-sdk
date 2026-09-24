"""Condition fields are expressions, including expressions without $ references."""
import json

import pytest

import fg_env


def contract(effects):
    return {'name': 'Discount eligibility', 'clock': {'rounds': 3}, 'types': {'item': {}},
            'world': {'total': 0, 'text': ''}, 'events': [{'at': 1, 'do': effects}],
            'outputs': {'total': '$world.total', 'text': '$world.text'}}


@pytest.mark.parametrize('condition,expected', [
    ('false', 7), ('0', 7), ('1 > 2', 7), ('null', 7), ('[]', 7), ("''", 7),
    ('not true', 7), ('true', 100), ('1 < 2', 100), ('2', 100),
    (False, 7), (True, 100), (0, 7), ('$world.total == 0', 100),
])
def test_if_uses_expression_truthiness(condition, expected):
    c = contract([{'if': condition, 'then': ['$world.total = 100'], 'else': ['$world.total = 7']}])
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    result = fg_env.run(c)
    assert result.ok, result.error
    assert result.outputs['total'] == expected


@pytest.mark.parametrize('condition', ["'false'", 'yes', 'deal'])
def test_text_as_a_condition_is_refused_because_it_is_always_true(condition):
    c = contract([{'if': condition, 'then': ['$world.total = 100']}])
    assert any('always true' in i.message for i in fg_env.check(c, rounds=0) if i.severity == 'error')


@pytest.mark.parametrize('condition,expected', [('false', 0), ('1 > 2', 0), ('true', 6), ('$it > 1', 5)])
def test_each_filter_uses_expression_truthiness(condition, expected):
    c = contract([{'each': [1, 2, 3], 'where': condition, 'do': ['$world.total += $it']}])
    result = fg_env.run(c)
    assert result.ok, result.error
    assert result.outputs['total'] == expected


@pytest.mark.parametrize('condition', ['false', '0', '1 > 2', False])
def test_false_repeat_condition_performs_no_iterations(condition):
    result = fg_env.run(contract([{'repeat': 3, 'while': condition, 'do': ['$world.total += 1']}]))
    assert result.ok, result.error
    assert result.outputs['total'] == 0


def test_repeat_still_stops_at_dynamic_condition_and_enforces_the_limit():
    result = fg_env.run(contract([{'repeat': 3, 'while': '$world.total < 2', 'do': ['$world.total += 1']}]))
    assert result.ok and result.outputs['total'] == 2
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(contract([{'repeat': 3, 'while': 'true', 'do': ['$world.total += 1']}]))
    exhausted = failed.value.result
    assert not exhausted.ok
    assert 'reached its limit' in exhausted.error


def test_delayed_condition_survives_snapshot_continuation():
    c = contract([{'after': 1, 'do': [{'if': '1 > 2', 'then': ['$world.total = 100'],
                                    'else': ['$world.total = 7']}]}])
    env = fg_env.load(c, seed=1)
    env.run(rounds=1)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run()
    assert result.outputs['total'] == 7
    assert result.to_dict() == restored.run().to_dict()


def test_data_fields_keep_literal_text_semantics():
    # Literal strings remain data outside explicitly expression-valued conditions.
    c = contract([{'emit': 'label', 'data': {'text': 'false'}},
                  "$world.text = 'false'", {'if': 'false', 'then': ["$world.text = 'wrong'"]}])
    result = fg_env.run(c)
    assert result.ok, result.error
    assert result.outputs['text'] == 'false'
    assert [e['data']['text'] for e in result.events if e['kind'] == 'label'] == ['false']


@pytest.mark.parametrize('seed', [1, 17, 921])
def test_existing_reference_conditions_keep_draw_order_and_complete_results(seed, monkeypatch):
    from fg_env.effects.runner import EffectRunner
    c = contract([{'each': [1, 2, 3, 4], 'where': '$chance(0.8)', 'do': [
        {'if': '$chance(0.5)', 'then': ['$world.total += $it'], 'else': ['$world.total -= $it']}]}])
    corrected = fg_env.run(c, seed=seed).to_dict()
    monkeypatch.setattr(EffectRunner, '_condition', lambda self, value, variables: bool(self._eval(value, variables)))
    assert corrected == fg_env.run(c, seed=seed).to_dict()
