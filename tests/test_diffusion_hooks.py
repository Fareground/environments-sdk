"""Adoption hooks compose with diffusion state and follow-up effects."""
import copy
import json

import fg_env
import pytest

CONTRACT = {
    "name": "Composed adoption",
    "clock": {"rounds": 2},
    "types": {"person": {}, "controller": {"agent": True}},
    "entities": {"source": {"type": "person"}, "audience": {"type": "person"}, "operator": {"type": "controller"}},
    "relations": {"knows": {}},
    "links": [{"relation": "knows", "from": "source", "to": "audience"}],
    "world": {"seen": {"type": "list", "default": []}},
    "mechanisms": {"spread": {"kind": "social", "mode": "diffusion", "who": "person",
                              "over": "knows", "flow": "along", "p": 1, "phase": None,
                              "seeds": {"campaign": ["source"]},
                              "on_adopt": ["$world.seen += $spread_state($it, $item)",
                                  {"social": "spread", "action": "reject", "item": "$item", "who": "$it"},
                                  {"social": "spread", "action": "expose", "item": "followup", "who": "$it"}]}},
    "actions": {"advance": {"by": "controller", "do": [{"social": "spread", "action": "step"}]}},
    "stages": [{"name": "decide", "turns": "sequential"}],
    "outputs": {"state": "$spread_state($entity(audience), campaign)",
                "followup": "$exposures($entity(audience), followup)", "reach": "$reach(campaign)"},
}


@pytest.mark.parametrize("turns", ["sequential", "simultaneous"])
@pytest.mark.parametrize("mode", ["direct", "cascade", "threshold"])
def test_hook_sees_adoption_and_nested_changes_survive(mode, turns):
    contract = copy.deepcopy(CONTRACT)
    contract["stages"][0]["turns"] = turns
    if mode == "direct":
        contract["actions"]["advance"]["do"] = [
            {"social": "spread", "action": "adopt", "item": "campaign", "who": "$entity(audience)"}]
    else:
        contract["mechanisms"]["spread"]["model"] = mode

    def actor(wake):
        if wake.round == 1:
            assert wake.call("advance", {}).ok
        wake.end()

    env = fg_env.load(contract, seed=7)
    result = env.run(actor, rounds=1)
    assert result.status != "failed", result.error
    assert env.props["seen"] == ["adopted"]
    assert result.outputs == {"state": "rejected", "followup": 1, "reach": 2}
    snapshot = json.loads(json.dumps(env.snapshot()))
    assert env.run("idle").to_dict() == fg_env.Env.restore(contract, snapshot).run("idle").to_dict()


@pytest.mark.parametrize("mode", ["cascade", "threshold"])
def test_rejection_hook_stops_later_propagation_within_one_multi_step_action(mode):
    contract = copy.deepcopy(CONTRACT)
    contract["entities"]["later"] = {"type": "person"}
    contract["links"].append({"relation": "knows", "from": "audience", "to": "later"})
    contract["mechanisms"]["spread"].update(model=mode, steps=3)
    contract["outputs"]["later"] = "$spread_state($entity(later), campaign)"

    def actor(wake):
        assert wake.call("advance", {}).ok
        wake.end()

    env = fg_env.load(contract)
    result = env.run(actor, rounds=1)
    assert result.status != "failed", result.error
    assert env.props["seen"] == ["adopted"]
    assert result.outputs["state"] == "rejected"
    assert result.outputs["later"] == "unaware"
    assert result.outputs["followup"] == 1


def test_reentrant_adoption_of_the_same_actor_does_not_repeat_hooks():
    contract = copy.deepcopy(CONTRACT)
    contract["mechanisms"]["spread"]["on_adopt"] = [
        "$world.seen += $spread_state($it, $item)",
        {"social": "spread", "action": "adopt", "item": "$item", "who": "$it"}]

    def actor(wake):
        assert wake.call("advance", {}).ok
        wake.end()

    env = fg_env.load(contract)
    result = env.run(actor, rounds=1)
    assert result.status != "failed", result.error
    assert env.props["seen"] == ["adopted"]
    assert result.outputs["state"] == "adopted"


def test_refused_hook_rolls_back_published_adoption_and_nested_effects():
    contract = copy.deepcopy(CONTRACT)
    contract["mechanisms"]["spread"]["on_adopt"].append({"fail": "Campaign paused."})
    replies = []

    def actor(wake):
        replies.append(wake.call("advance", {}))
        wake.end()

    env = fg_env.load(contract)
    before = copy.deepcopy(env.props["spread"])
    result = env.run(actor, rounds=1)
    assert result.status != "failed", result.error
    assert len(replies) == 1 and not replies[0].ok
    assert "Campaign paused" in replies[0].text
    assert env.props["spread"] == before
    assert env.props["seen"] == []
