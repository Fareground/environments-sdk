"""Large coded crowds: type membership without scans, cheap invariants, choices without listing every entity."""
import json

import pytest

import fg_env
from fg_env import actions, expr
from fg_env.world import SdkWorld

HERD = {
    "name": "Herd",
    "clock": {"rounds": 6},
    "types": {"animal": {"props": {"energy": 3}},
              "sheep": {"extends": "animal"},
              "wolf": {"extends": "animal"},
              "keeper": {"agent": True, "props": {}}},
    "entities": {"k": {"type": "keeper"}},
    "population": [{"type": "sheep", "count": 5}, {"type": "wolf", "count": 2}, {"type": "sheep", "count": 3}],
    "actions": {"breed": {"by": "keeper", "do": [{"create": "sheep"}, {"create": "wolf"},
                                                {"if": "$chance(0.5)", "then": [{"fail": "The litter was lost."}]}]}},
    "events": [{"phase": "end", "each": "animal", "do": ["$it.energy -= $randint(0, 2)",
                                                        {"if": "$it.energy < 0", "then": [{"remove": "$it"}]}]},
               {"phase": "start", "every": 2, "do": [{"create": "sheep", "count": 2}]}],
    "metrics": {"animals": "$count(animal)", "picked": "$choice(animal).id if $count(animal) > 0 else null"},
    "outputs": {"animals": {"expr": "$metrics.animals", "type": "int"}},
}


def _scanned(world, type_name):
    kinds = set(world.contract.subtypes(type_name))
    return [e.id for e in world.entities.values() if e.alive and e.entity_type in kinds]


def test_type_members_match_a_full_scan_through_creations_removals_rollbacks_and_restores():
    env = fg_env.load(HERD, seed=4)
    for _ in range(6):
        env.run("random", rounds=1)
        for kind in ("animal", "sheep", "wolf"):
            assert [e.id for e in env.world.entities_of(kind)] == _scanned(env.world, kind)
        if not env.finished:
            env = fg_env.Env.restore(HERD, json.loads(json.dumps(env.snapshot())))
            assert [e.id for e in env.world.entities_of("animal")] == _scanned(env.world, "animal")
    assert env.result().stats["rejected_actions"] > 0  # some litters were rolled back


def test_a_split_run_ends_exactly_like_a_straight_run():
    straight = fg_env.load(HERD, seed=9).run("random").to_dict()
    env = fg_env.load(HERD, seed=9)
    env.run("random", rounds=3)
    env = fg_env.Env.restore(HERD, json.loads(json.dumps(env.snapshot())))
    assert env.run("random").to_dict() == straight


def test_choice_and_count_of_a_type_read_the_type_without_listing_it(monkeypatch):
    env = fg_env.load(HERD, seed=2)
    scope = env.world.scope()
    expected = [expr.evaluate("$choice($filter(animal, true)).id", scope) for _ in range(5)]
    env = fg_env.load(HERD, seed=2)

    def refuse(self, type_name):
        raise AssertionError("listed a whole type")

    monkeypatch.setattr(SdkWorld, "entities_of", refuse)
    scope = env.world.scope()
    assert [expr.evaluate("$choice(animal).id", scope) for _ in range(5)] == expected
    assert expr.evaluate("$count(animal)", scope) == 10


GUARDED = {
    "name": "Owners",
    "clock": {"rounds": 5},
    "types": {"owner": {"agent": True, "props": {"cash": 5}},
              "item": {"props": {"owner": {"type": "text", "default": ""}, "worth": 1, "tag": {"type": "any"}}}},
    "population": [{"type": "owner", "count": 4},
                   {"type": "item", "count": 30, "props": {"owner": "'owner_' + $text($randint(1, 4))",
                                                          "worth": "$randint(0, 3)"}}],
    "actions": {"sell": {"by": "owner",
                         "params": {"item": {"type": "entity", "of": "item",
                                             "where": "$it.owner == $actor.id and $chance(0.7) and $it.worth > 0"}},
                         "do": ["$params.item.owner = ''", "$actor.cash += $params.item.worth"]}},
    "policies": {"seller": {"rules": [{"do": "sell", "with": {
        "item": "$pick(item, $it.owner == $actor.id and $it.worth > $randint(0, 2))"}}]}},
    "events": [{"phase": "end", "each": "owner", "do": [
        "$it.cash += $count(item, $it.owner == $outer.id and $chance(0.5))",
        {"if": "$any(item, '' == $it.owner and $random() < 0.2)", "then": ["$it.cash -= 1"]}]}],
    "metrics": {"held": "$sum(owner, $count(item, $it.owner == $outer.id))"},
    "outputs": {"cash": {"expr": "$sum(owner, $it.cash)", "type": "number"}},
}


