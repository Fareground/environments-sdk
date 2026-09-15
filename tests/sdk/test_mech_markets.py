"""Market mechanisms: order book, automated market makers, auctions, posted prices and analytics."""
import json
import math

import pytest

import fg_env
from fg_env.sdk.mechanisms import amm, auctions, order_book, posted
from fg_env.sdk.mechanisms.market_stats import autocorr, excess_kurtosis, log_returns, realism_score, series_stats


def scripted(plan):
    """A participant that makes the calls planned for (round, entity) and records every reply."""
    replies = []

    def participant(wake):
        for tool, args in plan.pop((wake.round, wake.entity_id), []):
            replies.append((wake.round, wake.entity_id, tool, wake.call(tool, args)))
        if not wake.done:
            wake.end()

    return participant, replies


def play(contract, plan, rounds=1, seed=1):
    env = fg_env.load(contract, seed=seed)
    participant, replies = scripted(dict(plan))
    result = env.run(participant, rounds=rounds)
    assert result.status != "failed", result.error
    return env, replies


def props(env, entity_id):
    return env.world.entities[entity_id].properties


def replies_of(replies, entity_id):
    return [reply for _, who, _, reply in replies if who == entity_id]


# ---------------------------------------------------------------------------
# Order book
# ---------------------------------------------------------------------------


def book(**config):
    mechanism = {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50, "stage": "trade", **config}
    return {"name": "Book", "clock": {"rounds": 6},
            "types": {"trader": {"agent": True, "props": {"cash": 0}}},
            "entities": {t: {"type": "trader", "props": {"cash": 10000, "acme_shares": 100}} for t in "abcd"},
            "stages": [{"name": "trade", "turns": "sequential", "max_actions": 10, "max_calls": 12}],
            "mechanisms": {"acme": mechanism}}


def test_order_book_contract_checks_clean_and_configs_are_checked_with_fixes():
    assert not [i for i in fg_env.check(book(maker_fee_bps=2, halt_pct=0.1)) if i.severity == "error"]
    wrong = book(who="trdr")
    assert any("who 'trdr' is not a declared type" in i.message for i in fg_env.check(wrong))
    typo = book(crowd={"noize": {"count": 1}})
    assert any(i.path.startswith("mechanisms.acme.crowd") for i in fg_env.check(typo))


def test_price_time_priority_and_partial_fills():
    env, replies = play(book(), {
        (1, "a"): [("acme_sell", {"qty": 10, "price": 50})],
        (1, "b"): [("acme_sell", {"qty": 10, "price": 50})],
        (1, "c"): [("acme_sell", {"qty": 10, "price": 49.9})],
        (1, "d"): [("acme_buy", {"qty": 25, "price": 50})]})
    fill = replies_of(replies, "d")[0]
    assert fill.ok and "filled 25 at avg" in fill.text
    assert [(e["price"], e["qty"]) for e in env.world.records("acme_tape")] == [(49.9, 10), (50, 10), (50, 5)]
    assert [(o["owner"], o["qty"]) for o in env.props["acme_asks"]] == [("b", 5)]
    assert props(env, "d")["acme_shares"] == 125 and props(env, "d")["cash"] == pytest.approx(10000 - 499 - 500 - 250)
    assert props(env, "b")["acme_shares"] == 90 and props(env, "b")["acme_reserved_shares"] == 5
    assert props(env, "c")["cash"] == pytest.approx(10499)
    assert not order_book.audit(env.world, "acme")


def test_maker_and_taker_fees_settle_exactly():
    env, _ = play(book(maker_fee_bps=10, taker_fee_bps=20), {
        (1, "a"): [("acme_sell", {"qty": 10, "price": 50})],
        (1, "b"): [("acme_buy", {"qty": 10, "price": 49})],
        (1, "c"): [("acme_sell", {"qty": 10})],
        (1, "d"): [("acme_buy", {"qty": 10})]})
    a, b, c, d = (props(env, x) for x in "abcd")
    assert b["acme_reserved_cash"] == 0 and b["cash"] == pytest.approx(10000 - 490 * 1.001) and b["acme_shares"] == 110
    assert c["cash"] == pytest.approx(10000 + 490 * (1 - 0.002))
    assert a["cash"] == pytest.approx(10000 + 500 * (1 - 0.001))
    assert d["cash"] == pytest.approx(10000 - 500 * 1.002)
    assert env.props["acme_fees"] == pytest.approx(490 * 0.003 + 500 * 0.003)
    assert d["acme_fees_paid"] == pytest.approx(1.0) and a["acme_fees_paid"] == pytest.approx(0.5)
    assert not order_book.audit(env.world, "acme")


