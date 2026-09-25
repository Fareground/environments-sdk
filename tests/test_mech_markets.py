"""Market mechanisms: order book, automated market makers, auctions, posted prices and analytics."""
import json
import math
import statistics
from pathlib import Path

import pytest

import fg_env
from fg_env.mechanisms import amm, auctions, order_book, posted
from fg_env.mechanisms.market_stats import autocorr, excess_kurtosis, log_returns, realism_score, series_stats


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


def test_a_crowd_setting_is_checked_where_it_is_written():
    """A setting read for each coded trader names the trader as `$actor`; `$it` is reported at its field, not as
    refused attempts (audit 9 mech M1)."""
    contract = book(crowd={"noise": {"count": 4}}, base_qty="$it.size")
    issues = [i for i in fg_env.check(contract) if i.severity == "error"]
    assert [i.path for i in issues] == ["mechanisms.acme.base_qty"] and "$it" in issues[0].message


@pytest.mark.parametrize("price, says", [(-5, "greater than 0"), (0, "greater than 0"),
                                         ("-5", "a number above 0 or an expression")])
def test_an_order_book_needs_a_start_price_above_zero_said_at_the_field(price, says):
    """A price at or below zero made a dead market, reported only as actions that never succeeded (audit 9 mech
    M2); a number that does not fit reports that alone, not also that it is not text."""
    issues = [i for i in fg_env.check(book(start_price=price)) if i.severity == "error"]
    assert [i.path for i in issues] == ["mechanisms.acme.start_price"] and says in issues[0].message


def test_the_books_accounting_is_audited_each_round_unless_asked_for_every_action():
    """An audit after every action goes over every trader: a round then costs the square of the crowd, so it is the
    choice, not the default (audit 9 mech M3)."""
    checks = lambda contract: [i.check for i in fg_env.load(contract, seed=1).contract.invariants]  # noqa: E731
    assert checks(book()) == ["round"] and checks(book(conserve=True)) == ["action"]


def test_config_slips_are_said_plainly_at_their_field():
    """A near-miss enum value gets a suggestion, an outcome that is no outcome is named at `outcome`, and a number
    too large for a tool says so plainly (audit 9 mech LOWs)."""
    auction = {"kind": "market", "mode": "auction", "format": "secnd_price", "who": "trader", "item": "x", "stock": 1}
    issues = [i for i in fg_env.check({**book(), "mechanisms": {"acme": auction}}) if i.severity == "error"]
    assert issues[0].path == "mechanisms.acme.format" and "did you mean 'second_price'" in issues[0].fix
    wrong = [i for i in fg_env.check(market("lmsr", outcome="dan")) if i.severity == "error"]
    assert [i.path for i in wrong] == ["mechanisms.pm.outcome"] and "not one of the outcomes" in wrong[0].message
    env, replies = play(book(), {(1, "a"): [("acme_buy", {"qty": 1, "price": 1e300})]})
    assert "far too large" in replies_of(replies, "a")[0].text


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


def test_a_negative_maker_fee_is_a_rebate_paid_out_of_the_taker_fee():
    env, _ = play(book(maker_fee_bps=-10, taker_fee_bps=20), {
        (1, "a"): [("acme_sell", {"qty": 10, "price": 50})],
        (1, "b"): [("acme_buy", {"qty": 10, "price": 49})],
        (1, "c"): [("acme_sell", {"qty": 10})],
        (1, "d"): [("acme_buy", {"qty": 10})]})
    a, b, c, d = (props(env, x) for x in "abcd")
    assert (b["acme_reserved_cash"] == 0 and b["cash"]
            == pytest.approx(10000 - 490 * 0.999))  # the resting buyer's rebate
    assert a["cash"] == pytest.approx(10000 + 500 * 1.001) and a["acme_fees_paid"] == pytest.approx(-0.5)
    assert c["cash"] == pytest.approx(10000 + 490 * 0.998) and d["cash"] == pytest.approx(10000 - 500 * 1.002)
    assert env.props["acme_fees"] == pytest.approx((490 + 500) * 0.001)
    assert not order_book.audit(env.world, "acme")
    too_big = [i for i in fg_env.check(book(maker_fee_bps=-30, taker_fee_bps=20)) if i.severity == "error"]
    assert too_big and "at least -20" in str(too_big[0])


def test_large_fragmented_fill_clears_reserve_dust_without_breaking_conservation():
    """A production-sized order may fill in many pieces without ghost reserves."""
    price, total = 305.9825, 1_029_699_006
    pieces = [27_569_486, 18_398_676, 42_734_007, 101_929_627, 121_745_742,
              71_038_535, 109_451_567, 255_843_819, 213_283_669, 41_503_316,
              21_643_251, 4_557_311]
    contract = {
        "name": "Large fragmented fill",
        "clock": {"rounds": 1},
        "types": {"trader": {"agent": True, "props": {"cash": 0}}},
        "entities": {
            "buyer": {"type": "trader", "props": {"cash": total * price * 2, "acme_shares": 0}},
            "seller": {"type": "trader", "props": {"cash": 0, "acme_shares": total}},
        },
        "mechanisms": {"acme": {"kind": "market", "mode": "order_book", "who": "trader",
                                  "start_price": price, "tick_size": 0.0001, "lot_size": 1}},
    }
    env = fg_env.load(contract, seed=1)
    buyer, seller = env.world.entities["buyer"], env.world.entities["seller"]
    starting_cash = buyer.properties["cash"] + seller.properties["cash"]
    order_book.place(env.world, "acme", buyer, "buy", total, price)
    for quantity in pieces:
        order_book.place(env.world, "acme", seller, "sell", quantity)

    assert env.props["acme_bids"] == []
    assert buyer.properties["acme_reserved_cash"] == 0
    assert buyer.properties["cash"] + seller.properties["cash"] == pytest.approx(starting_cash)
    assert order_book.audit(env.world, "acme") == []


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
        (2, "a"): [("acme_cancel", {"order": "acme_4"}), ("acme_cancel", {"order": "acme_1"}),
                   ("acme_cancel_all", {})]})

    def watching(wake):
        if (wake.round, wake.entity_id) == (2, "a"):
            schemas.update({t.name: t.input_schema for t in wake.tools})
            a = props(env, "a")
            assert (a["cash"] == pytest.approx(10000 - 490 * 1.001) and a["acme_reserved_cash"]
                    == pytest.approx(490 * 1.001))
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
    env, replies = play(book(),
                        {(1, "a"): [("acme_buy", {"qty": 10, "price": 50}), ("acme_sell", {"qty": 5, "price": 49})]})
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
         "mechanisms": {"acme": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50,
                                 "maker_fee_bps": 1,
                                 "taker_fee_bps": 3, "halt_pct": 0.15, "short_limit": 20, "order_ttl": 5,
                                 "crowd": {"market_maker": {"count": 2, "cash": 50000, "shares": 1000},
                                           "noise": {"count": 6, "cash": 10000, "shares": 200}}}}}


