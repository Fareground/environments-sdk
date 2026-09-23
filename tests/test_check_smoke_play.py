"""`fg_env.check` plays what a real run plays: several rounds, every declared policy, and random agents that can
fill arguments whose choices depend on earlier arguments."""
import copy

import fg_env

SHOP = {
    "name": "Shop",
    "clock": {"rounds": 8, "unit": "week"},
    "world": {"rate": 0.0},
    "types": {"retailer": {"agent": True, "props": {"stock": 10}}},
    "entities": {"shop": {"type": "retailer"}},
    "actions": {"order": {"by": "retailer", "params": {"qty": {"type": "int", "min": 0, "max": 20}},
                          "do": ["$actor.stock += $params.qty"]}},
    "policies": {"steady": {"rules": [{"do": "order", "with": {"qty": 5}}]}},
    "outputs": {"stock": "$entity(shop).stock"},
}


def shop(**changes):
    contract = copy.deepcopy(SHOP)
    contract.update(changes)
    return contract


def errors(issues):
    return [i for i in issues if i.severity == "error"]


def test_a_clean_contract_stays_clean():
    assert fg_env.check(shop()) == []


def test_rules_that_break_when_an_agent_does_not_act_fail_the_check():
    """A turn can pass without an action (a timeout, a refusal): check plays one run where nobody acts."""
    pick = {"types": {"retailer": {"agent": True, "props": {"stock": 10, "plan": ""}}},
            "world": {"sizes": {"type": "map", "default": {"small": 5, "big": 20}}},
            "actions": {"plan": {"by": "retailer", "params": {"size": {"type": "enum", "values": ["small", "big"]}},
                                 "do": ["$actor.plan = $params.size"], "terminal": True}},
            "stages": [{"name": "plan", "turns": "simultaneous", "must_act": True}], "policies": {},
            "events": [{"phase": "end", "do": ["$entity(shop).stock += $world.sizes[$entity(shop).plan]"]}]}
    found = errors(fg_env.check(shop(**pick)))
    assert [i.path for i in found] == ["events[0].do[0]"]
    assert "agents that never act" in found[0].message and "default" in found[0].fix
    pick["types"]["retailer"]["props"]["plan"] = "small"  # a missed turn plans small
    assert errors(fg_env.check(shop(**pick))) == []


def test_a_failure_in_a_later_round_is_found():
    late = shop(events=[{"phase": "end", "do": ["$world.rate = 10 / (4 - $round)"]}])
    found = errors(fg_env.check(late))
    assert [i.path for i in found] == ["events[0].do[0]"]
    assert "division by zero" in found[0].message


def test_explicit_rounds_play_exactly_that_many():
    late = shop(events=[{"phase": "end", "do": ["$world.rate = 10 / (4 - $round)"]}])
    assert errors(fg_env.check(late, rounds=3)) == []
    assert errors(fg_env.check(late, rounds=0)) == []


def test_a_declared_policy_that_crashes_is_reported_with_its_name():
    broken = shop(policies={"steady": {"rules": [{"do": "order", "with": {"qty": "$actor.stock / ($actor.stock - 10)"}}]}})
    found = errors(fg_env.check(broken))
    assert [i.path for i in found] == ["policies.steady.rules[0]"]
    assert "division by zero" in found[0].message
    assert "policy 'steady'" in found[0].message


def test_a_type_default_policy_that_crashes_is_reported():
    broken = shop(policies={"steady": {"rules": [{"do": "order", "with": {"qty": "$actor.stock / ($actor.stock - 10)"}}]}})
    broken["types"]["retailer"]["policy"] = "steady"
    found = errors(fg_env.check(broken))
    assert [i.path for i in found] == ["policies.steady.rules[0]"]


def test_a_policy_whose_rule_is_always_refused_is_warned_about():
    greedy = shop(policies={"greedy": {"rules": [{"do": "order", "with": {"qty": 50}}]}})
    warned = [i for i in fg_env.check(greedy) if i.path == "policies.greedy.rules[0]"]
    assert len(warned) == 1 and warned[0].severity == "warning"
    assert "policy 'greedy'" in warned[0].message and "at most 20" in warned[0].message