def test_market_orders_stop_at_the_collar():
    env, replies = play(book(collar_pct=0.05), {
        (1, "a"): [("acme_sell", {"qty": 5, "price": 50})],
        (1, "b"): [("acme_sell", {"qty": 5, "price": 60})],
        (1, "d"): [("acme_buy", {"qty": 10})]})
    receipt = replies_of(replies, "d")[0].text
    assert "filled 5" in receipt and "5 cancelled" in receipt and "collar" in receipt
    assert [(o["owner"], o["price"]) for o in env.props["acme_asks"]] == [("b", 60)]
    assert props(env, "d")["acme_reserved_cash"] == 0 and props(env, "d")["acme_shares"] == 105


def test_circuit_breaker_halts_cancels_the_remainder_and_resumes():
    env = fg_env.load(book(halt_pct=0.1, halt_rounds=1), seed=1)
    tools = {}
    participant, replies = scripted({
        (1, "a"): [("acme_sell", {"qty": 5, "price": 50})],
        (1, "b"): [("acme_sell", {"qty": 5, "price": 56}), ("acme_sell", {"qty": 5, "price": 56.5})],
        (1, "d"): [("acme_buy", {"qty": 15, "price": 57}), ("acme_buy", {"qty": 1, "price": 57})]})

    def watching(wake):
        tools[(wake.round, wake.entity_id)] = {t.name for t in wake.tools}
        participant(wake)

    assert env.run(watching, rounds=3).status != "failed"
    first, second = replies_of(replies, "d")
    assert "filled 10" in first.text and "5 cancelled (the circuit breaker halted trading)" in first.text
    assert not second.ok
    assert env.props["acme_halts"] == 1 and env.props["acme_last"] == 56
    assert "acme_cancel" in tools[(2, "b")] and "acme_sell" not in tools[(2, "b")]
    assert "acme_buy" in tools[(3, "a")]
    kinds = [e["kind"] for e in env.result().events]
    assert kinds.index("acme_halt") < kinds.index("acme_resume")


def test_cancel_returns_reservations_and_lists_only_own_orders():
    env = fg_env.load(book(maker_fee_bps=10), seed=1)
    schemas = {}
    participant, replies = scripted({
        (1, "a"): [("acme_buy", {"qty": 10, "price": 49}), ("acme_sell", {"qty": 20, "price": 55}),
                   ("acme_sell", {"qty": 5, "price": 56})],
        (1, "b"): [("acme_sell", {"qty": 5, "price": 57})],
        (2, "a"): [("acme_cancel", {"order": "acme_4"}), ("acme_cancel", {"order": "acme_1"}), ("acme_cancel_all", {})]})

    def watching(wake):
        if (wake.round, wake.entity_id) == (2, "a"):
            schemas.update({t.name: t.input_schema for t in wake.tools})
            a = props(env, "a")
            assert a["cash"] == pytest.approx(10000 - 490 * 1.001) and a["acme_reserved_cash"] == pytest.approx(490 * 1.001)
            assert a["acme_shares"] == 75 and a["acme_reserved_shares"] == 25
        participant(wake)

    env.run(watching, rounds=2)
    assert schemas["acme_cancel"]["properties"]["order"]["enum"] == ["acme_1", "acme_2", "acme_3"]
    not_mine, cancelled, all_gone = replies_of(replies, "a")[3:]
    assert not not_mine.ok and cancelled.ok and "Cancelled 2 order(s)" in all_gone.text
    a = props(env, "a")
    assert (a["cash"], a["acme_shares"], a["acme_reserved_cash"], a["acme_reserved_shares"]) == (10000, 100, 0, 0)
    assert [o["id"] for o in env.props["acme_asks"]] == ["acme_4"] and env.props["acme_bids"] == []


def test_orders_snap_to_tick_and_lot_and_refusals_say_what_to_do():
    env, replies = play(book(tick_size=0.05, lot_size=5), {
        (1, "a"): [("acme_buy", {"qty": 12, "price": 49.99}), ("acme_sell", {"qty": 10, "price": 50.01}),
                   ("acme_buy", {"qty": 300, "price": 49}), ("acme_buy", {"qty": 3, "price": 49}),
                   ("acme_buy", {"qty": 5, "price": 80})]})
    _, _, broke, tiny, outside = [reply for _, _, _, reply in replies]
    assert env.props["acme_bids"][0]["price"] == 49.95 and env.props["acme_bids"][0]["qty"] == 10
    assert env.props["acme_asks"][0]["price"] == 50.05
    assert not broke.ok and "Not enough free cash" in broke.text and "Buy at most" in broke.text
    assert not tiny.ok and not outside.ok