@pytest.mark.slow
def test_conservation_holds_over_500_rounds_of_random_traders_makers_and_noise():
    env = fg_env.load(CROWD, seed=11)
    result = env.run()  # the generated invariant is checked after every action and round
    assert result.status == "completed", result.error
    assert not order_book.audit(env.world, "acme")
    assert result.outputs["acme_trades"] > 1000 and result.outputs["acme_fees"] > 0


@pytest.mark.parametrize("conserve, check",
                         [(True, "action"), ("action", "action"), ("round", "round"), ("end", "end")])
def test_conservation_can_be_checked_after_every_action_every_round_or_at_the_end(conserve, check):
    contract = {**CROWD, "clock": {"rounds": 3},
                "mechanisms": {"acme": {**CROWD["mechanisms"]["acme"], "conserve": conserve}}}
    assert [i.check for i in fg_env.parse(contract).invariants] == [check]
    straight = fg_env.load({**contract, "mechanisms": {"acme": {**contract["mechanisms"]["acme"], "conserve": True}}},
                           seed=4)
    assert fg_env.load(contract, seed=4).run().to_dict() == straight.run().to_dict()
    unchecked = {**contract, "mechanisms": {"acme": {**contract["mechanisms"]["acme"], "conserve": False}}}
    assert fg_env.parse(unchecked).invariants == []


def test_a_crowd_trades_on_its_book_but_is_not_one_of_the_traders_other_mechanisms_count():
    """Coded crowd traders are `<book>_crowd`, beside `who` rather than under it: a ballot's quorum and the candidates
    for a winner are the declared traders only, while the crowd still trades (and conserves) on the book."""
    contract = {**CROWD, "clock": {"rounds": 3},
                "types": {"trader": {"agent": True, "props": {"cash": 0, "mood": "calm"}}},
                "mechanisms": {**CROWD["mechanisms"],
                               "fee": {"kind": "decision", "mode": "ballot", "who": "trader",
                                       "options": ["cut", "keep"], "quorum": 0.5, "when": "$round == 2"}},
                "end": [{"when": "$round == 3",
                         "winner": "$best(trader, $it.cash + $it.acme_shares * $book(acme).last)"}],
                "outputs": {"fee": "$world.fee_result"}}
    env = fg_env.load(contract, seed=3)
    crowd = [e for e in env.world.entities.values() if e.entity_type != "trader"]
    assert {e.entity_type for e in crowd} == {"acme_market_maker", "acme_noise"}
    assert crowd[0].properties["mood"] == "calm"  # a crowd trader keeps the traders' own props
    assert all(env.contract.is_a(e.entity_type, "acme_crowd") and not env.contract.is_a(e.entity_type, "trader")
               for e in crowd)

    def everyone_votes(wake):
        if "fee_vote" in [tool.name for tool in wake.tools]:
            wake.call("fee_vote", {"choice": "cut"})
        wake.end()

    result = env.run({"trader": everyone_votes})  # the crowd keeps its coded policy
    assert result.status == "ended", result.error
    assert result.outputs["fee"]["decided"] and result.outputs["fee"]["turnout"] == 1
    assert env.world.entities[result.winner].entity_type == "trader"
    assert result.outputs["acme_trades"] > 0 and not order_book.audit(env.world, "acme")


@pytest.mark.slow
def test_coded_traders_produce_a_moving_stylized_facts_tape_across_seeds():
    """The default crowd's price follows a fair value that walks at the book's volatility: it neither pins to the start
    price nor bounces between bid and ask. One seed is not a claim, so the facts are medians over several."""
    crowd = {"market_maker": {"count": 3, "cash": 60000, "shares": 1200},
             "momentum": {"count": 4, "cash": 10000, "shares": 200},
             "mean_reversion": {"count": 4, "cash": 10000, "shares": 200},
             "fundamentalist": {"count": 4, "cash": 10000, "shares": 200},
             "noise": {"count": 8, "cash": 10000, "shares": 200}}
    reference = "{sigma: 0.01, kurtosis: 1, acf1: 0, acf_abs: 0.1, avg_volume: 60, vol_volume_corr: 0.3}"
    contract = {"name": "Tape", "clock": {"rounds": 200}, "types": {"trader": {"agent": True}},
                "mechanisms": {"acme": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50,
                                        "volatility": 0.02, "crowd": crowd}},
                "outputs": {"realism": {"expr": "$market_realism({prices: $series.acme_price, volumes: "
                                                f"$series.acme_volume}}, {reference})", "type": "map"}}}
    trade_acf, mid_sigma = [], []
    for seed in range(1, 5):
        result = fg_env.load(contract, seed=seed).run()
        assert result.status == "completed", result.error
        prices, mids, volumes, spreads = (result.series[f"acme_{k}"] for k in ("price", "mid", "volume", "spread"))
        assert sum(1 for v in volumes if v > 0) >= 0.9 * len(volumes) and sum(volumes) / len(volumes) > 10
        # Quotes stand for a whole round of a 2% volatility: makers who learn from flow need about two volatilities of
        # spread to cover what informed traders take from them, and no more.
        assert statistics.median(s for s in spreads if s is not None) < 0.05 * statistics.median(prices)
        trade_acf.append(abs(series_stats(prices, volumes)["acf1"]))
        mid_sigma.append(series_stats(mids, volumes)["sigma"])
        realism = result.outputs["realism"]
        assert {c["key"] for c in realism["components"]} == {"volatility", "fat_tails", "no_return_memory",
                                                             "volatility_clustering", "volume", "volume_volatility"}
        assert 0 <= realism["score"] <= 1 and all(0 <= c["score"] <= 1 for c in realism["components"])
    # Anchored to the start price the mid moved 0.03% a round and trade returns bounced at acf1 ≈ -0.41 (24 seeds).
    assert statistics.median(mid_sigma) > 0.02 / 8, mid_sigma
    assert statistics.median(trade_acf) < 0.35, trade_acf


