"""Run budgets: tokens, tool calls, host calls and seconds end a run (or idle its agents) at a safe point."""
import json
import time

import pytest

import fg_env
from fg_env.__main__ import main
from fg_env.sdk import host
from fg_env.sdk.host.stubs import StubEvaluator

from test_exposures import TOWN, reader
from test_host_tape import PITCH, _with, pitcher
from test_runtime import SHOP


def test_a_call_budget_ends_the_run_before_the_next_turn_with_ended_by_budget():
    result = fg_env.run(TOWN, reader, seed=1, budget={"calls": 3})  # each turn makes two calls
    assert (result.status, result.ended_by, result.rounds) == ("ended", "budget", 1)
    assert result.budget["exhausted"] == "calls" and result.budget["used"]["calls"] == 4
    assert result.events[-1]["text"] == "The tool call budget ran out (4 of 3); the run ended."
    assert "budget: calls ran out (4 of 3)" in result.summary()


def test_idle_on_exhaust_lets_the_world_finish_with_every_agent_idle():
    woken = []

    def counting(wake):
        woken.append(wake.round)
        reader(wake)

    result = fg_env.run(TOWN, counting, seed=1, budget={"calls": 3, "on_exhaust": "idle"})
    assert (result.status, result.ended_by, result.rounds) == ("completed", "rounds", 2)
    assert woken == [1, 1]
    kinds = [event["kind"] for event in result.events]
    assert "action" not in kinds[kinds.index("budget"):]


def test_a_token_budget_counts_the_input_and_output_tokens_participants_report():
    def spender(wake):
        wake.record_usage(input_tokens=60, output_tokens=40, cache_read_tokens=1_000)
        wake.end()

    result = fg_env.run(TOWN, spender, seed=1, budget={"tokens": 150})
    assert result.budget["used"]["tokens"] == 200 and result.stats["wakes"] == 2 and result.ended_by == "budget"


def test_host_calls_count_answers_on_the_tape_but_not_declared_fallbacks():
    live = host.load(PITCH, hosts={"judge": StubEvaluator()}, seed=1).run(pitcher, budget={"host_calls": 1})
    assert (live.ended_by, live.rounds, live.budget["used"]["host_calls"]) == ("budget", 1, 1)
    fallback = host.load(_with(fallback="midpoint"), seed=1).run(pitcher, budget={"host_calls": 1})
    assert fallback.status == "completed" and fallback.budget["used"]["host_calls"] == 0


def test_a_budget_is_deterministic_and_survives_a_snapshot():
    whole = fg_env.run(TOWN, reader, seed=1, budget={"calls": 5})
    assert whole.events == fg_env.run(TOWN, reader, seed=1, budget={"calls": 5}).events
    env = fg_env.load(TOWN, seed=1)
    env.run(reader, rounds=1, budget={"calls": 5})
    snapshot = json.loads(json.dumps(env.snapshot()))
    assert snapshot["budget"]["limits"] == {"calls": 5} and snapshot["budget"]["exhausted"] is None
    rest = fg_env.Env.restore(TOWN, snapshot).run(reader)
    assert rest.events == whole.events and rest.ended_by == whole.ended_by == "budget"
    assert rest.budget["used"]["calls"] == whole.budget["used"]["calls"] == 6


def test_a_seconds_budget_ends_a_slow_run():
    def slow(wake):
        time.sleep(0.02)
        wake.end()

    result = fg_env.run(TOWN, slow, seed=1, budget={"seconds": 0.01})
    assert result.ended_by == "budget" and result.budget["exhausted"] == "seconds" and result.rounds == 1


@pytest.mark.parametrize("budget, message", [
    ({"token": 5}, "budget has no 'token'"),
    ({"calls": 0}, "budget calls must be a whole number > 0, got 0"),
    ({"calls": 2.5}, "budget calls must be a whole number > 0, got 2.5"),
    ({"seconds": -1}, "budget seconds must be a number of seconds > 0, got -1"),
    ({"calls": 1, "on_exhaust": "stop"}, "budget on_exhaust must be 'end' or 'idle', got 'stop'"),
    ({"on_exhaust": "end"}, "budget sets no limit"),
    (100, "budget must be a mapping"),
])
def test_budget_mistakes_say_what_to_fix(budget, message):
    with pytest.raises(ValueError, match=message):
        fg_env.load(TOWN, seed=1).run(reader, budget=budget)


def test_cli_run_takes_a_budget(tmp_path, capsys):
    path = tmp_path / "shop.json"
    path.write_text(json.dumps(SHOP))
    assert main(["run", str(path), "--seed", "1", "--budget", "calls=3", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):])["ended_by"] == "budget"
