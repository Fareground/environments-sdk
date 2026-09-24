"""Spaces: sizes from inputs, neighbourhoods, torus, capacity, and neighbourhood queries on an index."""
import json

import pytest

import fg_env
from fg_env.expr import ExprError, evaluate


def _grid(**grid):
    return {
        "name": "Board",
        "clock": {"rounds": 3},
        "inputs": {"size": {"type": "int", "default": 5}},
        "space": {"grid": {"rows": "$inputs.size", "cols": "$inputs.size", **grid}},
        "types": {"walker": {"agent": True, "props": {}}, "rock": {"props": {}}},
        "entities": {"a": {"type": "walker", "at": [2, 2]}, "b": {"type": "walker", "at": [2, 3]},
                     "c": {"type": "rock", "at": [0, 0]}, "d": {"type": "rock", "at": [4, 4]}},
        "actions": {"step": {"by": "walker",
                             "params": {"to": {"type": "list", "items": {"type": "int"}, "unique": False}},
                             "do": [{"move": "$actor", "to": "$params.to"}]}},
    }


def _eval(env, text, **roots):
    return evaluate(text, env.world.scope(**{k: env.world.entities[v] for k, v in roots.items()}))


def test_grid_sizes_come_from_inputs():
    env = fg_env.load(_grid(), inputs={"size": 9}, seed=1)
    assert len(_eval(env, "$cells()")) == 81
    with pytest.raises(fg_env.RunError, match="space.grid.rows must be a whole number"):
        fg_env.load(_grid(), inputs={"size": 0}, seed=1)


@pytest.mark.parametrize("neighborhood, count, far", [("von_neumann", 4, 8.0), ("moore", 8, 4.0), ("hex", 6, 8.0)])
def test_each_neighborhood_has_its_neighbours_and_distance(neighborhood, count, far):
    env = fg_env.load(_grid(neighborhood=neighborhood), seed=1)
    assert len(_eval(env, "$cells([2, 2])")) == count
    assert _eval(env, "$distance($c, $d)", c="c", d="d") == far


def test_path_distance_walks_round_what_blocks_the_way():
    contract = _grid()
    contract["space"]["layers"] = {"wall": {"type": "bool", "default": "$cell[1] == 3 and $cell[0] < 4"}}
    env = fg_env.load(contract, seed=1)
    assert _eval(env, "$distance([0, 0], [0, 4])") == 4.0  # straight through the wall
    assert _eval(env, "$path_distance([0, 0], [0, 4], not $layer(wall, $it))") == 12  # down, through the gap, up
    assert _eval(env, "$path_distance($a, [0, 4], not $layer(wall, $it))", a="a") == 8
    assert _eval(env, "$path_distance([0, 0], [0, 4], $it[1] != 3)") is None  # no way round
    assert _eval(env, "$path_distance([0, 0], [2, 2])") == 4  # nothing blocks: every cell is open
    assert _eval(env, "$path_distance($c, $c, false)", c="c") == 0  # the start and the goal are always open
    with pytest.raises(ExprError, match="needs a grid"):
        _eval(fg_env.load(GRAPH, seed=1), "$path_distance(a, d)")


def test_hex_cells_use_axial_neighbours():
    env = fg_env.load(_grid(neighborhood="hex"), seed=1)
    assert _eval(env, "$cells([2, 2])") == [[1, 2], [1, 3], [2, 1], [2, 3], [3, 1], [3, 2]]
    assert _eval(env, "$distance([2, 2], [0, 4])") == 2.0
    assert _eval(env, "$distance([2, 2], [0, 0])") == 4.0


def test_a_torus_wraps_moves_and_takes_the_short_way():
    contract = _grid(neighborhood="moore", torus=True)
    env = fg_env.load(contract, seed=1)
    assert _eval(env, "$distance($c, $d)", c="c", d="d") == 1.0
    assert len(_eval(env, "$cells([0, 0])")) == 8
    result = env.run(lambda wake: wake.call("step", {"to": [-1, 7]}) and wake.end(), rounds=1)
    assert result.status == "running"
    assert env.entity("a")["at"] == [4, 2]


def test_positions_off_a_board_that_does_not_wrap_are_errors():
    env = fg_env.load(_grid(), seed=1)
    seen = []
    result = env.run(lambda wake: seen.append(wake.call("step", {"to": [9, 9]})), rounds=1)
    assert not seen[0].ok and result.error is None  # an agent's move the rules did not bound is refused and undone
    assert any("position [9, 9] is off the 5x5 grid" in d["message"] for d in result.diagnostics)