def test_self_trade_prevention_cancels_the_resting_order():
    env, replies = play(book(), {(1, "a"): [("acme_buy", {"qty": 10, "price": 50}), ("acme_sell", {"qty": 5, "price": 49})]})
    assert "own opposite order" in replies[-1][3].text
    assert env.props["acme_bids"] == [] and env.props["acme_asks"][0]["owner"] == "a"
    assert props(env, "a")["cash"] == 10000 and props(env, "a")["acme_reserved_cash"] == 0


def test_orders_expire_and_short_selling_is_bounded():
    env, replies = play(book(order_ttl=2, short_limit=50), {
        (1, "a"): [("acme_buy", {"qty": 10, "price": 40})],
        (1, "b"): [("acme_sell", {"qty": 150, "price": 60})],
        (1, "c"): [("acme_sell", {"qty": 151, "price": 60})]}, rounds=3)
    assert replies_of(replies, "b")[0].ok and not replies_of(replies, "c")[0].ok
    assert env.props["acme_bids"] == [] and env.props["acme_asks"] == []
    assert props(env, "a")["cash"] == 10000 and props(env, "b")["acme_shares"] == 100
    expired = [e for e in env.result().events if e["kind"] == "acme_expired"]
    assert {tuple(e["to"]) for e in expired} == {("a",), ("b",)}


CROWD = {"name": "Crowd", "clock": {"rounds": 500},
         "types": {"trader": {"agent": True, "props": {"cash": 0}}},
         "population": [{"type": "trader", "count": 3, "props": {"cash": 5000, "acme_shares": 100}}],
         "mechanisms": {"acme": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50, "maker_fee_bps": 1,
                                 "taker_fee_bps": 3, "halt_pct": 0.15, "short_limit": 20, "order_ttl": 5,
                                 "crowd": {"market_maker": {"count": 2, "cash": 50000, "shares": 1000},
                                           "noise": {"count": 6, "cash": 10000, "shares": 200}}}}}


def test_conservation_holds_over_500_rounds_of_random_traders_makers_and_noise():
    env = fg_env.load(CROWD, seed=11)
    result = env.run()  # the generated invariant is checked after every action and round
    assert result.status == "completed", result.error
    assert not order_book.audit(env.world, "acme")
    assert result.outputs["acme_trades"] > 1000 and result.outputs["acme_fees"] > 0


def test_coded_traders_produce_a_stylized_facts_tape():
    crowd = {"market_maker": {"count": 3, "cash": 60000, "shares": 1200},
             "momentum": {"count": 4, "cash": 10000, "shares": 200},
             "mean_reversion": {"count": 4, "cash": 10000, "shares": 200},
             "fundamentalist": {"count": 4, "cash": 10000, "shares": 200},
             "noise": {"count": 8, "cash": 10000, "shares": 200}}
    reference = "{sigma: 0.01, kurtosis: 1, acf1: 0, acf_abs: 0.1, avg_volume: 60, vol_volume_corr: 0.3}"
    contract = {"name": "Tape", "clock": {"rounds": 250}, "types": {"trader": {"agent": True}},
                "mechanisms": {"acme": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50,
                                        "crowd": crowd}},
                "outputs": {"realism": {"expr": "$market_realism({prices: $series.acme_price, volumes: $series.acme_volume}, "
                                                f"{reference})", "type": "map"}}}
    result = fg_env.load(contract, seed=3).run()
    assert result.status == "completed", result.error
    prices, volumes, spreads = (result.series[f"acme_{k}"] for k in ("price", "volume", "spread"))
    assert sum(1 for v in volumes if v > 0) >= 0.9 * len(volumes) and sum(volumes) / len(volumes) > 10
    assert max(spreads) < 0.1 * min(prices) and sum(spreads) / len(spreads) < 0.02 * sum(prices) / len(prices)
    stats = series_stats(prices, volumes)
    assert stats["sigma"] > 0 and abs(stats["acf1"]) < 0.5
    realism = result.outputs["realism"]
    assert {c["key"] for c in realism["components"]} == {"volatility", "fat_tails", "no_return_memory",
                                                         "volatility_clustering", "volume", "volume_volatility"}
    assert 0 <= realism["score"] <= 1 and all(0 <= c["score"] <= 1 for c in realism["components"])


