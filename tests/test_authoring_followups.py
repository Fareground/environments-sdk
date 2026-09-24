"""Authoring surprises the outsider test hit: literal placeholders, empty maps, ballot counting, receipts, loops."""
import json
from pathlib import Path

import fg_env
from fg_env.expr import compile_expr

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"

HINTS = {
    "name": "Hints",
    "clock": {"rounds": 2},
    "world": {"last": "", "best": 0},
    "types": {"player": {"agent": True, "props": {"hints": [], "pick": 0}}},
    "entities": {"ann": {"type": "player", "name": "Ann"}, "bob": {"type": "player", "name": "Bob"}},
    "actions": {"guess": {"by": "player", "params": {"n": {"type": "int", "min": 1, "max": 9}},
                          "do": "$actor.pick = $params.n"}},
    "outputs": {"last": "$world.last"},
}


def _contract(**changes):
    contract = json.loads(json.dumps(HINTS))
    contract.update(changes)
    return contract


def test_an_assignment_that_stores_a_placeholder_literally_is_warned_about():
    contract = _contract()
    contract["actions"]["guess"]["do"] = "$actor.hints += ['{$params.n}: higher']"
    warnings = [i for i in fg_env.check(contract, rounds=0) if i.path == "actions.guess.do[0]"]
    assert [i.message for i in warnings] == [
        "stores `{$...}` literally: placeholders fill in only in templates (outcome, say, show, text)"]


def test_reading_a_field_of_an_empty_map_says_the_map_is_empty():
    env = fg_env.load(_contract(world={"last": "", "best": 0, "result": {"type": "map", "default": {}}}), seed=1)
    try:
        compile_expr("$world.result.winner")(env.world.scope())
    except Exception as exc:  # the message is the point of the test
        assert "no field 'winner': the map is empty (nothing has set it yet)" in str(exc)
    else:
        raise AssertionError("reading a missing field must fail")


def test_a_loop_that_keeps_only_the_last_items_value_is_reported():
    contract = _contract(events=[{"phase": "end", "do": [{"each": "player", "do": ["$world.last = $it.name"]}]}])
    found = fg_env.run(contract, seed=1).diagnostics
    assert [(d["code"], d["path"]) for d in found] == [("loop_overwrites", "events[0].do[0]")]
    assert "`$world.last = $it.name` ran for several items with different values" in found[0]["message"]


def test_a_loop_that_reads_its_target_or_writes_each_item_is_not_reported():
    best = {"each": "player", "do": [{"if": "$it.pick > $world.best", "then": ["$world.best = $it.pick"]}]}
    own = {"each": "player", "do": ["$it.pick = 0"]}
    contract = _contract(events=[{"phase": "end", "do": [best, own]}])
    assert fg_env.run(contract, seed=1).diagnostics == []


def test_a_loop_that_adds_to_a_map_entry_it_reads_is_not_reported():
    tally = {"name": "Tally", "clock": {"rounds": 2},
             "types": {"p": {"agent": True, "props": {"tally": {"type": "map", "default": {}}}}},
             "population": [{"type": "p", "count": 3}], "actions": {"noop": {"by": "p", "do": []}},
             "events": [{"phase": "end", "do": [{"each": "$range(4)", "as": "k", "do": [
                 "$who = $choice($map(p, $it))", "$who.tally[x] = $get($who.tally, x, 0) + 1"]}]}],
             "outputs": {"total": "$sum(p, $get($it.tally, x, 0))"}}
    result = fg_env.run(tally, seed=1)
    assert result.outputs["total"] == 8 and result.diagnostics == []


BALLOT = {
    "name": "Exile",
    "clock": {"rounds": 1},
    "types": {"player": {"agent": True}},
    "entities": {"p1": {"type": "player", "name": "Alma"}, "p2": {"type": "player", "name": "Bruno"},
                 "p3": {"type": "player", "name": "Chloe"}},
    "mechanisms": {
        "roles": {"kind": "groups", "mode": "roles", "who": "player", "deck": {"villager": "rest"}},
        "exile": {"kind": "decision", "mode": "ballot", "who": "player", "options": ["p1", "p2", "p3"],
                  "method": "plurality", "abstain": False, "question": "Whom do you exile?"},
    },
    "events": [{"phase": "start", "at": 1, "do": [{"groups": "roles", "action": "eliminate", "who": "$entity(p3)"}]}],
    "outputs": {"turnout": "$world.exile_result.turnout"},
}