TENDER = {
    "name": "Tender",
    "clock": {"rounds": 3},
    "world": {"accusations": 0},
    "types": {"bidder": {"agent": True}, "auditor": {"agent": True}},
    "entities": {"b1": {"type": "bidder"}, "b2": {"type": "bidder"}, "b3": {"type": "bidder"},
                 "aud": {"type": "auditor"}},
    "actions": {"accuse": {"by": "auditor",
                           "params": {"a": {"type": "entity", "of": "bidder"},
                                      "b": {"type": "entity", "of": "bidder", "where": "$it.id != $params.a.id"}},
                           "do": ["$world.accusations += 1"]}},
    "stages": [{"name": "audit", "actions": {"auditor": ["accuse"]}}],
    "outputs": {"accusations": "$world.accusations"},
}


def test_a_choice_that_depends_on_another_argument_lists_every_candidate():
    tools = {t["name"]: t for t in fg_env.load(TENDER).preview("aud")["tools"]}
    schema = tools["accuse"]["input_schema"]["properties"]["b"]
    assert schema["enum"] == ["b1", "b2", "b3"]


def test_a_combination_the_choice_rules_out_is_refused_with_the_valid_ones():
    env = fg_env.load(TENDER)
    said = []

    def auditor(wake):
        said.append(wake.call("accuse", {"a": "b1", "b": "b1"}).text)
        wake.end()

    env.run({"aud": auditor, "bidder": "idle"}, rounds=1)
    assert "given a" in said[0] and "valid: b2, b3" in said[0]


def test_random_agents_fill_dependent_choices():
    result = fg_env.run(TENDER, seed=3)
    assert result.outputs["accusations"] == 3
    assert result.stats.get("rejected_actions", 0) == 0
    report = fg_env.analysis.behavior_checks(TENDER, runs=1)
    assert ("action_never_taken", "actions.accuse") not in report.codes()


def test_random_agents_choose_among_more_candidates_than_a_tool_lists():
    crowd = {"name": "Gifts", "clock": {"rounds": 1},
             "types": {"p": {"agent": True, "props": {"gifts": 0}}},
             "population": [{"type": "p", "count": 80}],
             "actions": {"give": {"by": "p", "params": {"to": {"type": "entity", "of": "p", "where": "$it.id != $actor.id"}},
                                  "do": ["$params.to.gifts += 1"], "terminal": True}},
             "outputs": {"gifts": {"expr": "$sum(p, $it.gifts)", "type": "int"}}}
    result = fg_env.run(crowd, "random", seed=1)
    assert result.outputs["gifts"] == 80 and result.stats["invalid_calls"] == 0


def test_an_invariant_broken_at_build_names_its_path_once():
    broken = shop(invariants=[{"expr": "$entity(shop).stock > 100", "why": "Stock starts high."}])
    found = errors(fg_env.check(broken))
    assert [i.path for i in found] == ["invariants[0]"]
    assert found[0].message.startswith("invariant `$entity(shop).stock > 100` no longer holds after build")


def test_a_structural_error_points_to_the_guide_part_that_lists_its_fields():
    found = errors(fg_env.check(shop(stages=[{"actions": ["order"]}], records={"log": {"title": "Log"}})))
    by_path = {i.path: i.fix for i in found}
    assert by_path["stages[0].name"] == "see guide('stages')"
    assert by_path["records.log.title"].endswith("remove it here; see guide('records')")


def test_a_suggested_replacement_needs_no_guide_part():
    found = errors(fg_env.check(shop(stages=[{"name": "buy", "turns": "simultanous"}])))
    assert [i.fix for i in found] == ["did you mean 'simultaneous'?"]


def test_an_error_that_already_names_a_guide_part_is_not_given_a_second():
    found = errors(fg_env.check(shop(patterns={"demand": {}})))
    assert found and all(i.fix.count("guide(") == 1 for i in found)
