"""Order book venue rules as expressions, bars of several rounds, and the bar-aware circuit breaker."""
import json

import pytest

import fg_env
from fg_env import expr
from fg_env.errors import RunError
from fg_env.mechanisms import order_book


def crowd_book(rounds=12, inputs=None, **config):
    """A small coded crowd on one book, with inputs the venue rules can read."""
    mechanism = {"kind": "market", "mode": "order_book", "who": "trader", "start_price": "$inputs.price",
                 "stage": "trade", "conserve": "round", "short_limit": 50, "volatility": 0.01,
                 "crowd": {"market_maker": {"count": 2, "cash": 50000, "shares": 400},
                           "momentum": {"count": 3, "cash": 10000, "shares": 100},
                           "noise": {"count": 6, "cash": 10000, "shares": 100}}, **config}
    return {"fg_env": "1", "name": "Venue", "clock": {"rounds": rounds},
            "inputs": {"price": {"type": "number", "default": 50}, "fee": {"type": "number", "default": 5},
                       "bar": {"type": "int", "default": 4}, **(inputs or {})},
            "types": {"trader": {"agent": True, "props": {"cash": 0}}},
            "stages": [{"name": "trade", "turns": "sequential", "order": "random"}],
            "mechanisms": {"acme": mechanism}}


def bars(env, name="acme_bars"):
    return expr.evaluate(f"$records({name})", env.world.scope())


def test_venue_rules_can_be_expressions_over_inputs_resolved_when_the_world_is_built():
    contract = crowd_book(tick_size="10 ** ($floor($log($inputs.price, 10)) - 4)", taker_fee_bps="$inputs.fee")
    for price, tick in ((50, 0.001), (1000, 0.1), (250.5, 0.01)):
        env = fg_env.load(contract, inputs={"price": price, "fee": 7}, seed=1)
        book = expr.evaluate("$book(acme)", env.world.scope())
        assert (book["tick"], book["taker_fee_bps"]) == (pytest.approx(tick), 7)
        assert env.world.props["acme_rules"]["tick_size"] == pytest.approx(tick)


def test_expression_rules_trade_exactly_like_the_same_literal_rules():
    by_expression = fg_env.run(crowd_book(taker_fee_bps="$inputs.fee", halt_pct="$inputs.fee / 100",
                                          bar_rounds="$inputs.bar"),
                               seed=3).to_dict()
    literal = fg_env.run(crowd_book(taker_fee_bps=5, halt_pct=0.05, bar_rounds=4), seed=3).to_dict()
    assert by_expression["outputs"] == literal["outputs"] and by_expression["series"] == literal["series"]


def test_a_rule_outside_its_limits_fails_at_load_naming_the_field():
    with pytest.raises(RunError) as error:
        fg_env.load(crowd_book(tick_size="$inputs.fee - 10"), seed=1)
    assert "mechanisms.acme.tick_size must be above 0, got -5" in str(error.value)
    with pytest.raises(RunError) as error:
        fg_env.load(crowd_book(bar_rounds="$inputs.fee / 2"), seed=1)
    assert "mechanisms.acme.bar_rounds must be a whole number, got 2.5" in str(error.value)
    issues = [i for i in fg_env.check(crowd_book(tick_size=-1)) if i.severity == "error"]
    assert any(i.path.startswith("mechanisms.acme.tick_size") for i in issues)


def test_an_expression_lot_gives_number_quantities_and_a_literal_whole_lot_integers():
    tools = fg_env.load(crowd_book(lot_size="$inputs.fee / 10"), seed=1).contract.actions
    assert tools["acme_buy"].params["qty"].type == "number" and tools["acme_buy"].params["qty"].min == "$book(acme).lot"
    assert fg_env.load(crowd_book(), seed=1).contract.actions["acme_buy"].params["qty"].type == "int"


def test_bars_of_several_rounds_aggregate_exactly_the_rounds_they_span():
    per_round = fg_env.load(crowd_book(rounds=10), seed=5)
    per_round.run()
    by_four = fg_env.load(crowd_book(rounds=10, bar_rounds=4), seed=5)
    by_four.run()
    rounds = bars(per_round)
    assert [b["bar"] for b in bars(by_four)] == [1, 2, 3]  # the last bar holds the run's final two rounds
    for bar, span in zip(bars(by_four), (rounds[0:4], rounds[4:8], rounds[8:10])):
        assert bar["open"] == span[0]["open"] and bar["close"] == span[-1]["close"]
        assert bar["high"] == max(r["high"] for r in span) and bar["low"] == min(r["low"] for r in span)
        assert bar["volume"] == pytest.approx(sum(r["volume"] for r in span))
        assert bar["trades"] == sum(r["trades"] for r in span)
        assert bar["vwap"] == pytest.approx(sum(r["vwap"] * r["volume"] for r in span) / bar["volume"])
        kinds = {kind for r in span for kind in r["flow"]}
        assert bar["flow"] == {k: {s: pytest.approx(sum(r["flow"].get(k, {}).get(s, 0) for r in span))
                                   for s in ("buy", "sell")}
                               for k in kinds}
    assert (by_four.world.props["acme_trades"]
            == per_round.world.props["acme_trades"])  # bar length never changes trading


