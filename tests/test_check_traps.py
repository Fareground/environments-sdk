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


def test_an_argument_that_decides_a_private_write_is_not_announced():
    """Control flow carries a value as well as a copy does: an action that may write a private property announces
    none of its arguments, whichever branch, key or transfer the write goes through."""
    contract = _secret_contract([])
    contract["types"]["p"]["props"].update({"cash": {"default": 50, "private": True},
                                             "marks": {"type": "map", "default": {}, "private": True}})
    for do in ([{"if": "$params.v > 5000", "then": ["$actor.secret = 1"], "else": ["$actor.secret = 0"]}],
               [{"each": "p", "where": "$it.note < $params.v", "do": ["$it.secret = 1"]}],
               ["$actor.marks[$params.v] = 1"],
               [{"transfer": "cash", "from": "$actor", "to": "$entity(bob)", "amount": "$params.v / 1000"}],
               [{"after": 1, "do": [{"if": "$params.v > 5000", "then": ["$actor.secret = 1"]}]}]):
        contract["actions"]["set_secret"]["do"] = do
        events = _announced(contract, "set_secret")
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


def test_a_misspelt_property_of_a_named_entity_is_caught_before_running_at_the_rule():
    """`$entity(a).vv = 1` resolves `a` to its type, like `$actor.vv`: the check names the rule and suggests the
    property, instead of leaving it to a smoke run that blames `p.vv`."""
    contract = {"name": "Typo", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"v": 0, "n": {"type": "int", "default": 0}}}},
                "entities": {"a": {"type": "p"}},
                "events": [{"on": "round.start", "do": ["$entity(a).vv = 1", "$entity('a').v = 'x'"]}]}
    errors = {i.path: i for i in _errors(contract, rounds=0)}
    assert "$entity(a).vv" in errors["events[0].do[0]"].message and "'v'" in errors["events[0].do[0]"].fix
    assert "declared as" in errors["events[0].do[1]"].message


def test_a_write_of_the_wrong_kind_found_at_run_time_names_the_rule_and_the_property():
    contract = {"name": "Kinds", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"n": {"type": "int", "default": 0}}}},
                "entities": {"a": {"type": "p"}},
                "events": [{"on": "round.start", "do": ["$entity(a).n += 0.5"]}]}
    [issue] = [i for i in _errors(contract) if "whole number" in i.message]
    assert issue.path == "events[0].do[0]"
    assert "a's n (types.p.props.n)" in issue.message and "`$entity(a).n += 0.5`" in issue.message


def test_an_entity_count_that_is_not_a_number_is_caught_before_running():
    contract = {"name": "Count", "clock": {"rounds": 1}, "types": {"p": {"agent": True}},
                "entities": {"p": {"type": "p", "count": "three"}}}
    [issue] = [i for i in _errors(contract, rounds=0) if i.path == "entities.p.count"]
    assert "whole number" in issue.message


def test_a_validation_message_is_the_validators_own_words_without_pydantics_prefix():
    contract = {"name": "x", "clock": {"rounds": 1}, "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
                "actions": {"go": {"by": "p", "do": []}}, "events": [{"on": "round.finish", "do": []}]}
    [issue] = [i for i in fg_env.check(contract) if i.path == "events[0].on"]
    assert issue.message.startswith("'round.finish' is not an anchor — did you mean 'round.end'?")
    ballot = {**contract, "events": [], "mechanisms": {"v": {"kind": "decision", "mode": "ballot", "who": "p",
                                                             "options": ["a", "b"], "threshold": 2}}}
    assert not [i for i in fg_env.check(ballot) if "error," in i.message or "failed," in i.message]


# -- a parameter field its type does not use is an error, never silently ignored -------------------------------------


def _param_contract(param):
    return {"name": "Params", "clock": {"rounds": 1},
            "types": {"p": {"agent": True, "props": {"n": 0}}},
            "entities": {"ann": {"type": "p"}, "bob": {"type": "p"}},
            "stages": [{"name": "s"}],
            "actions": {"pick": {"by": "p", "description": "pick", "params": {"x": param}, "do": []}}}


def test_a_parameter_field_its_type_ignores_is_an_error_at_its_path():
    cases = [({"type": "int", "values": [1, 2, 3]}, "values"),
             ({"type": "text", "values": ["x", "y"]}, "values"),
             ({"type": "int", "min": 0, "max": 9, "where": "$it != 7"}, "where"),
             ({"type": "enum", "values": ["a", "b"], "where": "$it != 'a'"}, "where"),
             ({"type": "list", "values": ["a", "b"], "where": "$it != 'a'"}, "where"),
             ({"type": "int", "max_len": 5}, "max_len"),
             ({"type": "text", "of": "p"}, "of"),
             ({"type": "number", "min_items": 1}, "min_items"),
             ({"type": "list", "items": {"type": "enum", "values": ["a"]}, "values": ["b"]}, "values")]
    for param, field in cases:
        errors = _errors(_param_contract(param), rounds=0)
        assert [e.path for e in errors] == [f"actions.pick.params.x.{field}"], (param, errors)