def test_crowd_run_resumes_identically_after_a_snapshot():
    contract = {**CROWD, "clock": {"rounds": 12}}
    straight = fg_env.load(contract, seed=5).run().to_dict()
    env = fg_env.load(contract, seed=5)
    env.run(rounds=5)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run().to_dict() == straight


def test_realism_scores_each_stylized_fact_against_the_reference_not_a_fixed_pass_mark():
    ref = {"sigma": 0.02, "kurtosis": 6.0, "acf1": 0.3, "acf_abs": 0.15, "avg_volume": 100, "vol_volume_corr": 0.6}
    thin_tails = {**ref, "kurtosis": 0.8, "acf1": 0.0, "acf_abs": 0.6, "vol_volume_corr": 0.15}
    scores = {c["key"]: c["score"] for c in realism_score(thin_tails, ref)["components"]}
    assert scores["fat_tails"] == pytest.approx(0.8 / 6, abs=0.01)  # a pass mark of 0.3 used to give it 1.0
    assert scores["no_return_memory"] < 0.5  # the reference's own memory is what the tape is held to
    assert scores["volatility_clustering"] == pytest.approx(0.25, abs=0.01)  # overshooting misses too
    assert scores["volume_volatility"] == pytest.approx(0.25, abs=0.01)
    assert all(score == 1.0 for score in (c["score"] for c in realism_score(ref, ref)["components"]))


