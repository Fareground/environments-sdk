"""Authoring traps that used to pass `check` silently: a private argument announced through a local, `$it` inside
`create` props, choices that depend on an earlier argument, an action that never succeeds, a stage that offers every
action, an `end` that cuts the last round, static gaps (a view over an unknown type, text into a number, a misspelt
type, a wrong effect shape) and a crash only a boundary value triggers."""
import copy

import fg_env


def _issues(contract, **kwargs):
    return fg_env.check(contract, **kwargs)


def _errors(contract, **kwargs):
    return [i for i in fg_env.check(contract, **kwargs) if i.severity == "error"]


def _warnings(contract, **kwargs):
    return [i for i in fg_env.check(contract, **kwargs) if i.severity == "warning"]


# -- a private argument stays private however the rule carries it ---------------------------------------------------


def _secret_contract(do):
    return {"name": "Secret", "clock": {"rounds": 1},
            "types": {"p": {"agent": True, "props": {"secret": {"type": "int", "default": 0, "private": True},
                                                     "note": 0}}},
            "entities": {"ann": {"type": "p"}, "bob": {"type": "p"}},
            "stages": [{"name": "s", "turns": "sequential"}],
            "actions": {"set_secret": {"by": "p", "params": {"v": {"type": "int", "min": 1000, "max": 9999}}, "do": do},
                        "set_note": {"by": "p", "params": {"v": {"type": "int", "min": 1000, "max": 9999}},
                                     "do": "$actor.note = $params.v"}},
            "outputs": {"note": "$entity(ann).note"}}


def _announced(contract, action):
    def ann(wake):
        if wake.entity_id == "ann":
            wake.call(action, {"v": 7777})
        wake.end()

    result = fg_env.run(contract, {"ann": ann, "bob": "idle"}, seed=1)
    return [e for e in result.events if e["kind"] == "action"]


def test_a_private_argument_copied_through_locals_is_not_announced():
    for do in (["$x = $params.v", "$actor.secret = $x"],
               ["$x = $params.v", "$y = $x + 1", "$actor.secret = $y - 1"],
               [{"if": "$params.v > 0", "then": ["$x = $params.v"]}, "$actor.secret = $x"]):
        events = _announced(_secret_contract(do), "set_secret")
        assert events and all("7777" not in e["text"] and "7777" not in str(e["data"]) for e in events), do


def test_a_public_argument_is_still_announced():
    events = _announced(_secret_contract("$actor.secret = $params.v"), "set_note")
    assert any("7777" in e["text"] for e in events)


# -- `$it` inside `create` props --------------------------------------------------------------------------------------


def _create_inside_each(each):
    return {"name": "Create in each", "clock": {"rounds": 1},
            "types": {"src": {"props": {"v": 7}}, "dst": {"props": {"from": ""}}},
            "entities": {"a": {"type": "src"}, "b": {"type": "src"}},
            "events": [{"phase": "start", "do": [each]}],
            "outputs": {"dst_from": "$map(dst, $it.from)"}}


def test_it_in_create_props_inside_a_loop_is_an_error_that_says_how_to_read_the_loop_item():
    contract = _create_inside_each({"each": "src", "do": [{"create": "dst", "props": {"from": "$it.id"}}]})
    issues = [i for i in _errors(contract, rounds=0) if i.path == "events[0].do[0].do[0].props.from"]
    assert len(issues) == 1
    assert "new dst" in issues[0].message and '"as"' in issues[0].fix


def test_naming_the_loop_item_reads_it_in_create_props():
    contract = _create_inside_each({"each": "src", "as": "s", "do": [{"create": "dst", "props": {"from": "$s.id"}}]})
    assert _errors(contract) == []
    assert fg_env.run(contract).outputs["dst_from"] == ["a", "b"]


# -- choices that depend on an earlier argument --------------------------------------------------------------------


_ORDERS = {"name": "Orders", "clock": {"rounds": 6},
           "types": {"power": {"agent": True, "props": {"moved": ""}},
                     "army": {"props": {"owner": "", "exits": {"type": "list", "default": []}}}},
           "entities": {"p1": {"type": "power"}, "p2": {"type": "power"},
                        "a1": {"type": "army", "props": {"owner": "p1", "exits": ["x", "y"]}},
                        "a3": {"type": "army", "props": {"owner": "p1", "exits": ["y", "w"]}},
                        "a2": {"type": "army", "props": {"owner": "p2", "exits": ["z"]}}},
           "actions": {"move": {"by": "power", "params": {
               "army": {"type": "entity", "of": "army", "where": "$it.owner == $actor.id"},
               "to": {"type": "enum", "values": "$params.army.exits"}}, "do": "$actor.moved = $params.to"}},
           "outputs": {"moved": "$map(power, $it.moved)"}}


