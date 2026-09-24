"""Traps found building real scenarios: emptied stock, zero repeats, counted stage settings, reserved-word ids,
typo'd settings, half-way rounding and stated word counts."""
import pytest

import fg_env
from fg_env.actions.tool_text import text_limit
from fg_env.errors import ContractError, RunError


def _errors(contract):
    return [i for i in fg_env.check(contract) if i.severity == "error"]


def _world(**sections):
    contract = {"name": "Trap", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
                "entities": {"a": {"type": "t"}}, "outputs": {"v": "$entity(a).v"}}
    contract.update(sections)
    return contract


def _players(**sections):
    contract = {"name": "Trap", "clock": {"rounds": 1}, "inputs": {"n": {"type": "int", "default": 3}},
                "types": {"p": {"agent": True, "props": {"v": 0}}}, "entities": {"a": {"type": "p"}},
                "actions": {"inc": {"by": "p", "do": "$actor.v += 1"}}, "outputs": {"v": "$entity(a).v"}}
    contract.update(sections)
    return contract


# -- declared goods read 0 once used up ------------------------------------------------------------------------


def _shop(uses):
    return _world(
        types={"shop": {"props": {}}}, entities={"s": {"type": "shop"}},
        mechanisms={"stock": {"kind": "economy", "mode": "inventory", "who": "shop", "actions": [],
                              "items": {"beer": {}, "wine": {}}, "start": {"beer": 2}}},
        events=[{"each": "shop", "do": [{"economy": "stock", "action": "use", "item": "beer", "from": "$it",
                                         "qty": uses, "sink": "sold"}]}],
        outputs={"beer": "$entity(s).stock.beer", "wine": "$entity(s).stock.wine", "held": "$items_text($entity(s))",
                 "owned": {"type": "list", "expr": "$owned_items($entity(s))"}})


def test_declared_goods_read_zero_when_never_started_or_used_up():
    result = fg_env.run(_shop(2), seed=1)
    assert result.outputs == {"beer": 0, "wine": 0, "held": "nothing", "owned": []}


def test_goods_left_over_still_read_as_a_count():
    result = fg_env.run(_shop(1), seed=1)
    assert result.outputs == {"beer": 1, "wine": 0, "held": "1 beer", "owned": ["beer"]}


def test_an_entity_that_starts_with_some_goods_still_reads_the_others_as_zero():
    """Its own map replaces the mechanism's `start` (so `t` has no beer) and lists every other item as 0."""
    contract = _shop(1)
    contract["entities"] = {"s": {"type": "shop", "props": {"stock": {"beer": 3}}}}
    contract["population"] = [{"type": "shop", "count": 1, "id": "t", "props": {"stock": {"wine": 1}}}]
    contract["outputs"]["t"] = {"type": "map", "expr": "$entity(t).stock"}
    contract["events"][0]["where"] = "$has($it, beer)"  # t has none to use: world logic may not be refused
    result = fg_env.run(contract, seed=1)
    assert result.outputs["beer"] == 2 and result.outputs["wine"] == 0
    assert result.outputs["t"] == {"beer": 0, "wine": 1}


# -- repeat 0 runs nothing -------------------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [0, "$inputs.zero"])
def test_repeat_zero_times_runs_nothing(limit):
    contract = _world(inputs={"zero": {"type": "int", "default": 0}},
                      events=[{"do": [{"repeat": limit, "do": ["$entity(a).v += 1"]}]}])
    assert _errors(contract) == []
    assert fg_env.run(contract, seed=1).outputs["v"] == 0


def test_a_negative_repeat_is_refused_with_its_range():
    [issue] = _errors(_world(events=[{"do": [{"repeat": -1, "do": ["$entity(a).v += 1"]}]}]))
    assert issue.path == "events[0].do[0].repeat" and "from 0" in issue.message and "0 to run nothing" in issue.fix


def test_a_repeat_count_that_comes_out_negative_fails_the_run_with_its_range():
    contract = _world(inputs={"zero": {"type": "int", "default": 0}},
                      events=[{"do": [{"repeat": "$inputs.zero - 1", "do": ["$entity(a).v += 1"]}]}])
    with pytest.raises(RunError, match="from 0 to"):
        fg_env.run(contract, seed=1)


# -- max_actions and max_calls take expressions over $inputs, like passes ---------------------------------------


def test_max_actions_may_be_an_expression_over_inputs():
    contract = _players(stages=[{"name": "s", "max_actions": "$inputs.n", "max_calls": "$inputs.n + 2"}])
    assert _errors(contract) == []

    def act(wake):
        while wake.call("inc", {}).ok:
            pass

    assert fg_env.run(contract, {"a": act}, seed=1).outputs["v"] == 3
    assert fg_env.run(contract, {"a": act}, seed=1, inputs={"n": 2}).outputs["v"] == 2