def test_market_analytics():
    prices = [100, 101, 100, 102, 101, 103]
    returns = log_returns(prices)
    assert len(returns) == 5 and returns[0] == pytest.approx(math.log(1.01))
    assert autocorr([1, -1] * 6, 1) < -0.8
    assert excess_kurtosis([0.0] * 50 + [5, -5]) > 3
    same = series_stats(prices, [10, 12, 9, 14, 10, 15])
    assert realism_score(same, same)["components"][0]["score"] == 1.0
    contract = {"name": "Stats", "clock": {"rounds": 1}, "types": {"thing": {}},
                "outputs": {"r": "$returns([1, 2], log)", "vol": "$market_stats([100, 110, 100]).sigma",
                            "ac": "$autocorr([1, 2, 3, 4, 5], 1)",
                            "k": "$excess_kurtosis([1, 2, 3, 4])", "s": "$market_stats([100, 101])",
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
    mechanism = {"kind": "market", "mode": "prediction", "who": "trader", "outcomes": ["ada", "bo", "cy"],
                 "maker": maker, "liquidity": 20, "fee_pct": 0.02, "resolve_at": 2, "outcome": "$world.truth", **config}
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
        (1, "b"): [("pm_buy", {"outcome": "bo", "spend": 20}),
                   ("pm_buy", {"outcome": "cy", "shares": 500, "spend": 1})],
        (2, "a"): [("pm_sell", {"outcome": "ada", "shares": 4}),
                   ("pm_sell", {"outcome": "ada", "shares": 1, "receive": 5})]},
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


def test_a_prediction_market_resolves_at_resolve_at_or_when_resolve_when_holds_whichever_comes_first():
    """Both set used to drop `resolve_when` silently (audit 9 mech H1)."""
    def resolved_in(**config):
        contract = market("lmsr", stage=False, **config)
        contract["clock"]["rounds"] = 8
        env = fg_env.load(contract, seed=1)
        for round_ in range(1, 9):
            env.run(rounds=1)
            if env.props["pm_resolved"]:
                return round_
        return None

    assert resolved_in(resolve_at=6, resolve_when="$round == 2") == 2
    assert resolved_in(resolve_at=3, resolve_when="$round == 7") == 3
    assert resolved_in(resolve_at=None, resolve_when="$round == 4") == 4
    broken = market("lmsr", resolve_when="$round ==")
    assert any(i.path == "mechanisms.pm.resolve_when" for i in fg_env.check(broken) if i.severity == "error")


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
    mechanism = {"kind": "market", "mode": "auction", "format": fmt, "who": "bidder", "item": "a vase", "stock": 2,
                 "reserve": 35, **config}
    return {"name": "Auction", "clock": {"rounds": 6}, "types": {"bidder": {"agent": True, "props": {"cash": 100}}},
            "entities": {t: {"type": "bidder"} for t in "abc"}, "mechanisms": {"house": mechanism}}


def test_a_stock_beside_a_house_is_refused_with_where_the_units_go():
    """A house sells the units it holds, so a `stock` beside it would silently sell nothing: check refuses it and says
    where to give the units."""
    contract = house("first_price", house="c")
    errors = [i for i in fg_env.check(contract) if i.severity == "error"]
    assert errors and "house 'c' sells the units it holds in `house_units`" in str(errors[0])
    assert "entities.c.props" in str(errors[0])
    del contract["mechanisms"]["house"]["stock"]
    assert not [i for i in fg_env.check(contract) if i.severity == "error"]


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
    seated = {**house("first_price", stage="bids"),
              "stages": [{"name": "bids", "turns": "simultaneous", "order": "seat"}]}
    env, _ = play(seated, plan)  # sealed bids commit in seat order only when the stage says so
    assert env.world.records("house_results")[-1]["winner"] == "a"
    winners = {play(house("first_price", ties="random"), plan, seed=s)[0].world.records("house_results")[-1]["winner"]
               for s in range(12)}
    assert winners == {"a", "b"}
    lone, _ = play(house("second_price"), {(1, "c"): [("house_bid", {"price": 90})]})
    assert props(lone, "c")["cash"] == 65 and lone.world.records("house_results")[-1]["note"] == "pays the reserve"
    over, _ = play(house("second_price", reserve=45),
                   {(1, "c"): [("house_bid", {"price": 90})], (1, "a"): [("house_bid", {"price": 50})]})
    assert over.world.records("house_results")[-1]["note"] == "pays the second-highest bid"
    under, _ = play(house("second_price", reserve=45),
                    {(1, "c"): [("house_bid", {"price": 90})], (1, "a"): [("house_bid", {"price": 40})]})
    assert under.world.records("house_results")[-1]["note"] == "pays the reserve"  # the reserve, not a's refused bid


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


@pytest.mark.parametrize("rule, price", [("lowest_accepted", 80), ("highest_rejected", 10)])
def test_uniform_price_with_units_left_over_prices_at_the_reserve_when_no_bid_is_rejected(rule, price):
    env, _ = play(house("uniform", units=3, stock=3, reserve=10, price_rule=rule), {
        (1, "a"): [("house_bid", {"price": 100, "qty": 1})], (1, "b"): [("house_bid", {"price": 80, "qty": 1})]})
    assert [(r["winner"], r["price"]) for r in env.world.records("house_results")] == [("a", price), ("b", price)]
    assert env.props["house_stock"] == 1 and env.props["house_revenue"] == 2 * price


def test_uniform_auction_sells_what_is_left_when_the_stock_is_smaller_than_a_lot():
    env, _ = play(house("uniform", units=3, stock=4, reserve=10, price_rule="highest_rejected"), {
        (1, "a"): [("house_bid", {"price": 30, "qty": 3})],
        (2, "b"): [("house_bid", {"price": 40, "qty": 2})], (2, "c"): [("house_bid", {"price": 30, "qty": 1})]},
                  rounds=3)
    first, last = env.world.records("house_results")
    assert (first["winner"], first["qty"], first["price"]) == ("a", 3, 10)
    assert (last["winner"], last["qty"], last["price"]) == ("b", 1, 40)  # one unit left: b's other unit is rejected
    assert env.props["house_stock"] == 0 and props(env, "c")["cash"] == 100
    assert not auctions.audit(env.world, "house")


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
    assert (props(env, "b")["cash"] == 100 and props(env, "t")["house_units"] == 2
            and props(env, "t")["house_escrow_units"] == 0)


@pytest.mark.parametrize("bids, asks, price", [
    ([100, 80, 79], [20, 50, 90], 79.5),       # the next bid (79) would buy at any price below 79: clears in [79, 80]
    ([100, 80, 60, 40], [20, 50, 70, 90], 65),  # clears in [60, 70]
    ([100, 80], [20, 50, 60], 55),             # the next ask (60) caps it: clears in [50, 60]
    ([100], [20], 60),                         # nothing unmatched: between the one bid and ask
])
def test_double_auction_clears_at_the_middle_of_the_market_clearing_range(bids, asks, price):
    contract = {"name": "Call", "clock": {"rounds": 1},
                "types": {"buyer": {"agent": True, "props": {"cash": 1000}},
                          "seller": {"agent": True, "props": {"cash": 0}}},
                "entities": {**{f"b{i}": {"type": "buyer"} for i in range(len(bids))},
                             **{f"s{i}": {"type": "seller", "props": {"sale_units": 1}} for i in range(len(asks))}},
                "mechanisms": {"sale": {"kind": "market", "mode": "auction", "format": "double", "who": "buyer",
                                        "sellers": "seller"}}}
    plan = {**{(1, f"b{i}"): [("sale_bid", {"price": p})] for i, p in enumerate(bids)},
            **{(1, f"s{i}"): [("sale_ask", {"price": p})] for i, p in enumerate(asks)}}
    env, _ = play(contract, plan)
    assert {r["price"] for r in env.world.records("sale_results")} == {price}
    assert not auctions.audit(env.world, "sale")


@pytest.mark.parametrize("fmt", ["english", "first_price", "second_price"])
def test_a_policy_that_bids_the_reported_min_bid_is_always_legal(fmt):
    contract = house(fmt, reserve=0, stock=1)
    contract["policies"] = {"floor": {"rules": [{"when": "$auction(house).open and $auction(house).leader != $actor.id "
                                                         "and $auction(house).min_bid < 3",
                                                 "do": "house_bid", "with": {"price": "$auction(house).min_bid"}}]}}
    contract["types"]["bidder"]["policy"] = "floor"
    result = fg_env.run(contract, None, seed=1)
    assert result.outputs["house_sold"] == 1 and result.outputs["house_prices"][0] > 0


def test_the_guide_names_where_each_market_keeps_its_goods_and_check_warns_on_a_bare_lookalike():
    assert "`<name>_units`" in fg_env.guide("market.auction") and "`<name>_shares`" in fg_env.guide("market.order_book")
    assert "`<name>_shares`" in fg_env.guide("market.prediction")
    contract = {"name": "Call", "clock": {"rounds": 1},
                "types": {"buyer": {"agent": True, "props": {"cash": 100}},
                          "seller": {"agent": True, "props": {"units": 3}}},
                "entities": {"b": {"type": "buyer"}, "s": {"type": "seller"}},
                "mechanisms": {"auc": {"kind": "market", "mode": "auction", "format": "double", "who": "buyer",
                                       "sellers": "seller"}}}
    warned = [i for i in fg_env.check(contract) if i.path == "types.seller.props.units"]
    assert len(warned) == 1 and warned[0].severity == "warning" and warned[0].fix == "rename it to `auc_units`"
    contract["types"]["seller"]["props"] = {"auc_units": 3}
    assert not [i for i in fg_env.check(contract) if i.path.endswith(".units")]


def tender(fmt, budget=1000, **config):
    """A city buying road contracts from builders (their `quality` feeds a scored award)."""
    contract = house(fmt, reverse=True, house="city", item="a road contract", reserve=80, **config)
    contract["types"].update(bidder={"agent": True, "props": {"cash": 0, "quality": 0}}, buyer={"props": {"cash": 0}})
    contract["entities"] = {"a": {"type": "bidder", "props": {"quality": 1}},
                            "b": {"type": "bidder", "props": {"quality": 5}},
                            "c": {"type": "bidder"}, "city": {"type": "buyer", "props": {"cash": budget}}}
    return contract


@pytest.mark.parametrize("fmt, paid", [("first_price", 50), ("second_price", 60)])
def test_reverse_auction_awards_the_lowest_offer_and_pays_by_format(fmt, paid):
    env, replies = play(tender(fmt),
                        {(1, "a"): [("house_bid", {"price": 90})], (1, "b"): [("house_bid", {"price": 50})],
                         (1, "c"): [("house_bid", {"price": 60})]})
    assert not replies_of(replies, "a")[0].ok  # above the most the house pays
    assert props(env, "b")["cash"] == paid and props(env, "b")["house_won"] == 1 and props(env, "c")["cash"] == 0
    assert (props(env, "b")["house_units"] == 0 and props(env, "city")["house_units"]
            == 1)  # the winner delivers to the buyer
    assert props(env, "city")["cash"] == 1000 - paid and env.props["house_stock"] == 1
    result = env.world.records("house_results")[-1]
    assert (result["winner"], result["price"]) == ("b", paid)
    assert not auctions.audit(env.world, "house")


def test_a_lone_vickrey_offer_is_paid_the_reserve_and_the_house_never_pays_more_than_it_holds():
    lone, _ = play(tender("second_price"), {(1, "c"): [("house_bid", {"price": 30})]})
    assert (props(lone, "c")["cash"] == 80 and lone.world.records("house_results")[-1]["note"]
            == "lowest offer, paid the reserve")
    broke, _ = play(tender("first_price", budget=40), {(1, "c"): [("house_bid", {"price": 50})]})
    assert broke.world.records("house_results")[-1]["winner"] == "" and props(broke, "city")["cash"] == 40


@pytest.mark.parametrize("fmt, paid", [("first_price", 50), ("second_price", 60)])
def test_a_tender_with_deliver_from_takes_the_winners_unit_out_of_its_stock(fmt, paid):
    contract = tender(fmt, deliver_from="stock")
    contract["types"]["bidder"]["props"]["stock"] = 0
    for bidder, stock in (("a", 1), ("c", 2)):
        contract["entities"][bidder]["props"] = {**contract["entities"][bidder].get("props", {}), "stock": stock}
    env, replies = play(contract, {(1, "a"): [("house_bid", {"price": 50})], (1, "b"): [("house_bid", {"price": 40})],
                                   (1, "c"): [("house_bid", {"price": 60})]})
    assert not replies_of(replies, "b")[0].ok  # nothing in stock to supply, so its low offer never sets a price
    assert props(env, "a")["stock"] == 0 and props(env, "a")["cash"] == paid and props(env, "c")["stock"] == 2
    assert props(env, "city")["house_units"] == 1 and env.props["house_stock"] == 1
    assert not auctions.audit(env.world, "house")


@pytest.mark.parametrize("reverse, a_price, b_price, cash", [(True, 60, 70, 70), (False, 70, 60, 100 - 60)])
def test_a_scored_award_goes_to_the_best_score_not_the_best_price(reverse, a_price, b_price, cash):
    """b's quality outweighs a's better price; b pays (or is paid) its own price."""
    if reverse:
        contract = tender("first_price", score="$it.quality * 10 - $price")
    else:
        contract = house("first_price", score="$it.quality * 10 + $price")
        contract["types"]["bidder"]["props"]["quality"] = 0
        contract["entities"]["b"] = {"type": "bidder", "props": {"quality": 5}}
    env, _ = play(contract,
                  {(1, "a"): [("house_bid", {"price": a_price})], (1, "b"): [("house_bid", {"price": b_price})]})
    result = env.world.records("house_results")[-1]
    assert (result["winner"], result["price"]) == ("b", b_price) and props(env, "b")["cash"] == cash
    assert not auctions.audit(env.world, "house")


@pytest.mark.parametrize("config, message", [
    ({"format": "english"}, "a reverse auction is sealed"),
    ({"house": None}, "a reverse auction needs `house`"),
    ({"reserve": 0}, "a reverse auction needs `reserve`"),
    ({"format": "second_price", "score": "$it.quality - $price"}, "a scored award pays the winner its own bid"),
    ({"score": "$bogus - $price"}, "$bogus is not available here"),
    ({"deliver_from": "stock"}, "deliver_from 'stock' is not a property of bidder"),
    ({"reverse": False, "house": None, "deliver_from": "quality"}, "`deliver_from` belongs to a tender"),
])
def test_misconfigured_tenders_say_how_to_fix_them(config, message):
    contract = tender("first_price")
    contract["mechanisms"]["house"].update(config)
    if contract["mechanisms"]["house"]["house"] is None:
        del contract["mechanisms"]["house"]["house"]
    with pytest.raises(fg_env.ContractError) as caught:
        fg_env.load(contract)
    assert message in str(caught.value)


@pytest.mark.parametrize("fmt, score",
                         [("first_price", None), ("second_price", None), ("first_price", "$it.quality - $price")])
def test_tenders_conserve_cash_and_units_with_random_bidders(fmt, score):
    contract = tender(fmt, stock=4, **({"score": score} if score else {}))
    contract["clock"]["rounds"] = 8
    assert not [i for i in fg_env.check(contract) if i.severity == "error"]
    env = fg_env.load(contract, seed=4)
    result = env.run()
    assert result.status == "completed", result.error
    assert env.props["house_sold"] > 0 and not auctions.audit(env.world, "house")


def test_a_tender_composes_with_a_declared_stage_and_outputs():
    contract = {**tender("first_price", stage="bidding", stock=2),
                "stages": [{"name": "bidding", "turns": "simultaneous"}]}
    contract["clock"]["rounds"] = 2
    contract["outputs"] = {"spent": "1000 - $entity(city).cash"}
    env, _ = play(contract, {(1, "a"): [("house_bid", {"price": 40})], (2, "c"): [("house_bid", {"price": 30})]},
                  rounds=2)
    assert env.result().outputs["spent"] == 70 and env.result().outputs["house_prices"] == [40, 30]


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
                              "pears": {"seller": "ben", "item": "pears", "price": 4, "stock": 5, "rating": 4.5,
                                        "ratings": 2},
                              "plums": {"seller": "ben", "item": "plums", "price": 2, "stock": 5}}, **config}
    contract = {"name": "Farm", "clock": {"rounds": 4},
                "types": {"shopper": {"agent": True, "props": {"cash": 50}},
                          "farmer": {"agent": True, "props": {"cash": 10}}},
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
        (1, "tia"): [("market_offer", {"listing": "apples", "price": 2.6}),
                     ("market_offer", {"listing": "apples", "price": 3.5}),
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


@pytest.mark.parametrize("old, contract, mode",
                         [("order_book", book(), "order_book"), ("auction", house("first_price"), "auction"),
                          ("prediction", market("lmsr"), "prediction"),
                          ("posted", farm(), "posted")])
def test_a_market_mode_written_as_the_kind_names_the_family(old, contract, mode):
    name, config = next(iter(contract["mechanisms"].items()))
    config = {key: value for key, value in config.items() if key != "mode"}
    issue = next(i for i in errors_of({**contract, "mechanisms": {name: {**config, "kind": old}}})
                 if i.path == f"mechanisms.{name}.kind")
    assert issue.message == f"'{old}' is a mode of kind 'market'"


def test_a_market_field_typo_or_an_old_field_name_names_the_mode_and_its_fields():
    typo = errors_of(book(tick_sise=0.05))
    assert [(i.path, i.message) for i in typo] == [("mechanisms.acme.tick_sise",
                                                    "`tick_sise` is not a field of `market` mode `order_book`")]
    assert typo[0].fix.startswith("did you mean 'tick_size'?") and "takes: who, start_price, currency" in typo[0].fix
    old = errors_of(house("first_price", bidders="bidder"))
    assert [i.message for i in old] == ["`bidders` is not a field of `market` mode `auction`"]
    foreign = errors_of(farm(outcomes=["a", "b"]))
    assert [i.message for i in foreign] == ["`outcomes` is not a field of `market` mode `posted`"]


def _op_issues(contract, *effects):
    return [(i.path, i.message, i.fix) for i in errors_of({**contract, "events": [{"do": list(effects)}]})]


def test_market_actions_check_their_own_keys():
    assert any(m == "`market.buy` needs `qty`"
               for _, m, _ in _op_issues(book(), {"market": "acme", "action": "buy", "price": 50}))
    assert any(m == "'trader' is not part of `market.cancel_all`" for _, m, _ in _op_issues(
        book(), {"market": "acme", "action": "cancel_all", "trader": "a"}))
    path, message, fix = _op_issues(book(), {"market": "acme", "action": "bid"})[0]
    assert path.endswith(".action") and message == "'bid' is not an action of acme (market order_book)"
    assert fix == "actions: buy, sell, cancel, cancel_all, algo, open, close"
    _, _, fix = _op_issues(book(), {"market": "acmee", "action": "open"})[0]
    assert fix == "did you mean 'acme'?"
    _, _, fix = _op_issues(book(), {"buy": "acme", "qty": 1})[0]
    assert fix.startswith('`buy` is an action of the `market` op: {"market": "<mechanism>", "action": "buy"')
    assert any(m == "a first_price auction takes no asks" for _, m, _ in _op_issues(
        house("first_price"), {"market": "house", "action": "ask", "price": 10}))
    assert any(m == "`package` belongs to a combinatorial auction, not a first_price auction" for _, m, _ in _op_issues(
        house("first_price"), {"market": "house", "action": "bid", "price": 40, "package": ["x"]}))
    assert any(m == "`market.bid` needs `price`"
               for _, m, _ in _op_issues(house("english"), {"market": "house", "action": "bid"}))
    assert any("needs `shares`, `spend` (money) or both" in m for _, m, _ in _op_issues(
        market("lmsr"), {"market": "pm", "action": "buy", "outcome": "ada"}))
    assert any(m == "`market.resolve` needs `outcome`"
               for _, m, _ in _op_issues(market("lmsr"), {"market": "pm", "action": "resolve"}))
    assert any(m == "`market.promote` needs `rounds`" for _, m, _ in _op_issues(
        farm(), {"market": "market", "action": "promote", "listing": "apples", "pct": 0.1}))
    assert any(m == "'stars' is not part of `market.offer`" for _, m, _ in _op_issues(
        farm(), {"market": "market", "action": "offer", "listing": "apples", "price": 2, "stars": 3}))


def test_market_actions_act_for_who_they_name():
    contract = {**book(), "events": [{"name": "quote", "phase": "start", "when": "$round == 1",
                                      "do": [{"market": "acme", "action": "sell", "who": "a", "qty": 10,
                                              "price": 51}]}]}
    env, _ = play(contract, {})
    assert [(o["owner"], o["qty"], o["price"]) for o in env.props["acme_asks"]] == [("a", 10, 51)]
    assert not order_book.audit(env.world, "acme")


def test_guide_documents_the_market_family():
    page = fg_env.guide("market.auction")
    assert (page.startswith("### `market.auction`") and "`house`" in page and "- `bid`" in page
            and "- `tick`" not in page)
    family = fg_env.guide("market")
    assert all(f"- `{mode}`:" in family for mode in ("order_book", "auction", "prediction", "posted"))
    posted = fg_env.guide("market.posted")
    assert "- `set_price`" in posted and "- `open`" not in posted


def test_the_price_band_is_anchored_to_the_rounds_open_not_the_last_print():
    env, replies = play(book(), {
        (1, "a"): [("acme_sell", {"qty": 2, "price": 74})],
        (1, "b"): [("acme_buy", {"qty": 2, "price": 74})],
        (1, "c"): [("acme_buy", {"qty": 1, "price": 100})]})
    assert env.props["acme_last"] == 74
    refused = replies_of(replies, "c")[0]
    assert not refused.ok and "at most 75" in refused.text
    quote = order_book.quote(env.world, "acme")
    assert (quote["band_low"], quote["band_high"]) == (25, 75)


def test_a_sealed_bid_schema_never_offers_a_price_the_rules_refuse():
    contract = {"name": "Lot", "clock": {"rounds": 1}, "types": {"bidder": {"agent": True, "props": {"cash": 100}}},
                "entities": {"a": {"type": "bidder"}},
                "mechanisms": {"sale": {"kind": "market", "mode": "auction", "format": "first_price", "who": "bidder",
                                        "stock": 1, "reserve": 0}}}
    told = {}

    def bid(wake):
        price = next(t for t in wake.tools if t.name == "sale_bid").input_schema["properties"]["price"]
        told["minimum"] = price["minimum"]
        told["at_minimum"] = wake.call("sale_bid", {"price": price["minimum"]}).ok
        wake.end()

    fg_env.run(contract, bid, seed=1)
    assert told["minimum"] > 0 and told["at_minimum"]  # the schema's floor is a bid the rules accept


def test_two_order_books_on_one_cash_prop_both_trade_and_settle():
    books = {name: {"kind": "market", "mode": "order_book", "who": "trader", "start_price": price, "stage": "trade"}
             for name, price in (("acme", 50), ("beta", 20))}
    contract = {"name": "Two books", "clock": {"rounds": 2},
                "types": {"trader": {"agent": True, "props": {"cash": 10000, "acme_shares": 100, "beta_shares": 100}}},
                "entities": {"a": {"type": "trader"}, "b": {"type": "trader"}},
                "stages": [{"name": "trade", "turns": "sequential", "order": "seat", "max_actions": 10}],
                "mechanisms": books}
    env, replies = play(contract,
                        {(1, "a"): [("acme_sell", {"qty": 10, "price": 50}), ("beta_sell", {"qty": 10, "price": 20})],
                         (1, "b"): [("acme_buy", {"qty": 10, "price": 50}),
                                    ("beta_buy", {"qty": 10, "price": 20})]})
    assert all(reply.ok for *_, reply in replies), [reply.text for *_, reply in replies]
    assert props(env, "b")["cash"] == 10000 - 500 - 200 and props(env, "a")["cash"] == 10000 + 500 + 200
    assert props(env, "b")["acme_shares"] == 110 and props(env, "b")["beta_shares"] == 110


def test_a_prediction_market_resolving_to_an_outcome_it_does_not_list_is_an_error():
    contract = {"name": "Bad outcome", "clock": {"rounds": 3}, "world": {"truth": {"default": "w"}},
                "types": {"forecaster": {"agent": True, "props": {"cash": 1000}}},
                "entities": {"a": {"type": "forecaster"}, "b": {"type": "forecaster"}},
                "mechanisms": {"m": {"kind": "market", "mode": "prediction", "who": "forecaster",
                                     "outcomes": ["x", "y"], "resolve_at": 2, "outcome": "$world.truth"}}}
    assert any("the winning outcome must be one of x, y, got 'w'" in i.message
               for i in fg_env.check(contract) if i.severity == "error")


def test_a_declared_stage_named_after_a_book_refines_its_generated_stage():
    contract = {"name": "Refined", "clock": {"rounds": 2}, "types": {"trader": {"agent": True, "props": {"cash": 100}}},
                "entities": {"t1": {"type": "trader"}, "t2": {"type": "trader"}},
                "stages": [{"name": "acme", "turns": "simultaneous"}],
                "mechanisms": {"acme": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 5}}}
    assert not [i for i in fg_env.check(contract) if i.severity == "error"]
    [stage] = fg_env.expand(contract, mechanisms=True)["stages"]
    assert stage["turns"] == "simultaneous" and stage["max_actions"] == 4
    assert {"acme_buy", "acme_sell", "acme_cancel"} <= set(stage["actions"])


def test_the_spread_metric_is_null_while_a_side_of_the_book_is_empty():
    """An ask and no bid has no spread; zero would say the book is perfectly liquid."""
    c = {"fg_env": "2", "name": "S", "clock": {"rounds": 2},
         "types": {"trader": {"agent": True, "props": {"cash": 10000, "x_shares": 50}}},
         "entities": {"a": {"type": "trader"}, "b": {"type": "trader"}},
         "mechanisms": {"x": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 100}}}
    plan = {(1, "a"): [("x_sell", {"qty": 5, "price": 105})], (2, "b"): [("x_buy", {"qty": 1, "price": 101})]}
    participant, _ = scripted(plan)
    result = fg_env.load(c, seed=1).run(participant)
    assert result.series["x_spread"] == [None, 4]


