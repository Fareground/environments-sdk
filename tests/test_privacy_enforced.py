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
from fg_env.checks import parse_contract
from fg_env.runtime.env import Env


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
    # Spelled so the checker cannot see whose property it reads (it reports `$params.target.cash` as an error).
    c = with_(actions={"gift": {"by": "player", "do": [], "params": {
        "target": {"type": "entity", "of": "player"},
        "amount": {"type": "int", "min": 1, "max": "$get($params.target, 'cash')"}}}})
    result, seen = play(c, [("gift", {"target": "bob", "amount": 50}), ("gift", {"target": "ann", "amount": 50})])
    assert result.status == "completed", result.error
    assert "maximum" not in json.dumps(seen["tools"]["gift"])
    refused, own = seen["calls"]
    assert "3" not in refused and "could not be worked out" in refused
    assert "at most 10" in own  # her own cash she may know


def test_outcome_text_reveals_only_what_game_logic_worked_out():
    peek = {"by": "player", "params": {"target": {"type": "entity", "of": "player"}}}
    direct = with_(actions={"peek": {**peek, "do": [], "outcome": "{$params.target.name} is {$get($params.target, 'role')}."}})
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


# -- text the engine writes for several agents, and what its refusals say ------------------------------------------


def test_a_transfer_refusal_names_no_amount_of_anothers_private_property():
    c = with_(actions={"steal": {"by": "player", "params": {"target": {"type": "entity", "of": "player"},
                                                           "amount": {"type": "int", "min": 1, "max": 100}},
                                 "do": [{"transfer": "cash", "from": "$params.target", "to": "$actor",
                                         "amount": "$params.amount"}]}})
    _, seen = play(c, [("steal", {"target": "bob", "amount": 5}), ("steal", {"target": "ann", "amount": 50})])
    theirs, own = seen["calls"]
    assert theirs.startswith("bob cannot cover that.") and "3" not in theirs
    assert "has only 10 cash" in own  # her own cash she may know


def test_an_announcement_cannot_read_an_agents_private_property_not_even_the_actors():
    own = with_(actions={"brag": {"by": "player", "do": [], "announce": "{$actor.name} holds {$actor.cash}."}})
    assert any(i.path == "actions.brag.announce" and i.severity == "error" and "$actor.cash" in i.message
               for i in fg_env.check(own))
    theirs = with_(actions={"brag": {"by": "player", "do": [], "announce": "Bob holds {$entity(bob).cash}."}})
    result, seen = play(theirs, [("brag", {})])
    assert "could not be worked out" in seen["calls"][0] and "Bob holds" not in json.dumps(result.events)


def test_an_announcement_reveals_what_game_logic_worked_out():
    c = with_(actions={"brag": {"by": "player", "do": ["$shown = $actor.cash"], "announce": "{$actor.name} holds {$shown}."}})
    result, _ = play(c, [("brag", {})])
    assert result.status == "completed", result.error
    assert any(e.get("text") == "ann holds 10." for e in result.events)
    assert not [i for i in fg_env.check(c) if i.path.startswith("actions.brag")]


def test_news_cannot_read_an_agents_private_property_unless_sent_only_to_that_agent():
    event_say = with_()
    event_say["events"] = [{"phase": "start", "do": [], "say": "Bob holds {$entity(bob).cash}."}]
    to_everyone = with_(actions={"wave": {"by": "player", "do": [{"emit": "x", "say": "Bob holds {$entity(bob).cash}."}]}})
    own_to_everyone = with_(actions={"wave": {"by": "player", "do": [{"emit": "x", "say": "I hold {$actor.cash}."}]}})
    to_bob = with_(actions={"wave": {"by": "player", "do": [
        {"emit": "x", "say": "You hold {$entity(bob).cash}.", "to": "$entity(bob)"}]}})
    result, _ = play(event_say)
    assert result.status == "failed" and "bob's cash is private" in result.error
    result, seen = play(to_everyone, [("wave", {})])
    assert "could not be worked out" in seen["calls"][0] and "Bob holds" not in json.dumps(result.events)
    assert any(i.path == "events[0].say" and i.severity == "error" for i in fg_env.check(event_say))
    assert any(i.path == "actions.wave.do[0].say" and i.severity == "error" for i in fg_env.check(own_to_everyone))
    result, _ = play(to_bob, [("wave", {})])
    assert result.status == "completed", result.error
    assert any(e.get("text") == "You hold 3." for e in result.events)


