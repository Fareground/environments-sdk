"""Run budgets: tokens, tool calls, host calls and seconds end a run (or idle its agents) at a safe point."""
import json
import time
from types import SimpleNamespace as NS

import pytest

import fg_env
from fg_env import participants
from fg_env.__main__ import main
from fg_env import host
from fg_env.host.stubs import StubEvaluator

from test_exposures import TOWN, reader
from test_host_tape import PITCH, _with, pitcher
from test_llm_participants import FakeAnthropic
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


def test_a_token_budget_counts_input_output_and_cache_writes_in_full_and_cache_reads_at_a_tenth():
    def spender(wake):
        wake.record_usage(input_tokens=60, output_tokens=40, cache_write_tokens=50, cache_read_tokens=500)
        wake.end()

    result = fg_env.run(TOWN, spender, seed=1, budget={"tokens": 350})
    assert result.budget["used"]["tokens"] == 400 and result.stats["wakes"] == 2 and result.ended_by == "budget"


@pytest.mark.parametrize("turns, used", [("sequential", 300), ("simultaneous", 400)])
def test_a_token_budget_stops_turns_in_progress_once_it_is_spent(turns, used):
    def chatty(wake):
        while not wake.done:  # a model loop: one reply, then its tool call
            wake.record_usage(input_tokens=100)
            wake.call("look", {"view": "board"})

    town = {**TOWN, "stages": [{"name": "talk", "turns": turns}]}
    result = fg_env.run(town, chatty, seed=1, budget={"tokens": 250})
    assert (result.ended_by, result.rounds) == ("budget", 1)
    # Ann's turn ends with the reply that spends it; in a simultaneous stage Bo's first reply ends Bo's turn too
    assert result.budget["used"]["tokens"] == used


def test_an_llm_participant_makes_no_more_model_calls_once_the_token_budget_is_spent():
    client = FakeAnthropic([[("look", {"view": "board"})]] * 20)
    town = {**TOWN, "stages": [{"name": "talk", "turns": "simultaneous"}]}
    result = fg_env.run(town, participants.anthropic(client, "m"), seed=1, budget={"tokens": 200})
    # The reply that spends it ends both turns. The two turns run in parallel, so one may already have sent its next
    # request before the other's reply spent the budget: at most one call per agent is in flight, never more.
    assert result.ended_by == "budget" and 2 <= len(client.requests) <= 3


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


class CacheWriting(FakeAnthropic):
    """Every reply writes 3,000 tokens to the prompt cache, as a long cached brief does on a cache miss."""

    def __init__(self, script, pause=0.0):
        super().__init__(script)
        self.pause = pause

    def create(self, **request):
        time.sleep(self.pause)
        reply = super().create(**request)
        reply.usage = type(reply.usage)(input_tokens=50, output_tokens=20, cache_read_input_tokens=0,
                                        cache_creation_input_tokens=3_000)
        return reply


def test_prompt_cache_writes_count_toward_the_token_budget():
    client = CacheWriting([[("say", {"text": "hi"})]] * 20)
    result = fg_env.run(TOWN, participants.anthropic(client, "m"), seed=1, budget={"tokens": 1_000})
    assert result.ended_by == "budget" and len(client.requests) == 1
    assert result.budget["used"]["tokens"] == 3_070 and result.stats["cache_write_tokens"] == 3_000


def test_parallel_model_calls_wait_while_the_calls_under_way_may_spend_the_token_budget():
    crowd = {**TOWN, "entities": {f"c{i}": {"type": "citizen", "name": f"C{i}"} for i in range(8)},
             "stages": [{"name": "talk", "turns": "simultaneous"}]}
    client = CacheWriting([[("say", {"text": "hi"})]] * 40, pause=0.05)
    result = fg_env.run(crowd, participants.anthropic(client, "m"), seed=1, budget={"tokens": 1_000})
    assert result.ended_by == "budget" and len(client.requests) <= 2  # not one call per agent in flight


def test_a_host_models_cache_tokens_count_toward_the_run_budget():
    class Judge:
        def __init__(self):
            self.messages = self

        def create(self, **request):
            usage = NS(input_tokens=10, output_tokens=10, cache_read_input_tokens=1_000,
                       cache_creation_input_tokens=500)
            return NS(content=[NS(type="text", text='{"scores": {"quality": 5}, "rationale": "ok"}')],
                      stop_reason="end_turn", usage=usage)

    env = host.load(PITCH, hosts={"judge": host.adapters.anthropic(Judge(), "m")}, seed=1)
    result = env.run(pitcher, budget={"tokens": 100})
    assert result.ended_by == "budget" and result.budget["used"]["tokens"] == 620
    assert result.stats["cache_read_tokens"] == 1_000 and result.stats["cache_write_tokens"] == 500
