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
    (False, 7), (True, 100), ('$world.total == 0', 100),
])
def test_if_uses_expression_truthiness(condition, expected):
    c = contract([{'if': condition, 'then': ['$world.total = 100'], 'else': ['$world.total = 7']}])
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == 'error']
    result = fg_env.run(c)
    assert result.ok, result.error
    assert result.outputs['total'] == expected


@pytest.mark.parametrize('condition', [0, [True], {'expr': 'true'}])
def test_a_condition_is_text_or_true_or_false(condition):
    """A number, list or object as a condition is refused rather than taken for its truthiness (audit 12 H2)."""
    c = contract([{'if': condition, 'then': ['$world.total = 100']}])
    assert any(i.path == 'events[0].do[0].if' for i in fg_env.check(c, rounds=0) if i.severity == 'error')


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


# -- every field that holds a condition refuses bare text ----------------------------------------------------------

def _fishery():
    return {"name": "Lake", "clock": {"rounds": 2}, "world": {"fish": 10},
            "types": {"fisher": {"agent": True, "props": {"caught": 0}}},
            "entities": {"fisher": {"type": "fisher", "count": 2}},
            "records": {"chat": {"fields": {"text": "text"}}},
            "actions": {"fish": {"by": "fisher", "params": {"who": {"type": "entity", "of": "fisher"}},
                                 "do": ["$actor.caught += 1"]}},
            "stages": [{"name": "fishing"}],
            "views": {"lake": {"for": "fisher", "show": "Fish: {$world.fish}"}},
            "outputs": {"caught": "$sum(fisher, $it.caught)"}}


def _put(path, value):
    def edit(contract):
        *parents, last = path
        target = contract
        for key in parents:
            target = target[key]
        target[last] = value
    return edit


CONDITION_FIELDS = {
    "actions.*.when": _put(("actions", "fish", "when"), "fisher_1"),
    "actions.*.params.*.where": _put(("actions", "fish", "params", "who", "where"), "fisher_1"),
    "actions.*.terminal": _put(("actions", "fish", "terminal"), "fisher_1"),
    "stages.*.when": _put(("stages", 0, "when"), "fisher_1"),
    "stages.*.who": _put(("stages", 0, "who"), "fisher_1"),
    "stages.*.until": _put(("stages", 0, "until"), "fisher_1"),
    "stages.*.valid": _put(("stages", 0, "valid"), [{"expr": "fisher_1", "why": "no"}]),
    "events.*.when": _put(("events",), [{"on": "round.end", "when": "fisher_1", "do": "$world.fish += 1"}]),
    "effects if": _put(("actions", "fish", "do"), [{"if": "fisher_1", "then": ["$actor.caught += 1"]}]),
    "effects each.where": _put(("actions", "fish", "do"),
                               [{"each": "fisher", "where": "fisher_1", "do": ["$it.caught += 1"]}]),
    "effects repeat.while": _put(("actions", "fish", "do"),
                                 [{"repeat": 2, "while": "fisher_1", "do": ["$actor.caught += 1"]}]),
    "views.*.when": _put(("views", "lake", "when"), "fisher_1"),
    "views.*.where": _put(("views", "lake"), {"for": "fisher", "of": "fisher", "where": "fisher_1", "show": "{name}"}),
    "records.*.visible": _put(("records", "chat", "visible"), "fisher_1"),
    "end.*.when": _put(("end",), [{"when": "fisher_1"}]),
    "invariants": _put(("invariants",), [{"expr": "fisher_1"}]),
}


@pytest.mark.parametrize("field", list(CONDITION_FIELDS))
def test_every_field_that_holds_a_condition_refuses_bare_text_as_always_true(field):
    """A bare word where a condition goes is that text itself, true for everything: `check` refuses it wherever a
    condition is written, so no field can silently hold every time."""
    contract = _fishery()
    CONDITION_FIELDS[field](contract)
    errors = [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert any("always true" in i.message for i in errors), (field, [str(i) for i in errors])


def test_the_checker_checks_every_condition_field_as_a_condition():
    """The static half of the rule: a field whose name says it holds a condition is never checked as a plain
    expression, which would let bare text through (as a stage's `who` once did)."""
    import ast
    import pathlib

    conditional = ("when", "who", "where", "until", "if", "valid", "visible", "terminal", "while")
    source = pathlib.Path(fg_env.__file__).parent / "checks"
    wrong = []
    for file in source.glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "expr" \
                    and len(node.args) >= 2 and isinstance(node.args[1], ast.JoinedStr):
                tail = node.args[1].values[-1]
                if isinstance(tail, ast.Constant) and str(tail.value).rsplit(".", 1)[-1] in conditional:
                    wrong.append(f"{file.name}:{node.lineno}")
    assert wrong == []