def test_dependent_choices_list_every_value_they_can_take():
    tools = {t["name"]: t for t in fg_env.load(_ORDERS).preview("p1")["tools"]}
    to = tools["move"]["input_schema"]["properties"]["to"]
    assert to["enum"] == ["x", "y", "w"] and to["type"] == "string"
    assert "depend on the other arguments" in to["description"]


def test_random_agents_pick_dependent_choices_that_are_valid():
    result = fg_env.run(_ORDERS, {"*": "random"}, seed=3)
    assert result.stats["invalid_calls"] == 0 and result.stats["actions"] > 0
    assert _issues(_ORDERS) == []


# -- an action that never succeeds in the smoke plays ---------------------------------------------------------------


def test_an_action_refused_on_every_call_is_reported_with_its_reason():
    contract = {"name": "Lock", "clock": {"rounds": 6},
                "types": {"p": {"agent": True, "props": {"open": False}}},
                "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
                "actions": {"guess": {"by": "p", "params": {"code": {"type": "number", "min": 0}},
                                      "when": {"expr": "$params.code == 42.5", "why": "wrong code"},
                                      "do": "$actor.open = true"},
                            "wait": {"by": "p", "do": "$actor.open = $actor.open"}},
                "outputs": {"open": "$count(p, $it.open)"}}
    found = [i for i in _warnings(contract) if i.path == "actions.guess"]
    assert len(found) == 1
    assert "never succeeded" in found[0].message and "wrong code" in found[0].message


# -- a stage without `actions` --------------------------------------------------------------------------------------


def _staged(rank_stage):
    return {"name": "Stages", "clock": {"rounds": 2},
            "types": {"p": {"agent": True, "props": {"posted": 0, "ranked": 0}}},
            "entities": {"a": {"type": "p"}},
            "stages": [{"name": "post", "actions": ["post"]}, rank_stage],
            "actions": {"post": {"by": "p", "do": "$actor.posted += 1"},
                        "rank": {"by": "p", "do": "$actor.ranked += 1"}},
            "outputs": {"posted": "$entity(a).posted"}}


def test_a_stage_without_actions_that_offers_another_stages_action_is_warned():
    found = [i for i in _warnings(_staged({"name": "rank"}), rounds=0) if i.path == "stages[1].actions"]
    assert len(found) == 1 and "post" in found[0].message and '"actions": "all"' in found[0].fix


def test_listing_the_actions_or_all_silences_the_warning():
    for stage in ({"name": "rank", "actions": ["rank"]}, {"name": "rank", "actions": "all"}):
        assert not [i for i in _warnings(_staged(stage), rounds=0) if i.path.startswith("stages")]


# -- an `end` that cuts the last round ------------------------------------------------------------------------------


def _jury(when, rounds="$inputs.sessions"):
    return {"name": "Jury", "clock": {"rounds": rounds}, "inputs": {"sessions": {"type": "int", "default": 3}},
            "types": {"j": {"agent": True, "props": {"votes": 0}}}, "entities": {"a": {"type": "j"}},
            "actions": {"vote": {"by": "j", "do": "$actor.votes += 1"}},
            "end": [{"when": when, "name": "done"}], "outputs": {"votes": "$entity(a).votes"}}


def test_an_end_at_the_last_round_number_is_warned():
    for contract in (_jury("$round == $inputs.sessions"), _jury("$round >= 3", rounds=3),
                     _jury("$round == $clock.rounds")):
        found = [i for i in _warnings(contract, rounds=0) if i.path == "end[0].when"]
        assert len(found) == 1 and "last round" in found[0].message, contract["end"]
    assert not [i for i in _warnings(_jury("$round == 2"), rounds=0) if i.path.startswith("end")]


def test_the_warned_end_really_cuts_the_last_round():
    votes = {"a": lambda wake: (wake.call("vote", {}), wake.end())}
    assert fg_env.run(_jury("$round == $inputs.sessions"), votes).outputs["votes"] == 2


# -- static gaps ----------------------------------------------------------------------------------------------------


_LEMON = {"name": "Lemonade", "clock": {"rounds": 3},
          "types": {"stand": {"agent": True, "props": {"price": 3, "label": "", "open": True,
                                                       "size": {"type": "enum", "values": ["small", "large"],
                                                                "default": "small"}}}},
          "entities": {"alice": {"type": "stand"}, "bob": {"type": "stand"}},
          "actions": {"set_price": {"by": "stand", "params": {"price": {"type": "int", "min": 1, "max": 5}},
                                    "do": "$actor.price = $params.price"}},
          "views": {"rivals": {"of": "stand", "show": "{name} charged {price}"}},
          "outputs": {"prices": "$map(stand, $it.price)"}}


