"""The flagship exchange: a calibrated order-book session rebuilt as a contract, held to the platform Exchange's bar."""
import csv
import json
import os
import re
import statistics
from pathlib import Path

import pytest

import fg_env
from fg_env import expr
from fg_env.mechanisms import order_book

pytestmark = pytest.mark.slow  # statistical or engine-behaviour: `make test-fast` leaves it out

PATH = Path(__file__).parents[1] / "examples" / "contracts" / "exchange_flagship.json"
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
    counts = {kind: len(env.entities(f"demo_{kind}"))
              for kind in ("market_maker", "momentum", "mean_reversion", "fundamentalist",
                            "noise", "passive")}
    assert counts == {"market_maker": 18, "momentum": 54, "mean_reversion": 48, "fundamentalist": 42, "noise": 120,
                      "passive": 18}
    world = env.world.props
    with open(PATH.parent / "exchange_flagship" / "seed_history.csv", newline="") as handle:
        history = [{k: float(v) for k, v in row.items()} for row in csv.DictReader(handle)][-120:]
    avg_volume = sum(row["volume"] for row in history) / len(history)
    expected_orders = 8 * (18 * 0.9 + 54 * 0.12 + 48 * 0.15 + 42 * 0.08 + 120 * 0.06 + 18 * 0.5)
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

    def totals(world):
        traders = [e for e in world.entities.values() if e.alive and e.properties.get("demo_shares") is not None]
        cash = (sum(t.properties["cash"] + t.properties["demo_reserved_cash"] for t in traders)
                + world.props["demo_fees"])
        return cash, sum(t.properties["demo_shares"] + t.properties["demo_reserved_shares"] for t in traders)

    start_cash, start_shares = totals(fg_env.load(PATH, inputs=inputs, seed=2).world)
    env, result = run(inputs, seed=2)
    assert result.outputs["demo_halts"] > 0
    assert order_book.audit(env.world, "demo") == []
    cash, shares = totals(env.world)
    assert cash == pytest.approx(start_cash) and shares == start_shares


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
    exp = fg_env.experiment(PATH, runs=4, seed=1, arms=["breaker_tight", "breaker_off"],
                            inputs={**SMALL, "events": CRASH})
    deltas = exp.deltas("breaker_off")["breaker_tight"]
    assert deltas["worst_bar_drop"]["clear"] and deltas["worst_bar_drop"]["mean"] > 0.02  # the crash bar falls less
    assert deltas["demo_volume"]["max"] < 0  # halted passes trade nothing: less volume in every paired run
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


#: The stylized facts a session's tape is held to: [low, high) bounds one realistic session meets.
FACT_BOUNDS = {
    "realism_score": (0.7, None),
    "kurtosis": (1, None),  # fat tails
    "acf_abs": (0.1, None),  # volatility clusters
    "abs_acf1": (None, 0.3),  # little memory in returns
    "sigma_ratio": (0.5, 2),  # no pilot fit: the volatility controller only nudges it
    "volume_ratio": (0.75, 1.25),
    "spread_bps_avg": (5, 150),
    "liquidations": (1, None),  # stop-losses fire in a real session
}


def realism_facts(result):
    assert result.status == "completed", result.error
    out = result.outputs
    stats, calibration = out["bar_stats"], out["calibration"]
    assert out["depth_avg"] > 0
    return {"realism_score": out["realism_score"], "kurtosis": stats["kurtosis"], "acf_abs": stats["acf_abs"],
            "abs_acf1": abs(stats["acf1"]), "sigma_ratio": stats["sigma"] / calibration["target_sigma"],
            "volume_ratio": stats["avg_volume"] / calibration["target_volume"],
            "spread_bps_avg": out["spread_bps_avg"], "liquidations": out["liquidations"]}


def inside(value, bound):
    low, high = bound
    return (low is None or value >= low) and (high is None or value < high)


def check_realism_over_seeds(runs, least_every_bound, loosen=None):
    """Each fact's median over ``runs`` sessions lies inside its bound, and at least ``least_every_bound`` sessions
    meet every bound. One session is not a claim: the tape is bimodal across seeds (sessions where stop-loss
    cascades start are volatile and fat-tailed, the rest calm; realised volatility tracks liquidations at r≈0.9)."""
    exp = fg_env.experiment(PATH, runs=runs, seed=1, inputs={"participants": 150, "bars": 60},
                            workers=min(runs, os.cpu_count() or 1))
    facts = [realism_facts(result) for result in next(iter(exp.arms.values())).runs]
    bounds = {**FACT_BOUNDS, **(loosen or {})}
    medians = {name: statistics.median(f[name] for f in facts) for name in bounds}
    assert all(inside(medians[name], bound) for name, bound in bounds.items()), medians
    every_bound = sum(all(inside(f[name], bound) for name, bound in FACT_BOUNDS.items()) for f in facts)
    assert every_bound >= least_every_bound, (every_bound, facts)


# Thresholds come from 40 seeded sessions at this scale before and after the patterns migration (no fact's
# distribution differs, Mann-Whitney p 0.2-0.9): one session meets every bound with chance 0.60 (0.55 before).
# Resampling those sessions, this fast check fails a healthy session set 2.6% of the time (a 4-run one 8%).
def test_the_tape_shows_the_stylized_facts_of_the_seed_history_in_the_median_session():
    check_realism_over_seeds(runs=6, least_every_bound=1, loosen={"abs_acf1": (None, 0.35)})


# FG_ENV_SLOW=1: strict medians and at least 8 of 24 sessions meeting every bound. A healthy set fails 0.3% of the
# time (3% on the pre-migration numbers); if one session in five met every bound it would fail 91% of the time.
@pytest.mark.skipif(not os.environ.get("FG_ENV_SLOW"), reason="slow verification: set FG_ENV_SLOW=1")
def test_the_stylized_facts_hold_across_many_seeds():
    check_realism_over_seeds(runs=24, least_every_bound=8)