def test_crowd_run_resumes_identically_after_a_snapshot():
    contract = {**CROWD, "clock": {"rounds": 12}}
    straight = fg_env.load(contract, seed=5).run().to_dict()
    env = fg_env.load(contract, seed=5)
    env.run(rounds=5)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def test_market_analytics():
    prices = [100, 101, 100, 102, 101, 103]
    returns = log_returns(prices)
    assert len(returns) == 5 and returns[0] == pytest.approx(math.log(1.01))
    assert autocorr([1, -1] * 6, 1) < -0.8
    assert excess_kurtosis([0.0] * 50 + [5, -5]) > 3
    same = series_stats(prices, [10, 12, 9, 14, 10, 15])
    assert realism_score(same, same)["components"][0]["score"] == 1.0
    contract = {"name": "Stats", "clock": {"rounds": 1}, "types": {"thing": {}},
                "outputs": {"r": "$returns([1, 2], log)", "vol": "$realized_vol([100, 110, 100])", "ac": "$autocorr([1, 2, 3, 4, 5], 1)",
                            "k": "$excess_kurtosis([1, 2, 3, 4])", "cl": "$vol_clustering([100, 101, 100, 102, 101, 103, 100, 104])",
                            "vv": "$volume_vol_corr([100, 101, 103, 100], [1, 2, 3, 4])", "s": "$market_stats([100, 101])",
                            "real": "$market_realism([100, 101, 100, 102], $market_stats([100, 102, 101, 103]))"}}
    assert not [i for i in fg_env.check(contract) if i.severity == "error"]
    out = fg_env.load(contract, seed=1).run().outputs
    assert out["r"] == [pytest.approx(math.log(2))] and out["vol"] > 0 and out["s"]["bars"] == 2
    assert set(out["real"]) == {"score", "components"}


# ---------------------------------------------------------------------------
# Automated market makers
# ---------------------------------------------------------------------------


def test_lmsr_and_cpmm_math_invert_exactly():
    q, b = [0.0, 0.0, 0.0], 20.0
    assert sum(amm.lmsr_prices(q, b)) == pytest.approx(1)
    n = amm.lmsr_shares_for_cost(q, b, 1, 7.5)
    after = [0.0, n, 0.0]
    assert amm.lmsr_cost(after, b) - amm.lmsr_cost(q, b) == pytest.approx(7.5)
    assert amm.lmsr_shares_for_refund(after, b, 1, 7.5) == pytest.approx(n)
    assert amm.lmsr_prices(after, b)[1] > 1 / 3
    pools = [50.0, 50.0]
    shares, grown = amm.cpmm_buy(pools, 0, 10)
    assert math.prod(grown) == pytest.approx(2500) and amm.cpmm_prices(grown)[0] > 0.5
    refund, restored = amm.cpmm_sell(grown, 0, shares)
    assert refund == pytest.approx(10, rel=1e-6) and restored == pytest.approx(pools)
    assert amm.cpmm_cost_for_shares(pools, 0, shares) == pytest.approx(10, rel=1e-6)
    assert amm.cpmm_shares_for_refund(grown, 0, 10) == pytest.approx(shares, rel=1e-6)


def market(maker, stage=True, **config):
    mechanism = {"kind": "market", "mode": "prediction", "who": "trader", "outcomes": ["ada", "bo", "cy"], "maker": maker,
                 "liquidity": 20, "fee_pct": 0.02, "resolve_at": 2, "outcome": "$world.truth", **config}
    contract = {"name": "PM", "clock": {"rounds": 3}, "world": {"truth": "ada"},
                "types": {"trader": {"agent": True, "props": {"cash": 100}}},
                "entities": {t: {"type": "trader"} for t in "abc"}, "mechanisms": {"pm": mechanism}}
    if stage:
        mechanism["stage"] = "trade"
        contract["stages"] = [{"name": "trade", "turns": "sequential", "max_actions": 5, "max_calls": 8}]
    return contract


@pytest.mark.parametrize("maker", ["lmsr", "cpmm"])
def test_prediction_market_trades_with_limits_resolves_and_pays(maker):
    assert not [i for i in fg_env.check(market(maker)) if i.severity == "error"]
    env, replies = play(market(maker), {
        (1, "a"): [("pm_buy", {"outcome": "ada", "shares": 10})],
        (1, "b"): [("pm_buy", {"outcome": "bo", "spend": 20}), ("pm_buy", {"outcome": "cy", "shares": 500, "spend": 1})],
        (2, "a"): [("pm_sell", {"outcome": "ada", "shares": 4}), ("pm_sell", {"outcome": "ada", "shares": 1, "receive": 5})]},
        rounds=3)
    bought, sold, floor = replies_of(replies, "a")
    spent, limited = replies_of(replies, "b")
    assert bought.ok and "Bought 10 ada" in bought.text and spent.ok
    assert not limited.ok and "more than your limit" in limited.text
    assert sold.ok and not floor.ok and "less than your minimum" in floor.text
    assert env.props["pm_resolved"] == "ada" and env.props["pm_payout"] == pytest.approx(6)
    assert props(env, "a")["pm_shares"] == {} and props(env, "b")["pm_shares"] == {}
    subsidy = 20 * math.log(3) if maker == "lmsr" else 20
    total = sum(props(env, t)["cash"] for t in "abc") + env.props["pm_vault"] + env.props["pm_fees"]
    assert total == pytest.approx(300 + subsidy)
    assert not amm.audit(env.world, "pm")