def _lemon(change):
    contract = copy.deepcopy(_LEMON)
    change(contract)
    return contract


def _lemon_doing(do):
    return _lemon(lambda c: c["actions"]["set_price"].__setitem__("do", do))


def test_a_view_over_an_unknown_type_is_a_static_error_with_a_suggestion():
    contract = _lemon(lambda c: c["views"]["rivals"].__setitem__("of", "stands"))
    found = [i for i in _errors(contract, rounds=0) if i.path == "views.rivals.of"]
    assert len(found) == 1 and found[0].fix == "did you mean 'stand'?"


def test_assigning_a_value_of_the_wrong_type_is_a_static_error():
    cases = {'$actor.price = "cheap"': "text", "$actor.open = 'yes'": "text", "$actor.label = 3": "number",
             "$actor.price = true": "true or false", "$actor.size = 'lage'": "did you mean 'large'?"}
    for do, word in cases.items():
        contract = _lemon_doing(do)
        found = [i for i in _errors(contract, rounds=0) if i.path == "actions.set_price.do[0]"]
        assert len(found) == 1 and word in f"{found[0].message} {found[0].fix}", do
        assert len(_issues(contract)) == 1, do
    for do in ("$actor.price = 4", "$actor.price = $params.price * 2", "$actor.label = 'x'", "$actor.size = 'large'"):
        assert _errors(_lemon_doing(do), rounds=0) == [], do


def test_a_misspelt_type_name_is_suggested_through_its_common_spelling():
    contract = _lemon(lambda c: c["actions"]["set_price"]["params"]["price"].__setitem__("type", "strng"))
    found = [i for i in _errors(contract, rounds=0) if i.path == "actions.set_price.params.price.type"]
    assert len(found) == 1 and found[0].fix == "did you mean 'text'?"


def test_common_type_spellings_are_read_as_the_contract_types():
    contract = _lemon(lambda c: c["actions"]["set_price"]["params"]["price"].__setitem__("type", "integer"))
    assert _issues(contract) == []
    tool = fg_env.load(contract).preview("alice")["tools"][0]
    assert tool["input_schema"]["properties"]["price"]["type"] == "integer"


def test_a_wrong_effect_shape_names_the_closest_operation_not_every_one():
    shapes = {"set": "write an assignment as text", "eachh": "did you mean 'each'?"}
    for key, hint in shapes.items():
        contract = _lemon_doing([{key: "$actor.price"}])
        found = [i for i in _errors(contract, rounds=0) if i.path == "actions.set_price.do[0]"]
        assert len(found) == 1 and hint in found[0].fix, key
        assert "transfer" not in found[0].message, key
    both = _lemon_doing([{"if": "true", "each": "stand"}])
    found = [i for i in _errors(both, rounds=0) if i.path == "actions.set_price.do[0]"]
    assert len(found) == 1 and "if, each" in found[0].message


def test_an_action_whose_rule_always_fails_is_reported_once_per_cause():
    contract = _lemon(lambda c: c["actions"].__setitem__("split", {
        "by": "stand", "params": {"n": {"type": "int", "min": 0, "max": 0}}, "do": "$actor.price = 10 / $params.n"}))
    about_split = [i for i in _issues(contract) if i.path.startswith("actions.split")]
    assert [i.path for i in about_split] == ["actions.split", "actions.split.do[0]"]


# -- a crash only a boundary value triggers ---------------------------------------------------------------------------


def test_a_world_rule_that_crashes_on_a_boundary_value_is_reported():
    contract = {"name": "Rates", "clock": {"rounds": 20},
                "types": {"p": {"agent": True, "props": {"cash": {"default": 30, "min": 0},
                                                         "rate": {"default": 1, "min": 0, "max": 10}}}},
                "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
                "actions": {"spend": {"by": "p", "params": {"n": {"type": "int", "min": 0, "max": "$actor.cash"}},
                                      "do": ["$actor.cash -= $params.n"]},
                            "setrate": {"by": "p", "params": {"r": {"type": "int", "min": 0, "max": 10}},
                                        "do": ["$actor.rate = $params.r"]}},
                "events": [{"phase": "end", "each": "p", "do": ["$it.cash -= 1", "$it.cash += 10 / $it.rate"]}],
                "outputs": {"cash": "$entity(a).cash"}}
    found = [i for i in _errors(contract) if i.path == "events[0].do[0].do[1]"]
    assert len(found) == 1 and "division by zero" in found[0].message
