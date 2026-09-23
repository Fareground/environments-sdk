"""Reach remains unique while distinct exposure events accumulate."""
import copy
import json

import fg_env


CONTRACT = {
    "name": "Overlapping outreach and network influence",
    "clock": {"rounds": 4},
    "types": {"person": {}, "operator": {"agent": True}},
    "entities": {"s1": {"type": "person"}, "s2": {"type": "person"},
                 "audience": {"type": "person"}, "operator": {"type": "operator"}},
    "relations": {"follows": {}},
    "links": [{"relation": "follows", "from": source, "to": "audience"} for source in ("s1", "s2")],
    "mechanisms": {"spread": {"kind": "social", "mode": "diffusion", "who": "person", "over": "follows",
                              "flow": "along", "model": "threshold", "threshold": 0.75, "phase": None,
                              "seeds": {"product": ["s1"]}}},
    "actions": {
        "outreach": {"by": "operator", "do": [
            {"social": "spread", "action": "expose", "item": "product", "who": ["audience", "audience"]}]},
        "step": {"by": "operator", "do": [{"social": "spread", "action": "step"}]},
        "seed_other": {"by": "operator", "do": [{"social": "spread", "action": "seed", "item": "product", "who": "s2"}]},
    },
    "stages": [{"name": "round", "turns": "sequential", "max_actions": 5, "max_calls": 8}],
    "outputs": {"reach": "$reach(product)", "exposures": "$exposures(audience, product)",
                "state": "$spread_state(audience, product)", "adopters": "$adopters(product)"},
}


def test_threshold_steps_preserve_outreach_and_accumulate_contacts_without_inflating_reach():
    def actor(wake):
        if wake.round == 1:
            assert wake.call("outreach", {}).ok
            assert wake.call("outreach", {}).ok
        if wake.round == 3:
            assert wake.call("seed_other", {}).ok
        assert wake.call("step", {}).ok
        wake.end()

    env = fg_env.load(CONTRACT)
    first = env.run(actor, rounds=1)
    assert first.status != "failed", first.error
    assert first.outputs == {"reach": 2, "exposures": 3, "state": "exposed", "adopters": 1}
    snapshot = json.loads(json.dumps(env.snapshot()))
    second = env.run(actor, rounds=1)
    assert second.outputs == {"reach": 2, "exposures": 4, "state": "exposed", "adopters": 1}
    third = env.run(actor, rounds=1)
    assert third.outputs == {"reach": 3, "exposures": 6, "state": "adopted", "adopters": 3}
    fourth = env.run(actor)
    assert fourth.outputs == third.outputs  # adopted actors stop receiving diffusion attempts
    assert fourth.to_dict() == fg_env.Env.restore(CONTRACT, snapshot).run(actor).to_dict()


def test_cascade_counts_each_attempt_but_not_repeated_idle_steps():
    contract = copy.deepcopy(CONTRACT)
    contract["mechanisms"]["spread"].update(model="cascade", p=0)

    def actor(wake):
        if wake.round == 1:
            assert wake.call("outreach", {}).ok
            assert wake.call("outreach", {}).ok
        assert wake.call("step", {}).ok
        wake.end()

    result = fg_env.load(contract).run(actor)
    assert result.status == "completed", result.error
    assert result.outputs == {"reach": 2, "exposures": 3, "state": "exposed", "adopters": 1}


def test_weighted_reverse_flow_uses_the_receivers_links_not_reciprocal_weights():
    contract = copy.deepcopy(CONTRACT)
    contract["mechanisms"]["spread"].update(flow="against", weighted=True, threshold=0.75)
    contract["links"] = [
        {"relation": "follows", "from": "audience", "to": "s1", "value": 9},
        {"relation": "follows", "from": "audience", "to": "s2", "value": 1},
        {"relation": "follows", "from": "s1", "to": "audience", "value": 1},
        {"relation": "follows", "from": "s2", "to": "audience", "value": 9},
    ]

    def actor(wake):
        assert wake.call("step", {}).ok
        wake.end()

    reverse = fg_env.load(contract).run(actor, rounds=1)
    assert reverse.status != "failed", reverse.error
    assert reverse.outputs["state"] == "adopted"  # 9 / (9 + 1) exceeds 0.75

    contract["mechanisms"]["spread"]["flow"] = "along"
    forward = fg_env.load(contract).run(actor, rounds=1)
    assert forward.status != "failed", forward.error
    assert forward.outputs["state"] == "exposed"  # 1 / (1 + 9) does not