def _fingerprint(contract, participants):
    return fg_env.load(contract, seed=5).run(participants).to_dict()


@pytest.mark.parametrize("participants", ["random", "policy:seller"])
def test_equality_guards_skip_items_with_the_same_results_and_draws(monkeypatch, participants):
    guarded = _fingerprint(GUARDED, participants)
    monkeypatch.setattr(expr.EqualityGuard, "key", lambda self, scope: expr._NO_KEY)
    assert _fingerprint(GUARDED, participants) == guarded


def test_equality_guards_keep_errors_of_the_items_they_would_evaluate(monkeypatch):
    broken = {**GUARDED, "events": [{"phase": "end", "each": "owner",
                                     "do": ["$it.cash += $count(item, $it.owner == $outer.id and $it.tag.x > 0)"]}]}
    guarded = fg_env.load(broken, seed=5).run()
    assert guarded.status == "failed" and "cannot read '.x' of null" in guarded.error
    monkeypatch.setattr(expr.EqualityGuard, "key", lambda self, scope: expr._NO_KEY)
    assert fg_env.load(broken, seed=5).run().to_dict() == guarded.to_dict()


def _validation_listings(monkeypatch, contract):
    listings = {"n": 0}
    original = actions.ActionBook._choices

    def counting(self, actor, action, pname, param, params=None, first=False):
        listings["n"] += params is not None
        return original(self, actor, action, pname, param, params, first)

    monkeypatch.setattr(actions.ActionBook, "_choices", counting)
    result = _fingerprint(contract, "policy:seller")
    monkeypatch.undo()
    return result, listings["n"]


PLAIN_RULE = json.loads(json.dumps(GUARDED))
PLAIN_RULE["actions"]["sell"]["params"]["item"]["where"] = "$it.owner == $actor.id and $it.worth > 0"


def test_a_policy_choice_is_validated_without_listing_every_candidate(monkeypatch):
    result, listings = _validation_listings(monkeypatch, PLAIN_RULE)
    assert listings == 0 and result["stats"]["actions"] > 0
    monkeypatch.setattr(actions.ActionBook, "_chosen", lambda self, *args: None)
    assert _fingerprint(PLAIN_RULE, "policy:seller") == result


def test_a_choice_whose_rule_draws_randomness_is_decided_by_the_full_listing(monkeypatch):
    result, listings = _validation_listings(monkeypatch, GUARDED)
    assert listings > 0
    monkeypatch.setattr(actions.ActionBook, "_chosen", lambda self, *args: None)
    assert _fingerprint(GUARDED, "policy:seller") == result


def _choices_of(where):
    contract = {"name": "Teams", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"team": "red", "rival": ""}}},
                "entities": {"a": {"type": "p", "props": {"rival": "c"}}, "b": {"type": "p"},
                             "c": {"type": "p", "props": {"team": "blue"}}, "d": {"type": "p", "props": {"team": "blue"}}},
                "actions": {"pick": {"by": "p", "params": {"who": {"type": "entity", "of": "p", "where": where}},
                                     "do": []}}}
    tools = {t["name"]: t for t in fg_env.load(contract).preview("a")["tools"]}
    return tools["pick"]["input_schema"]["properties"]["who"]["enum"]


def test_a_choice_that_only_excludes_by_inequality_lists_exactly_what_its_rule_allows():
    assert _choices_of("$it.id != $actor.id") == ["b", "c", "d"]
    assert _choices_of("$it.team != $actor.team") == ["c", "d"]
    assert _choices_of("$actor.rival != $it") == ["a", "b", "d"]
    assert _choices_of("$it.team != 'red' or $it.id == 'a'") == ["a", "c", "d"]