def test_the_default_announcement_leaves_out_arguments_kept_in_a_private_property():
    c = contract()
    c["types"]["player"]["props"]["vote"] = {"default": "", "private": True}
    c["actions"]["accuse"] = {"by": "player", "do": ["$actor.vote = $params.target.id"], "params": {
        "target": {"type": "entity", "of": "player"}, "note": {"type": "text", "max_len": 20}}}
    result, _ = play(c, [("accuse", {"target": "bob", "note": "hunch"})])
    line = next(e for e in result.events if e.get("kind") == "action")
    assert line["text"] == "ann: accuse (note=«hunch»)." and "bob" not in json.dumps(line)


def test_a_requirement_reading_a_chosen_agents_private_property_is_a_warning():
    c = with_(actions={"poke": {"by": "player", "params": {"target": {"type": "entity", "of": "player"}}, "do": [],
                                "when": [{"expr": "$params.target.cash < 5", "why": "too rich"}]}})
    issue = next(i for i in fg_env.check(c) if i.path == "actions.poke.when[0]")
    assert issue.severity == "warning" and "$params.target.cash" in issue.message


def test_bounds_and_outcomes_reading_a_chosen_agents_private_property_are_check_errors():
    c = with_(actions={"spy": {"by": "player", "do": [], "outcome": "It holds {$params.target.cash}.", "params": {
        "target": {"type": "entity", "of": "player"}, "n": {"type": "int", "min": 0, "max": "$params.target.cash"}}}})
    errors = {i.path for i in fg_env.check(c) if i.severity == "error"}
    assert {"actions.spy.outcome", "actions.spy.params.n.max"} <= errors


def test_choices_worked_out_from_anothers_private_property_fail_loudly_not_as_an_empty_schema():
    c = with_(actions={"guess": {"by": "player", "do": [], "params": {
        "x": {"type": "enum", "values": "$map(player, $it.cash)"}}}})
    result, seen = play(c)
    assert result.status == "failed" and "actions.guess.params.x.values" in result.error
    assert "bob's cash is private" in result.error and "tools" not in seen


def test_an_inspectable_agent_type_with_a_secret_subtype_is_a_warning():
    c = contract()
    c["types"]["wolf"] = {"extends": "player"}
    c["entities"]["bob"]["type"] = "wolf"
    c["actions"]["kill"] = {"by": "wolf", "private": True, "params": {"target": {"type": "entity", "of": "player"}},
                            "do": []}
    issue = next(i for i in fg_env.check(c) if i.path == "types.wolf")
    assert issue.severity == "warning" and "inspect" in issue.message
    c["types"]["player"]["inspect"] = False
    assert not [i for i in fg_env.check(c) if i.path == "types.wolf"]


def _secrets(**extra):
    c = {"name": "Secrets", "clock": {"rounds": 1},
         "types": {"p": {"agent": True, "props": {"secret": {"type": "int", "default": 0, "private": True}}}},
         "entities": {"ann": {"type": "p", "props": {"secret": 4242}}, "bob": {"type": "p"}},
         "actions": {"wave": {"by": "p", "do": []}}}
    c.update(extra)
    return c


@pytest.mark.parametrize("extra, path", [
    ({"brief": {"roles": {"p": "Ann holds {$entity(ann).secret}."}}}, "brief.roles.p"),
    ({"stages": [{"name": "s", "brief": "Ann holds {$entity(ann).secret}."}]}, "stages.s.brief"),
    ({"records": {"log": {"fields": {"n": "int"}, "show": "Ann holds {$entity(ann).secret}"}},
      "events": [{"phase": "start", "do": [{"post": "log", "n": 1}]}]}, "records.log.show"),
])
def test_a_brief_or_record_line_naming_another_agents_private_property_is_refused(extra, path):
    c = _secrets(**extra)
    assert any(i.severity == "error" and "ann's secret is private" in i.message for i in fg_env.check(c))
    seen = []

    def participant(wake):
        if wake.entity_id == "bob":
            seen.append(wake.brief + wake.update)
        wake.end()

    result = fg_env.load(c, seed=1).run(participant)
    assert result.status == "failed" and path in result.error
    assert not any("4242" in text for text in seen)  # bob never reads ann's secret


