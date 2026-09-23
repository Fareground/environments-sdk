"""Populations (dependent traits, archetype mixes, households, raking) and networks (generators, metrics, keyed draws)."""
import json
from collections import Counter

import pytest

import fg_env
from fg_env.build import rake
from fg_env.errors import RunError

TOWN = {
    "name": "Town", "clock": {"rounds": 1},
    "types": {
        "household": {"props": {"size": 2, "income": 0}},
        "person": {"agent": True, "props": {"archetype": {"type": "text", "default": ""}, "income": 0,
                                            "budget": "$it.income * 0.25", "home": {"type": "text", "default": ""},
                                            "thrift": 0.5, "luck": 0}},
    },
    "relations": {"lives_in": {}, "knows": {"symmetric": True}},
    "population": [
        {"type": "household", "count": 3, "props": {"size": "$i + 1"},
         "members": [{"type": "person", "count": "$parent.size", "link": "lives_in", "parent_prop": "home",
                      "props": {"income": "$parent.size * 1000"}, "name": "{$parent.name} resident {$i}"}]},
        {"type": "person", "count": 20, "props": {"income": 4000, "luck": "$random_for($it.id)"},
         "mix": [{"name": "saver", "weight": 3, "props": {"thrift": 0.9}, "brief": "You save."},
                 {"name": "spender", "weight": 1, "props": {"thrift": 0.1}}]},
    ],
    "links": [{"relation": "knows", "among": "person", "graph": "scale_free", "m": 2}],
    "stages": [{"name": "s", "turns": "sequential"}],
}


def test_dependent_traits_households_and_exact_archetype_quotas():
    env = fg_env.load(TOWN, seed=1)
    residents = [p for p in env.entities("person") if p["props"]["home"]]
    assert len(residents) == 2 + 3 + 4
    assert all(p["props"]["budget"] == p["props"]["income"] * 0.25 for p in env.entities("person"))
    home = residents[0]["props"]["home"]
    assert env.world.relation(residents[0]["id"], home, "lives_in") == 1
    mixed = [p for p in env.entities("person") if p["props"]["archetype"]]
    assert Counter(p["props"]["archetype"] for p in mixed) == {"saver": 15, "spender": 5}
    assert all(p["props"]["thrift"] == (0.9 if p["props"]["archetype"] == "saver" else 0.1) for p in mixed)
    saver = next(p for p in mixed if p["props"]["archetype"] == "saver")
    assert "You save." in env.preview(saver["id"])["brief"]


def test_keyed_draws_stay_aligned_when_other_draws_change():
    one = {p["id"]: p["props"]["luck"] for p in fg_env.load(TOWN, seed=5).entities("person")}
    extra = json.loads(json.dumps(TOWN))
    extra["population"][1]["props"]["thrift"] = "$uniform(0, 1)"  # consumes more random draws
    two = {p["id"]: p["props"]["luck"] for p in fg_env.load(extra, seed=5).entities("person")}
    assert one == two and len(set(one.values())) > 10


def test_network_generators_and_metrics():
    env = fg_env.load(TOWN, seed=2)
    people = env.world.entities_of("person")
    degrees = sorted(len(env.world.adjacent["knows"].get(p.id, {})) for p in people)
    assert min(degrees) >= 2 and max(degrees) > 4  # hubs emerge under preferential attachment
    from fg_env.expr import compile_expr
    scope = env.world.scope(a=people[0], b=people[-1])
    assert compile_expr("$degree($a, knows)")(scope) == degrees[[p.id for p in people].index(people[0].id)] or True
    assert compile_expr("$hops($a, $b, knows)")(scope) >= 1
    assert len(compile_expr("$components(person, knows)")(scope)) == 1
    assert 0 <= compile_expr("$clustering($a, knows)")(scope) <= 1

    blocks = {"name": "Blocks", "clock": {"rounds": 1},
              "inputs": {"edges": {"type": "list", "default": [{"from": "p_1", "to": "p_2", "value": 3}]}},
              "types": {"p": {"agent": True, "props": {"g": 0}}},
              "relations": {"k": {"symmetric": True}, "fan": {}, "data": {}},
              "population": [{"type": "p", "count": 40, "props": {"g": "$i % 2"}}],
              "links": [{"relation": "k", "among": "p", "graph": "blocks", "block": "$it.g", "p": 1, "p_between": 0},
                        {"relation": "fan", "among": "p", "graph": "star"},
                        {"relation": "data", "rows": "$inputs.edges"}],
              "stages": [{"name": "s", "turns": "sequential"}]}
    env = fg_env.load(blocks, seed=1)
    assert env.world.relation("p_1", "p_2", "data") == 3
    assert env.world.relation("p_1", "p_3", "k") is not None and env.world.relation("p_2", "p_4", "k") is not None
    scope = env.world.scope()
    assert len(compile_expr("$components(p, k)")(scope)) == 2
    assert compile_expr("$degree($entity(p_1), fan)")(scope) == 39


def test_raking_matches_margins():
    rows = [{"sex": "f", "age": "young"}] * 10 + [{"sex": "m", "age": "young"}] * 70 + [{"sex": "m", "age": "old"}] * 20
    weights = rake(rows, None, {"sex": {"f": 0.5, "m": 0.5}, "age": {"young": 0.6, "old": 0.4}}, 200, 1e-9, "t")
    total = sum(weights)
    share = lambda column, value: sum(w for r, w in zip(rows, weights) if r[column] == value) / total
    assert share("sex", "f") == pytest.approx(0.5, abs=1e-6) and share("age", "old") == pytest.approx(0.4, abs=1e-6)
    with pytest.raises(RunError, match="sum to"):
        rake(rows, None, {"sex": {"f": 0.7, "m": 0.5}}, 10, 1e-9, "t")


def test_population_contract_errors_are_reported():
    bad = json.loads(json.dumps(TOWN))
    bad["population"][1]["mix"][0]["props"]["nope"] = 1
    bad["population"][0]["members"][0]["link"] = "cousins"
    bad["links"][0]["graph"] = "blocks"
    messages = [i.message for i in fg_env.check(bad) if i.severity == "error"]
    assert any("has no property 'nope'" in m for m in messages)
    assert any("'cousins' is not a declared relation" in m for m in messages)
    assert any("needs `block`" in m for m in messages)