def test_queries_find_who_is_where_in_creation_order():
    env = fg_env.load(_grid(neighborhood="moore"), seed=1)
    assert [e.id for e in _eval(env, "$at([2, 3])")] == ["b"]
    assert [e.id for e in _eval(env, "$near($a, 1)", a="a")] == ["b"]
    assert [e.id for e in _eval(env, "$near([2, 2], 1.5)")] == ["a", "b"]
    assert [e.id for e in _eval(env, "$near([2, 2], 2)")] == ["a", "b", "c", "d"]
    assert [e.id for e in _eval(env, "$near([2, 2], 3, rock)")] == ["c", "d"]
    assert _eval(env, "$nearest($a, rock)", a="a").id == "c"  # both 2 away: the first created
    assert _eval(env, "$nearest($a, rock, $it.at[0] > 0)", a="a").id == "d"
    assert _eval(env, "$nearest($a, rock, false)", a="a") is None
    assert len(_eval(env, "$empty()")) == 21 and [2, 2] not in _eval(env, "$empty(walker)")
    assert [0, 0] in _eval(env, "$empty(walker)")


def test_a_random_empty_cell_is_seeded_and_null_when_full():
    contract = _grid()
    contract["inputs"]["size"]["default"] = 2
    contract["entities"] = {"a": {"type": "walker", "at": [0, 0]}, "b": {"type": "walker", "at": [0, 1]},
                            "c": {"type": "rock", "at": [1, 0]}}
    env = fg_env.load(contract, seed=3)
    assert _eval(env, "$random_empty()") == [1, 1]
    assert _eval(env, "$random_empty(walker)") in ([1, 0], [1, 1])
    env.world.create("rock", "e", None, {}, [1, 1], env.world.scope(), "test")
    assert _eval(env, "$random_empty()") is None


CROWDED = {
    "name": "Crowded",
    "clock": {"rounds": 2},
    "space": {"grid": {"rows": 1, "cols": 3}, "capacity": {"walker": 1}},
    "types": {"walker": {"agent": True, "props": {}}, "rock": {"props": {}}},
    "entities": {"a": {"type": "walker", "at": [0, 0]}, "b": {"type": "walker", "at": [0, 1]},
                 "r": {"type": "rock", "at": [0, 1]}},
    "actions": {"shove": {"by": "walker", "do": [{"move": "$actor", "to": [0, 1]}]}},
    "events": [{"at": 2, "do": [{"create": "walker", "at": [0, 0]}]}],
}


def test_moving_into_a_full_cell_is_refused_and_changes_nothing():
    env = fg_env.load(CROWDED, seed=1)
    seen = {}

    def shove(wake):
        seen[wake.entity_id] = wake.call("shove", {})
        wake.end()

    env.run({"a": shove, "b": "idle"}, rounds=1)
    assert not seen["a"].ok and "is full: it holds 1 walker of 1" in seen["a"].text
    assert env.entity("a")["at"] == [0, 0]


def test_creating_into_a_full_cell_in_world_logic_fails_the_run_like_any_refused_effect():
    result = fg_env.load(CROWDED, seed=1).run("idle")
    assert result.status == "failed" and result.error.startswith("events[0].do: ")
    assert "cannot be placed" in result.error


def test_a_full_cell_at_build_is_an_error():
    contract = json.loads(json.dumps(CROWDED))
    contract["entities"]["c"] = {"type": "walker", "at": [0, 1]}
    with pytest.raises(fg_env.RunError, match="cannot be placed"):
        fg_env.load(contract, seed=1)


WANDER = {
    "name": "Wander",
    "clock": {"rounds": 8},
    "space": {"grid": {"rows": 6, "cols": 6, "neighborhood": "moore", "torus": True}, "capacity": 2},
    "types": {"ant": {"agent": True, "props": {"n": 0}}},
    "population": [{"type": "ant", "count": 12, "at": "[($i * 7) % 6, ($i * 5) % 6]"}],
    "actions": {
        "wander": {"by": "ant", "do": [
            {"move": "$actor", "to": "$choice($cells($actor))"},
            "$actor.n = $count($near($actor, 1))",
            {"if": "$chance(0.3)", "then": [{"fail": "stumbled"}]}]},
        "hatch": {"by": "ant", "when": "$count(ant) < 20",
                  "do": [{"create": "ant", "at": "$actor.at"},
                         {"if": "$chance(0.5)", "then": [{"remove": "$actor"}]}]}},
    "stages": [{"name": "go", "order": "random"}],
    "metrics": {"crowd": "$sum(ant, $len($at($it)))"},
}


def _scan(world, center, radius):
    geometry = world.space.geometry
    return [e.id for e in world.entities.values()
            if e.alive and e.location_id is not None and geometry.distance(center, e.location_id) <= radius]