def test_a_bound_read_through_entity_of_another_agents_private_property_is_an_error_not_a_dropped_bound():
    c = _secrets(actions={"guess": {"by": "p", "do": [],
                                    "params": {"x": {"type": "int", "min": 0, "max": "$entity(ann).secret"}}}})
    errors = [i for i in fg_env.check(c) if i.severity == "error"]
    assert [i.path for i in errors] == ["actions.guess.params.x.max"] and "ann's secret is private" in errors[0].message

    def participant(wake):
        list(wake.tools)
        wake.end()

    result = fg_env.load(c, seed=1).run(participant)
    assert result.status == "failed" and "actions.guess.params.x.max" in result.error


@pytest.mark.parametrize("path, patch", [
    ("views.v", {"views": {"v": {"show": "{$get($entity(ann), secret)}"}}}),
    ("views.v", {"views": {"v": {"show": "{$dict(p, $it.id, $it.secret)}"}}}),
    ("types.p.inspect", {"types": {"p": {"agent": True, "inspect": "$entity(ann).secret > 10",
                                          "props": {"secret": {"type": "int", "default": 0, "private": True}}}}}),
])
def test_another_agents_private_property_cannot_be_read_around_the_rule(path, patch):
    errors = [i for i in fg_env.check(_secrets(**patch)) if i.severity == "error"]
    assert [i.path for i in errors] == [path] and "'s secret is private" in errors[0].message


@pytest.mark.parametrize("show", ["Ann holds {$metrics.held}.", "Ann held {$last($series.held)}."])
def test_a_metric_worked_out_from_a_private_property_is_not_shown_to_agents(show):
    c = _secrets(metrics={"held": "$entity(ann).secret", "count": "$count(p)"}, views={"v": {"show": show}},
                 clock={"rounds": 2})
    assert any(i.severity == "error" and i.path == "views.v" and "metric held" in i.message for i in fg_env.check(c))
    seen = []

    def participant(wake):
        seen.append(wake.update)
        wake.end()

    result = Env(parse_contract(c), {}, 1).run(participant)
    assert result.status == "failed" and "views.v" in result.error
    assert not any("4242" in text for text in seen)
    public = _secrets(metrics={"count": "$count(p)"}, views={"v": {"show": "{$metrics.count} players."}})
    assert fg_env.run(public, seed=1).status == "completed"


def test_turn_order_cannot_rank_agents_by_a_private_property():
    c = _secrets(stages=[{"name": "s", "order": "-$it.secret"}])
    assert any(i.severity == "error" and i.path == "stages.s.order" and "ann's secret is private" in i.message
               for i in fg_env.check(c))
    result = Env(parse_contract(c), {}, 1).run()
    assert result.status == "failed" and "stages.s.order" in result.error


@pytest.mark.parametrize("why, told", [("{$entity(bob).name} cannot owe secrets", "bob cannot owe secrets."),
                                       ("ann holds {$entity(ann).secret}", "the environment's rules could not be")])
def test_an_invariants_why_is_a_template_that_shows_only_what_everyone_may_know(why, told):
    c = _secrets(invariants=[{"expr": "$entity(bob).secret >= 0", "why": why}],
                 actions={"steal": {"by": "p", "do": ["$entity(bob).secret -= 1"]}})
    replies = []
    fg_env.run(c, lambda wake: replies.append(wake.call("steal")) if wake.entity_id == "ann" else None, seed=1)
    assert replies[0].text.startswith(f"Your steal was not done: {told}"), replies[0].text
    broken = _secrets(invariants=[{"expr": "$entity(bob).secret >= 0", "why": "{$actor.name} broke it"}])
    assert any(i.severity == "error" and i.path == "invariants[0].why" for i in fg_env.check(broken))