BALANCE = {
    "name": "Balance",
    "clock": {"rounds": 3},
    "world": {"a": 5, "b": 5},
    "types": {"clerk": {"props": {}}},
    "events": [{"phase": "start", "do": ["$world.a -= 1"]}, {"phase": "end", "do": ["$world.b -= 1"]}],
    "invariants": [{"expr": "$world.a == $world.b", "why": "the books balance"}],
    "outputs": {"a": {"expr": "$world.a", "type": "int"}},
}


def test_an_action_invariant_fails_the_moment_a_change_breaks_it():
    result = fg_env.load(BALANCE, seed=1).run()
    assert result.status == "failed" and "after events[0].do" in result.error and "the books balance" in result.error


#: Clerks take turns lending and repaying: the books are off after every lender and balance again after the
#: borrower who follows, so they balance whenever the whole event has run.
LENDING = {
    "name": "Lending",
    "clock": {"rounds": 2},
    "world": {"owed": 0},
    "types": {"clerk": {"props": {"seat": 0}}},
    "population": [{"type": "clerk", "count": 4, "props": {"seat": "$i"}}],
    "events": [{"phase": "end", "each": "clerk", "order": "$it.seat",
                "do": [{"if": "$it.seat % 2 == 0", "then": ["$world.owed += 1"], "else": ["$world.owed -= 1"]}]}],
    "invariants": [{"expr": "$world.owed == 0", "why": "the books balance"}],
    "outputs": {"owed": {"expr": "$world.owed", "type": "int"}},
}


def test_an_each_event_is_checked_once_after_its_last_item():
    assert fg_env.load(LENDING, seed=1).run().status == "completed"


def test_an_each_event_that_leaves_an_invariant_broken_fails_the_run_naming_the_event():
    contract = json.loads(json.dumps(LENDING))
    contract["population"][0]["count"] = 3
    result = fg_env.load(contract, seed=1).run()
    assert result.status == "failed" and "after events[0].do" in result.error and "the books balance" in result.error


def test_a_round_invariant_only_needs_to_hold_when_the_round_ends():
    contract = json.loads(json.dumps(BALANCE))
    contract["invariants"][0]["check"] = "round"
    assert fg_env.run(contract, seed=1).status == "completed"


def test_an_end_invariant_is_checked_once_when_the_run_finishes():
    contract = json.loads(json.dumps(BALANCE))
    contract["invariants"] = [{"expr": "$world.a > 2", "check": "end"}]
    failed = fg_env.load(contract, seed=1).run()
    assert failed.status == "failed" and "after the run" in failed.error
    contract["clock"]["rounds"] = 2
    assert fg_env.run(contract, seed=1).status == "completed"


def test_an_unknown_invariant_check_is_reported_with_a_fix():
    contract = json.loads(json.dumps(BALANCE))
    contract["invariants"][0]["check"] = "rounds"
    issues = [i for i in fg_env.check(contract) if i.path == "invariants[0].check"]
    assert issues and "did you mean 'round'?" in issues[0].fix


def test_an_each_event_with_a_trigger_is_still_checked_once_after_its_last_item():
    contract = json.loads(json.dumps(LENDING))
    contract["triggers"] = [{"when": "$world.owed > 5", "do": ["$world.owed = 0"]}]
    assert fg_env.load(contract, seed=1).run().status == "completed"


def _lending_with(hook):
    contract = json.loads(json.dumps(LENDING))
    contract["world"]["alarms"] = 0
    contract["types"]["boss"] = {"agent": True, "props": {}}
    contract["entities"] = {"boss": {"type": "boss"}}
    contract["actions"] = {"wait": {"by": "boss", "terminal": True}}
    contract.update(hook)
    return contract