def _crowd_book(fair_value):
    return {"fg_env": "2", "name": "S", "clock": {"rounds": 4}, "world": {"x": 100, "label": "high"},
            "types": {"trader": {"agent": True, "props": {"cash": 10000, "x_shares": 50}}},
            "entities": {"a": {"type": "trader"}},
            "mechanisms": {"x": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 100,
                                 "fair_value": fair_value,
                                 "crowd": {"fundamentalist": {"count": 4, "cash": 10000, "shares": 50},
                                           "market_maker": {"count": 1, "cash": 100000, "shares": 500}}}}}


def test_a_broken_mechanism_expression_is_a_check_error_at_its_field():
    for fair_value, words in (("$world.x +", "syntax error"), ("$world.nothere", "no such world property")):
        issues = [i for i in fg_env.check(_crowd_book(fair_value), rounds=0) if i.severity == "error"]
        assert [i.path for i in issues] == ["mechanisms.x.fair_value"], issues
        assert words in issues[0].message


def test_a_coded_population_whose_every_action_fails_degrades_the_run():
    """fair_value reads text, so every fundamentalist's algorithm call fails; the market maker still quotes."""
    result = fg_env.run(_crowd_book("$world.label"), {"a": "idle"}, seed=1)
    assert "action_always_faulted" in result.degraded
    finding = next(d for d in result.diagnostics if d["path"] == "types.x_fundamentalist")
    assert "fair_value must be a number" not in finding["message"] and "rule failed" in finding["message"]


