"""The flagship exchange: a calibrated order-book session rebuilt as a contract, held to the platform Exchange's bar."""
import csv
import json
import re
from pathlib import Path

import pytest

import fg_env
from fg_env.sdk import expr
from fg_env.sdk.mechanisms import order_book

PATH = Path(__file__).parents[2] / "examples" / "contracts" / "exchange_flagship.json"
SMALL = {"participants": 40, "bars": 4, "substeps": 6}
CRASH = [{"bar": 2, "headline": "DEMO's auditor resigns.", "shock_pct": -30, "sentiment": -1, "duration_bars": 3}]


def run(inputs, seed=1, participants=None, arm=None):
    env = fg_env.load(PATH, inputs=inputs, seed=seed, arm=arm)
    result = env.run(participants)
    assert result.status == "completed", result.error
    return env, result


def bars(env):
    return expr.evaluate("$records(demo_bars)", env.world.scope())


def test_the_flagship_checks_clean_including_its_seed_history_file():
    assert [str(i) for i in fg_env.check(PATH) if i.severity == "error"] == []


def test_the_default_crowd_follows_the_balanced_preset_and_is_calibrated_to_the_seed_tape():
    env = fg_env.load(PATH, seed=1)
    counts = {kind: len(env.entities(f"demo_{kind}")) for kind in ("market_maker", "momentum", "mean_reversion", "fundamentalist",
                                                                    "noise", "passive")}
    assert counts == {"market_maker": 18, "momentum": 54, "mean_reversion": 48, "fundamentalist": 42, "noise": 120, "passive": 18}
    world = env.world.props
    with open(PATH.parent / "exchange_flagship" / "seed_history.csv", newline="") as handle:
        history = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(handle)][-120:]
    avg_volume = sum(row["volume"] for row in history) / len(history)
    expected_orders = 12 * (18 * 0.9 + 54 * 0.12 + 48 * 0.15 + 42 * 0.08 + 120 * 0.06 + 18 * 0.5)
    assert world["target_volume"] == pytest.approx(avg_volume)
    assert world["base_qty"] == pytest.approx(avg_volume / (expected_orders * 0.35))
    assert world["median_capital"] == pytest.approx(world["base_qty"] * history[-1]["close"] / 0.03)
    assert world["demo_last"] == history[-1]["close"] and world["fundamental"] == history[-1]["close"]


def test_a_seeded_session_is_deterministic_and_a_snapshot_resumes_it_exactly():
    straight = run(SMALL, seed=3)[1].to_dict()
    assert run(SMALL, seed=3)[1].to_dict() == straight
    env = fg_env.load(PATH, inputs=SMALL, seed=3)
    env.run(rounds=7)  # part-way through the second bar
    resumed = fg_env.Env.restore(PATH, json.loads(json.dumps(env.snapshot())))
    assert resumed.run().to_dict() == straight


def test_cash_and_shares_are_conserved_through_a_crash_and_its_halts():
    inputs = {**SMALL, "bars": 5, "events": CRASH, "circuit_breaker_pct": 4}
    env, result = run(inputs, seed=2)
    assert result.outputs["demo_halts"] > 0
    assert order_book.audit(env.world, "demo") == []
    traders = [e for e in env.world.entities.values() if e.alive and e.properties.get("demo_shares") is not None]
    cash = sum(t.properties["cash"] + t.properties["demo_reserved_cash"] for t in traders) + env.world.props["demo_fees"]
    shares = sum(t.properties["demo_shares"] + t.properties["demo_reserved_shares"] for t in traders)
    assert cash == pytest.approx(env.world.props["demo_supply"]["cash"]) and shares == env.world.props["demo_supply"]["shares"]


