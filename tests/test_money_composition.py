"""Money mechanisms compose: markets that share a currency, an economy ledger, and the author's own wages, taxes
and dividends all move the same cash without any one of them refusing the others."""
import json

import pytest

import fg_env

MARKETS = {
    "acme": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50, "taker_fee_bps": 50},
    "pm": {"kind": "market", "mode": "prediction", "who": "trader", "outcomes": ["up", "down"]},
    "sale": {"kind": "market", "mode": "auction", "format": "english", "who": "trader", "stock": 2},
    "shop": {"kind": "market", "mode": "posted", "who": "trader"},
}
SELLS = {"acme": [("acme_sell", {"qty": 10, "price": 50})], "pm": [("pm_buy", {"outcome": "up", "spend": 100})],
         "sale": [("sale_bid", {"price": 10})], "shop": []}
BUYS = {"acme": [("acme_buy", {"qty": 10, "price": 50})], "pm": [], "sale": [], "shop": []}


def scripted(plan):
    replies = []

    def participant(wake):
        for tool, args in plan.pop((wake.round, wake.entity_id), []):
            replies.append((tool, wake.call(tool, args)))
        if not wake.done:
            wake.end()

    return participant, replies


def play(contract, plan, rounds=1):
    env = fg_env.load(contract, seed=1)
    participant, replies = scripted(dict(plan))
    result = env.run(participant, rounds=rounds)
    assert result.status != "failed", result.error
    return env, replies


def economy(mechanisms, **extra):
    return {"name": "Economy", "clock": {"rounds": 2},
            "types": {"trader": {"agent": True, "props": {"cash": 10000, "acme_shares": 100}}},
            "entities": {t: {"type": "trader"} for t in "ab"},
            "stages": [{"name": "trade", "turns": "sequential", "max_actions": 10, "max_calls": 12}],
            "mechanisms": {name: {**use, **({"stage": "trade"} if use["kind"] == "market" else {})} for name, use in mechanisms.items()},
            **extra}


def errors(contract):
    return [i.message for i in fg_env.check(contract) if i.severity == "error"]


@pytest.mark.parametrize("pair", [("acme", "pm"), ("acme", "sale"), ("pm", "sale"), ("acme", "shop"), ("pm", "shop")])
def test_markets_sharing_cash_never_refuse_each_other(pair):
    contract = economy({name: MARKETS[name] for name in pair})
    assert not errors(contract)
    _, replies = play(contract, {(1, "a"): [c for m in pair for c in SELLS[m]], (1, "b"): [c for m in pair for c in BUYS[m]]})
    assert all(reply.ok for _, reply in replies), [(tool, reply.text) for tool, reply in replies]


def test_author_wages_taxes_and_dividends_run_beside_a_book():
    contract = economy({"acme": MARKETS["acme"]}, world={"treasury": {"default": 0}},
                       actions={"pay_tax": {"by": "trader", "do": ["$actor.cash -= 10", "$world.treasury += 10"]},
                                "earn": {"by": "trader", "do": ["$actor.cash += 100"]}},
                       events=[{"do": [{"each": "trader", "do": ["$it.cash += $it.acme_shares * 0.5"]}]}])
    assert not errors(contract)
    env, replies = play(contract, {(1, "a"): [("acme_buy", {"qty": 1, "price": 49}), ("pay_tax", {}), ("earn", {})]})
    assert all(reply.ok for _, reply in replies), [(tool, reply.text) for tool, reply in replies]
    a = env.world.entities["a"].properties
    assert a["cash"] + a["acme_reserved_cash"] == pytest.approx(10000 + 50 - 10 + 100)


def test_a_ledger_counts_the_cash_its_markets_hold():
    contract = economy({"money": {"kind": "economy", "mode": "ledger", "who": "trader", "currencies": {"cash": {"start": 1000}},
                                  "sources": {"wage": {"to": "trader", "amount": 50}}},
                        "acme": MARKETS["acme"], "pm": MARKETS["pm"], "sale": MARKETS["sale"]})
    contract["types"]["trader"]["props"] = {"acme_shares": 100}
    assert not errors(contract)
    _, replies = play(contract, {(1, "a"): [("acme_sell", {"qty": 5, "price": 50}), ("pm_buy", {"outcome": "up", "spend": 100}),
                                            ("sale_bid", {"price": 10})],
                                 (1, "b"): [("acme_buy", {"qty": 5, "price": 50}), ("acme_buy", {"qty": 1, "price": 40})]},
                     rounds=2)
    assert all(reply.ok for _, reply in replies), [(tool, reply.text) for tool, reply in replies]


