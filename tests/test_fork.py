"""Forks: continue a run from a moment under another arm, inputs, a patch or a whole new contract."""
import copy
import json

import pytest

import fg_env
from fg_env import ContractError, InvariantViolation, RunError, SnapshotError

PRICES = {
    "name": "Price war",
    "clock": {"rounds": 6},
    "inputs": {"tax": {"type": "number", "default": 0}},
    "world": {"price": 10, "sales": 0, "boosts": 0},
    "types": {"shop": {"agent": True, "props": {"cash": 0}}},
    "entities": {"s": {"type": "shop"}},
    "actions": {"sell": {"by": "shop", "terminal": True,
                         "do": ["$actor.cash += $world.price - $inputs.tax", "$world.sales += 1"]}},
    "stages": [{"name": "trade", "must_act": True}],
    "events": [{"phase": "start", "arms": ["discount"], "do": ["$world.price = 5"]},
               {"phase": "end", "when": "$world.sales >= 2", "once": True, "do": ["$world.boosts += 1"]}],
    "arms": {"control": {}, "discount": {"inputs": {"tax": 1}}},
    "invariants": ["$world.sales >= 0"],
    "outputs": {"cash": {"expr": "$entity('s').cash", "type": "number"}},
}

DICE = {
    "name": "Dice",
    "clock": {"rounds": 6},
    "world": {"rolls": {"type": "list", "default": []}},
    "types": {"player": {"agent": True, "props": {}}},
    "entities": {"p": {"type": "player"}},
    "actions": {"wait": {"by": "player", "do": []}},
    "events": [{"phase": "start", "do": ["$world.rolls += $dice('d20')"]}],
    "outputs": {"rolls": {"expr": "$world.rolls", "type": "list"}},
}


def _sell(wake):
    wake.call("sell", {})


def _after(arm, rounds, seed=1):
    env = fg_env.load(PRICES, seed=seed, arm=arm)
    env.run(_sell, rounds=rounds)
    return env


def test_a_fork_without_changes_continues_exactly_like_the_run():
    forked = _after("control", 3).fork()
    assert forked.run(_sell).events == _after("control", 3).run(_sell).events


def test_switching_arms_at_a_round_changes_what_happens_from_then_on():
    env = _after("control", 3)
    forked = env.fork(arm="discount")
    result = forked.run(_sell)
    assert result.arm == "discount" and result.inputs["tax"] == 1
    assert result.outputs["cash"] == 3 * 10 + 3 * (5 - 1)
    assert env.run(_sell).outputs["cash"] == 60  # the original is untouched
    assert [e["data"]["arm"] for e in result.events if e["kind"] == "fork"] == ["discount"]


def test_inputs_set_by_the_old_arm_go_back_to_their_defaults():
    result = _after("discount", 2).fork(arm="control").run(_sell)
    assert result.inputs["tax"] == 0
    assert result.outputs["cash"] == 2 * (5 - 1) + 4 * 5  # the price the old arm set stays: it is state


def test_a_patch_can_add_state_and_intervention_effects_apply_at_the_fork():
    env = _after("control", 2)
    patch = {"world": {"bonus": {"type": "number", "default": "$inputs.tax + 1"}},
             "types": {"shop": {"props": {"rating": "$it.cash / 10"}}}}
    forked = env.fork(patch=patch, effects=["$world.bonus += $world.price", "$entity('s').rating += 3"])
    assert forked.props["bonus"] == 11
    assert forked.entity("s")["props"] == {"cash": 20, "rating": 5}
    fork_event = [e for e in forked.result().events if e["kind"] == "fork"][0]
    assert fork_event["data"]["effects"] == 2 and fork_event["data"]["patch"] == ["types", "world"]
    with pytest.raises(InvariantViolation):
        env.fork(effects=["$world.sales = -1"])


def test_what_the_state_cannot_follow_is_refused_with_fixes():
    env = _after("control", 3)
    changed = copy.deepcopy(PRICES)
    del changed["types"]["shop"]["props"]["cash"]
    changed["actions"]["sell"]["do"] = ["$world.sales += 1"]
    changed["outputs"] = {"sales": {"expr": "$world.sales", "type": "int"}}
    changed["clock"] = {"rounds": 2}
    with pytest.raises(ContractError) as refused:
        env.fork(contract=changed)
    paths = {issue.path: issue for issue in refused.value.issues}
    assert {"types.shop.props.cash", "clock.rounds"} <= set(paths)
    assert all(issue.fix for issue in refused.value.issues)
    with pytest.raises(ContractError, match="above the maximum 5"):
        env.fork(patch={"types": {"shop": {"props": {"cash": {"default": 0, "max": 5}}}}})


