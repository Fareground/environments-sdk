"""Relation attributes: typed link fields, created, read, updated, generated, removed and restored."""
import copy
import json

import fg_env

TOWN = {
    "name": "Trust network",
    "clock": {"rounds": 3},
    "inputs": {"edges": {"type": "table", "default": [
        {"from": "ana", "to": "ben", "value": 0.4, "channel": "work", "comment": "not a field"}]}},
    "types": {"person": {"agent": True, "props": {"age": 30}}},
    "entities": {"ana": {"type": "person", "name": "Ana", "props": {"age": 40}},
                 "ben": {"type": "person", "name": "Ben"},
                 "cy": {"type": "person", "name": "Cy"}},
    "relations": {
        "trusts": {"default": 0.5, "min": 0, "max": 1, "props": {
            "since": {"type": "int", "default": "$round"},
            "channel": {"type": "enum", "values": ["family", "work", "online"], "default": "online"},
            "gap": {"type": "number", "default": "$from.age - $to.age"},
            "note": {"type": "text", "default": ""}}},
        "knows": {"symmetric": True, "props": {"met": {"type": "text", "default": "school"}}},
        "plain": {},
    },
    "links": [{"relation": "trusts", "rows": "$inputs.edges"},
              {"relation": "knows", "among": "person", "graph": "complete",
               "props": {"met": "{$from.name} & {$to.name}"}}],
    "actions": {
        "befriend": {"by": "person", "params": {"who": {"type": "entity", "of": "person"}, "note": "text"},
                     "do": [{"link": "trusts", "from": "$actor", "to": "$params.who",
                             "props": {"note": "$params.note"}}]},
        "bump": {"by": "person", "params": {"who": {"type": "entity", "of": "person",
                                                    "where": "$link($actor, $it, trusts) != null"}},
                 "do": ["$link($actor, $params.who, trusts).value += 0.3",
                        "$link($actor, $params.who, trusts).channel = family"]},
        "bump_then_fail": {"by": "person", "params": {"who": {"type": "entity", "of": "person",
                                                              "where": "$link($actor, $it, trusts) != null"}},
                           "do": ["$link($actor, $params.who, trusts).channel = work",
                                  {"unlink": "trusts", "from": "$actor", "to": "$params.who"}, {"fail": "no"}]},
        "wait": {"by": "person", "do": []},
    },
    "views": {"trust": {"for": "person", "of": "$links($actor, trusts)",
                        "show": "{target.name} via {channel} since {since}"}},
    "outputs": {"ana_ben": "$link(ana, ben, trusts).value", "channel": "$link(ana, ben, trusts).channel"},
}


def _errors(contract):
    return [(i.path, i.message) for i in fg_env.check(contract) if i.severity == "error"]


def _link(env, a, b, kind):
    return env.world.link_view(a, b, kind).as_dict()


def test_links_carry_typed_fields_with_defaults_over_from_and_to():
    assert _errors(TOWN) == []
    env = fg_env.load(TOWN, seed=1)
    assert _link(env, "ana", "ben", "trusts") == {
        "source": "ana", "target": "ben", "kind": "trusts", "value": 0.4,
        "since": 0, "channel": "work", "gap": 10,
        "note": ""}  # the row's `channel` column fills that field; `comment` is not a field
    assert _link(env, "cy", "ana", "knows")["met"] == "Ana & Cy"  # symmetric: one link, ends sorted by id
    assert env.world.link_view("ana", "cy", "knows") == env.world.link_view("cy", "ana", "knows")


def test_the_link_effect_creates_with_defaults_and_updates_only_what_it_names():
    env = fg_env.load(TOWN, seed=1)

    def play(wake):
        if wake.round == 1 and wake.entity_id == "ben":
            assert wake.call("befriend", {"who": "cy", "note": "met at the fair"}).ok
        if wake.round == 2 and wake.entity_id == "ana":
            assert wake.call("befriend", {"who": "ben", "note": "again"}).ok
        wake.end()

    env.run(play, rounds=2)
    made = _link(env, "ben", "cy", "trusts")
    # relation default value
    assert (made["value"], made["since"], made["channel"], made["gap"]) == (0.5, 1, "online", 0)
    assert isinstance(made["note"], fg_env.expr.Untrusted)
    kept = _link(env, "ana", "ben", "trusts")
    assert (kept["value"], kept["since"], kept["channel"], kept["note"]) == (0.4, 0, "work", "again")


def test_assignments_update_link_values_and_fields_and_roll_back_with_the_action():
    env = fg_env.load(TOWN, seed=1)
    outcomes = []

    def play(wake):
        if wake.entity_id == "ana" and wake.round == 1:
            outcomes.append(wake.call("bump", {"who": "ben"}).ok)
            outcomes.append(wake.call("bump_then_fail", {"who": "ben"}).ok)
        wake.end()

    result = env.run(play, rounds=1)
    assert outcomes == [True, False]
    assert result.outputs == {"ana_ben": 0.7, "channel": "family"}  # the failed action's change and unlink are undone
    assert set(_link(env, "ana", "ben", "trusts")) >= {"since", "gap", "note"}