@pytest.mark.parametrize("value, message", [("$inputs.n - 3", "whole number ≥ 1"), ("$actor.v", "actor")])
def test_a_counted_turn_setting_that_is_not_a_count_fails_at_load(value, message):
    contract = _players(stages=[{"name": "s", "max_actions": value}])
    with pytest.raises((ContractError, fg_env.RunError), match=message):
        fg_env.load(contract)


@pytest.mark.parametrize("setting", ["max_actions", "max_calls", "passes"])
def test_zero_turn_settings_are_refused_not_quietly_raised(setting):
    [issue] = _errors(_players(stages=[{"name": "s", setting: 0}]))
    assert issue.path == f"stages[0].{setting}" and "at least 1" in issue.message


# -- ids that are Python keywords work in expressions ----------------------------------------------------------


def test_types_entities_and_props_named_like_python_keywords_work_in_expressions():
    contract = {"name": "Keywords", "clock": {"rounds": 1},
                "types": {"class": {"props": {"from": 1, "is": 2}}, "def": {"props": {}}},
                "entities": {"for": {"type": "class"}, "return": {"type": "def"}},
                "outputs": {"n": "$count(class)", "from": "$entity(for).from + $entity(for).is",
                            "same": "$entity(return).type == def", "words": {"type": "list", "expr": "[lambda, with]"}}}
    assert _errors(contract) == []
    assert fg_env.run(contract, seed=1).outputs == {"n": 1, "from": 3, "same": True, "words": ["lambda", "with"]}


@pytest.mark.parametrize("word", ["in", "and", "not", "if", "true", "null", "None"])
def test_expression_words_cannot_name_types_or_entities(word):
    issues = _errors(_world(types={word: {"props": {}}}, entities={word: {"type": word}}))
    assert {i.path for i in issues} == {f"types.{word}", f"entities.{word}"}
    assert all("expression" in i.message and i.fix for i in issues)


# -- typo'd settings are refused -------------------------------------------------------------------------------


def test_a_misspelled_turn_order_is_refused_with_a_suggestion():
    [issue] = _errors(_players(stages=[{"name": "s", "order": "randon"}]))
    assert issue.path == "stages[0].order" and issue.fix == "did you mean 'random'?"


def test_a_misspelled_event_order_is_refused_with_a_suggestion():
    [issue] = _errors(_world(events=[{"each": "t", "order": "randm", "do": ["$it.v += 1"]}]))
    assert issue.path == "events[0].do[0].order"


def test_orders_by_expression_still_work():
    contract = _players(stages=[{"name": "s", "order": "$it.v"}])
    assert _errors(contract) == []


@pytest.mark.parametrize("field, value, hint", [("turns", "simultanous", "simultaneous"), ("quiet", "skp", "skip")])
def test_misspelled_stage_words_suggest_the_right_one(field, value, hint):
    [issue] = _errors(_players(stages=[{"name": "s", field: value}]))
    assert issue.fix == f"did you mean '{hint}'?"


def test_a_misspelled_event_phase_suggests_the_right_one():
    [issue] = _errors(_world(events=[{"phase": "ned", "do": ["$entity(a).v += 1"]}]))
    assert issue.path == "events[0].on" and "did you mean 'round.end'?" in issue.message


def test_misspelled_checks_fail_at_load():
    with pytest.raises(ContractError, match="did you mean 'round'"):
        fg_env.load(_world(invariants=[{"expr": "true", "check": "rounds"}]))
    with pytest.raises(ContractError, match="did you mean 'action'"):
        fg_env.load(_world(end=[{"when": "false", "check": "actoin"}]))


# -- $round rounds halves away from zero -----------------------------------------------------------------------


def test_round_takes_halves_away_from_zero_as_money_is_rounded():
    outputs = {"half": "$round(2.5)", "cents": "$round(0.125, 2)", "negative": "$round(-2.5)",
               "written": "$round(1.005, 2)", "hundreds": "$round(1250, -2)", "whole": "$round(7)",
               "tiny": "$round(0.1, 30)", "huge": "$round(1e300)"}
    assert fg_env.run(_world(outputs=outputs), seed=1).outputs == {
        "half": 3, "cents": 0.13, "negative": -3, "written": 1.01, "hundreds": 1300, "whole": 7, "tiny": 0.1,
        "huge": int(1e300)}


# -- stated word counts follow one ratio ------------------------------------------------------------------------


@pytest.mark.parametrize("chars, words", [(1200, 150), (500, 60), (400, 50), (300, 35), (60, 7), (4, 1)])
def test_word_counts_in_text_limits_follow_one_ratio(chars, words):
    assert text_limit(chars) == f"Up to {chars} characters (about {words} word{'s' if words != 1 else ''})."