def test_the_bar_in_progress_is_readable_mid_bar():
    env = fg_env.load(crowd_book(bar_rounds=4), seed=5)
    env.run(rounds=6)
    running = expr.evaluate("$book(acme).bar", env.world.scope())
    per_round = fg_env.load(crowd_book(), seed=5)
    per_round.run(rounds=6)
    span = bars(per_round)[4:6]
    assert running["bar"] == 2 and running["ends"] == 8 and len(bars(env)) == 1
    assert running["open"] == span[0]["open"] and running["close"] == span[-1]["close"]
    assert running["volume"] == pytest.approx(sum(r["volume"] for r in span))


def scripted_book(**config):
    """Four scripted traders on one book of 4-round bars."""
    mechanism = {"kind": "market", "mode": "order_book", "who": "trader", "start_price": 50, "stage": "trade",
                 "bar_rounds": 4, **config}
    return {"fg_env": "1", "name": "Scripted", "clock": {"rounds": 8},
            "types": {"trader": {"agent": True, "props": {"cash": 0}}},
            "entities": {t: {"type": "trader", "props": {"cash": 10000, "acme_shares": 100}} for t in "abc"},
            "stages": [{"name": "trade", "turns": "sequential", "max_actions": 6, "max_calls": 8}],
            "mechanisms": {"acme": mechanism}}


def test_a_bar_open_breaker_checked_at_round_end_halts_to_the_end_of_the_bar():
    plan = {(1, "a"): [("acme_sell", {"qty": 10, "price": 50.5})], (1, "b"): [("acme_buy", {"qty": 10, "price": 49.5})],
            (2, "a"): [("acme_cancel_all", {}), ("acme_sell", {"qty": 10, "price": 45})],
            (2, "b"): [("acme_cancel_all", {}), ("acme_buy", {"qty": 10, "price": 44})],
            (3, "c"): [("acme_buy", {"qty": 5})],
            (5, "a"): [("acme_cancel_all", {}), ("acme_sell", {"qty": 10, "price": 49.6})],
            (5, "b"): [("acme_cancel_all", {}), ("acme_buy", {"qty": 10, "price": 49.4})],
            (5, "c"): [("acme_buy", {"qty": 5})]}
    replies = {}

    def participant(wake):
        for tool, args in plan.get((wake.round, wake.entity_id), []):
            replies[(wake.round, wake.entity_id, tool)] = wake.call(tool, args)
        wake.end()

    env = fg_env.load(scripted_book(halt_pct=0.03, halt_reference="bar_open", halt_check="round_end",
                                    halt_until="bar_end"),
                      seed=1)
    halted = []
    while not env.finished:
        env.run(participant, rounds=1)
        halted.append(env.world.props["acme_halted"])
    assert halted == [False, True, True, True, False, False, False,
                      False]  # the mid is 11% off the bar's open after round 2
    assert not replies[(3, "c", "acme_buy")].ok  # halted to the end of the bar
    assert replies[(5, "c", "acme_buy")].ok and "filled 5" in replies[(5, "c", "acme_buy")].text  # the next bar trades
    assert [b["halted"] for b in bars(env)] == [True, False]
    halt = [e for e in env.result().events if e["kind"] == "acme_halt"]
    assert len(halt) == 1 and halt[0]["text"].endswith("Trading is halted for the rest of this bar.")
    assert "from the bar's open" in halt[0]["text"]
    assert not order_book.audit(env.world, "acme")


def test_a_rolling_reference_measures_from_the_close_its_window_back():
    for window, back in ((3, -3), (1, -1)):
        env = fg_env.load(crowd_book(rounds=8, halt_pct=0.5, halt_reference="rolling", halt_window=window), seed=4)
        env.run(rounds=5)
        closes = list(env.world.props["acme_closes"])  # rounds 1-5
        env.run(rounds=1)
        assert env.world.props["acme_ref"] == closes[back]  # round 6 measures from the close `window` rounds back


def test_an_author_end_event_that_closes_the_round_first_reads_the_bar_just_closed():
    contract = crowd_book(rounds=8, bar_rounds=4)
    contract["world"] = {"seen": {"type": "list", "default": []}}
    contract["events"] = [{"name": "read_the_bar", "phase": "end", "at": [4, 8],
                           "do": [{"market": "acme", "action": "close"},
                                  "$world.seen = $world.seen + [$last($records(acme_bars)).bar]"]}]
    env = fg_env.load(contract, seed=1)
    env.run()
    assert env.world.props["seen"] == [1, 2] and [b["bar"] for b in bars(env)] == [1, 2]  # closed once per round


def test_a_snapshot_mid_bar_resumes_bars_rules_and_breaker_exactly():
    contract = crowd_book(rounds=14, bar_rounds="$inputs.bar", taker_fee_bps="$inputs.fee", halt_pct=0.02,
                          halt_reference="bar_open", halt_check="round_end", halt_until="bar_end")
    straight = fg_env.load(contract, seed=9).run().to_dict()
    env = fg_env.load(contract, seed=9)
    env.run(rounds=6)
    resumed = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert resumed.run().to_dict() == straight
    copy = fg_env.load(contract, seed=9)
    copy.run(rounds=5)
    assert copy.clone().run().to_dict() == straight
