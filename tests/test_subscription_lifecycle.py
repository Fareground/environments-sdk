"""Participant exit must not break unrelated recurring agreements."""
import copy
import json

import pytest
from test_shared_subscription_choices import CONTRACT

import fg_env


@pytest.mark.parametrize("removed", ["seller", "one", "a"])
@pytest.mark.parametrize("trial", [0, 1])
def test_exit_ends_due_agreement_without_breaking_other_provider(removed, trial):
    contract = copy.deepcopy(CONTRACT)
    contract["entities"]["backup"] = {"type": "provider"}
    contract["entities"]["a"]["props"]["cash"] = (0 if trial else 20) if removed == "a" else 100
    contract["mechanisms"]["second"]["plans"]["two"]["provider"] = "backup"
    for name, plan in (("first", "one"), ("second", "two")):
        contract["mechanisms"][name]["plans"][plan].update(trial=trial, period=1)
    settle = [{"transfer": "cash", "from": "$entity(seller)", "to": "$entity(backup)",
               "amount": "$entity(seller).cash"}] if removed == "seller" else []
    contract["actions"] = {"exit": {"by": "customer", "do": settle + [{"remove": f"$entity({removed})"}]}}

    def actor(wake):
        if wake.entity_id == "a" and wake.round == 1:
            assert wake.call("first_subscribe", {"plan": "one"}).ok
            assert wake.call("second_subscribe", {"plan": "two"}).ok
            assert wake.call("exit", {}).ok
        wake.end()

    env = fg_env.load(contract, seed=1)
    first = env.run(actor, rounds=1)
    assert first.status != "failed", first.error
    snapshot = json.loads(json.dumps(env.snapshot()))
    result = env.run("idle", rounds=1)
    assert result.status != "failed", result.error
    first_sub = env.entities("first_sub")[0]["props"]
    assert first_sub["status"] == "ended"
    assert first_sub["reason"] == ("subscriber gone" if removed == "a" else "plan withdrawn")
    assert env.props["first_stats"]["revenue"] == (0 if trial else 10)
    assert env.props["second_stats"]["revenue"] == (0 if trial else 10) + (0 if removed == "a" else 10)
    assert result.outputs["conserved"] is True
    restored = fg_env.Env.restore(contract, snapshot).run("idle", rounds=1)
    assert result.to_dict() == restored.to_dict()


def test_withdrawn_provider_is_not_offered_and_custom_subscribe_aborts_cleanly():
    contract = copy.deepcopy(CONTRACT)
    contract["actions"] = {
        "close": {"by": "customer", "do": [{"remove": "$entity(seller)"}]},
        "custom_subscribe": {"by": "customer",
                             "do": [{"agreements": "first", "action": "subscribe",
                                     "who": "$actor", "plan": "$entity(one)"}]},
    }
    seen = {}

    def actor(wake):
        if wake.entity_id == "a" and wake.round == 1:
            assert wake.call("close", {}).ok
        if wake.entity_id == "b" and wake.round == 1:
            seen["tools"] = {tool.name for tool in wake.tools}
            seen["attempt"] = wake.call("custom_subscribe", {})
        wake.end()

    env = fg_env.load(contract)
    result = env.run(actor, rounds=1)
    assert result.status != "failed", result.error
    assert "first_subscribe" not in seen["tools"]
    assert "second_subscribe" not in seen["tools"]
    assert not seen["attempt"].ok
    assert "no longer available" in seen["attempt"].text
    assert env.entities("first_sub") == []
    assert env.props["first_stats"]["started"] == 0