def test_parameter_fields_on_the_types_that_use_them_pass():
    for param in ({"type": "enum", "values": ["a", "b"]}, {"type": "entity", "of": "p", "where": "$it.id != $actor.id"},
                  {"type": "list", "of": "p", "where": "$it.n >= 0", "max_items": 2},
                  {"type": "list", "values": ["a", "b"]}, {"type": "text", "max_len": 20},
                  {"type": "int", "min": 0, "max": 5, "step": 1}):
        assert _errors(_param_contract(param), rounds=0) == [], param


def test_malformed_events_do_not_hide_the_rest_of_the_check_and_other_structural_errors_say_more_follows():
    c = {"name": "x", "clock": {"rounds": 1}, "types": {"p": {"agent": True, "props": {"n": 0}}},
         "entities": {"a": {"type": "p"}},
         "events": [{"on": "round.begin", "do": ["$world.nn = 1"]}, {"on": "change", "do": ["$actor.n += 1"]},
                    {"on": "round.end", "do": ["$world.q = 1"]}],
         "stages": [{"name": "s", "actions": ["nope"]}], "outputs": {"o": "$wrold.x"}}
    paths = [i.path for i in _errors(c, rounds=0)]
    assert paths == ["events[0].on", "events[1]", "stages[0].actions", "events[2].do[0]", "outputs.o"]
    c["types"]["p"]["agent"] = "maybe"
    issues = _issues(c, rounds=0)
    assert issues[-1].path == "(contract)" and "checked once these are fixed" in issues[-1].message


def test_a_whole_number_past_the_exact_range_is_refused_with_a_true_message():
    def run(expr):
        c = {"name": "BI", "clock": {"rounds": 40}, "world": {"x": {"type": "int", "default": 10}},
             "types": {"player": {"agent": True}}, "entities": {"ann": {"type": "player"}},
             "events": [{"on": "round.end", "do": [expr]}], "outputs": {"x": "$world.x"}}
        return fg_env.load(c, seed=1).run("idle")

    squared = run("$world.x = $world.x * $world.x")
    assert squared.status == "failed" and "beyond the exact whole-number range" in squared.error
    assert "must be a whole number" not in squared.error
    assert "beyond the exact" in run("$world.x = $world.x * 1000000 + 1").error
    assert run("$world.x = $world.x * 1000").status == "failed"  # 10^39 by round 13, not silently rounded
    fraction = run("$world.x = $round(10 ** 400 * 1.5)")
    assert "OverflowError" not in fraction.error and "too large for a fraction" in fraction.error


def test_an_output_reading_itself_or_one_written_after_it_is_an_error():
    c = {"name": "x", "clock": {"rounds": 2}, "world": {"t": 0}, "types": {"p": {"agent": True, "props": {"n": 0}}},
         "entities": {"a": {"type": "p"}}, "actions": {"go": {"by": "p", "description": "g", "do": ["$world.t += 1"]}},
         "outputs": {"a": "$outputs.b + 1", "b": "$world.t", "c": "$outputs.c + 1", "e": "$outputs.b * 2",
                     "s": {"expr": "$world.t", "series": True}, "d": "$outputs.s"}}
    errors = {i.path: i.message for i in _errors(c, rounds=0)}
    assert set(errors) == {"outputs.a", "outputs.c"}
    assert errors["outputs.c"].startswith("reads itself") and "$outputs.b" in errors["outputs.a"]


def test_a_bare_word_naming_a_listed_items_prop_is_warned_in_a_list_view_too():
    c = {"name": "x", "clock": {"rounds": 1},
         "types": {"p": {"agent": True, "props": {"status": ""}}, "item": {"props": {"decision": ""}}},
         "entities": {"a": {"type": "p"}, "item": {"type": "item", "count": 2}},
         "actions": {"go": {"by": "p", "description": "g", "do": []}},
         "views": {"items": {"for": "p", "of": "item", "show": "{id}{' — ' + decision if decision else ''}"}},
         "outputs": {"n": "$count(item)"}}
    warned = [i for i in _warnings(c, rounds=0) if i.path == "views.items.show"]
    assert warned and "did you mean $it.decision?" in warned[0].fix


