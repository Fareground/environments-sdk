"""Layers: values on every cell, read with $layer and changed by the `layer` effect."""
import json

import pytest

import fg_env
from fg_env.sdk.expr import evaluate

FIELD = {
    "name": "Field",
    "clock": {"rounds": 3},
    "space": {"grid": {"rows": 3, "cols": 3},
              "layers": {"sugar": {"type": "int", "default": "$cell[0] + $cell[1]", "max": 3},
                         "scent": {"default": 0.0},
                         "alive": {"type": "bool", "default": False}}},
    "types": {"ant": {"agent": True, "props": {"eaten": 0}}},
    "entities": {"a": {"type": "ant", "at": [1, 1]}},
    "actions": {"eat": {"by": "ant", "do": ["$actor.eaten += $layer(sugar, $actor)", {"layer": "sugar", "at": "$actor", "set": 0}]},
                "gorge": {"by": "ant", "do": [{"layer": "sugar", "at": "$actor", "set": "$value + 1"}, {"fail": "Too much."}]}},
}


def _layer(env, name):
    return list(env.world.space.layers.values[name])


def _with(events, **space):
    contract = json.loads(json.dumps(FIELD))
    contract["events"] = events
    contract["space"]["grid"].update(space)
    return contract


def test_layer_defaults_read_their_cell_and_keep_to_type_and_bounds():
    env = fg_env.load(FIELD, seed=1)
    assert _layer(env, "sugar") == [0, 1, 2, 1, 2, 3, 2, 3, 3]
    assert evaluate("$layer(sugar, [0, 1]) + $layer(scent, [2, 2])", env.world.scope()) == 1.0
    assert evaluate("$layer(alive, [1, 1])", env.world.scope()) is False


def test_setting_one_cell_rolls_back_with_its_action():
    env = fg_env.load(FIELD, seed=1)
    outcomes = {}

    def play(wake):
        outcomes["gorge"] = wake.call("gorge", {})
        outcomes["eat"] = wake.call("eat", {})
        wake.end()

    env.run(play, rounds=1)
    assert not outcomes["gorge"].ok and outcomes["eat"].ok
    assert env.entity("a")["props"]["eaten"] == 2 and _layer(env, "sugar")[4] == 0


def test_setting_every_cell_reads_the_values_as_they_were():
    env = fg_env.load(_with([{"do": [{"layer": "sugar", "set": "$layer(sugar, [($cell[0] + 2) % 3, $cell[1]])"}]}]), seed=1)
    env.run("idle", rounds=1)
    assert _layer(env, "sugar") == [2, 3, 3, 0, 1, 2, 1, 2, 3]


def test_where_limits_which_cells_are_set():
    env = fg_env.load(_with([{"do": [{"layer": "scent", "set": 5, "where": "$cell[0] == 0"}]}]), seed=1)
    env.run("idle", rounds=1)
    assert _layer(env, "scent") == [5, 5, 5, 0, 0, 0, 0, 0, 0]


def test_a_blinker_oscillates_on_a_layer():
    rule = "$count($filter($cells($cell), $layer(alive, $it)))"
    contract = _with([{"phase": "end", "do": [{"layer": "alive", "set": f"{rule} == 3 or ($value and {rule} == 2)"}]}],
                     rows=5, cols=5, neighborhood="moore")
    contract["space"]["layers"]["alive"]["default"] = "$cell in [[2, 1], [2, 2], [2, 3]]"
    contract["metrics"] = {"cells": "$filter($cells(), $layer(alive, $it))"}
    result = fg_env.run(contract, "idle", seed=1)
    assert result.series["cells"] == [[[1, 2], [2, 2], [3, 2]], [[2, 1], [2, 2], [2, 3]], [[1, 2], [2, 2], [3, 2]]]


def test_diffusion_shares_a_value_with_the_neighbourhood_and_conserves_it():
    contract = _with([{"do": [{"layer": "scent", "diffuse": 0.5}]}], torus=True)
    contract["space"]["layers"]["scent"]["default"] = "10.0 if $cell == [1, 1] else 0.0"
    env = fg_env.load(contract, seed=1)
    env.run("idle", rounds=1)
    assert _layer(env, "scent") == [0, 1.25, 0, 1.25, 5.0, 1.25, 0, 1.25, 0]


def test_a_cell_at_an_edge_that_does_not_wrap_keeps_the_shares_it_cannot_hand_out():
    contract = _with([{"do": [{"layer": "scent", "diffuse": 0.5}, {"layer": "scent", "decay": 0.5}]}])
    contract["space"]["layers"]["scent"]["default"] = "8.0 if $cell == [0, 0] else 0.0"
    env = fg_env.load(contract, seed=1)
    env.run("idle", rounds=1)
    assert _layer(env, "scent") == [3.0, 0.5, 0, 0.5, 0, 0, 0, 0, 0]


def test_graph_places_diffuse_to_the_places_their_edges_reach():
    contract = {"name": "Rumour", "space": {"graph": {"nodes": ["a", "b", "c"], "edges": [["a", "b"], ["a", "c"]]},
                                            "layers": {"heat": {"default": "6.0 if $cell == a else 0.0"}}},
                "types": {"x": {"props": {}}}, "clock": {"rounds": 1},
                "events": [{"do": [{"layer": "heat", "diffuse": 0.5}]}]}
    env = fg_env.load(contract, seed=1)
    env.run(rounds=1)
    assert _layer(env, "heat") == [3.0, 1.5, 1.5]


def test_layers_round_trip_through_snapshots():
    contract = _with([{"do": [{"layer": "scent", "set": "$value + $uniform(0, 1)"}, {"layer": "scent", "diffuse": 0.2}]}])
    straight = fg_env.load(contract, seed=4)
    straight.run("random")
    env = fg_env.load(contract, seed=4)
    env.run("random", rounds=1)
    env = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert env.run("random").to_dict() == straight.result().to_dict()
    assert _layer(env, "scent") == _layer(straight, "scent")


def test_layer_mistakes_are_reported_with_a_path():
    contract = _with([{"do": [{"layer": "sugar", "diffuse": 0.1}, {"layer": "smell", "decay": 0.1},
                              {"layer": "scent", "decay": 0.1, "at": [0, 0]}]}])
    issues = {(i.path, i.message) for i in fg_env.check(contract) if i.severity == "error"}
    assert ("events[0].do[0].diffuse", "`diffuse` needs a number layer; 'sugar' is int") in issues
    assert ("events[0].do[1].layer", "'smell' is not a declared layer") in issues
    assert ("events[0].do[2].at", "`at` goes with `set`, not `decay`") in issues
    result = fg_env.run(_with([{"do": [{"layer": "scent", "decay": 2}]}]), "idle", seed=1)
    assert result.status == "failed" and "`decay` is a share from 0 to 1" in result.error


def test_a_value_of_the_wrong_type_is_an_error_naming_the_layer():
    result = fg_env.run(_with([{"do": [{"layer": "alive", "at": [0, 0], "set": 3}]}]), "idle", seed=1)
    assert result.status == "failed" and "layer 'alive' holds true or false" in result.error


@pytest.mark.parametrize("raw", ["$layer(sugar, [5, 5])", "$layer(nope, [0, 0])"])
def test_reading_a_layer_badly_says_what_is_wrong(raw):
    env = fg_env.load(FIELD, seed=1)
    with pytest.raises(Exception, match="off the 3x3 grid|'nope' is not a declared layer"):
        evaluate(raw, env.world.scope())