@pytest.mark.parametrize("maker", ["lmsr", "cpmm"])
def test_prediction_market_conserves_cash_under_random_traders(maker):
    contract = market(maker, stage=False, resolve_at=8)
    contract["clock"]["rounds"] = 10
    env = fg_env.load(contract, seed=9)
    result = env.run()
    assert result.status == "completed", result.error
    assert result.outputs["pm_volume"] > 0 and result.outputs["pm_winner"] == "ada"
    assert not amm.audit(env.world, "pm")


# ---------------------------------------------------------------------------
# Auctions
# ---------------------------------------------------------------------------


def house(fmt, **config):
    mechanism = {"kind": "market", "mode": "auction", "format": fmt, "who": "bidder", "item": "a vase", "stock": 2, "reserve": 35,
                 **config}
    return {"name": "Auction", "clock": {"rounds": 6}, "types": {"bidder": {"agent": True, "props": {"cash": 100}}},
            "entities": {t: {"type": "bidder"} for t in "abc"}, "mechanisms": {"house": mechanism}}


@pytest.mark.parametrize("fmt, pays", [("first_price", 50), ("second_price", 40)])
def test_sealed_auctions_pick_the_highest_bid_and_price_by_format(fmt, pays):
    env, replies = play(house(fmt), {(1, "a"): [("house_bid", {"price": 30})], (1, "b"): [("house_bid", {"price": 50})],
                                     (1, "c"): [("house_bid", {"price": 40})]})
    assert not replies_of(replies, "a")[0].ok  # below the reserve
    b, c = props(env, "b"), props(env, "c")
    assert b["house_units"] == 1 and b["cash"] == pytest.approx(100 - pays) and b["house_escrow"] == 0
    assert c["cash"] == 100 and env.props["house_revenue"] == pays and env.props["house_stock"] == 1
    result = env.world.records("house_results")[-1]
    assert result["winner"] == "b" and result["price"] == pays


def test_ties_go_to_the_earliest_bid_or_a_seeded_draw_and_a_lone_vickrey_bid_pays_the_reserve():
    plan = {(1, "a"): [("house_bid", {"price": 60})], (1, "b"): [("house_bid", {"price": 60})]}
    env, _ = play(house("first_price"), plan)
    assert env.world.records("house_results")[-1]["winner"] == "a"
    winners = {play(house("first_price", ties="random"), plan, seed=s)[0].world.records("house_results")[-1]["winner"]
               for s in range(12)}
    assert winners == {"a", "b"}
    lone, _ = play(house("second_price"), {(1, "c"): [("house_bid", {"price": 90})]})
    assert props(lone, "c")["cash"] == 65


def test_english_auction_raises_refunds_and_closes_after_the_timeout():
    env, replies = play(house("english", increment=5, timeout=1, stock=1), {
        (1, "a"): [("house_bid", {"price": 40})],
        (2, "b"): [("house_bid", {"price": 42}), ("house_bid", {"price": 45})],
        (3, "a"): [("house_bid", {"price": 50})]}, rounds=5)
    too_low, raised = replies_of(replies, "b")
    assert not too_low.ok and raised.ok
    assert props(env, "a")["house_units"] == 1 and props(env, "a")["cash"] == 50
    assert props(env, "b")["cash"] == 100 and props(env, "b")["house_escrow"] == 0
    assert env.props["house_revenue"] == 50 and not env.props["house_lot"]["open"]
    assert env.world.records("house_results")[-1]["round"] == 4


