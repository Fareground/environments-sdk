"""Repeated agreements share money while keeping per-actor, per-plan history separate."""
import copy
import json

import pytest

import fg_env

CONTRACT = {
    "name": "Shared budget and independent agreements",
    "clock": {"rounds": 5},
    "types": {"customer": {"agent": True}, "provider": {}},
    "entities": {"a": {"type": "customer", "props": {"cash": 15}},
                 "b": {"type": "customer", "props": {"cash": 0}}, "seller": {"type": "provider"}},
    "mechanisms": {
        "money": {"kind": "economy", "mode": "ledger", "who": ["customer", "provider"], "currencies": {"cash": {}}},
        "first": {"kind": "agreements", "mode": "subscriptions", "who": "customer", "currency": "cash",
                  "plans": {"one": {"provider": "seller", "price": 10, "trial": 1, "period": 5}}},
        "second": {"kind": "agreements", "mode": "subscriptions", "who": "customer", "currency": "cash",
                   "plans": {"two": {"provider": "seller", "price": 10, "trial": 1, "period": 5}}},
    },
    "stages": [{"name": "decide", "turns": "sequential", "max_actions": 5, "max_calls": 10}],
    "outputs": {"conserved": "$conserved(money)"},
}


@pytest.mark.parametrize("reverse_mechanisms", [False, True])
def test_tools_do_not_offer_a_used_trial_that_the_shared_budget_cannot_pay(reverse_mechanisms):
    contract = copy.deepcopy(CONTRACT)
    if reverse_mechanisms:
        contract["mechanisms"] = dict(reversed(list(contract["mechanisms"].items())))
    seen = {}

    def actor(wake):
        if wake.entity_id == "a" and wake.round == 1:
            assert wake.call("first_subscribe", {"plan": "one"}).ok
            assert wake.call("first_cancel", {"subscription": "first_sub_1"}).ok
            assert wake.call("second_subscribe", {"plan": "two"}).ok
        if wake.entity_id == "a" and wake.round == 2:
            # Second converts for 10, leaving 5. First's trial was cancelled and used.
            seen["a_tools"] = {t.name for t in wake.tools}
            seen["a_plans"] = wake.call("look", {"view": "first_plans"}).text
        if wake.entity_id == "b" and wake.round == 2:
            seen["b_tools"] = {t.name for t in wake.tools}
            seen["b_plans"] = wake.call("look", {"view": "first_plans"}).text
            assert wake.call("first_subscribe", {"plan": "one"}).ok  # another actor still has a free trial
        wake.end()

    env = fg_env.load(contract, seed=1)
    result = env.run(actor, rounds=2)
    assert result.status != "failed", result.error
    assert "first_subscribe" not in seen["a_tools"]
    assert "first_subscribe" in seen["b_tools"]
    assert "One" in seen["a_plans"]
    assert "1 rounds free" not in seen["a_plans"]
    assert "1 rounds free" in seen["b_plans"]
    assert env.entity("a")["props"]["cash"] == 5
    assert env.entity("seller")["props"]["cash"] == 10
    assert result.outputs["conserved"] is True
    snapshot = json.loads(json.dumps(env.snapshot()))
    assert env.run("idle").to_dict() == fg_env.Env.restore(contract, snapshot).run("idle").to_dict()


def test_used_trial_can_be_restarted_at_full_price_when_affordable():
    contract = copy.deepcopy(CONTRACT)
    contract["entities"]["a"]["props"]["cash"] = 30
    def actor(wake):
        if wake.entity_id == "a":
            if wake.round == 1:
                assert wake.call("first_subscribe", {"plan": "one"}).ok
                assert wake.call("first_cancel", {"subscription": "first_sub_1"}).ok
            if wake.round == 2:
                assert wake.call("first_subscribe", {"plan": "one"}).ok
        wake.end()
    env = fg_env.load(contract)
    result = env.run(actor, rounds=2)
    assert result.status != "failed", result.error
    assert env.entity("a")["props"]["cash"] == 20
    assert env.props["first_stats"]["trials"] == 1
    assert env.props["first_stats"]["started"] == 2
