"""Reference solutions and representation invariance for coupled dynamics."""
import copy
import math

import pytest

import fg_env


def heat_contract():
    return {
        "name": "Conservative heat exchange",
        "clock": {"rounds": 1},
        "types": {"tank": {"props": {"temperature": 0.0}}},
        "entities": {"hot": {"type": "tank", "props": {"temperature": 100.0}},
                     "cold": {"type": "tank"}},
        "physics": {"substeps": 40, "per": {"tank": {
            "read": {"other": "$sum(tank, $it.temperature) - $it.temperature"},
            "vars": {"temperature": "other - temperature"}}}},
        "invariants": [{"expr": "$abs($sum(tank, $it.temperature) - 100) < 0.000001",
                        "why": "Heat must be conserved"}],
        "outputs": {"hot": "$entity(hot).temperature", "cold": "$entity(cold).temperature"},
    }


def test_coupled_entities_follow_reference_solution_in_either_declaration_order():
    contract = heat_contract()
    for _ in range(2):
        result = fg_env.load(contract).run()
        assert result.status == "completed"
        assert result.outputs["hot"] == pytest.approx(50 + 50 * math.exp(-2), rel=1e-7)
        assert result.outputs["cold"] == pytest.approx(50 - 50 * math.exp(-2), rel=1e-7)
        contract["entities"] = dict(reversed(list(contract["entities"].items())))


def test_world_and_entity_rates_see_the_same_intermediate_state():
    contract = heat_contract()
    del contract["entities"]["cold"]
    contract["physics"] = {
        "substeps": 40,
        "read": {"hot": "$entity(hot).temperature"},
        "vars": {"cold": {"start": 0, "rate": "hot - cold"}},
        "per": {"tank": {"vars": {"temperature": "cold - temperature"}}},
    }
    contract["invariants"] = [{"expr": "$abs($entity(hot).temperature + $physics.cold - 100) < 0.000001"}]
    contract["outputs"]["cold"] = "$physics.cold"
    result = fg_env.load(contract).run()
    assert result.status == "completed"
    assert result.outputs["hot"] == pytest.approx(50 + 50 * math.exp(-2), rel=1e-7)
    assert result.outputs["cold"] == pytest.approx(50 - 50 * math.exp(-2), rel=1e-7)


def test_coupling_across_types_and_cached_definitions_is_order_independent():
    contract = heat_contract()
    contract["types"]["other_tank"] = copy.deepcopy(contract["types"]["tank"])
    contract["entities"]["cold"]["type"] = "other_tank"
    contract["defs"] = {"total": "$entity(hot).temperature + $entity(cold).temperature"}
    dynamics = {"read": {"other": "$total - $it.temperature"}, "vars": {"temperature": "other-temperature"}}
    contract["physics"]["per"] = {"tank": dynamics, "other_tank": copy.deepcopy(dynamics)}
    contract["invariants"] = [{"expr": "$abs($total - 100) < 0.000001"}]
    first = fg_env.load(contract).run()
    contract["physics"]["per"] = dict(reversed(list(contract["physics"]["per"].items())))
    second = fg_env.load(contract).run()
    assert first.status == second.status == "completed"
    assert first.outputs == second.outputs
    assert first.outputs["hot"] == pytest.approx(50 + 50 * math.exp(-2), rel=1e-7)


@pytest.mark.parametrize("coupled", [False, True])
def test_later_entity_failure_restores_entire_physical_interval(coupled):
    contract = {
        "name": "Atomic physical state",
        "clock": {"rounds": 1},
        "types": {"cell": {"props": {"x": 1.0, "bad": 0.0}}},
        "entities": {"a": {"type": "cell"}, "b": {"type": "cell", "props": {"bad": 1.0}}},
        "physics": {"per": {"cell": {"vars": {"x": "1/(1-bad)"}}}},
    }
    if coupled:
        contract["physics"]["vars"] = {"elapsed": {"start": 0, "rate": "1"}}
    env = fg_env.load(contract)
    result = env.run()
    assert result.status == "failed"
    assert env.entity("a")["props"]["x"] == 1.0
    assert env.entity("b")["props"]["x"] == 1.0
    assert env.world.physics.time == 0
    if coupled:
        assert env.world.physics.values["elapsed"] == 0


def test_writeback_failure_restores_world_and_entities():
    contract = heat_contract()
    contract["world"] = {"ok": 0.0, "bad": 0.0}
    contract["physics"]["write"] = {"world.ok": "1", "world.bad": "1/0"}
    env = fg_env.load(contract)
    result = env.run()
    assert result.status == "failed"
    assert env.props["ok"] == env.props["bad"] == 0
    assert env.entity("hot")["props"]["temperature"] == 100
    assert env.entity("cold")["props"]["temperature"] == 0
    assert env.world.physics.time == 0