def test_the_circuit_breaker_halts_the_rest_of_the_bar_and_trading_resumes_at_the_next_bar():
    env = fg_env.load(PATH, inputs={**SMALL, "events": CRASH, "circuit_breaker_pct": 4}, seed=2)
    trades, halted_rounds = [], []
    while not env.finished:
        env.run(rounds=1)
        trades.append(env.world.props["demo_trades"])
        if env.world.props["demo_halted"]:
            halted_rounds.append(env.round)
    first = halted_rounds[0]
    bar_end = first + 6 - 1 - (first - 1) % 6
    assert halted_rounds[:bar_end - first + 1] == list(range(first, bar_end + 1))  # halted to the end of its bar
    assert trades[bar_end - 1] == trades[first - 1]  # nothing trades while halted
    assert bar_end + 1 not in halted_rounds and trades[bar_end] > trades[bar_end - 1]  # the next bar trades again
    assert bars(env)[(first - 1) // 6]["halted"] is True


def test_a_tight_circuit_breaker_clearly_softens_the_crash_bar_against_no_breaker():
    exp = fg_env.experiment(PATH, runs=4, seed=1, arms=["breaker_tight", "breaker_off"], inputs={**SMALL, "events": CRASH})
    deltas = exp.deltas("breaker_off")["breaker_tight"]
    assert deltas["worst_bar_drop"]["clear"] and deltas["worst_bar_drop"]["mean"] > 0.02  # the crash bar falls less
    assert deltas["demo_volume"]["clear"] and deltas["demo_volume"]["mean"] < 0  # halted passes trade nothing
    assert deltas["halted_bars"]["mean"] > 0


def test_news_moves_the_hidden_fundamental_and_sentiment_which_fades():
    inputs = {**SMALL, "jump_prob_bar": 0, "volatility_scale": 0.1, "events": CRASH}
    env, result = run(inputs, seed=4)
    fundamental, sentiment = result.series["fundamental"], result.series["sentiment"]
    before, after = fundamental[5], fundamental[6]
    assert after / before == pytest.approx(0.7, abs=0.01)
    assert sentiment[6] < -0.9 and sentiment[-1] > sentiment[6] and sentiment[5] == 0
    assert any(e["kind"] == "news" and "auditor resigns" in e["text"] for e in result.events)


def test_a_model_seat_reads_a_compact_picture_and_trades_through_typed_tools():
    seen = {}

    def seat(wake):
        if wake.round == 7:
            seen["update"], seen["tools"] = wake.update, {t.name: t for t in wake.tools}
            seen["buy"] = wake.call("demo_buy", {"qty": 50})
            seen["note"] = wake.call("explain", {"text": "Buying the dip after the halt."})
        wake.end()

    env, result = run({**SMALL, "seats": 1}, seed=5, participants={"seat": seat})
    for part in ("Session", "Price moves", "Recent bars", "Aggressive flow last bar", "DEMO order book"):
        assert part in seen["update"]
    assert not re.search(r"\bfundamental\b", seen["update"], re.IGNORECASE)  # the latent value stays hidden
    assert {"demo_buy", "demo_sell", "explain"} <= set(seen["tools"]) and "demo_algo" not in seen["tools"]
    assert seen["tools"]["demo_buy"].input_schema["properties"]["qty"]["type"] == "integer"
    assert seen["buy"].ok and seen["note"].ok
    assert env.world.entities["seat_1"].properties["demo_shares"] > 0


def test_seats_without_a_model_play_the_coded_trend_policy():
    _, result = run({**SMALL, "bars": 8, "seats": 6}, seed=6)
    seat_actions = [e for e in result.events if e["kind"] == "action" and str(e.get("actor", "")).startswith("seat_")]
    assert seat_actions and {e["data"]["action"] for e in seat_actions} <= {"demo_buy", "demo_sell"}


def test_the_tape_shows_the_stylized_facts_of_the_seed_history_over_several_seeds():
    inputs = {"participants": 150, "bars": 60}
    exp = fg_env.experiment(PATH, runs=3, seed=1, inputs=inputs, workers=3)
    runs = next(iter(exp.arms.values())).runs
    for result in runs:
        assert result.status == "completed", result.error
        out = result.outputs
        stats, calibration = out["stats"], out["calibration"]
        assert out["realism_score"] >= 0.7
        assert stats["kurtosis"] > 1  # fat tails
        assert stats["acf_abs"] > 0.1  # volatility clusters
        assert abs(stats["acf1"]) < 0.3  # little memory in returns
        assert 0.5 < stats["sigma"] / calibration["target_sigma"] < 2  # no pilot fit: the controller only nudges it
        assert 0.75 < stats["avg_volume"] / calibration["target_volume"] < 1.25
        assert 5 < out["spread_bps_avg"] < 150 and out["depth_avg"] > 0
        assert out["liquidations"] > 0  # stop-losses fire in a real session
