"""Per-entity continuous dynamics and stochastic (Euler–Maruyama) terms."""
import copy
import json
import math
import statistics
import time

import fg_env


def ode(**spec):
    """The contract's physics: its one `dynamics` mechanism."""
    return {"mechanisms": {"physics": {"kind": "dynamics", "mode": "ode", **spec}}}


DECAY = {
    "name": "Decay",
    "clock": {"rounds": 5},
    "world": {"temperature": 2.0},
    "types": {"cell": {"props": {"load": 10.0, "k": 0.1, "hot": False}}},
    "entities": {"slow": {"type": "cell"}, "fast": {"type": "cell", "props": {"k": 0.5}}},
    **ode(substeps=20, per={"cell": {
        "vars": {"load": "-k * load"},
        "write": {"hot": "load > 5"},
    }}),
    "outputs": {"slow": "$entity(slow).load", "fast": "$entity(fast).load"},
}


def _load(contract, seed=1):
    return fg_env.load(contract, seed=seed)


def _errors(contract):
    return [(i.path, i.message) for i in fg_env.check(contract) if i.severity == "error"]


def test_every_entity_integrates_its_own_equation_from_its_own_props():
    result = _load(DECAY).run(rounds=3)
    assert math.isclose(result.outputs["slow"], 10 * math.exp(-0.3), rel_tol=1e-8)  # RK4 truncation error
    assert math.isclose(result.outputs["fast"], 10 * math.exp(-1.5), rel_tol=1e-8)


def test_writes_turn_math_into_other_props_after_each_step():
    env = _load(DECAY)
    env.run(rounds=2)
    assert env.entity("slow")["props"]["hot"] is True
    assert env.entity("fast")["props"]["hot"] is False


def test_reads_world_values_and_world_physics_per_entity():
    contract = copy.deepcopy(DECAY)
    contract["mechanisms"]["physics"]["vars"] = {"heat": {"start": 1, "rate": "0"}}
    contract["mechanisms"]["physics"]["per"]["cell"] = {"params": {"scale": "$world.temperature"},
                                          "read": {"boost": "$it.k * 10"},
                                          "vars": {"load": "scale * heat * boost"}}
    result = _load(contract).run(rounds=1)
    assert math.isclose(result.outputs["slow"], 10 + 2 * 1 * 1, rel_tol=1e-9)
    assert math.isclose(result.outputs["fast"], 10 + 2 * 1 * 5, rel_tol=1e-9)


def test_where_limits_which_entities_integrate_this_step():
    contract = copy.deepcopy(DECAY)
    contract["mechanisms"]["physics"]["per"]["cell"]["where"] = "$it.k > 0.2"
    result = _load(contract).run(rounds=2)
    assert result.outputs["slow"] == 10
    assert result.outputs["fast"] < 10


def test_entities_created_mid_run_join_and_removed_ones_stop():
    contract = copy.deepcopy(DECAY)
    contract["events"] = [{"at": 2, "do": [{"create": "cell", "id": "late"}, {"remove": "$entity(fast)"}]}]
    contract["outputs"]["late"] = "$entity(late).load"
    env = _load(contract)
    result = env.run(rounds=3)
    frozen = _load(DECAY).run(rounds=1).outputs["fast"]
    assert math.isclose(result.outputs["fast"], frozen, rel_tol=1e-12)  # removed at the start of round 2
    assert math.isclose(result.outputs["late"], 10 * math.exp(-0.2), rel_tol=1e-9)  # stepped in rounds 2 and 3


BROWNIAN = {
    "name": "Brownian particles",
    "clock": {"rounds": 4},
    "types": {"particle": {"props": {"x": 0.0, "floor": 0.0}}},
    "population": [{"type": "particle", "count": 2000}],
    **ode(substeps=4, params={"sigma": 0.5}, per={"particle": {"vars": {"x": {"rate": "0", "noise": "sigma"}}}}),
    "outputs": {"var": "$variance($map(particle, $it.x))"},
}


def test_noise_is_a_wiener_increment_drawn_from_the_seed():
    first = _load(BROWNIAN, seed=3).run()
    again = _load(BROWNIAN, seed=3).run()
    other = _load(BROWNIAN, seed=4).run()
    assert first.outputs == again.outputs != other.outputs
    xs = [e["props"]["x"] for e in _load(BROWNIAN, seed=3).entities("particle")]
    assert xs == [0.0] * 2000
    env = _load(BROWNIAN, seed=3)
    env.run()
    xs = [e["props"]["x"] for e in env.entities("particle")]
    assert math.isclose(statistics.pvariance(xs), 0.25 * 4, rel_tol=0.1)  # sigma² · t
    assert abs(statistics.fmean(xs)) < 0.1


def test_bounds_hold_at_every_sub_step_of_a_noisy_variable():
    contract = copy.deepcopy(BROWNIAN)
    contract["types"]["particle"]["props"]["x"] = {"default": 0.0, "min": 0}
    contract["population"][0]["count"] = 200
    env = _load(contract, seed=1)
    env.run()
    xs = [e["props"]["x"] for e in env.entities("particle")]
    assert min(xs) >= 0 and statistics.fmean(xs) > 0.3


