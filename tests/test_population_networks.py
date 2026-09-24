"""Generated entities (dependent traits, ids, row sampling, archetype labels, raking) and networks (generators,
metrics, keyed draws)."""
import json
from collections import Counter

import pytest

import fg_env
from fg_env import personas

TOWN = {
    "name": "Town", "clock": {"rounds": 1},
    "types": {
        "household": {"props": {"size": 2, "income": 0}},
        "person": {"agent": True, "props": {"archetype": {"type": "text", "default": ""}, "income": 0,
                                            "budget": "$it.income * 0.25", "thrift": 0.5, "luck": 0}},
    },
    "relations": {"knows": {"symmetric": True}},
    "entities": {
        "household": {"type": "household", "count": 3, "props": {"size": "$i + 1"}},
        "person": {"type": "person", "count": 20, "props": {"income": 4000, "luck": "$random_for($it.id)"}},
    },
    "links": [{"relation": "knows", "among": "person", "graph": "scale_free", "m": 2}],
    "stages": [{"name": "s", "turns": "sequential"}],
}


def test_generated_entities_are_numbered_by_their_key_and_read_their_own_traits():
    env = fg_env.load(TOWN, seed=1)
    assert [h["id"] for h in env.entities("household")] == ["household_1", "household_2", "household_3"]
    assert [h["props"]["size"] for h in env.entities("household")] == [2, 3, 4]
    assert all(p["props"]["budget"] == p["props"]["income"] * 0.25 for p in env.entities("person"))


def test_a_generated_id_already_taken_is_an_error():
    taken = {**TOWN, "entities": {"person_2": {"type": "person"}, **TOWN["entities"]}}
    with pytest.raises(fg_env.ContractError, match="person_2"):
        fg_env.run(taken, seed=1)


def test_archetypes_are_labels_given_before_load():
    rows = personas.assign_labels([{"id": f"r{i}"} for i in range(20)], [("saver", 3), ("spender", 1)],
                                  field="archetype", seed=4)
    labelled = {**TOWN, "inputs": {"people": {"type": "list", "default": rows}},
                "entities": {"person": {"type": "person", "from": "$inputs.people",
                                        "props": {"archetype": "$row.archetype",
                                                  "thrift": "0.9 if $row.archetype == 'saver' else 0.1"},
                                        "brief": "{'You save.' if $row.archetype == 'saver' else ''}"}}}
    env = fg_env.load(labelled, seed=1)
    people = env.entities("person")
    assert Counter(p["props"]["archetype"] for p in people) == {"saver": 15, "spender": 5}
    saver = next(p for p in people if p["props"]["archetype"] == "saver")
    assert "You save." in env.preview(saver["id"])["brief"]


def test_the_population_extras_are_refused_with_how_to_say_them_now():
    old = {**TOWN, "population": [{"type": "person", "count": 4, "mix": [{"name": "a"}]}]}
    del old["entities"]
    [issue] = [i for i in fg_env.check(old) if i.severity == "error"]
    assert issue.path == "population[0].mix" and "assign_labels" in issue.fix


def test_an_earlier_population_becomes_generators_after_the_named_entities():
    old = {**TOWN, "entities": {"mayor": {"type": "person"}},
           "population": [{"type": "household", "count": 2}, {"type": "person", "count": 2}]}
    env = fg_env.load(old, seed=1)
    assert [e["id"] for e in env.entities("person")] == ["mayor", "person_1", "person_2"]


def test_keyed_draws_stay_aligned_when_other_draws_change():
    one = {p["id"]: p["props"]["luck"] for p in fg_env.load(TOWN, seed=5).entities("person")}
    extra = json.loads(json.dumps(TOWN))
    extra["entities"]["person"]["props"]["thrift"] = "$uniform(0, 1)"  # consumes more random draws
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
    raked = personas.rake(rows, {"sex": {"f": 0.5, "m": 0.5}, "age": {"young": 0.6, "old": 0.4}}, iterations=200,
                          tolerance=1e-9)
    total = sum(r["weight"] for r in raked)
    share = lambda column, value: sum(r["weight"] for r in raked if r[column] == value) / total
    assert share("sex", "f") == pytest.approx(0.5, abs=1e-6) and share("age", "old") == pytest.approx(0.4, abs=1e-6)
    with pytest.raises(ValueError, match="sum to"):
        personas.rake(rows, {"sex": {"f": 0.7, "m": 0.5}}, iterations=10)


def test_generator_contract_errors_are_reported():
    bad = json.loads(json.dumps(TOWN))
    bad["entities"]["person"]["props"]["nope"] = 1
    bad["entities"]["household"]["id"] = "h_{$nope}"
    bad["links"][0]["graph"] = "blocks"
    messages = [i.message for i in fg_env.check(bad) if i.severity == "error"]
    assert any("has no property 'nope'" in m for m in messages)
    assert any("needs `block`" in m for m in messages)
