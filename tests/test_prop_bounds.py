"""A write past a declared min or max — a property's (T-799), a link value's or a layer cell's (T-820) — is refused,
never silently clamped."""
import pytest

import fg_env
from fg_env.errors import RunError

SHOP = {
    "name": "Shop",
    "clock": {"rounds": 1},
    "world": {"stock": {"default": 3, "min": 0}},
    "types": {"buyer": {"agent": True, "props": {"coins": {"default": 5, "min": 0},
                                                 "bag": {"default": 0, "max": 10}}}},
    "entities": {"a": {"type": "buyer", "name": "Ann"}, "b": {"type": "buyer", "name": "Ben"}},
    "actions": {
        "spend": {"by": "buyer", "params": {"n": {"type": "number", "min": 0}},
                  "do": ["$actor.bag += 1", "$actor.coins -= $params.n"]},
        "fill": {"by": "buyer", "params": {"n": {"type": "number", "min": 0}}, "do": ["$actor.bag += $params.n"]},
        "take": {"by": "buyer", "params": {"n": {"type": "number", "min": 0}}, "do": ["$world.stock -= $params.n"]},
    },
    "stages": [{"name": "shop", "turns": "sequential"}],
}


def _first_call(contract, name, args):
    """Ann's one call in a real turn: the tool result, and the environment after the run."""
    env = fg_env.load(contract, seed=1)
    results = []

    def participant(wake):
        if wake.entity_id == "a" and not results:
            results.append(wake.call(name, args))
        wake.end()

    env.run(participant, rounds=1)
    return results[0], env


def _props(env, entity_id):
    return next(e["props"] for e in env.entities("buyer") if e["id"] == entity_id)


def test_spending_more_than_you_have_is_refused_and_rolled_back():
    result, env = _first_call(SHOP, "spend", {"n": 100})
    assert not result.ok
    assert "Ann's coins cannot go below 0" in result.text and "it would be -95" in result.text
    assert _props(env, "a") == {"coins": 5, "bag": 0}  # the earlier write in the same action is undone too


def test_a_write_within_the_bounds_still_applies():
    result, env = _first_call(SHOP, "spend", {"n": 5})
    assert result.ok, result.text
    assert _props(env, "a") == {"coins": 0, "bag": 1}


def test_a_max_is_refused_the_same_way():
    result, env = _first_call(SHOP, "fill", {"n": 11})
    assert not result.ok and "Ann's bag cannot go above 10: it would be 11" in result.text
    assert _props(env, "a")["bag"] == 0


def test_a_world_property_is_bounded_too():
    result, env = _first_call(SHOP, "take", {"n": 4})
    assert not result.ok and "stock cannot go below 0" in result.text
    assert env.props["stock"] == 3


def test_a_sealed_choice_past_a_bound_is_refused_at_submit():
    sealed = {**SHOP, "stages": [{"name": "shop", "turns": "simultaneous"}]}
    result, env = _first_call(sealed, "spend", {"n": 6})
    assert not result.ok and "cannot go below 0" in result.text
    assert _props(env, "a")["coins"] == 5


def test_sealed_choices_that_only_cross_together_refuse_the_later_one_at_commit():
    sealed = {**SHOP, "stages": [{"name": "shop", "turns": "simultaneous"}]}
    env = fg_env.load(sealed, seed=1)

    def participant(wake):
        assert wake.call("take", {"n": 2}).ok  # each fits alone; together they would take 4 of 3
        wake.end()

    result = env.run(participant, rounds=1)
    assert env.props["stock"] == 1
    failed = [e for e in result.events if e["kind"] == "outcome" and not e["data"]["ok"]]
    assert len(failed) == 1 and "stock cannot go below 0" in failed[0]["text"]


def test_an_event_that_pushes_a_property_past_its_bound_fails_the_run_at_its_path():
    """No agent caused it, so no agent can fix it: it is a contract bug, reported where it is written."""
    drain = {**SHOP, "stages": [], "events": [{"phase": "end", "do": ["$world.stock -= 5"]}]}
    with pytest.raises(RunError, match=r"events\[0\]\.do.*stock cannot go below 0: it would be -2.*\$clamp"):
        fg_env.run(drain, seed=1)


def test_a_trigger_an_action_sets_off_that_crosses_a_bound_refuses_the_action():
    guarded = {**SHOP, "triggers": [{"when": "$world.stock < 3", "do": ["$world.stock -= 10"]}]}
    env = fg_env.load(guarded, seed=1)

    def participant(wake):
        result = wake.call("take", {"n": 1})
        assert not result.ok and "Nothing changed" in result.text
        wake.end()

    result = env.run(participant, rounds=1)
    assert result.status == "completed" and env.props["stock"] == 3