def test_an_authors_syntax_error_in_a_mechanism_field_is_reported_at_the_field_not_as_a_bug():
    c = {"fg_env": "2", "name": "P", "clock": {"rounds": 2}, "world": {"x": 1},
         "types": {"t": {"agent": True, "props": {"cash": 100}}}, "entities": {"a": {"type": "t"}},
         "mechanisms": {"m": {"kind": "market", "mode": "prediction", "who": "t", "outcomes": ["yes", "no"],
                              "outcome": "$world.x ??"}}}
    issues = [i for i in fg_env.check(c, rounds=0) if i.severity == "error"]
    assert [i.path for i in issues] == ["mechanisms.m.outcome"]
    assert "bug" not in issues[0].message + (issues[0].fix or "")


def test_market_makers_quote_for_a_big_crowds_flow_so_the_book_keeps_both_sides():
    """160 coded traders and 2 market makers: quotes sized only by base_qty were taken away on one side most rounds."""
    crowd = {"market_maker": {"count": 2, "cash": 100000, "shares": 1000},
             **{kind: {"count": 40, "cash": 5000, "shares": 50}
                for kind in ("noise", "momentum", "fundamentalist", "mean_reversion")}}
    c = {"fg_env": "2", "name": "T", "clock": {"rounds": 20},
         "types": {"trader": {"agent": True, "props": {"cash": 1000}}}, "entities": {"me": {"type": "trader"}},
         "mechanisms": {"x": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 100,
                              "crowd": crowd}}}
    spreads = [spread for seed in (1, 2)
               for spread in fg_env.load(c, seed=seed, events=False).run({"trader": "idle"}).series["x_spread"]]
    assert sum(spread is None for spread in spreads) < len(spreads) / 3


