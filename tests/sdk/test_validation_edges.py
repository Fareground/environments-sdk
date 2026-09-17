"""Reserved-word names, refusals inside events, and arguments whose checks read an earlier bad argument."""
import fg_env

BASE = {"name": "Edges", "clock": {"rounds": 1},
        "types": {"player": {"agent": True, "props": {"cash": 10}}},
        "entities": {"ann": {"type": "player"}},
        "stages": [{"name": "play", "turns": "sequential"}]}


def _contract(**sections):
    return {**BASE, **sections}


def test_reserved_word_names_are_errors_because_expressions_cannot_read_them():
    contract = _contract(types={"player": {"agent": True, "props": {"from": "", "cash": 10}}},
                         actions={"pay": {"by": "player", "params": {"in": {"type": "int"}}, "do": []}})
    errors = [str(i) for i in fg_env.check(contract) if i.severity == "error"]
    assert any("types.player.props.from" in e and "reserved word" in e for e in errors)
    assert any("actions.pay.params.in" in e and "reserved word" in e for e in errors)


def test_a_refusal_inside_an_event_is_logged_for_the_record_and_shown_to_no_agent():
    env = fg_env.load(_contract(events=[{"phase": "start", "do": ["$world.tick = 1", {"fail": "the vault is empty"}]}],
                                world={"tick": 0}), seed=1)
    result = env.run("idle")
    refused = [e for e in result.events if "was refused" in e.get("text", "")]
    assert refused and "the vault is empty" in refused[0]["text"]
    assert env.props["tick"] == 0  # the refused block was undone


def test_an_argument_whose_bound_reads_an_earlier_bad_argument_is_reported_not_crashed():
    contract = _contract(actions={"split": {"by": "player", "terminal": True, "params": {
        "total": {"type": "int", "min": 1, "max": "$actor.cash"},
        "part": {"type": "int", "min": 0, "max": "$params.total"}}, "do": []}})
    env = fg_env.load(contract, seed=1)
    told = []

    def play(wake):
        told.append(wake.call("split", {"total": "lots", "part": 3}).text)
        told.append(wake.call("split", {"total": 5, "part": 3}).text)
        wake.end()

    result = env.run(play)
    assert result.status == "completed", result.error
    assert "total must be a number" in told[0] and "part can be checked once total is corrected" in told[0]
    assert "not done" not in told[1]


def test_entity_name_diagnostic_points_to_working_population_label():
    contract = _contract(types={"player": {"agent": True, "props": {"name": "Label"}}})
    issue = next(i for i in fg_env.check(contract) if i.path == "types.player.props.name")
    assert "outside props" in issue.fix
    contract["types"]["player"]["props"] = {}
    contract["entities"] = {"ann": {"type": "player", "name": "Label"}}
    assert not [i for i in fg_env.check(contract) if i.severity == "error"]
    assert fg_env.load(contract).entity("ann")["name"] == "Label"


def test_create_props_validate_new_entity_scope_without_leaking_into_outer_loop():
    contract = {"name": "Creation scope", "clock": {"rounds": 1},
                "types": {"source": {"props": {"order_index": 7}},
                          "cohort": {"props": {"channel_order": 0, "twice": 0}}},
                "entities": {"source": {"type": "source", "name": "Channel"}},
                "world": {"after": 0},
                "events": [{"each": "source", "do": [
                    {"create": "cohort", "name": "{$it.name}",
                     "props": {"channel_order": "$it.order_index", "twice": "$it.channel_order * 2"}},
                    "$world.after = $it.order_index"]}],
                "outputs": {"after": "$world.after", "created": "$sum(cohort, $it.twice)"}}
    issues = fg_env.check(contract, rounds=0)
    assert any(i.path.endswith("props.channel_order") and "cohort" in i.message
               and "order_index" in i.message for i in issues)
    contract["events"][0]["do"].insert(0, "$source = $it")
    contract["events"][0]["do"][1]["props"]["channel_order"] = "$source.order_index"
    assert not [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    result = fg_env.run(contract)
    assert result.ok and result.outputs == {"after": 7, "created": 14}


def test_create_props_can_read_the_new_entity_without_an_outer_it():
    contract = {"name": "Self initialization", "clock": {"rounds": 1},
                "types": {"item": {"props": {"base": 0, "double": 0}}},
                "events": [{"do": {"create": "item", "props": {"base": 3, "double": "$it.base * 2"}}}],
                "outputs": {"value": "$sum(item, $it.double)"}}
    assert not [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    result = fg_env.run(contract)
    assert result.ok and result.outputs == {"value": 6}


def test_action_parameter_input_fields_point_to_executable_repair():
    amount = {"type": "number", "min": 0.01, "multiple_of": 0.01,
              "label": "Amount", "display": "number"}
    contract = _contract(actions={"pay": {"by": "player", "params": {
        "amounts": {"type": "list", "unique": False, "items": amount}},
        "do": "$actor.cash -= $sum($params.amounts)"}},
        outputs={"cash": "$sum(player, $it.cash)"})
    issues = {i.path: i for i in fg_env.check(contract, rounds=0)}
    prefix = "actions.pay.params.amounts.items."
    assert "step" in issues[prefix + "multiple_of"].fix
    assert "from min" in issues[prefix + "multiple_of"].fix
    for name in ("label", "display"):
        assert "description" in issues[prefix + name].fix
    amount.clear()
    amount.update(type="number", min=0.01, max=0.30, step=0.01, description="Amount in dollars")
    assert not [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    def pay(wake):
        for invalid, expected in ((0.105, "steps of 0.01 from 0.01 (got 0.105)"),
                                  (0.001, "at least 0.01 (got 0.001)"),
                                  (0.305, "at most 0.3 (got 0.305)")):
            refused = wake.call("pay", {"amounts": [0.10, invalid]})
            assert not refused.ok and expected in refused.text
        assert wake.call("pay", {"amounts": [0.10, 0.20]}).ok
        wake.end()
    result = fg_env.run(contract, pay)
    assert result.ok and result.outputs == {"cash": 9.7}