def test_an_each_item_that_breaks_an_invariant_fails_the_run_before_a_trigger_acts_on_it():
    contract = _lending_with({"triggers": [{"when": "$world.owed != 0", "do": ["$world.alarms += 1"]}]})
    env = fg_env.load(contract, seed=1)
    result = env.run("idle")
    assert result.status == "failed" and "after events[0].do" in result.error and "the books balance" in result.error
    assert env.props["alarms"] == 0


def test_an_each_item_that_breaks_an_invariant_fails_the_run_before_an_agent_reacts_to_it():
    contract = _lending_with({})
    contract["events"][0]["do"][0]["else"].append({"wake": "$entity(boss)", "now": True, "why": "A loan went out."})
    woken = []
    result = fg_env.load(contract, seed=1).run(lambda wake: woken.append(wake.reason) or wake.call("wait"))
    assert result.status == "failed" and "the books balance" in result.error
    assert "A loan went out." not in woken


#: Everyone may hold at most 5: an `$all` over one type that reads only each member's own properties is re-checked
#: after an action only for the entities the action changed, so a crowd's round costs the same per turn at any size.
CAPPED = {
    "name": "Capped crowd",
    "clock": {"rounds": 1},
    "world": {"cap": 5},
    "types": {"p": {"agent": True, "props": {"c": 0, "rival": {"type": "text", "default": "p_1"}}},
              "vip": {"extends": "p"}},
    "population": [{"type": "p", "count": 30}],
    "entities": {"v": {"type": "vip"}},
    "invariants": [{"expr": "$all(p, $it.c <= 5)", "why": "nobody may hold more than 5"}],
    "actions": {
        "give": {"by": "p", "params": {"to": {"type": "entity", "of": "p"}, "n": {"type": "int", "min": 1, "max": 9}},
                 "do": "$params.to.c += $params.n"},
        "spawn": {"by": "p", "params": {"n": {"type": "int", "min": 0, "max": 9}},
                  "do": {"create": "vip", "props": {"c": "$params.n"}}},
        "cap": {"by": "p", "params": {"n": {"type": "int", "min": 0, "max": 9}}, "do": "$world.cap = $params.n"},
    },
    "stages": [{"name": "play", "max_actions": 3}],
}


def _refusals(invariant, *calls):
    contract = json.loads(json.dumps(CAPPED))
    contract["invariants"][0]["expr"] = invariant
    seen = []

    def play(wake):
        for name, args in calls:
            seen.append(wake.call(name, args))
        wake.call("end_turn")
    env = fg_env.load(contract, seed=1)
    result = env.run({"p_2": play, "*": "idle"})
    assert result.status == "completed", result.error
    return [not outcome.ok and "nobody may hold more than 5" in outcome.text for outcome in seen], env


def test_an_action_that_breaks_an_all_invariant_on_another_entity_is_refused():
    refused, env = _refusals("$all(p, $it.c <= 5)", ("give", {"to": "p_7", "n": 4}), ("give", {"to": "p_7", "n": 2}),
                             ("give", {"to": "v", "n": 6}))
    assert refused == [False, True, True]
    assert env.entity("p_7")["props"]["c"] == 4 and env.entity("v")["props"]["c"] == 0


def test_an_action_that_creates_an_entity_breaking_an_all_invariant_is_refused():
    refused, env = _refusals("$all(p, $it.c <= 5)", ("spawn", {"n": 5}), ("spawn", {"n": 6}))
    assert refused == [False, True] and len(env.entities("vip")) == 2


def test_an_all_invariant_that_reads_more_than_its_items_is_checked_whole():
    refused, _ = _refusals("$all(p, $it.c <= $world.cap)", ("give", {"to": "p_7", "n": 3}), ("cap", {"n": 2}))
    assert refused == [False, True]
    refused, _ = _refusals("$all(p, $it.c <= $entity($it.rival).c + 5)", ("give", {"to": "p_7", "n": 5}),
                           ("give", {"to": "p_7", "n": 1}), ("give", {"to": "p_1", "n": 1}))
    assert refused == [False, True, False]
    refused, _ = _refusals("$all(p, $it.c < 5) and $all(vip, $it.c < 3)", ("give", {"to": "v", "n": 3}),
                           ("give", {"to": "p_3", "n": 4}))
    assert refused == [True, False]