def test_link_fields_a_patch_adds_start_from_their_defaults_on_existing_links():
    social = {"name": "Circle", "clock": {"rounds": 3}, "world": {"since": -1},
              "types": {"person": {"agent": True, "props": {}}},
              "entities": {"a": {"type": "person"}, "b": {"type": "person"}},
              "relations": {"knows": {}}, "links": [{"relation": "knows", "from": "a", "to": "b"}],
              "actions": {"wait": {"by": "person", "do": []}}, "outputs": {"since": "$world.since"}}
    env = fg_env.load(social, seed=1)
    env.run(rounds=1)
    forked = env.fork(patch={"relations": {"knows": {"props": {"since": {"type": "int", "default": 7}}}}},
                      effects=["$world.since = $link('a', 'b', knows).since"])
    assert forked.props["since"] == 7


def test_a_new_seed_changes_the_luck_from_the_fork_only():
    env = fg_env.load(DICE, seed=3)
    env.run(rounds=2)
    reseeded = env.fork(seed=99).run().outputs["rolls"]
    straight = env.run().outputs["rolls"]
    assert reseeded[:2] == straight[:2]
    assert reseeded[2:] != straight[2:]


def test_a_completed_run_forked_with_more_rounds_plays_on():
    env = _after("control", 6)
    assert env.status == "completed"
    longer = env.fork(patch={"clock": {"rounds": 8}})
    assert longer.status == "running"
    result = longer.run(_sell)
    assert result.rounds == 8 and result.outputs["cash"] == 80
    assert [e["kind"] for e in result.events].count("end") == 1


def test_a_once_event_that_fired_stays_fired_only_while_it_is_declared_unchanged():
    env = _after("control", 3)
    assert env.props["boosts"] == 1
    kept = env.fork(patch={"world": {"note": 0}})
    kept.run(_sell)
    assert kept.props["boosts"] == 1
    edited = copy.deepcopy(PRICES["events"])
    edited[1]["do"] = ["$world.boosts += 10"]
    renewed = env.fork(patch={"events": edited})
    renewed.run(_sell)
    assert renewed.props["boosts"] == 11


def test_a_stored_snapshot_forks_and_restore_explains_what_it_refuses():
    env = _after("control", 2)
    stored = json.loads(json.dumps(env.snapshot()))
    assert fg_env.fork(PRICES, stored, arm="discount").run(_sell).outputs["cash"] == 20 + 4 * 4
    patched = copy.deepcopy(PRICES)
    patched["world"]["extra"] = 1
    with pytest.raises(SnapshotError, match="fg_env.fork"):
        fg_env.Env.restore(patched, stored)
    edited = dict(stored, arm="discount")
    with pytest.raises(SnapshotError, match="changed after it was taken"):
        fg_env.Env.restore(PRICES, edited)


def test_changes_part_way_through_a_round_are_refused_but_a_plain_fork_is_a_clone():
    stops = {"n": 0}

    def stop(_env):
        stops["n"] += 1
        return stops["n"] == 3

    env = fg_env.load(PRICES, seed=1, arm="control")
    env.run(_sell, stop=stop)
    assert env._in_round
    with pytest.raises(RunError, match="between rounds"):
        env.fork(arm="discount")
    twin = env.fork()
    assert twin.run(_sell).events == env.run(_sell).events


def test_an_experiment_can_branch_every_arm_from_one_shared_history():
    result = fg_env.experiment(PRICES, runs=2, arms=["control", "discount"], branch_at=3, participants={"shop": _sell})
    control, discount = result.arms["control"].runs, result.arms["discount"].runs
    for mine, theirs in zip(control, discount):
        assert mine.seed == theirs.seed
        assert [e for e in mine.events if e["round"] <= 3 and e["kind"] != "fork"] == \
            [e for e in theirs.events if e["round"] <= 3 and e["kind"] != "fork"]
        assert mine.outputs["cash"] == 60 and theirs.outputs["cash"] == 42
    with pytest.raises(ValueError, match="branch_at"):
        fg_env.experiment(PRICES, runs=1, branch_at=7, rounds=6)
