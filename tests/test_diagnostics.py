"""Run diagnostics name the logic problems a run reveals, with a fix, and stay quiet about ordinary play."""
import json

import pytest

import fg_env

SHOP = {
    "name": "Shop",
    "clock": {"rounds": 3},
    "world": {"price": 5, "phase": "open", "sold": 0},
    "types": {"buyer": {"agent": True, "props": {"cash": 3, "loaves": 0}}},
    "entities": {"ann": {"type": "buyer"}, "bob": {"type": "buyer"}},
    "actions": {"buy": {"by": "buyer", "params": {"qty": {"type": "int", "min": 1, "max": 3}},
                        "when": {"expr": "$params.qty * $world.price <= $actor.cash", "why": "You cannot afford that"},
                        "do": ["$actor.cash -= $params.qty * $world.price", "$actor.loaves += $params.qty"]}},
    "outputs": {"loaves": "$sum(buyer, $it.loaves)"},
}


def _contract(**changes):
    contract = json.loads(json.dumps(SHOP))
    contract.update(changes)
    return contract


def _codes(result):
    return sorted((d["code"], d["path"]) for d in result.diagnostics)


def test_a_tool_offered_when_no_choice_can_succeed_is_reported_by_check_with_the_refusal():
    warnings = [i for i in fg_env.check(SHOP) if i.path == "actions.buy"]
    assert len(warnings) == 1 and warnings[0].severity == "warning"
    assert warnings[0].message.startswith("was offered 6 time(s) when none of its choices could succeed; refused with: "
                                          "buy was not done: You cannot afford that")
    assert "`when` over $actor" in warnings[0].fix


def _capped(**changes):
    """Always usable: a purchase may cost at most the cap, so small orders work and large ones are refused."""
    contract = _contract(types={"buyer": {"agent": True, "props": {"cash": 1000, "loaves": 0}}},
                         world={"price": 10, "cap": 20, "phase": "open", "sold": 0}, **changes)
    contract["actions"]["buy"]["params"]["qty"]["max"] = 9
    contract["actions"]["buy"]["when"] = {"expr": "$params.qty * $world.price <= $world.cap", "why": "Too big an order"}
    return contract


def test_a_tool_that_works_for_some_choices_is_not_reported_however_often_random_agents_miss():
    result = fg_env.run(_capped(), seed=2)
    assert result.stats["rejected_actions"] + result.stats["invalid_calls"] > 0 and result.diagnostics == []


def test_mostly_refused_calls_are_reported_for_model_participants_only():
    contract = _capped()

    def greedy(wake):
        wake.call("buy", {"qty": 3})
        wake.call("buy", {"qty": 2})
        wake.end()

    def model(wake):
        wake.record_usage(llm_calls=1, input_tokens=10, output_tokens=2)
        greedy(wake)

    assert _codes(fg_env.run(contract, greedy, seed=1)) == []
    found = fg_env.run(contract, model, seed=1).diagnostics
    assert [(d["code"], d["message"]) for d in found] == [
        ("action_mostly_refused", "refused 6 of 12 calls; most often: buy was not done: Too big an order. "
                                  "Correct the arguments and call again (6×)")]


def test_sealed_choices_that_replace_each_others_values_are_reported():
    contract = _contract(world={"price": 1, "phase": "open", "sold": 0, "pick": 0},
                         stages=[{"name": "pick", "turns": "simultaneous", "actions": ["choose"]}],
                         actions={"choose": {"by": "buyer", "params": {"n": {"type": "int", "min": 1, "max": 2}},
                                             "do": "$world.pick = $params.n"}})
    picks = {"ann": 1, "bob": 2}

    def play(wake):
        wake.call("choose", {"n": picks[wake.entity_id]})
        wake.end()

    found = fg_env.run(contract, play, seed=1).diagnostics
    assert [(d["code"], d["path"]) for d in found] == [("sealed_choices_overwrite", "stages.pick")]
    assert "`$world.pick = $params.n` in actions.choose set $world.pick, replacing the value ann's choice had set" \
        in found[0]["message"]
    assert "an event on `stage.pick.end`" in found[0]["fix"] and "on_exit" not in found[0]["fix"]


def test_sealed_choices_that_build_on_the_value_before_are_not_overwrites():
    contract = _contract(world={"price": 1, "phase": "open", "sold": 0, "pick": 0},
                         stages=[{"name": "pick", "turns": "simultaneous", "actions": ["choose"]}],
                         actions={"choose": {"by": "buyer", "params": {"n": {"type": "int", "min": 1, "max": 2}},
                                             "do": "$world.pick = $world.pick * 10 + $params.n"}})
    assert fg_env.run(contract, seed=1).diagnostics == []