def test_dutch_clock_falls_until_someone_takes_the_lot_or_the_reserve():
    env, replies = play(house("dutch", start_price=100, decrement=20, reserve=50, stock=1), {
        (2, "b"): [("house_bid", {"price": 70}), ("house_bid", {"price": 90})]}, rounds=3)
    too_low, took = replies_of(replies, "b")
    assert not too_low.ok and took.ok and "clock price 80" in took.text
    assert props(env, "b")["cash"] == 20 and props(env, "b")["house_units"] == 1
    unsold, _ = play(house("dutch", start_price=100, decrement=20, reserve=50, stock=1), {}, rounds=4)
    result = unsold.world.records("house_results")[-1]
    assert result["winner"] == "" and "reserve" in result["note"] and unsold.props["house_stock"] == 1


@pytest.mark.parametrize("rule, price", [("lowest_accepted", 25), ("highest_rejected", 20)])
def test_uniform_price_auction_sells_units_to_the_highest_bids_at_one_price(rule, price):
    env, _ = play(house("uniform", units=3, stock=3, reserve=10, price_rule=rule), {
        (1, "a"): [("house_bid", {"price": 30, "qty": 2})], (1, "b"): [("house_bid", {"price": 25, "qty": 1})],
        (1, "c"): [("house_bid", {"price": 20, "qty": 1})]})
    assert props(env, "a")["house_units"] == 2 and props(env, "a")["cash"] == 100 - 2 * price
    assert props(env, "b")["house_units"] == 1 and props(env, "b")["cash"] == 100 - price
    assert props(env, "c")["cash"] == 100 and env.props["house_revenue"] == 3 * price


def dealers(contract):
    contract["types"]["dealer"] = {"agent": True, "props": {"cash": 0}}
    contract["entities"].update({s: {"type": "dealer", "props": {"house_units": 2}} for s in ("s", "t")})
    return contract


def test_double_auction_clears_bids_and_asks_at_one_price():
    env, _ = play(dealers(house("double", sellers="dealer", units=2)), {
        (1, "a"): [("house_bid", {"price": 60, "qty": 1})], (1, "b"): [("house_bid", {"price": 40, "qty": 1})],
        (1, "s"): [("house_ask", {"price": 30, "qty": 1})], (1, "t"): [("house_ask", {"price": 50, "qty": 1})]})
    assert props(env, "a")["house_units"] == 1 and props(env, "a")["cash"] == 55
    assert props(env, "s")["cash"] == 45 and props(env, "s")["house_units"] == 1
    assert props(env, "b")["cash"] == 100 and props(env, "t")["house_units"] == 2 and props(env, "t")["house_escrow_units"] == 0


@pytest.mark.parametrize("fmt", auctions.FORMATS)
def test_auctions_conserve_cash_and_units_with_random_bidders(fmt):
    multi = fmt in ("uniform", "double")
    packages = {"items": ["north", "south", "east"], "reserves": {"east": 20}} if fmt == "combinatorial" else {}
    contract = house(fmt, start_price=90, decrement=10, units=2 if multi else 1, stock=4, **packages)
    if fmt == "double":
        contract = dealers(contract)
        contract["mechanisms"]["house"]["sellers"] = "dealer"
    contract["clock"]["rounds"] = 10
    assert not [i for i in fg_env.check(contract) if i.severity == "error"]
    env = fg_env.load(contract, seed=4)
    result = env.run()
    assert result.status == "completed", result.error
    assert not auctions.audit(env.world, "house")


# ---------------------------------------------------------------------------
# Posted-price markets
# ---------------------------------------------------------------------------


def farm(stage=True, **config):
    mechanism = {"kind": "market", "mode": "posted", "who": "shopper", "sellers": "farmer", "sponsor_fee": 2,
                 "listings": {"apples": {"seller": "ana", "item": "apples", "price": 3, "stock": 10, "capacity": 4,
                                         "negotiable": True, "floor": 2.5},
                              "pears": {"seller": "ben", "item": "pears", "price": 4, "stock": 5, "rating": 4.5, "ratings": 2},
                              "plums": {"seller": "ben", "item": "plums", "price": 2, "stock": 5}}, **config}
    contract = {"name": "Farm", "clock": {"rounds": 4},
                "types": {"shopper": {"agent": True, "props": {"cash": 50}}, "farmer": {"agent": True, "props": {"cash": 10}}},
                "entities": {"ana": {"type": "farmer", "name": "Ana"}, "ben": {"type": "farmer", "name": "Ben"},
                             "sam": {"type": "shopper"}, "tia": {"type": "shopper"}},
                "mechanisms": {"market": mechanism}}
    if stage:
        mechanism["stage"] = "shop"
        contract["stages"] = [{"name": "shop", "turns": "sequential", "max_actions": 6, "max_calls": 10}]
    return contract


