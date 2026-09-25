"""Impossible literal delays should be repairable before an agent runs its scenario."""
import pytest

import fg_env


def contract(delay):
    return {"name": "Delayed delivery",
            "clock": {"rounds": 3},
            "types": {"worker": {}}, "world": {"delivered": 0},
            "inputs": {"delay": {"default": 1}},
            "events": [{"name": "order", "at": 1, "do": [{"after": delay, "do": ["$world.delivered += 1"]}]}],
            "outputs": {"delivered": "$world.delivered"}}


@pytest.mark.parametrize("delay", [-1, 0, True, None, "tomorrow", [], {}, float("inf"), float("nan"), 1.5])
def test_invalid_literal_delay_is_a_precise_static_error(delay):
    issues = list(fg_env.check(contract(delay), rounds=0))
    found = [i for i in issues if i.path == "events[0].do[0].after" and i.severity == "error"]
    assert len(found) == 1
    assert "positive" in found[0].message or "≥ 1" in found[0].message
    assert found[0].fix


@pytest.mark.parametrize("delay", [1, 2, "$inputs.delay"])
def test_valid_delays_remain_runnable(delay):
    c = contract(delay)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == "error"]
    r = fg_env.run(c)
    assert r.status == "completed", r.error
    assert r.outputs == {"delivered": 1}


def test_dynamic_invalid_delay_still_fails_at_runtime():
    c = contract("$inputs.delay")
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == "error"]
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(c, inputs={"delay": -1})
    r = failed.value.result
    assert r.status == "failed"
    assert "whole number of rounds" in r.error


def test_what_an_action_set_for_later_does_not_happen_once_its_agent_has_left():
    """Effects an action scheduled are that action's: once its agent is removed they are refused, as its sealed choice
    would be, rather than writing to an entity no longer in the run (audit 11 L4)."""
    contract = {"name": "Leave", "clock": {"rounds": 3}, "world": {"hits": 0},
                "types": {"p": {"agent": True, "props": {"cash": 5}}}, "entities": {"a": {"type": "p"}},
                "actions": {"sched": {"by": "p", "do": [{"after": 1, "do": ["$world.hits += 1", "$actor.cash += 1"]}]},
                            "quit": {"by": "p", "do": [{"remove": "$actor"}]}},
                "stages": [{"name": "s", "max_actions": 2}],
                "outputs": {"hits": "$world.hits", "cash": "$entity(a).cash"}}

    def play(wake):
        wake.call("sched", {})
        wake.call("quit", {})

    result = fg_env.run(contract, play, seed=1)
    assert result.status == "completed", result.error
    assert result.outputs == {"hits": 0, "cash": 5}