def test_an_agent_type_that_never_can_act_is_reported_after_two_rounds_with_the_reason():
    contract = _contract(actions={"buy": {"by": "buyer", "when": {"expr": "$actor.cash > 10", "why": "You are broke"},
                                          "do": "$actor.loaves += 1"}})
    assert fg_env.run(contract, seed=1, rounds=1).diagnostics == []
    found = fg_env.run(contract, seed=1).diagnostics
    assert [(d["code"], d["message"]) for d in found] == [
        ("agents_never_able_to_act", "no buyer had an action it could take in any of its 6 turn(s) over 3 rounds; "
                                     "most often: buy: You are broke (6×)")]


def test_a_stage_whose_condition_reads_only_what_no_rule_changes_is_reported():
    contract = _contract(stages=[{"name": "shop", "actions": ["buy"], "when": "$world.phase == 'closed'"}],
                         types={"buyer": {"agent": True, "props": {"cash": 30, "loaves": 0}}})
    found = fg_env.run(contract, seed=1).diagnostics
    assert [(d["code"], d["message"]) for d in found if d["code"] != "agents_never_played"] == [
        ("stage_never_runs", "never ran and cannot: its `when` is false and it reads only `$world.phase`, which no "
                             "rule changes")]
    changed = _contract(stages=contract["stages"], types=contract["types"],
                        events=[{"at": 9, "do": "$world.phase = 'closed'"}])
    # a rule could open the shop, though not within these rounds: no agent played, and that is all that is said
    assert [d["code"] for d in fg_env.run(changed, seed=1).diagnostics] == ["agents_never_played"]


def test_measures_that_read_only_what_nothing_changes_are_reported_and_ones_rules_could_change_are_not():
    contract = _contract(types={"buyer": {"agent": True, "props": {"cash": 30, "loaves": 0}}},
                         outputs={"champion": "$result.winner.name if $result.winner else null",
                                  "sold": {"expr": "$world.sold", "series": True},
                                  "loaves": {"expr": "$sum(buyer, $it.loaves)", "series": True}})
    found = fg_env.run(contract, "idle", seed=1).diagnostics
    assert [(d["code"], d["path"], d["message"]) for d in found] == [
        ("output_empty", "outputs.champion", "is empty (null) at the end of the run: it reads only `$result.winner`, "
                                             "which no `end` condition or effect gives"),
        ("metric_never_changes", "outputs.sold", "stayed 0 for all 3 rounds: it reads only `$world.sold`, which no "
                                                 "rule changes")]