def test_a_ledger_starts_its_supply_from_everything_that_holds_its_currency():
    crowd = {**MARKETS["acme"], "crowd": {"noise": {"count": 3, "cash": 500, "shares": 10}}}
    contract = economy({"money": {"kind": "economy", "mode": "ledger", "who": "trader", "currencies": {"cash": {"start": 1000}}},
                        "acme": crowd, "pm": MARKETS["pm"]})
    contract["types"]["trader"]["props"] = {"acme_shares": 100}
    assert not errors(contract)
    env = fg_env.load(contract, seed=1)
    assert env.props["money_supply"]["cash"] == pytest.approx(2 * 1000 + 3 * 500 + env.props["pm_vault"])
    assert env.run(None, rounds=2).status == "completed"


def test_a_ledger_still_catches_cash_the_author_creates_outside_its_sources():
    contract = economy({"money": {"kind": "economy", "mode": "ledger", "who": "trader", "currencies": {"cash": {"start": 1000}}},
                        "acme": MARKETS["acme"]},
                       actions={"earn": {"by": "trader", "do": ["$actor.cash += 100"]}})
    contract["types"]["trader"]["props"] = {"acme_shares": 100}
    participant, _ = scripted({(1, "a"): [("earn", {})]})
    result = fg_env.load(contract, seed=1).run(participant, rounds=1)
    assert result.status == "failed" and "named sources and sinks" in result.error


def test_mechanisms_attached_to_one_declared_stage_share_its_turn():
    contract = {"name": "Floor", "clock": {"rounds": 1},
                "types": {"trader": {"agent": True, "props": {"cash": 1000, "acme_shares": 10}}},
                "entities": {"a": {"type": "trader"}, "b": {"type": "trader"}},
                "stages": [{"name": "floor", "turns": "sequential"}],
                "mechanisms": {"acme": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 10, "stage": "floor"},
                               "chat": {"kind": "social", "mode": "channels", "who": "trader", "stage": "floor"},
                               "v": {"kind": "decision", "mode": "ballot", "who": "trader", "options": ["y", "n"], "stage": "floor"}}}
    _, replies = play(contract, {(1, "a"): [("chat_say", {"channel": "general", "text": "selling"}),
                                            ("acme_sell", {"qty": 1, "price": 10}), ("acme_cancel_all", {}),
                                            ("v_vote", {"choice": "y"})]})
    assert all(reply.ok for _, reply in replies), [(tool, reply.text) for tool, reply in replies]
    declared = json.loads(json.dumps(contract))
    declared["stages"][0]["max_actions"] = 1
    _, replies = play(declared, {(1, "a"): [("chat_say", {"channel": "general", "text": "hi"}), ("acme_sell", {"qty": 1, "price": 10})]})
    assert [reply.ok for _, reply in replies] == [True, False]  # the author's own budget stands
    game = json.loads(json.dumps(contract))
    game["actions"] = {"move": {"by": "trader", "do": []}}
    _, replies = play(game, {(1, "a"): [("move", {}), ("move", {})]})
    assert [reply.ok for _, reply in replies] == [True, False]  # a stage with the author's own moves keeps one per turn


def test_check_warns_when_one_agent_type_takes_a_separate_turn_per_mechanism():
    floor = {"acme": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 10},
             "chat": {"kind": "social", "mode": "channels", "who": "trader"},
             "v": {"kind": "decision", "mode": "ballot", "who": "trader", "options": ["y", "n"]}}
    contract = {"name": "Floor", "clock": {"rounds": 1}, "types": {"trader": {"agent": True}},
                "entities": {"a": {"type": "trader"}, "b": {"type": "trader"}}, "mechanisms": floor}
    warned = [i for i in fg_env.check(contract) if i.severity == "warning" and i.path == "mechanisms"]
    assert len(warned) == 1 and "acme, chat and v" in warned[0].message and '"stage"' in warned[0].fix
    shared = {**contract, "stages": [{"name": "floor", "turns": "sequential"}],
              "mechanisms": {name: {**use, "stage": "floor"} for name, use in floor.items()}}
    assert not [i for i in fg_env.check(shared) if i.path == "mechanisms"]