@pytest.mark.parametrize("house, ok", [("buyer_1", True), ("buyer", False)])
def test_a_house_from_a_counted_group_is_named_by_its_generated_id(house, ok):
    """`check` and the run agree on ids: a group with `count` makes `buyer_1` …, and naming the group is refused with
    the ids to use."""
    tender = {"name": "Tender", "clock": {"rounds": 1},
              "types": {"bidder": {"agent": True, "props": {"cash": 0}}, "buyer": {"props": {"cash": 1000}}},
              "entities": {"bidder": {"type": "bidder", "count": 2}, "buyer": {"type": "buyer", "count": 1}},
              "mechanisms": {"a": {"kind": "market", "mode": "auction", "format": "first_price", "who": "bidder",
                                   "reverse": True, "house": house, "reserve": 100}}}
    errors = [i for i in fg_env.check(tender, rounds=0) if i.severity == "error"]
    if ok:
        assert errors == [] and fg_env.run(tender, seed=1).status != "failed"
    else:
        assert [i.path for i in errors] == ["mechanisms.a.house"] and "buyer_1" in errors[0].fix


def test_a_small_market_order_collar_is_told_with_its_digits():
    book = {"name": "Book", "clock": {"rounds": 1}, "types": {"t": {"agent": True, "props": {"cash": 1000}}},
            "entities": {"t": {"type": "t", "count": 1}},
            "mechanisms": {"bk": {"kind": "market", "mode": "order_book", "who": "t", "start_price": 100,
                                  "collar_pct": 0.005}}}
    buy = next(t for t in fg_env.load(book, seed=1).preview("t_1").tools if t["name"] == "bk_buy")
    assert "within 0.5% of the best ask" in buy["description"]