def test_diagnostics_show_in_the_summary_and_resume_exactly_from_a_snapshot():
    straight = fg_env.load(SHOP, seed=4).run()
    assert "diagnostic: actions.buy: was offered" in straight.summary()
    env = fg_env.load(SHOP, seed=4)
    env.run(rounds=1)
    restored = fg_env.Env.restore(SHOP, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight.to_dict()


def test_a_policy_rule_refused_every_time_it_was_tried_is_reported_by_the_run_quoting_the_refusal():
    contract = _contract(policies={"greedy": {"rules": [{"do": "buy", "with": {"qty": 3}}]}})
    contract["types"]["buyer"]["policy"] = "greedy"
    found = [d for d in fg_env.run(contract, seed=1).diagnostics if d["code"] == "policy_rule_never_acted"]
    assert [(d["path"], d["message"]) for d in found] == [
        ("types.buyer.policies.greedy.rules[0]", "was tried 6 time(s) and refused every time: You cannot afford that")]
    assert "`with`" in found[0]["fix"]
    warned = [i for i in fg_env.check(contract) if i.path == "types.buyer.policies.greedy.rules[0]"]
    assert len(warned) == 1 and warned[0].severity == "warning"
    assert warned[0].message.startswith("was tried 6 time(s) and refused every time: You cannot afford that (smoke run")


def test_a_policy_rule_that_acts_at_least_once_is_not_reported():
    contract = _contract(world={"price": 1, "phase": "open", "sold": 0},
                         policies={"thrifty": {"rules": [{"do": "buy", "with": {"qty": 2}}]}})
    contract["types"]["buyer"]["policy"] = "thrifty"
    assert _codes(fg_env.run(contract, seed=1)) == []


def test_agents_whose_turns_all_ran_out_of_time_are_told_to_take_less_time():
    """Turns out of time are among what went wrong, with `time_limit` in the fix (audit 9 LLM M2)."""
    import time as clock

    contract = {"name": "Slow", "clock": {"rounds": 3}, "types": {"p": {"agent": True}},
                "entities": {"a": {"type": "p"}}, "actions": {"go": {"by": "p", "do": []}}}
    result = fg_env.load(contract, seed=1).run(lambda wake: clock.sleep(0.2), time_limit=0.05)
    [found] = [d for d in result.diagnostics if d["code"] in ("agents_never_acted", "agents_often_failed")]
    assert "out of time" in found["message"] and "time_limit" in found["fix"]


@pytest.mark.parametrize("patch", [{"stages": [{"name": "trade", "when": "false"}]}, {"end": [{"when": "true"}]}])
def test_a_run_in_which_no_agent_ever_has_a_turn_is_degraded(patch):
    """Whatever such a run measured, no agent's choice shaped it: it is not ok, with a finding that says why."""
    contract = {"name": "Stall", "clock": {"rounds": 3}, "types": {"p": {"agent": True, "props": {"n": 0}}},
                "entities": {"p": {"type": "p", "count": 2}}, "actions": {"bump": {"by": "p", "do": "$actor.n += 1"}},
                "outputs": {"total": "$sum(p, $it.n)"}, **patch}
    result = fg_env.run(contract, "random", seed=1)
    assert not result.ok and "agents_never_played" in result.degraded


def test_a_one_round_run_whose_turns_never_offer_an_action_is_degraded():
    contract = {"name": "Locked", "clock": {"rounds": 1}, "types": {"p": {"agent": True, "props": {"n": 0}}},
                "entities": {"p": {"type": "p", "count": 2}},
                "actions": {"bump": {"by": "p", "when": [{"expr": "$round > 5", "why": "not yet"}],
                                     "do": "$actor.n += 1"}},
                "outputs": {"total": "$sum(p, $it.n)"}}
    result = fg_env.run(contract, "random", seed=1)
    assert "agents_never_able_to_act" in result.degraded and "not yet" in str(result.diagnostics)


def test_an_agent_a_stage_offers_actions_that_never_has_a_turn_is_degraded():
    """One player of two never woken (its stage's `who` never picks it) is as broken as none (audit 12 agentif H1)."""
    contract = {"name": "Half", "clock": {"rounds": 3}, "types": {"p": {"agent": True, "props": {"n": 0}}},
                "entities": {"north": {"type": "p"}, "south": {"type": "p"}},
                "actions": {"bump": {"by": "p", "do": "$actor.n += 1"}},
                "stages": [{"name": "s", "who": "$it.id == 'north'"}], "outputs": {"total": "$sum(p, $it.n)"}}
    result = fg_env.run(contract, "random", seed=1)
    assert not result.ok and "agents_never_played" in result.degraded
    assert "south never had a turn" in next(d["message"] for d in result.diagnostics
                                             if d["code"] == "agents_never_played")
    contract["stages"][0]["who"] = "true"
    assert fg_env.run(contract, "random", seed=1).ok


def test_what_an_action_set_for_later_is_counted_when_its_agent_left_first():
    """An action's `after` block runs as that action's, so it is dropped once its agent is removed: said, not silent
    (audit 12 M4)."""
    contract = {"name": "Later", "clock": {"rounds": 3}, "world": {"hits": 0},
                "types": {"p": {"agent": True, "props": {"x": 0}}},
                "entities": {"ann": {"type": "p"}, "bob": {"type": "p"}},
                "actions": {"sched": {"by": "p", "do": [{"after": 1, "do": ["$world.hits += 1"]}]},
                            "kill": {"by": "p", "params": {"t": {"type": "entity", "of": "p"}},
                                     "do": {"remove": "$params.t"}}},
                "outputs": {"hits": "$world.hits"}}

    def play(wake):
        if wake.round == 1:
            wake.call("sched" if wake.entity_id == "ann" else "kill", {} if wake.entity_id == "ann" else {"t": "ann"})
        wake.end()

    result = fg_env.run(contract, play, seed=1)
    assert result.outputs["hits"] == 0
    assert [d["path"] for d in result.diagnostics if d["code"] == "after_dropped"] == ["actions.sched"]


def test_turns_out_of_time_name_the_provider_retries_that_spent_it():
    """A turn lost to rate-limit retries says so, not only that it ran out of time (audit 12 agentif A-L4)."""
    from fg_env.runtime.diagnostics import _out_of_time
    from fg_env.runtime.facts import Stats

    assert "8 model call(s) were retried after a rate limit" in _out_of_time([("north", Stats(timeouts=4,
                                                                                               llm_retries=8))])
    assert "retried" not in _out_of_time([("north", Stats(timeouts=4))])
    assert _out_of_time([("north", Stats(llm_retries=8))]) == ""
