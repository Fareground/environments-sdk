"""Refusal diagnostics must not evaluate another role's or another stage's rules."""
import copy

import pytest

import fg_env


@pytest.mark.parametrize("grouped", [False, True])
def test_wrong_role_call_is_refused_without_probing_role_specific_effects(grouped):
    c = {"name": "Role refusal", "clock": {"rounds": 1},
         "types": {"manager": {"agent": True},
                   "worker": {"agent": True, "props": {"done": False}}},
         "entities": {"m": {"type": "manager"}, "w": {"type": "worker"}},
         "actions": {"ping": {"by": "manager"},
                     "finish": {"by": "worker", "params": {"value": {"type": "bool"}},
                                "do": "$actor.done = $params.value"}}}
    if grouped:
        c["actions"]["finish"]["tool"] = "work"
    env = fg_env.load(c, seed=1)
    refused = []

    def play(wake):
        if wake.entity_id == "m":
            before = copy.deepcopy((env.props, env.entities()))
            reply = wake.call("work" if grouped else "finish",
                              {"action": "finish", "value": True} if grouped else {"value": True})
            assert not reply.ok
            assert before == (env.props, env.entities())
            refused.append(reply.text)
            assert wake.call("ping", {}).ok
        else:
            assert wake.call("work" if grouped else "finish",
                             {"action": "finish", "value": True} if grouped else {"value": True}).ok
        wake.end()

    result = env.run(play)
    assert result.ok, result.summary()
    assert len(refused) == 1
    assert env.entity("w")["props"]["done"] is True


def test_wrong_stage_refusal_does_not_evaluate_conditions_valid_only_in_later_stage():
    c = {"name": "Stage refusal", "clock": {"rounds": 1},
         "world": {"divisor": 0, "completed": False},
         "types": {"person": {"agent": True}}, "entities": {"p": {"type": "person"}},
         "actions": {"wait": {"by": "person"},
                     "finish": {"by": "person", "when": "1 / $world.divisor > 0",
                                "do": "$world.completed = true"}},
         "stages": [{"name": "before", "actions": ["wait"]},
                    {"name": "ready", "actions": ["finish"], "on_enter": "$world.divisor = 1"}]}
    env = fg_env.load(c, seed=1)

    def play(wake):
        if wake.stage == "before":
            assert not wake.call("finish", {}).ok
            assert env.props == {"divisor": 0, "completed": False}
            assert wake.call("wait", {}).ok
        else:
            assert wake.call("finish", {}).ok
        wake.end()

    result = env.run(play)
    assert result.ok, result.summary()
    assert env.props["completed"] is True