def test_posted_prices_sell_stock_within_capacity_and_pay_the_seller():
    assert not [i for i in fg_env.check(farm()) if i.severity == "error"]
    env, replies = play(farm(), {(1, "sam"): [("market_buy", {"listing": "apples", "qty": 3}),
                                              ("market_buy", {"listing": "apples", "qty": 2})],
                                 (2, "sam"): [("market_buy", {"listing": "apples", "qty": 2})]}, rounds=2)
    first, over, next_round = replies_of(replies, "sam")
    assert first.ok and not over.ok and "only 1 more this round" in over.text and next_round.ok
    assert props(env, "sam")["market_basket"] == {"apples": 5} and props(env, "sam")["cash"] == 35
    assert props(env, "ana")["cash"] == 25 and props(env, "apples")["stock"] == 5


def test_shelf_ranks_sponsored_then_rating_then_price_and_promotions_cut_prices():
    env, _ = play(farm(), {(1, "ana"): [("market_promote", {"listing": "apples", "pct": 0.2, "rounds": 2})],
                           (1, "ben"): [("market_sponsor", {"listing": "plums", "rounds": 1})]})
    world = env.world
    assert posted.price_now(world, world.entities["apples"]) == 2.4
    assert [e.id for e in posted.shelf(world, "market")] == ["plums", "pears", "apples"]
    assert props(env, "ben")["cash"] == 8 and env.props["market_ad_revenue"] == 2
    env.run("idle", rounds=2)
    assert posted.price_now(world, world.entities["apples"]) == 3
    assert [e.id for e in posted.shelf(world, "market")] == ["pears", "plums", "apples"]


def test_haggling_accepts_at_the_floor_counters_below_it_and_the_counter_can_be_accepted():
    env, replies = play(farm(), {
        (1, "sam"): [("market_offer", {"listing": "apples", "price": 2}), ("market_accept", {"listing": "apples"})],
        (1, "tia"): [("market_offer", {"listing": "apples", "price": 2.6}), ("market_offer", {"listing": "apples", "price": 3.5}),
                     ("market_offer", {"listing": "pears", "price": 1})]})
    countered, accepted = replies_of(replies, "sam")
    deal, too_much, fixed = replies_of(replies, "tia")
    assert countered.ok and "counters at 2.5" in countered.text
    assert accepted.ok and props(env, "sam")["cash"] == 47.5
    assert deal.ok and "accepts" in deal.text and props(env, "tia")["cash"] == pytest.approx(47.4)
    assert not too_much.ok and "No need to offer" in too_much.text and not fixed.ok


def test_ratings_are_once_per_buyer_and_the_market_conserves_cash_and_goods():
    env, replies = play(farm(), {(1, "sam"): [("market_rate", {"listing": "pears", "stars": 5}),
                                              ("market_buy", {"listing": "pears", "qty": 1}),
                                              ("market_rate", {"listing": "pears", "stars": 1}),
                                              ("market_rate", {"listing": "pears", "stars": 5})]})
    before, bought, rated, again = replies_of(replies, "sam")
    assert not before.ok and bought.ok and rated.ok and not again.ok
    assert props(env, "pears")["rating"] == pytest.approx((4.5 * 2 + 1) / 3, abs=1e-4)
    crowd = fg_env.load(farm(stage=False), seed=2)
    result = crowd.run()
    assert result.status == "completed", result.error
    assert result.outputs["market_sales"] > 0 and not posted.audit(crowd.world, "market")


# ---------------------------------------------------------------------------
# The market family: kinds, fields, actions, tools, guide
# ---------------------------------------------------------------------------


def errors_of(contract):
    return [i for i in fg_env.check(contract) if i.severity == "error"]


@pytest.mark.parametrize("old, contract, mode", [("order_book", book(), "order_book"), ("auction", house("first_price"), "auction"),
                                                 ("prediction_market", market("lmsr"), "prediction"),
                                                 ("posted_market", farm(), "posted")])
def test_an_old_market_kind_says_the_family_and_mode(old, contract, mode):
    name, config = next(iter(contract["mechanisms"].items()))
    config = {key: value for key, value in config.items() if key != "mode"}
    issue = next(i for i in errors_of({**contract, "mechanisms": {name: {**config, "kind": old}}})
                 if i.path == f"mechanisms.{name}.kind")
    assert issue.message == f"'{old}' is now kind 'market' with mode '{mode}'"