def test_i_inside_a_per_item_function_in_generated_props_is_warned_and_outer_reads_the_entity():
    c = {"name": "x", "clock": {"rounds": 1}, "inputs": {"cats": {"type": "list", "default": ["a", "b"]}},
         "types": {"p": {"agent": True, "props": {"n": 0, "prefs": {"type": "map", "default": {}}}}},
         "entities": {"p": {"type": "p", "count": 3,
                            "props": {"prefs": "$dict($inputs.cats, $it, $random_for([$i, $it]))"}}},
         "actions": {"go": {"by": "p", "description": "g", "do": []}}, "outputs": {"prefs": "$map(p, $it.prefs)"}}
    warned = [i for i in _warnings(c, rounds=0) if i.path == "entities.p.props.prefs"]
    assert warned and "$outer.n" in warned[0].fix
    assert len({str(prefs) for prefs in fg_env.run(c, "idle", seed=1).outputs["prefs"]}) == 1  # all the same
    c["entities"]["p"]["props"] = {"n": "$i", "prefs": "$dict($inputs.cats, $it, $random_for([$outer.n, $it]))"}
    assert not [i for i in _warnings(c, rounds=0) if i.path == "entities.p.props.prefs"]
    assert len({str(prefs) for prefs in fg_env.run(c, "idle", seed=1).outputs["prefs"]}) == 3


def test_a_named_entitys_name_is_a_template_as_a_generated_ones_is():
    c = {"name": "x", "clock": {"rounds": 1}, "inputs": {"town": {"type": "text", "default": "Oak"}},
         "types": {"p": {"agent": True}}, "entities": {"ann": {"type": "p", "name": "Ann of {$inputs.town}"}},
         "actions": {"go": {"by": "p", "description": "g", "do": []}}, "outputs": {"n": "$entity(ann).name"}}
    assert fg_env.run(c, "idle", seed=1).outputs["n"] == "Ann of Oak"
    c["entities"]["ann"]["name"] = "Ann of {$inputs.twon}"
    assert [i.path for i in _errors(c, rounds=0)] == ["entities.ann.name"]


def test_indexing_text_says_how_to_read_its_characters():
    c = {"name": "x", "clock": {"rounds": 1}, "inputs": {"plan": {"type": "list", "default": ["#.", ".#"]}},
         "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
         "actions": {"go": {"by": "p", "description": "g", "do": []}}, "outputs": {"c": "$inputs.plan[0][1]"}}
    issue = next(i for i in fg_env.run(c, "idle", seed=1).output_issues if i["path"] == "outputs.c")
    assert "$chars(text)" in issue["message"]
    c["outputs"]["c"] = "$chars($inputs.plan[0])[1]"
    assert fg_env.run(c, "idle", seed=1).outputs["c"] == "."


def test_top_and_sort_keep_listing_order_for_ties():
    c = {"name": "x", "clock": {"rounds": 1}, "types": {"p": {"agent": True, "props": {"s": 0}}},
         "entities": {"a": {"type": "p", "props": {"s": 5}}, "b": {"type": "p", "props": {"s": 5}},
                      "c": {"type": "p", "props": {"s": 1}}},
         "actions": {"go": {"by": "p", "description": "g", "do": []}},
         "outputs": {"top": "$map($top(p, $it.s), $it.id)", "sort": "$map($sort(p, $it.s), $it.id)"}}
    assert fg_env.run(c, "idle", seed=1).outputs == {"top": ["a", "b", "c"], "sort": ["c", "a", "b"]}


def test_a_type_default_that_does_not_fit_its_type_is_a_static_error_at_the_type():
    c = {"name": "x", "clock": {"rounds": 1}, "types": {"a": {"agent": True, "props": {"clicks": {"type": "int",
                                                                                              "default": 0.5}}}},
         "entities": {"advertiser": {"type": "a", "count": 2}},
         "actions": {"go": {"by": "a", "description": "g", "do": []}}, "outputs": {"n": "$count(a)"}}
    assert [i.path for i in _errors(c, rounds=0)] == ["types.a.props.clicks.default"]


def test_len_of_a_type_name_is_refused_with_count_as_the_fix():
    """`$len(trader)` would count the letters of the text 'trader', where `$count(trader)` counts the traders."""
    market = {"name": "Market", "clock": {"rounds": 1}, "types": {"trader": {}},
              "entities": {"trader": {"type": "trader", "count": 5}},
              "outputs": {"n": "$len(trader)", "ids": "$len($map(trader, $it.id))"}}
    errors = [i for i in fg_env.check(market) if i.severity == "error"]
    assert [i.path for i in errors] == ["outputs.n"] and "$count(trader)" in errors[0].fix


def test_a_bare_word_naming_a_property_is_warned_about_even_when_an_output_shares_its_name():
    """Outputs are read as `$outputs.<name>`, never as bare words: `{' - DONE' if done else ''}` is always true
    whether or not an output is called `done`."""
    tasks = {"name": "Tasks", "clock": {"rounds": 1},
             "types": {"p": {"agent": True}, "task": {"props": {"done": False}}},
             "entities": {"a": {"type": "p"}, "t1": {"type": "task"}},
             "actions": {"noop": {"by": "p", "do": []}},
             "views": {"tasks": {"for": "p", "of": "task", "show": "{name}{' - DONE' if done else ''}"}},
             "outputs": {"done": "$count(task, $it.done)"}}
    assert any(i.path == "views.tasks.show" and "bare word 'done'" in i.message and "$it.done" in i.fix
               for i in fg_env.check(tasks, rounds=0))
