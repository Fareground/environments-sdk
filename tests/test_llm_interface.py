"""What an LLM participant and an LLM host send and are told: budgets that keep turns parallel, previews that never
call a model, host failures that say how to fix them, and tool text without noise (fake clients, no network)."""
import copy
import json
import threading
import time
from types import SimpleNamespace as NS

import fg_env
from fg_env import host, participants

from test_host_tape import PITCH, _Anthropic, pitcher
from test_llm_failures import EmptyThenBidding
from test_llm_participants import FakeAnthropic, FakeOpenAI
from test_runtime import AUCTION, SHOP


class SlowBidder:
    """An Anthropic client whose every call takes a moment, counting how many are under way at once."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = self.most = 0
        self.messages = self

    def create(self, **request):
        with self.lock:
            self.active += 1
            self.most = max(self.most, self.active)
        time.sleep(0.2)
        with self.lock:
            self.active -= 1
        usage = NS(input_tokens=100, output_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return NS(content=[NS(type="tool_use", id="c1", name="bid", input={"amount": 10})], usage=usage)


def test_a_token_budget_far_from_its_limit_keeps_parallel_turns_parallel():
    client = SlowBidder()
    bidders = {name: participants.anthropic(client, "m") for name in ("ann", "bo", "cy")}
    result = fg_env.load(AUCTION, seed=1).run(bidders, budget={"tokens": 10**9})
    assert result.ok, result.summary()
    assert client.most == 3  # each first call holds the size of its prompt, not all that is left


def test_a_preview_plays_earlier_turns_without_calling_a_model(monkeypatch):
    client = FakeAnthropic([[("buy", {"offer": "espresso", "qty": 1}), ("end_turn", {})]] * 20)
    monkeypatch.setattr(participants, "official_client", lambda provider, model: client)
    env = fg_env.load(SHOP, seed=1, inputs={"shoppers": 3})
    env.run({"*": "anthropic:claude-x"}, rounds=1)
    calls = len(client.requests)
    last = [e["id"] for e in env.entities() if e["type"] == "shopper"][-1]
    preview = env.preview(last)
    assert len(client.requests) == calls and "tools" in preview


def test_a_rejected_host_key_fails_the_run_once_with_the_fix_and_no_correction_is_sent():
    class Unauthorized(Exception):
        status_code = 401

    client = _Anthropic([Unauthorized("invalid x-api-key")] * 3)
    env = host.load(PITCH, hosts={"judge": host.adapters.anthropic(client, "claude-x")}, seed=1)
    result = host.run(env, pitcher)
    assert result.status == "failed" and len(client.requests) == 1
    assert "Unauthorized (HTTP 401): invalid x-api-key" in result.error
    assert "Check the API key your client was made with" in result.error


def test_a_host_still_failing_after_its_retries_says_so_without_asking_again(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)

    class Overloaded(Exception):
        status_code = 529

    client = _Anthropic([Overloaded("busy")] * 5)
    env = host.load(PITCH, hosts={"judge": host.adapters.anthropic(client, "claude-x", retries=1)}, seed=1)
    result = host.run(env, pitcher)
    assert result.status == "failed" and len(client.requests) == 2  # one try, one retry, no correction
    assert "still failed after 1 retry with Overloaded (HTTP 529): busy" in result.error


def test_host_retries_never_wait_past_the_deadline_of_the_turn_that_asked():
    class RateLimited(Exception):
        status_code = 429
        response = NS(headers={"retry-after": "30"})

    client = _Anthropic([RateLimited("slow down")] * 3)
    env = host.load(PITCH, hosts={"judge": host.adapters.anthropic(client, "claude-x")}, seed=1)
    started = time.monotonic()
    result = env.run(pitcher, rounds=1, time_limit=2)
    assert time.monotonic() - started < 5 and len(client.requests) == 1
    assert result.status == "failed" and "The turn's time ran out before another try." in result.error


def test_ends_your_turn_follows_the_terminal_flag_not_the_words_of_the_description():
    contract = copy.deepcopy(SHOP)
    contract["actions"]["buy"].update(terminal=True, description="Buy units of an offer; the rest are turned away.")
    contract["actions"]["review"]["description"] = "Rate a return visit."  # "return" is no reason to say it
    env = fg_env.load(contract, seed=1, inputs={"shoppers": 1})
    buy = next(t for t in env.preview("shopper_1")["tools"] if t["name"] == "buy")
    assert buy["description"].endswith("Ends your turn.")
    contract["actions"]["buy"]["description"] = "Buy units of an offer. Ends your turn."
    buy = next(t for t in fg_env.load(contract, seed=1, inputs={"shoppers": 1}).preview("shopper_1")["tools"]
               if t["name"] == "buy")
    assert buy["description"].count("Ends your turn.") == 1


SOLO = {
    "name": "Solo",
    "stages": [{"name": "day", "max_calls": 3}],
    "types": {"trader": {"agent": True, "props": {"cash": 5}}, "stall": {"inspect": True, "props": {"price": 2}}},
    "entities": {"ann": {"type": "trader", "name": "Ann"}},
    "actions": {"wait": {"by": "trader", "do": []}},
}
WITH_STALL = {**SOLO, "entities": {**SOLO["entities"], "fruit": {"type": "stall", "name": "Fruit"}}}


def test_inspect_is_not_offered_when_its_only_choice_is_the_agent_itself():
    assert "inspect" not in [t["name"] for t in fg_env.load(SOLO, seed=1).preview("ann")["tools"]]
    assert "inspect" in [t["name"] for t in fg_env.load(WITH_STALL, seed=1).preview("ann")["tools"]]


def test_the_update_mentions_free_reads_only_when_the_turn_offers_a_read():
    update = fg_env.load(SOLO, seed=1).preview("ann")["update"]
    assert "You have 3 tool calls this turn." in update and "free reads" not in update
    assert "up to 3 free reads (look and inspect)" in fg_env.load(WITH_STALL, seed=1).preview("ann")["update"]


class ErrorThenBidding(EmptyThenBidding):
    """An OpenRouter-style reply whose only choice says the provider failed, then scripted bids."""

    def create(self, **request):
        if self.empties:
            self.empties -= 1
            self.requests.append(None)
            return NS(choices=[NS(message=NS(content="", tool_calls=None), finish_reason="error")],
                      usage=NS(prompt_tokens=5, completion_tokens=0))
        return FakeOpenAI.create(self, **request)


def test_an_openai_reply_that_finished_with_an_error_is_retried_not_blamed_on_the_model():
    client = ErrorThenBidding([[("bid", json.dumps({"amount": 30}))]], empties=2)
    result = fg_env.run(AUCTION, {"ann": participants.openai(client, "m"), "bo": "idle", "cy": "idle"}, seed=1)
    assert result.outputs["price"] == 30 and result.stats["llm_retries"] == 2
    assert result.stats["no_tool_replies"] == 0


def test_broken_json_arguments_are_refused_saying_the_json_is_invalid():
    client = FakeOpenAI([[("bid", '{"amount": 30')], [("bid", json.dumps({"amount": 30}))]])
    result = fg_env.run(AUCTION, {"ann": participants.openai(client, "m"), "bo": "idle", "cy": "idle"}, seed=1)
    [reply] = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert reply["content"].startswith("bid was not done: its arguments are not valid JSON (")
    assert result.agent_stats["ann"]["invalid_calls"] == 1 and result.outputs["price"] == 30