def test_saturating_is_written_with_clamp():
    capped = {**SHOP, "stages": [],
              "events": [{"phase": "end", "do": ["$world.stock = $clamp($world.stock - 5, 0, 3)"]}]}
    env = fg_env.load(capped, seed=1)
    assert env.run().status == "completed"
    assert env.props["stock"] == 0


def test_a_starting_value_outside_the_bounds_fails_the_build():
    bad = {**SHOP, "entities": {"a": {"type": "buyer", "props": {"coins": -1}}}}
    with pytest.raises(RunError, match="coins cannot go below 0"):
        fg_env.load(bad, seed=1)


LINKED = {**SHOP, "relations": {"trusts": {"min": 0, "max": 1}},
          "links": [{"relation": "trusts", "from": "a", "to": "b", "value": 0.8}],
          "actions": {"trust": {"by": "buyer", "params": {"n": {"type": "number"}},
                                "do": ["$link($actor, b, trusts).value += $params.n"]}}}


def test_a_link_value_past_its_relations_bound_is_refused_and_rolled_back():
    result, env = _first_call(LINKED, "trust", {"n": 0.5})
    assert not result.ok and "Ann's trusts link to Ben cannot go above 1: it would be 1.3" in result.text
    assert env.world.relation("a", "b", "trusts") == 0.8


def test_a_link_value_within_its_bounds_applies():
    result, env = _first_call(LINKED, "trust", {"n": -0.8})
    assert result.ok, result.text
    assert env.world.relation("a", "b", "trusts") == 0.0


def test_a_starting_link_value_outside_the_bounds_fails_the_build():
    bad = {**LINKED, "links": [{"relation": "trusts", "from": "a", "to": "b", "value": 2}]}
    with pytest.raises(RunError, match="Ann's trusts link to Ben cannot go above 1: it would be 2"):
        fg_env.load(bad, seed=1)


FIELD = {**SHOP, "space": {"grid": {"rows": 1, "cols": 2}, "layers": {"sugar": {"default": 2, "min": 0, "max": 5}}},
         "entities": {"a": {"type": "buyer", "name": "Ann", "at": [0, 0]}},
         "actions": {"graze": {"by": "buyer", "params": {"n": {"type": "number"}},
                               "do": [{"layer": "sugar", "at": "$actor", "set": "$value - $params.n"}]},
                     "sow": {"by": "buyer", "params": {"n": {"type": "number"}},
                             "do": [{"layer": "sugar", "set": "$value + $params.n"}]}}}


def test_a_layer_cell_past_its_bound_is_refused_and_rolled_back():
    for action, n, message in (("graze", 3, "layer 'sugar' cannot go below 0: it would be -1"),
                               ("sow", 4, "layer 'sugar' cannot go above 5: it would be 6")):
        result, env = _first_call(FIELD, action, {"n": n})
        assert not result.ok and message in result.text
        assert env.world.space.layers.values["sugar"] == [2, 2]


def test_a_layer_default_outside_the_bounds_fails_the_build():
    bad = {**FIELD,
           "space": {"grid": {"rows": 1, "cols": 2}, "layers": {"sugar": {"default": "$cell[1] * 9", "max": 5}}}}
    with pytest.raises(RunError, match="layer 'sugar' cannot go above 5: it would be 9"):
        fg_env.load(bad, seed=1)


def test_decaying_a_layer_stays_within_its_bounds_because_no_rule_wrote_the_value():
    floored = {**FIELD, "space": {"grid": {"rows": 1, "cols": 2}, "layers": {"sugar": {"default": 2, "min": 1}}},
               "events": [{"do": [{"layer": "sugar", "decay": 0.9}]}]}
    env = fg_env.load(floored, seed=1)
    assert env.run("idle").status == "completed"
    assert env.world.space.layers.values["sugar"] == [1, 1]


def test_a_fractional_value_written_to_a_whole_number_property_fails_the_run_at_its_path():
    split = {"name": "Split", "clock": {"rounds": 1}, "types": {"p": {"props": {"n": {"type": "int", "default": 0}}}},
             "entities": {"a": {"type": "p"}}, "events": [{"do": ["$entity(a).n = 7 / 2"]}]}
    at_the_rule = r"events\[0\]\.do\[0\]: .*a's n \(types\.p\.props\.n\) must be a whole number, got 3\.5"
    with pytest.raises(RunError, match=at_the_rule):
        fg_env.run(split, seed=1)