def test_the_index_matches_a_full_scan_through_moves_refusals_removals_and_restores():
    env = fg_env.load(WANDER, seed=5)
    for _ in range(8):
        env.run("random", rounds=1)
        for row in range(6):
            for col in range(6):
                assert [e.id for e in _eval(env, f"$near([{row}, {col}], 1)")] == _scan(env.world, [row, col], 1)
        if not env.finished:
            env = fg_env.Env.restore(WANDER, json.loads(json.dumps(env.snapshot())))
    assert env.result().stats["rejected_actions"] > 0


def test_a_split_spatial_run_ends_exactly_like_a_straight_run():
    straight = fg_env.load(WANDER, seed=2).run("random").to_dict()
    env = fg_env.load(WANDER, seed=2)
    env.run("random", rounds=4)
    env = fg_env.Env.restore(WANDER, json.loads(json.dumps(env.snapshot())))
    assert env.run("random").to_dict() == straight


GRAPH = {
    "name": "Towns",
    "inputs": {"towns": {"type": "list", "default": ["a", "b", "c", "d"]}},
    "space": {"graph": {"nodes": "$inputs.towns",
                        "edges": [["a", "b"], {"from": "b", "to": "c", "weight": 2}, ["c", "d"]]}},
    "types": {"trader": {"props": {}}},
    "entities": {"t1": {"type": "trader", "at": "d"}, "t2": {"type": "trader", "at": "b"},
                 "t3": {"type": "trader", "at": "a"}},
}


def test_graph_queries_measure_path_length():
    env = fg_env.load(GRAPH, seed=1)
    assert _eval(env, "$cells(a, 3)") == ["b", "c"]
    assert [e.id for e in _eval(env, "$near(a, 1)")] == ["t2", "t3"]
    assert _eval(env, "$nearest($t3, trader)", t3="t3").id == "t2"
    assert _eval(env, "$distance(a, d)") == 4.0
    assert env.world.space.geometry.adjacent("b") == ["a", "c"]


def test_a_graph_edge_naming_a_place_the_built_nodes_lack_is_refused_at_the_edge():
    """The nodes come from an input, so only the build can tell the edge names a place that is not there."""
    with pytest.raises(fg_env.RunError, match=r"space\.graph\.edges\[2\]: 'd' is not a place \(places: a, b, c\).*"
                                              r"add it to the nodes or fix the edge"):
        fg_env.load(GRAPH, inputs={"towns": ["a", "b", "c"]}, seed=1)


PLANE = {
    "name": "Pond",
    "space": {"plane": {"width": 10, "height": 10, "torus": True}},
    "types": {"duck": {"props": {}}},
    "entities": {"d1": {"type": "duck", "at": [0.5, 0.5]}, "d2": {"type": "duck", "at": [9.5, 9.5]},
                 "d3": {"type": "duck", "at": [5, 5]}},
}


def test_plane_queries_use_straight_lines_round_a_torus():
    env = fg_env.load(PLANE, seed=1)
    assert [e.id for e in _eval(env, "$near($d1, 1.5)", d1="d1")] == ["d2"]
    assert _eval(env, "$nearest([6, 6], duck)").id == "d3"
    with pytest.raises(ExprError, match="a plane has no cells"):
        _eval(env, "$cells()")


def test_space_functions_without_a_space_say_what_to_declare():
    env = fg_env.load({"name": "Nowhere", "types": {"x": {"props": {}}}}, seed=1)
    with pytest.raises(ExprError, match=r"\$near needs a space"):
        _eval(env, "$near([0, 0], 1)")


def _errors(contract):
    return {i.path: i for i in fg_env.check(contract) if i.severity == "error"}


def test_check_reports_space_mistakes_with_fixes():
    contract = _grid(neighborhood="moor")
    contract["space"]["capacity"] = {"walkr": 1}
    contract["space"]["layers"] = {"scent": {"type": "number", "default": "$cell[0] + $nope"}}
    contract["events"] = [{"do": ["$world.x = $layer(sent, [0, 0]) + $count($empty(walkr))"]},
                          {"sync": True, "do": ["$world.x = 1"]}]
    contract["world"] = {"x": 0}
    errors = _errors(contract)
    assert "did you mean 'moore'?" in errors["space.grid.neighborhood"].fix
    assert "space.capacity.walkr" in errors
    assert "space.layers.scent.default" in errors
    messages = " ".join(i.message for i in fg_env.check(contract))
    assert "'sent' is not a declared layer" in messages and "$empty(walkr)" in messages
    assert "is not a field here" in errors["events[1].sync"].message


def test_check_reports_plane_capacity_and_layers():
    errors = _errors({**PLANE, "space": {"plane": {"width": 1, "height": 1}, "capacity": 1,
                                         "layers": {"x": {"default": 0}}}})
    assert "space.capacity" in errors and "space.layers.x" in errors
