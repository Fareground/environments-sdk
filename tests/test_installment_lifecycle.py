"""Delayed commitments survive participant exit without stopping unrelated deals."""
import copy
import json

import pytest

import fg_env

CONTRACT = {
    "name": "Independent supply agreements",
    "clock": {"rounds": 4},
    "types": {"firm": {"agent": True}},
    "entities": {name: {"type": "firm", "props": {"cash": 100}} for name in ("a", "b", "c", "d")},
    "world": {"breach_hooks": 0},
    "mechanisms": {
        "money": {"kind": "economy", "mode": "ledger", "who": "firm", "currencies": {"cash": {}}},
        "deals": {"kind": "agreements", "mode": "negotiation", "who": "firm", "once": False,
                  "issues": {"price": {"min": 1, "max": 20}},
                  "obligations": [{"from": "$proposer", "to": "$acceptor", "pay": "cash",
                                   "amount": "$terms.price", "times": 2}],
                  "breach": {"penalty": 5, "terminate": True, "on_breach": ["$world.breach_hooks += 1"]}},
    },
    "actions": {"exit": {"by": "firm", "params": {"target": {"type": "entity", "of": "firm"}},
                         "do": [{"transfer": "cash", "from": "$params.target", "to": "$entity(d)",
                                 "amount": "$params.target.cash"}, {"remove": "$params.target"}]}},
    "stages": [{"name": "trade", "turns": "sequential", "max_actions": 4, "max_calls": 10}],
    "outputs": {"conserved": "$conserved(money)"},
}


@pytest.mark.parametrize("asset", ["payment", "delivery"])
@pytest.mark.parametrize("removed", ["a", "b"])
@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("terminate", [False, True])
def test_exit_obeys_breach_policy_and_preserves_independent_deal(removed, manual, terminate, asset):
    contract = copy.deepcopy(CONTRACT)
    contract["mechanisms"]["deals"]["obligations"][0]["manual"] = manual
    contract["mechanisms"]["deals"]["breach"]["terminate"] = terminate

    if asset == "delivery":
        contract["mechanisms"]["goods"] = {"kind": "economy", "mode": "inventory", "who": "firm",
                                            "items": {"units": {}}}
        for name in ("a", "c"):
            contract["entities"][name]["props"]["goods"] = {"units": 20}
        obligation = contract["mechanisms"]["deals"]["obligations"][0]
        del obligation["pay"]
        obligation["give"] = "units"
        contract["actions"]["exit"]["do"].insert(0, {
            "economy": "goods", "action": "give", "item": "units", "from": "$params.target",
            "to": "$entity(d)", "qty": "$count_items($params.target, units)"})
        contract["outputs"]["goods_conserved"] = "$conserved(goods)"

    attempts = []

    def actor(wake):
        if wake.round == 1:
            if wake.entity_id in ("a", "c"):
                assert wake.call("deals_propose", {"to": "b" if wake.entity_id == "a" else "d", "price": 10}).ok
            if wake.entity_id in ("b", "d"):
                offer = "deals_offer_1" if wake.entity_id == "b" else "deals_offer_2"
                assert wake.call("deals_accept", {"offer": offer}).ok
            if wake.entity_id == "d":
                assert wake.call("exit", {"target": removed}).ok
        elif manual and wake.entity_id == "c":
            # Due installments are checked at round end.
            if wake.round in (2, 3):
                assert wake.call("deals_fulfill", {"duty": f"deals_duty_{wake.round + 1}"}).ok
        if manual and removed == "b" and wake.entity_id == "a" and wake.round == 2:
            attempts.append(wake.call("deals_fulfill", {"duty": "deals_duty_1"}))
        wake.end()

    env = fg_env.load(contract, seed=3)
    first = env.run(actor, rounds=1)
    assert first.status != "failed", first.error
    snapshot = json.loads(json.dumps(env.snapshot()))
    result = env.run(actor)
    assert result.status != "failed", result.error
    assert result.outputs["conserved"] is True
    if manual and removed == "b":
        assert len(attempts) == 1
        assert not attempts[0].ok
        assert "recipient is no longer active" in attempts[0].text
    duties = [row["props"]["status"] for row in env.entities("deals_duty")]
    assert duties == ["breached", "cancelled" if terminate else "breached", "done", "done"]
    deals = [row["props"] for row in env.entities("deals_deal")]
    assert deals[0]["status"] == ("terminated" if terminate else "completed")
    assert deals[1]["status"] == "completed"
    assert env.props["breach_hooks"] == (1 if terminate else 2)
    assert env.props["deals_stats"]["penalties"] == 0
    assert env.entity("c")["props"]["cash"] == (80 if asset == "payment" else 100)
    if asset == "delivery":
        assert env.entity("c")["props"]["goods"].get("units", 0) == 0
        assert result.outputs["goods_conserved"] is True
    assert result.to_dict() == fg_env.Env.restore(contract, snapshot).run(actor).to_dict()
