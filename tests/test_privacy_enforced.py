"""The engine, not a lint, keeps private properties private.

While the SDK works out something for one agent — its views, tool choices and bounds, outcome text, policy — reading
another entity's private property is an error, however the read is spelled (a def, a sort key, a view with a `where`,
a bound). Game logic reads the true state. A requirement (`when`) that reads hidden state does not hide the tool: it
is listed and a call is refused when the requirement fails.
"""
import copy
import json

import pytest

import fg_env


def contract():
    return {
        "name": "Secrets", "clock": {"rounds": 1},
        "types": {"player": {"agent": True, "inspect": True, "props": {
            "cash": {"default": 10, "min": 0, "private": True},
            "role": {"type": "enum", "values": ["good", "traitor"], "default": "good", "private": True}}}},
        "entities": {"ann": {"type": "player"}, "bob": {"type": "player", "props": {"cash": 3, "role": "traitor"}},
                     "cat": {"type": "player"}},
        "defs": {"bad": {"args": ["p"], "expr": "$p.role == traitor"}},
        "stages": [{"name": "play", "max_actions": 50, "max_calls": 50}],
        "actions": {
            "fine": {"by": "player", "params": {"target": {"type": "entity", "of": "player"},
                                                "amount": {"type": "int", "min": 1, "max": 100}},
                     "do": "$params.target.cash -= $params.amount"},
            "hunt": {"by": "player", "when": [{"expr": "$count(player, $it.role == traitor && $it.id != $actor.id) > 0",
                                               "why": "there is nobody to hunt"}], "do": []},
        },
        "outputs": {"traitors": "$count(player, $it.role == traitor)", "cash": "$sum(player, $it.cash)"},
    }


def play(c, calls=()):
    """ann's tools and update, and the text of each of ``calls`` she makes."""
    seen = {}

    def participant(wake):
        if wake.entity_id == "ann":
            seen["tools"] = {t.name: t.input_schema for t in wake.tools}
            seen["update"] = wake.update
            seen["calls"] = [wake.call(name, args).text for name, args in calls]
        wake.end()

    result = fg_env.load(c, seed=1).run(participant)
    return result, seen


def with_(**parts):
    c = contract()
    for section, items in parts.items():
        c.setdefault(section, {}).update(items)
    return c


def test_a_view_cannot_show_or_sort_by_anothers_private_property_whatever_its_where():
    for view in ({"of": "player", "where": "$it.cash >= 0", "show": "{$it.name}: {$it.cash}"},
                 {"of": "player", "sort": "$it.cash", "show": "{$it.name}", "where": "true"},
                 {"show": "top: {$best(player, $it.cash, 'random').name}"}):
        result, seen = play(with_(views={"leak": view}))
        assert result.status == "failed" and "bob's cash is private" in result.error, view
        assert "update" not in seen


def test_an_agent_reads_its_own_private_properties_and_logic_reads_everyones():
    view = {"of": "player", "where": "$it.alive", "show": "{$it.name}{$': ' + $text($it.cash) if $it.id == $actor.id else ''}"}
    result, seen = play(with_(views={"mine": view, "role": {"show": "You are {$actor.role}."}}))
    assert result.status == "completed", result.error
    assert "- ann: 10" in seen["update"] and "- bob\n" in seen["update"] and "You are good." in seen["update"]
    assert result.outputs == {"traitors": 1, "cash": 23}


def test_a_choice_filtered_through_a_def_reading_private_state_is_refused():
    c = with_(actions={"accuse": {"by": "player", "do": [],
                                  "params": {"target": {"type": "entity", "of": "player", "where": "$bad($it)"}}}})
    result, seen = play(c)
    assert result.status == "failed" and "bob's role is private" in result.error
    assert "actions.accuse.params.target.where" in result.error
    assert any(i.path == "actions.accuse.params.target.where" and i.severity == "warning"
               and "role (in $bad)" in i.message for i in fg_env.check(c))


def test_a_bound_read_from_a_chosen_entitys_private_property_refuses_without_revealing_it():
    c = with_(actions={"gift": {"by": "player", "do": [], "params": {
        "target": {"type": "entity", "of": "player"},
        "amount": {"type": "int", "min": 1, "max": "$params.target.cash"}}}})
    result, seen = play(c, [("gift", {"target": "bob", "amount": 50}), ("gift", {"target": "ann", "amount": 50})])
    assert result.status == "completed", result.error
    assert "maximum" not in json.dumps(seen["tools"]["gift"])
    refused, own = seen["calls"]
    assert "3" not in refused and "could not be worked out" in refused
    assert "at most 10" in own  # her own cash she may know
    assert any(i.path == "actions.gift.params.amount.max" and i.severity == "warning" for i in fg_env.check(c))


def test_outcome_text_reveals_only_what_game_logic_worked_out():
    peek = {"by": "player", "params": {"target": {"type": "entity", "of": "player"}}}
    direct = with_(actions={"peek": {**peek, "do": [], "outcome": "{$params.target.name} is {$params.target.role}."}})
    worked_out = with_(actions={"peek": {**peek, "do": ["$seen = $params.target.role"],
                                         "outcome": "{$params.target.name} is {$seen}."}})
    _, seen = play(direct, [("peek", {"target": "bob"})])
    assert "traitor" not in seen["calls"][0]
    _, seen = play(worked_out, [("peek", {"target": "bob"})])
    assert seen["calls"][0].startswith("bob is traitor.")


@pytest.mark.parametrize("traitor", [True, False])
def test_a_requirement_over_hidden_state_lists_the_tool_either_way_and_decides_at_the_call(traitor):
    c = contract()
    if not traitor:
        c["entities"]["bob"]["props"]["role"] = "good"
    _, seen = play(c, [("hunt", {})])
    assert "hunt" in seen["tools"]
    assert ("nobody to hunt" in seen["calls"][0]) is not traitor


def test_a_requirement_over_the_actors_own_private_state_still_hides_the_tool():
    c = with_(actions={"sabotage": {"by": "player", "when": "$actor.role == traitor", "do": []}})
    _, seen = play(c)
    assert "sabotage" not in seen["tools"]


def test_an_out_of_bounds_refusal_does_not_show_a_private_value():
    _, seen = play(contract(), [("fine", {"target": "bob", "amount": 10})])
    assert "bob's cash cannot go below 0." in seen["calls"][0] and "-7" not in seen["calls"][0]


def test_the_checker_warns_when_a_filtered_view_reads_private_state():
    c = with_(views={"leak": {"of": "player", "where": "$it.cash >= 0", "show": "{$it.name}: {$it.cash}"},
                     "ranked": {"of": "player", "sort": "$it.cash", "show": "{$it.name}"}})
    issues = {i.path: i for i in fg_env.check(copy.deepcopy(c))}
    assert issues["views.leak.show"].severity == "warning"
    assert issues["views.ranked.show"].severity == "error"


def test_a_stepped_game_and_its_clones_enforce_it_too():
    from fg_env.game import game

    c = contract()
    c["views"] = {"leak": {"show": "Bob holds {$entity('bob').cash}."}}
    state = game(c).new_initial_state()
    for seat in (state, state.clone()):
        with pytest.raises(fg_env.RunError, match="bob's cash is private"):
            seat.observation_string(seat.current_player())