def test_a_ballot_names_the_winner_and_counts_turnout_among_players_still_in_the_game():
    def vote(wake):
        if wake.me["living"] and any(t.name == "exile_vote" for t in wake.tools):
            wake.call("exile_vote", {"choice": "p2"})
        wake.end()

    result = fg_env.run(BALLOT, vote, seed=1)
    announced = [e.get("text") for e in result.events if e["kind"] == "exile"]
    assert announced == ["Whom do you exile?: Bruno wins (Bruno 2, Alma 0, Chloe 0)."]
    assert result.outputs["turnout"] == 1.0


def test_the_ballot_guide_says_it_counts_after_the_stage_on_exit():
    assert "after that stage's own on_exit effects" in fg_env.guide("decision.ballot")


def test_a_sealed_bid_receipt_names_the_item_without_a_count_for_one_unit():
    contract = {"name": "Sale", "clock": {"rounds": 1},
                "types": {"bidder": {"agent": True, "props": {"cash": 100}}}, "entities": {"b1": {"type": "bidder"}},
                "mechanisms": {"sale": {"kind": "market", "mode": "auction", "format": "first_price", "who": "bidder",
                                        "item": "a painting"}}}

    def bid(wake):
        wake.call("sale_bid", {"price": 10, "qty": 1})
        wake.end()

    texts = [e.get("text") for e in fg_env.run(contract, bid, seed=1).events]
    assert "Your sealed bid of 10 for a painting is in." in texts


def test_diplomacy_offers_disband_only_when_an_army_can_be_disbanded():
    env = fg_env.load(EXAMPLES / "diplomacy.json", seed=1)
    preview = env.preview(next(e["id"] for e in env.entities("nation")))
    assert "disband" not in [tool["name"] for tool in preview["tools"]]



def test_input_options_and_role_brief_errors_preserve_the_authors_intent():
    contract = _contract(inputs={'priority': {'type': 'text', 'display': 'select', 'options': ['due', 'fee']}},
                         brief={'player': 'Prioritize urgent work.'})
    issues = {issue.path: issue for issue in fg_env.check(contract, rounds=0)}
    assert 'type="enum", values=[...], display="select"' in issues['inputs.priority.options'].fix
    assert 'brief.roles.player' in issues['brief.player'].fix


def test_bool_type_name_as_default_has_an_actionable_load_error():
    import pytest
    contract = {'name': 'Bool shorthand', 'types': {'item': {'props': {'done': 'bool'}}},
                'entities': {'one': {'type': 'item', 'props': {'done': False}}}}
    with pytest.raises(fg_env.RunError, match='type declaration'):
        fg_env.load(contract)
    # Preserve literal text compatibility; only the diagnostic changes.
    contract['entities']['one']['props']['done'] = 'bool'
    assert fg_env.load(contract).entities('item')[0]['props']['done'] == 'bool'



def test_dynamic_goal_in_static_action_description_points_to_participant_brief():
    contract = _contract()
    contract['actions']['guess']['description'] = 'Pursue {$actor.pick}.'
    issues = fg_env.check(contract, rounds=0)
    warning = next(issue for issue in issues if issue.path == 'actions.guess.description')
    assert warning.severity == 'warning'
    assert 'brief.roles' in warning.fix and 'env.preview' in warning.fix
    contract['actions']['guess']['description'] = 'Make a guess.'
    contract['brief'] = {'roles': {'player': 'Pursue {$actor.pick}.'}}
    assert not [issue for issue in fg_env.check(contract, rounds=0) if issue.path.endswith('.description')]
    assert 'Pursue 0.' in fg_env.load(contract).preview('ann')['brief']


def test_event_round_diagnostic_points_to_executable_schedule():
    for misspelling in ('round', 'rounds'):
        contract = {'name': 'Scheduled update', 'clock': {'rounds': 3}, 'types': {},
                    'world': {'count': 0}, 'outputs': {'count': '$world.count'},
                    'events': [{misspelling: 2, 'do': '$world.count += 1'}]}
        issue = next(i for i in fg_env.check(contract, rounds=0) if i.path == f'events[0].{misspelling}')
        assert '"$round == 2"' in issue.fix
        contract['events'][0]['when'] = f"$round == {contract['events'][0].pop(misspelling)}"
        result = fg_env.run(contract, lambda wake: wake.end())
        assert result.ok and result.outputs['count'] == 1
