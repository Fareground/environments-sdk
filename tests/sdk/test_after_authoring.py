"""Impossible literal delays should be repairable before an agent runs its scenario."""
import pytest

import fg_env


def contract(delay, continuous=False):
    return {"name": "Delayed delivery", "clock": ({"mode": "continuous", "horizon": 5} if continuous else {"rounds": 3}),
            "types": {"worker": {}}, "world": {"delivered": 0},
            "inputs": {"delay": {"default": 1}},
            "events": [{"name": "order", "at": 1, "do": [{"after": delay, "do": ["$world.delivered += 1"]}]}],
            "outputs": {"delivered": "$world.delivered"}}


@pytest.mark.parametrize("continuous", [False, True])
@pytest.mark.parametrize("delay", [-1, 0, True, None, "tomorrow", [], {}, float("inf"), float("nan")])
def test_invalid_literal_delay_is_a_precise_static_error(delay, continuous):
    issues = list(fg_env.check(contract(delay, continuous), rounds=0))
    found = [i for i in issues if i.path == "events[0].do[0].after" and i.severity == "error"]
    assert len(found) == 1
    assert "positive" in found[0].message or "≥ 1" in found[0].message
    assert found[0].fix


def test_fractional_round_delay_is_rejected_but_fractional_continuous_delay_is_allowed():
    assert any(i.path == "events[0].do[0].after" for i in fg_env.check(contract(1.5), rounds=0))
    assert not [i for i in fg_env.check(contract(1.5, True), rounds=0) if i.severity == "error"]
    result = fg_env.run(contract(1.5, True))
    assert result.status == "completed", result.error
    assert result.outputs == {"delivered": 1}


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
    r = fg_env.run(c, inputs={"delay": -1})
    assert r.status == "failed"
    assert "whole number of rounds" in r.error