def test_link_errors_say_what_to_fix():
    contract = copy.deepcopy(TOWN)
    contract["actions"]["bad"] = {"by": "person", "do": ["$link(ana, ben, trusts).source = $entity(cy)"]}
    contract["actions"]["worse"] = {"by": "person", "do": ["$link(ana, ben, trusts).mood = 1"]}
    contract["actions"]["gone"] = {"by": "person", "do": ["$x = $link(ana, cy, trusts).value"]}
    for action, message in (("bad", "a link's `source` cannot be assigned"), ("worse", "has no field 'mood'"),
                            ("gone", "cannot read '.value' of null")):
        env = fg_env.load(contract, seed=1)

        def play(wake, action=action):
            wake.call(action, {})
            wake.end()

        result = env.run(play, rounds=1)
        assert result.error is None, action  # an agent's action whose rule fails is refused; the run goes on
        assert any(message in d["message"] for d in result.diagnostics), (action, result.diagnostics)


def test_links_lists_outgoing_links_for_views_and_rules():
    env = fg_env.load(TOWN, seed=1)
    preview = env.preview("ana")
    assert "- Ben via work since 0" in preview["update"]
    world = env.world
    assert [str(link) for link in world.links_of("ben", "trusts")] == []  # directed: ana → ben is not ben's
    assert len(world.links_of("ben", "knows")) == 2
    scope = world.scope()
    count = fg_env.expr.compile_expr("$count($links(ana, knows, $it.met != 'x'))")(scope)
    assert count == 2


def test_unlink_removes_fields_and_a_new_link_starts_from_defaults():
    contract = copy.deepcopy(TOWN)
    contract["events"] = [{"at": 1, "do": [{"unlink": "trusts", "from": "ana", "to": "ben"}]},
                          {"at": 2, "do": [{"link": "trusts", "from": "ana", "to": "ben"}]}]
    env = fg_env.load(contract, seed=1)
    env.run("idle", rounds=1)
    assert env.world.link_view("ana", "ben", "trusts") is None and ("ana", "ben") not in env.world.link_fields["trusts"]
    env.run("idle", rounds=1)
    assert (_link(env, "ana", "ben", "trusts")["channel"] == "online" and _link(env, "ana", "ben", "trusts")["since"]
            == 2)


def test_a_run_split_by_a_snapshot_keeps_every_link_field_and_its_provenance():
    def play(wake):
        wake.call("befriend", {"who": "cy" if wake.entity_id != "cy" else "ana", "note": f"round {wake.round}"})
        wake.end()

    straight = fg_env.load(TOWN, seed=5).run(play).to_dict()
    env = fg_env.load(TOWN, seed=5)
    env.run(play, rounds=1)
    restored = fg_env.Env.restore(TOWN, json.loads(json.dumps(env.snapshot())))
    assert isinstance(restored.world.link_view("ana", "cy", "trusts").expr_attr("note", None), fg_env.expr.Untrusted)
    assert restored.run(play).to_dict() == straight


def test_generated_graphs_fill_fields_per_pair():
    contract = copy.deepcopy(TOWN)
    contract["links"] = [{"relation": "trusts", "among": "person", "graph": "random", "p": 1,
                          "props": {"gap": "$from.age - $to.age", "channel": "family"}}]
    env = fg_env.load(contract, seed=1)
    assert _link(env, "ana", "ben", "trusts")["gap"] == 10 and _link(env, "ben", "ana", "trusts")["gap"] == -10
    assert {_link(env, "cy", "ben", "trusts")["channel"]} == {"family"}


def test_the_checker_validates_link_field_names_types_and_uses():
    contract = copy.deepcopy(TOWN)
    contract["relations"]["trusts"]["props"]["value"] = 1
    contract["relations"]["trusts"]["props"]["mood"] = {"type": "enum", "default": "x"}
    contract["links"][1]["props"]["colour"] = "red"
    contract["actions"]["befriend"]["do"][0]["props"]["nope"] = 1
    contract["actions"]["plain"] = {"by": "person",
                                    "do": [{"link": "plain", "from": "ana", "to": "ben", "props": {"x": 1}}]}
    found = _errors(contract)
    for issue in [
        ("relations.trusts.props.value", "'value' is built into every link"),
        ("relations.trusts.props.mood", "an enum property needs `values`"),
        ("links[1].props.colour", "a knows link has no field 'colour'"),
        ("actions.befriend.do[0].props.nope", "a trusts link has no field 'nope'"),
        ("actions.plain.do[0].props", "relation 'plain' declares no link fields"),
    ]:
        assert issue in found, (issue, found)
