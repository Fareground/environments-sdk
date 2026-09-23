"""Conservation across business scales, and explicit limits of floating-point balances."""
import copy
import json

import pytest

import fg_env


def contract(bank=1e6):
    return {
        "name": "Business monetary precision", "clock": {"rounds": 1},
        "types": {"holder": {}},
        "entities": {"bank": {"type": "holder", "props": {"cash": bank}},
                     "buyer": {"type": "holder", "props": {"cash": 10}}},
        "mechanisms": {"money": {"kind": "economy", "mode": "ledger", "who": "holder",
                                   "currencies": {"cash": {}}}},
        "stages": [{"name": "business", "turns": "sequential"}],
        "outputs": {"conserved": "$conserved(money)"},
    }


@pytest.mark.parametrize("bank", [1e6, 1e9, 1e12])
@pytest.mark.parametrize("loss", [1, 0.01])
def test_large_supply_does_not_hide_untracked_dollar_or_cent_losses(bank, loss):
    c = contract(bank)
    c["stages"][0]["on_enter"] = [f"$entity(bank).cash -= {loss}"]
    result = fg_env.load(c).run("idle")
    assert result.status == "failed"
    assert "conserved" in str(result.error)


@pytest.mark.parametrize("operation", ["pay", "mint", "burn"])
@pytest.mark.parametrize("bank, amount", [(1e16, 1), (100, 1e-12)])
def test_unrepresentable_money_changes_fail_without_partial_settlement(operation, bank, amount):
    c = contract(bank)
    effect = {"economy": "money", "action": operation, "amount": amount}
    effect.update({"pay": {"from": "$entity(buyer)", "to": "$entity(bank)"},
                   "mint": {"to": "$entity(bank)", "source": "income"},
                   "burn": {"from": "$entity(bank)", "sink": "expense"}}[operation])
    c["stages"][0]["on_enter"] = [effect]
    env = fg_env.load(c)
    before = copy.deepcopy(env.props)
    result = env.run("idle")
    assert result.status == "failed"
    assert "cannot be represented" in str(result.error)
    assert env.entity("bank")["props"]["cash"] == bank
    assert env.entity("buyer")["props"]["cash"] == 10
    assert env.props == before


@pytest.mark.parametrize("bank", [1e6, 1e9, 1e12])
def test_ordinary_cent_payment_and_checkpoint_replay(bank):
    c = contract(bank)
    c["stages"][0]["on_enter"] = [{"economy": "money", "action": "pay", "amount": 0.01,
                                      "from": "$entity(buyer)", "to": "$entity(bank)"}]
    env = fg_env.load(c, seed=7)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    result = env.run("idle")
    assert result.status == "completed", result.error
    assert result.outputs["conserved"] is True
    assert env.entity("buyer")["props"]["cash"] == 9.99
    assert result.to_dict() == restored.run("idle").to_dict()


def test_tiny_credit_cannot_round_an_existing_balance_down_to_zero():
    c = contract(1e-10)
    c["stages"][0]["on_enter"] = [{"economy": "money", "action": "mint", "amount": 1e-10,
                                      "to": "$entity(bank)", "source": "income"}]
    env = fg_env.load(c)
    result = env.run("idle")
    assert result.status == "failed"
    assert "cannot be represented" in str(result.error)
    assert env.entity("bank")["props"]["cash"] == 1e-10
    assert env.props["money_flows"] == {}


def test_total_holdings_preserve_small_balances_independent_of_entity_order():
    outputs = []
    for reverse in (False, True):
        c = contract(1e16)
        c["entities"] = {"bank": c["entities"]["bank"],
                         **{f"customer_{i}": {"type": "holder", "props": {"cash": 1}} for i in range(100)}}
        if reverse:
            c["entities"] = dict(reversed(list(c["entities"].items())))
        c["outputs"]["total"] = "$total_held(holder, cash)"
        result = fg_env.load(c).run("idle")
        assert result.status == "completed", result.error
        outputs.append(result.outputs["total"])
    assert outputs == [1e16 + 100, 1e16 + 100]