def test_a_market_field_typo_or_an_old_field_name_names_the_mode_and_its_fields():
    typo = errors_of(book(tick_sise=0.05))
    assert [(i.path, i.message) for i in typo] == [("mechanisms.acme.tick_sise", "`tick_sise` is not a field of `market` mode `order_book`")]
    assert typo[0].fix.startswith("did you mean 'tick_size'?") and "takes: who, start_price, currency" in typo[0].fix
    old = errors_of(house("first_price", bidders="bidder"))
    assert [i.message for i in old] == ["`bidders` is not a field of `market` mode `auction`"]
    foreign = errors_of(farm(outcomes=["a", "b"]))
    assert [i.message for i in foreign] == ["`outcomes` is not a field of `market` mode `posted`"]


def _op_issues(contract, *effects):
    return [(i.path, i.message, i.fix) for i in errors_of({**contract, "events": [{"do": list(effects)}]})]


def test_market_actions_check_their_own_keys():
    assert any(m == "`market.buy` needs `qty`" for _, m, _ in _op_issues(book(), {"market": "acme", "action": "buy", "price": 50}))
    assert any(m == "'trader' is not part of `market.cancel_all`" for _, m, _ in _op_issues(
        book(), {"market": "acme", "action": "cancel_all", "trader": "a"}))
    path, message, fix = _op_issues(book(), {"market": "acme", "action": "bid"})[0]
    assert path.endswith(".action") and message == "'bid' is not an action of acme (market order_book)"
    assert fix == "actions: buy, sell, cancel, cancel_all, algo, rebase"
    _, _, fix = _op_issues(book(), {"market": "acmee", "action": "rebase"})[0]
    assert fix == "did you mean 'acme'?"
    _, _, fix = _op_issues(book(), {"book": "acme", "action": "rebase"})[0]
    assert fix.startswith('`book` is now the `market` op: {"market": "<mechanism>", "action": <action>')
    assert any(m == "a first_price auction takes no asks" for _, m, _ in _op_issues(
        house("first_price"), {"market": "house", "action": "ask", "price": 10}))
    assert any(m == "`items` belongs to a combinatorial auction, not a first_price auction" for _, m, _ in _op_issues(
        house("first_price"), {"market": "house", "action": "bid", "price": 40, "items": ["x"]}))
    assert any(m == "`market.bid` needs `price`" for _, m, _ in _op_issues(house("english"), {"market": "house", "action": "bid"}))
    assert any("needs `qty` (shares), `amount` (money) or both" in m for _, m, _ in _op_issues(
        market("lmsr"), {"market": "pm", "action": "buy", "outcome": "ada"}))
    assert any(m == "`market.resolve` needs `outcome`" for _, m, _ in _op_issues(market("lmsr"), {"market": "pm", "action": "resolve"}))
    assert any(m == "`market.promote` needs `rounds`" for _, m, _ in _op_issues(
        farm(), {"market": "market", "action": "promote", "listing": "apples", "pct": 0.1}))
    assert any(m == "'stars' is not part of `market.offer`" for _, m, _ in _op_issues(
        farm(), {"market": "market", "action": "offer", "listing": "apples", "price": 2, "stars": 3}))


def test_market_actions_act_for_who_they_name():
    contract = {**book(), "events": [{"name": "quote", "phase": "start", "when": "$round == 1",
                                      "do": [{"market": "acme", "action": "sell", "who": "a", "qty": 10, "price": 51}]}]}
    env, _ = play(contract, {})
    assert [(o["owner"], o["qty"], o["price"]) for o in env.props["acme_asks"]] == [("a", 10, 51)]
    assert not order_book.audit(env.world, "acme")


def test_tools_one_offers_a_book_as_a_single_tool():
    env = fg_env.load(book(tools="one"), seed=1)
    offered = {}

    def trade(wake):
        tools = {t.name: t for t in wake.tools}
        offered[wake.entity_id] = tools
        if wake.entity_id == "a":
            assert wake.call("acme", {"action": "sell", "qty": 10, "price": 50}).ok
        if wake.entity_id == "b":
            assert wake.call("acme", {"action": "buy", "qty": 4}).ok
        wake.end()

    env.run(trade, rounds=1)
    assert "acme_buy" not in offered["a"] and offered["a"]["acme"].input_schema["properties"]["action"]["enum"] == ["buy", "sell"]
    assert props(env, "b")["acme_shares"] == 104 and [o["qty"] for o in env.props["acme_asks"]] == [6]


def test_guide_documents_the_market_family():
    page = fg_env.guide("market.auction")
    assert page.startswith("### `market.auction`") and "`house`" in page and "- `bid`" in page and "- `tick`" not in page
    family = fg_env.guide("market")
    assert all(f"- `{mode}`:" in family for mode in ("order_book", "auction", "prediction", "posted"))
    posted = fg_env.guide("market.posted")
    assert "- `set_price`" in posted and "- `open`" not in posted