def test_an_auctions_revenue_counts_what_its_house_is_paid():
    """With a `house` entity the proceeds go to its cash, and the auction's revenue output, metric and
    `$auction(name).revenue` count them too (audit 11 mechanisms HIGH-1): they read 0 before."""
    contract = {"name": "House sale", "clock": {"rounds": 2},
                "types": {"bidder": {"agent": True, "props": {"value": {"type": "number", "default": 0,
                                                                         "private": True}, "cash": 100}},
                          "house": {"props": {"cash": 0, "t_units": 2}}},
                "entities": {**{f"b{i}": {"type": "bidder", "props": {"value": v}} for i, v in enumerate([30, 40, 50])},
                             "hq": {"type": "house"}},
                "mechanisms": {"t": {"kind": "market", "mode": "auction", "format": "second_price", "who": "bidder",
                                     "house": "hq"}},
                "outputs": {"house_cash": "$entity('hq').cash", "read": "$auction('t').revenue"}}

    def bid(wake):
        wake.call("t_bid", {"price": wake.me["value"]})
        wake.end()

    result = fg_env.run(contract, bid, seed=1)
    assert result.status == "completed", result.error
    assert result.outputs["house_cash"] == result.outputs["t_revenue"] == result.outputs["read"] == 80


def test_a_number_written_as_text_in_a_config_field_is_refused_where_it_is_written():
    """`"reserve": "45"` is text, which the run refused only once it read it: the config says so (audit 11 mechanisms
    LOW-2). A field that is always an expression still reads "2" as two."""
    contract = {"name": "Reserve", "clock": {"rounds": 1}, "types": {"bidder": {"agent": True, "props": {"cash": 100}}},
                "entities": {"bidder": {"type": "bidder", "count": 2}},
                "mechanisms": {"t": {"kind": "market", "mode": "auction", "format": "second_price", "who": "bidder",
                                     "reserve": "45"}}}
    found = [i for i in fg_env.check(contract, rounds=0) if i.path == "mechanisms.t.reserve"]
    assert [i.severity for i in found] == ["error"] and "write the number without quotes: 45" in str(found[0])
    contract["mechanisms"]["t"]["reserve"] = 45
    assert not [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]


def test_a_misspelt_crowd_strategy_parameter_is_an_error_with_a_suggestion():
    """A typo in a crowd's params used to be ignored silently (audit 12 mech M4)."""
    contract = {"name": "Cr", "clock": {"rounds": 2}, "types": {"trader": {"agent": True}},
                "entities": {"me": {"type": "trader"}},
                "mechanisms": {"bk": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50,
                                      "crowd": {"noise": {"count": 3, "cash": 500, "shares": 10,
                                                          "params": {"activty": 0.2}}}}}}
    issues = [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert [(i.path, i.fix) for i in issues] == [("mechanisms.bk.crowd.noise.params.activty",
                                                  "did you mean 'activity'?")]


def test_a_posted_markets_average_price_is_what_its_units_sold_for():
    """`<name>_avg_price` is turnover ÷ units sold, offers and counter-offers included — not the average asking price
    of the listings (audit 12 mech M5)."""
    result = fg_env.run(Path(__file__).parents[1] / "examples" / "contracts" / "farmers_market.json", seed=1)
    metrics = {name: values[-1] for name, values in result.series.items()}
    assert metrics["market_avg_price"] == pytest.approx(metrics["market_turnover"] / metrics["market_sales"])


def test_default_market_makers_quote_for_the_whole_crowd_not_only_last_rounds_flow():
    """Two default market makers facing ten fundamentalists and ten noise traders size their quotes to what that crowd
    sends in a round, not only to the last round's flow, so the book keeps both sides far more often (audit 12 mech
    M6: 12 of 160 rounds ended one-sided before, 8 now)."""
    crowd = {"market_maker": {"count": 2, "cash": 100000, "shares": 2000},
             "fundamentalist": {"count": 10, "cash": 10000, "shares": 200},
             "noise": {"count": 10, "cash": 10000, "shares": 200}}
    contract = {"name": "Crowd", "clock": {"rounds": 40}, "types": {"trader": {"agent": True, "props": {"cash": 1000}}},
                "entities": {"me": {"type": "trader"}},
                "mechanisms": {"bk": {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50,
                                      "volatility": 0.02, "crowd": crowd}},
                "outputs": {"book": {"expr": "$book('bk')", "series": True}}}
    one_sided = sum(book["bid"] is None or book["ask"] is None
                    for seed in range(4) for book in fg_env.run(contract, None, seed=seed).series["book"])
    assert one_sided <= 8