def test_world_variables_take_noise_too():
    contract = {"name": "Walk", "clock": {"rounds": 3}, "types": {"a": {"agent": True}},
                "actions": {"wait": {"by": "a", "do": []}},
                **ode(vars={"price": {"start": 100, "rate": "0.01 * price", "noise": "0.2 * price", "min": 0}}),
                "outputs": {"price": "$physics.price"}}
    runs = [_load(contract, seed=s).run().outputs["price"] for s in (1, 1, 2)]
    assert runs[0] == runs[1] != runs[2]
    assert _errors({**contract, **ode(vars={"p": {"start": 1, "noise": "1"}}), "outputs": {}}) == [
        ("mechanisms.physics.vars.p.noise", "noise needs a rate")]


def test_noise_never_shifts_any_other_random_draw():
    base = {"name": "Draws", "clock": {"rounds": 3}, "world": {"hits": 0},
            "types": {"particle": {"props": {"x": 0.0}}}, "population": [{"type": "particle", "count": 5}],
            "events": [{"do": ["$world.hits += $uniform(0, 1)"]}], "outputs": {"hits": "$world.hits"}}
    noisy = {**base, **ode(per={"particle": {"vars": {"x": {"rate": "0", "noise": "1"}}}})}
    assert _load(base).run().outputs == _load(noisy).run().outputs


def test_a_run_split_by_a_snapshot_ends_exactly_like_a_straight_run():
    contract = copy.deepcopy(BROWNIAN)
    contract["population"][0]["count"] = 50
    contract["mechanisms"]["physics"]["vars"] = {"drift": {"start": 0, "rate": "1", "noise": "0.3"}}
    contract["mechanisms"]["physics"]["per"]["particle"]["vars"]["x"]["rate"] = "drift * 0.01"
    straight = _load(contract, seed=9).run().to_dict()
    env = _load(contract, seed=9)
    env.run(rounds=2)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def test_the_checker_names_every_problem_with_per_entity_dynamics():
    contract = copy.deepcopy(DECAY)
    contract["types"]["cell"]["props"].update({"label": "x", "note": "y", "count": {"type": "int", "default": 0},
                                               "t": 1})
    contract["types"]["hot_cell"] = {"extends": "cell"}
    contract["mechanisms"]["physics"]["params"] = {"scale": 1}
    contract["mechanisms"]["physics"]["per"] = {
        "cell": {"vars": {"load": "-k * load", "label": "1", "missing": "1", "count": "1"},
                 "params": {"pi": 3, "scale": 2}, "write": {"load": "1", "note": "2"}, "read": {"heat": "$it.nope"}},
        "hot_cell": {"vars": {"load": "unknown_name"}},
        "ghost": {"vars": {}},
    }
    found = _errors(contract)
    expected = [
        ("mechanisms.physics.per.cell",
         "'scale' is both a world physics name and a param, so the math cannot tell them apart"),
        ("mechanisms.physics.per.cell", "'pi' is a math function, constant or the time t"),
        ("mechanisms.physics.per.cell.vars.label", "'label' is a text property; integrated variables are numbers"),
        ("mechanisms.physics.per.cell.vars.missing", "'cell' has no property 'missing'"),
        ("mechanisms.physics.per.cell.vars.count", "'count' is a int property; integrated variables are numbers"),
        ("mechanisms.physics.per.cell.write.load", "'load' is integrated, so a write would overwrite it"),
        ("mechanisms.physics.per.cell.write.note", "'note' is a text property; physics math gives numbers"),
        ("mechanisms.physics.per.hot_cell.vars.load",
         "is also integrated by mechanisms.physics.per.cell, which covers every hot_cell"),
        ("mechanisms.physics.per.ghost", "'ghost' is not a declared type"),
    ]
    for issue in expected:
        assert issue in found, (issue, found)
    assert any(path == "mechanisms.physics.per.cell.read.heat" for path, _ in found)
    assert any(path == "mechanisms.physics.per.hot_cell.vars.load.rate" and "unknown_name" in message
               for path, message in found)
    assert _errors(DECAY) == []


def test_thousands_of_entities_step_in_reasonable_time():
    contract = copy.deepcopy(DECAY)
    contract["entities"] = {}
    contract["population"] = [{"type": "cell", "count": 5000, "props": {"k": "$uniform(0.1, 0.5)"}}]
    contract["mechanisms"]["physics"]["substeps"] = 4
    contract["outputs"] = {"mean": "$avg(cell, $it.load)"}
    env = _load(contract)
    started = time.perf_counter()
    result = env.run(rounds=2)
    elapsed = time.perf_counter() - started
    assert 10 * math.exp(-1.0) < result.outputs["mean"] < 10 * math.exp(-0.2)
    assert elapsed < 10  # 10,000 entity steps × 4 RK4 sub-steps; well under a second on a laptop


def test_independent_noise_survives_unrelated_entities_and_variable_reordering():
    contract = {
        "name": "Independent stochastic coordinates",
        "clock": {"rounds": 3},
        "types": {"particle": {"props": {"x": 0.0, "y": 0.0}}},
        "entities": {"a": {"type": "particle"}, "b": {"type": "particle"}},
        **ode(per={"particle": {"vars": {"x": {"rate": "0", "noise": "1"}, "y": {"rate": "0", "noise": "2"}}}}),
        "outputs": {"a": "$entity(a).x", "b": "$entity(b).y"},
    }
    expected = fg_env.load(contract, seed=42).run().outputs
    variant = copy.deepcopy(contract)
    variant["entities"] = {"unrelated": {"type": "particle"},
                           **dict(reversed(list(variant["entities"].items())))}
    variables = variant["mechanisms"]["physics"]["per"]["particle"]["vars"]
    variant["mechanisms"]["physics"]["per"]["particle"]["vars"] = dict(reversed(list(variables.items())))
    assert fg_env.load(variant, seed=42).run().outputs == expected
