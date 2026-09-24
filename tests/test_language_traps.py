"""Language traps an author falls into, and previews that show the turn exactly as the run plays it."""
import json

import pytest

import fg_env
from fg_env import ContractError


def _errors(contract):
    return [issue for issue in fg_env.check(contract) if issue.severity == "error"]


# -- previews -----------------------------------------------------------------------------------------------------

AUTO = {"name": "Auto", "clock": {"rounds": 3},
        "types": {"p": {"agent": True, "props": {"busy": False}}},
        "entities": {"a": {"type": "p", "props": {"busy": True}}},
        "actions": {"work": {"by": "p", "when": ["not $actor.busy"], "do": ["$actor.busy = true"]}},
        "stages": [{"name": "s", "actions": ["work"], "auto": True}]}


def test_preview_does_not_show_a_turn_that_auto_plays_without_the_agent():
    woken = []
    fg_env.load(AUTO, seed=1).run({"a": lambda wake: woken.append(wake.round)})
    assert woken == []
    view = fg_env.load(AUTO, seed=1).preview("a")
    assert "auto" in view["update"] and "not be woken" in view["update"]


SEATS = {"name": "Seats", "clock": {"rounds": 2}, "world": {"last": ""},
         "types": {"p": {"agent": True}},
         "entities": {"first": {"type": "p"}, "second": {"type": "p"}},
         "actions": {"left": {"by": "p", "do": "$world.last = 'left'", "announce": "{$actor.name} went left."},
                     "right": {"by": "p", "do": "$world.last = 'right'", "announce": "{$actor.name} went right."}},
         "stages": [{"name": "go", "turns": "sequential", "actions": ["left", "right"]}],
         "policies": {"lefty": {"rules": [{"do": "left"}]}, "righty": {"rules": [{"do": "right"}]}}}


def test_preview_plays_the_earlier_seats_with_the_participants_it_is_given():
    env = fg_env.load(SEATS, seed=1)
    assert "first went left." in env.preview("second", participants={"first": "policy:lefty"})["update"]
    assert "first went right." in env.preview("second", participants={"first": "policy:righty"})["update"]
    with pytest.raises(ContractError, match="not an entity id"):
        env.preview("second", participants={"frist": "policy:lefty"})


def test_preview_clone_and_fork_play_with_the_participants_the_run_was_given():
    env = fg_env.load(SEATS, seed=1)
    env.run({"first": "policy:righty"}, rounds=1)
    plain = env.preview("second")["update"]
    assert "first went right." in plain
    assert env.clone().preview("second")["update"] == plain
    assert env.fork(effects=["$world.last = $world.last"]).preview("second")["update"] == plain


def test_cli_preview_plays_earlier_seats_with_agent_flags(tmp_path, capsys):
    from fg_env.__main__ import main

    path = tmp_path / "seats.json"
    path.write_text(json.dumps(SEATS))
    assert main(["preview", str(path), "second", "--agent", "first=policy:lefty"]) == 0
    assert "first went left." in capsys.readouterr().out


# -- reactions offer the actions the wake names ------------------------------------------------------------------

TRADE = {"name": "Trade", "clock": {"rounds": 1}, "world": {"log": {"type": "list", "default": []}},
         "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
         "actions": {
             "build": {"by": "p", "do": "$world.log += $actor.id + ' builds'"},
             "offer": {"by": "p", "params": {"to": {"type": "entity", "of": "p", "where": "$it.id != $actor.id"}},
                       "do": ["$world.log += $actor.id + ' offers'",
                              {"wake": "$params.to", "now": True, "why": "An offer awaits your answer.",
                               "actions": ["accept", "reject"]}]},
             "accept": {"by": "p", "do": "$world.log += $actor.id + ' accepts'"},
             "reject": {"by": "p", "do": "$world.log += $actor.id + ' rejects'"}},
         "stages": [{"name": "turns", "turns": "sequential", "actions": ["build", "offer"], "max_actions": 2}]}


def test_a_reaction_turn_offers_only_the_actions_its_wake_names():
    offered = {}

    def play(wake):
        if wake.reason == "An offer awaits your answer.":
            offered[wake.entity_id] = sorted(t.name for t in wake.tools if t.name not in ("inspect", "end_turn"))
            assert not wake.call("build").ok  # building out of turn is refused
            wake.call("accept")
        elif wake.entity_id == "a":
            wake.call("offer", {"to": "b"})
        wake.end()

    env = fg_env.load(TRADE, seed=1)
    result = env.run(play)
    assert result.status == "completed", result.error
    assert offered == {"b": ["accept", "reject"]}
    assert env.props["log"] == ["a offers", "b accepts"]


def test_check_warns_when_a_reaction_would_be_offered_every_action_of_the_stage():
    contract = json.loads(json.dumps(TRADE))
    del contract["actions"]["offer"]["do"][1]["actions"]
    warned = [i for i in fg_env.check(contract, rounds=0) if i.path == "actions.offer.do[1]"]
    assert warned and "actions" in warned[0].fix
    assert not [i for i in fg_env.check(TRADE, rounds=0) if i.path.startswith("actions.offer.do")]


def test_reaction_actions_must_name_declared_actions_and_need_now():
    contract = json.loads(json.dumps(TRADE))
    contract["actions"]["offer"]["do"][1]["actions"] = ["acept"]
    assert any("did you mean 'accept'" in i.fix for i in _errors(contract))
    del contract["actions"]["offer"]["do"][1]["now"]
    contract["actions"]["offer"]["do"][1]["actions"] = ["accept"]
    assert any("now" in i.message for i in _errors(contract))


# -- list bounds from inputs ---------------------------------------------------------------------------------------

PICKS = {"name": "Picks", "clock": {"rounds": 1},
         "inputs": {"seats": {"type": "int", "default": 3, "min": 1}},
         "world": {"picked": {"type": "list", "default": []}},
         "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
         "actions": {"pick": {"by": "p", "params": {"picks": {
             "type": "list", "values": ["x", "y", "z", "w"], "unique": False,
             "min_items": "$inputs.seats", "max_items": "$inputs.seats"}},
             "do": "$world.picked = $params.picks"}}}


@pytest.mark.parametrize("seats", [2, 3])
def test_list_bounds_accept_expressions_like_min_and_max(seats):
    assert _errors(PICKS) == []
    env = fg_env.load(PICKS, inputs={"seats": seats}, seed=1)
    schema = next(t for t in env.preview("a")["tools"] if t["name"] == "pick")["input_schema"]
    assert schema["properties"]["picks"]["minItems"] == seats == schema["properties"]["picks"]["maxItems"]
    answers = []

    def play(wake):
        answers.append(wake.call("pick", {"picks": ["x"] * (seats + 1)}))
        answers.append(wake.call("pick", {"picks": ["x"] * seats}))

    env.run(play)
    assert not answers[0].ok and "at most" in answers[0].text
    assert answers[1].ok and len(env.props["picked"]) == seats


def test_a_list_bound_expression_is_checked():
    broken = json.loads(json.dumps(PICKS))
    broken["actions"]["pick"]["params"]["picks"]["max_items"] = "$inputs.seets"
    assert any(i.path.endswith("max_items") for i in _errors(broken))
    broken["actions"]["pick"]["params"]["picks"]["max_items"] = "3"
    assert any(i.path.endswith("max_items") and "text '3'" in i.message for i in _errors(broken))


# -- created props read each other in dependency order ------------------------------------------------------------

def test_created_props_are_evaluated_after_the_props_they_read():
    contract = {"name": "Order", "clock": {"rounds": 1},
                "types": {"thing": {"props": {"double": 0, "base": 0}}},
                "events": [{"phase": "end", "do": [{"create": "thing", "props": {"base": 3, "double": "$it.base * 2"}}]}],
                "outputs": {"d": "$map(thing, $it.double)"}}
    assert _errors(contract) == []
    assert fg_env.run(contract, seed=1).outputs == {"d": [6]}


def test_created_props_that_read_each_other_in_a_circle_say_so():
    contract = {"name": "Circle", "clock": {"rounds": 1},
                "types": {"thing": {"props": {"a": 0, "b": 0}}},
                "events": [{"do": [{"create": "thing", "props": {"a": "$it.b + 1", "b": "$it.a + 1"}}]}]}
    with pytest.raises(fg_env.RunError, match="circle"):
        fg_env.load(contract, seed=1).run(raise_errors=True)


# -- an event's say and its each item -----------------------------------------------------------------------------

def test_an_each_events_say_reading_the_item_names_the_fix():
    contract = {"name": "Say", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"coins": 0}}}, "entities": {"a": {"type": "p"}},
                "events": [{"each": "p", "as": "who", "do": "$who.coins += 1", "say": "{$who.name} got a coin."}]}
    problem = next(i for i in _errors(contract) if i.path == "events[0].say")
    assert "emit" in problem.fix


# -- strict contract types ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,value,fix", [
    (("types", "p", "agent"), "yes", "true or false, without quotes"),
    (("types", "p", "props", "c", "private"), "false", "true or false, without quotes"),
    (("actions", "go", "per_turn"), "2", "whole number without quotes"),
    (("stages", 0, "max_actions"), "2", "write 2 without quotes"),
    (("clock", "rounds"), "5", "write 5 without quotes")])
def test_contract_fields_refuse_values_of_the_wrong_type(path, value, fix):
    contract = {"name": "Strict", "clock": {"rounds": 2},
                "types": {"p": {"agent": True, "props": {"c": {"default": 0}}}},
                "entities": {"a": {"type": "p"}}, "actions": {"go": {"by": "p", "do": "$actor.c += 1"}},
                "stages": [{"name": "s", "actions": ["go"]}]}
    target = contract
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    problems = _errors(contract)
    assert [p.path for p in problems] == [".".join(str(k) for k in path).replace(".0.", "[0].")]
    assert fix in problems[0].fix


# -- literal type errors and output failures ----------------------------------------------------------------------

def test_text_plus_a_number_is_a_static_error():
    contract = {"name": "Types", "clock": {"rounds": 1}, "types": {},
                "outputs": {"bad": "1 + 'a'"}}
    problems = fg_env.check(contract, rounds=0)
    assert any(i.path == "outputs.bad" and i.severity == "error" for i in problems)


def test_an_output_that_fails_at_runtime_is_in_the_diagnostics():
    contract = {"name": "Late", "clock": {"rounds": 1}, "world": {"label": "a"}, "types": {},
                "outputs": {"bad": "1 + $world.label"}}
    result = fg_env.run(contract, seed=1)
    assert any(d.get("kind") == "output_failed" or "bad" in json.dumps(d) for d in result.diagnostics)
