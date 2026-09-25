"""LLM participant failures are loud: permanent provider errors fail the run, lost turns and refusals are counted,
tool names are ones providers accept (fake clients, no network)."""
import copy
import json
import time
import warnings
from types import SimpleNamespace as NS

import pytest
from test_llm_participants import FailingAnthropic, FakeAnthropic, FakeOpenAI, Flaky
from test_runtime import AUCTION, SHOP

import fg_env
from fg_env import participants

ONE_SHOPPER = {"shoppers": 1}


class AuthenticationError(Exception):
    status_code = 401


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)


def test_a_rejected_api_key_fails_the_run_at_once_naming_the_participant_error_and_fix():
    client = FailingAnthropic([], [AuthenticationError("invalid x-api-key")] * 20)
    with pytest.raises(fg_env.RunError) as info:
        fg_env.run(SHOP, participants.anthropic(client, "claude-x"), seed=1, inputs=ONE_SHOPPER)
    message = str(info.value)
    assert "participant:shopper_1" in message
    assert "AuthenticationError (HTTP 401): invalid x-api-key" in message
    assert "API key" in message and "claude-x" in message
    assert len(client.requests) == 1  # never retried
    result = info.value.result
    assert result.status == "failed" and result.error == message and result.stats["forfeits"] == 0


@pytest.mark.parametrize("error, fix", [
    (Flaky(400), "rejected the request"),
    (Flaky(404), "model id 'claude-x'"),
    (TypeError("create() got an unexpected keyword argument 'tools'"), "anthropic.Anthropic()"),
])
def test_other_permanent_errors_fail_the_run_with_a_fix(error, fix):
    agent = participants.anthropic(FailingAnthropic([], [error] * 20), "claude-x")
    result = fg_env.load(SHOP, seed=1, inputs=ONE_SHOPPER).run(agent, rounds=1)
    assert result.status == "failed" and fix in result.error
    assert type(error).__name__ in result.error


def test_only_retries_that_run_out_forfeit_the_turn_and_a_diagnostic_says_so():
    client = FailingAnthropic([[("end_turn", {})]] * 4, [Flaky(503)] * 3)
    agent = participants.anthropic(client, "claude-x", retries=2)
    result = fg_env.load(SHOP, seed=1, inputs={"shoppers": 2}).run(agent, rounds=1)
    assert result.status != "failed", result.error
    assert result.stats["forfeits"] == 1 and result.stats["llm_retries"] == 2
    [found] = [d for d in result.diagnostics if d["code"] == "turns_forfeited"]
    assert "1 turn" in found["message"] and "shopper_1 1" in found["message"] and "retries" in found["fix"]
    clean = fg_env.load(SHOP, seed=1, inputs=ONE_SHOPPER).run(participants.anthropic(FakeAnthropic([]), "m"), rounds=1)
    assert not [d for d in clean.diagnostics if d["code"] == "turns_forfeited"]


def test_on_error_is_gone():
    with pytest.raises(TypeError):
        participants.anthropic(FakeAnthropic([]), "m", on_error="end_turn")


class AsyncAnthropic:
    def __init__(self):
        self.messages = self

    async def create(self, **request):
        raise AssertionError("never awaited")


class AsyncOpenAI:
    def __init__(self):
        self.chat = NS(completions=self)

    async def create(self, **request):
        raise AssertionError("never awaited")


@pytest.mark.parametrize("make, sync_client", [
    (lambda: participants.anthropic(AsyncAnthropic(), "m"), "anthropic.Anthropic()"),
    (lambda: participants.openai(AsyncOpenAI(), "m"), "openai.OpenAI()"),
])
def test_an_async_client_fails_the_run_saying_to_pass_the_sync_client(make, sync_client):
    env = fg_env.load(SHOP, seed=1, inputs=ONE_SHOPPER)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # the coroutine is closed, not left to warn "never awaited"
        result = env.run(make(), rounds=1)
    assert result.status == "failed"
    assert "async client" in result.error and sync_client in result.error and "env.arun" in result.error


def test_an_anthropic_refusal_is_counted_and_ends_the_turn_without_a_nudge():
    class Refusing(FakeAnthropic):
        def create(self, **request):
            self.requests.append(request)
            return NS(content=[], stop_reason="refusal", usage=NS(input_tokens=10, output_tokens=0))

    client = Refusing([])
    result = fg_env.load(SHOP, seed=1, inputs=ONE_SHOPPER).run(participants.anthropic(client, "m"), rounds=1)
    assert result.stats["refusals"] == 1 and len(client.requests) == 1


def test_an_openai_refusal_is_counted():
    class Refusing(FakeOpenAI):
        def create(self, **request):
            self.requests.append(request)
            message = NS(content=None, tool_calls=None, refusal="I can't help with that.")
            return NS(choices=[NS(message=message, finish_reason="stop")],
                      usage=NS(prompt_tokens=5, completion_tokens=5))

    client = Refusing([])
    result = fg_env.run(AUCTION, {"ann": participants.openai(client, "m"), "bo": "idle", "cy": "idle"}, seed=1)
    assert result.stats["refusals"] == 1 and len(client.requests) == 1


def test_malformed_openai_arguments_count_as_invalid_calls():
    client = FakeOpenAI([[("bid", "{not json")], [("bid", json.dumps({"amount": 30}))]])
    result = fg_env.run(AUCTION, {"ann": participants.openai(client, "m"), "bo": "idle", "cy": "idle"}, seed=1)
    assert result.agent_stats["ann"]["invalid_calls"] == 1 and result.outputs["price"] == 30
    [reply] = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert "not valid JSON" in reply["content"]


def test_openai_sends_max_completion_tokens_and_both_pass_extra_request_fields():
    client = FakeOpenAI([[("bid", json.dumps({"amount": 30}))]])
    agent = participants.openai(client, "m", max_tokens=500, extra={"temperature": 0, "user": "sim"})
    fg_env.run(AUCTION, {"ann": agent, "bo": "idle", "cy": "idle"}, seed=1)
    sent = client.requests[0]
    assert sent["max_completion_tokens"] == 500 and "max_tokens" not in sent
    assert sent["temperature"] == 0 and sent["user"] == "sim"

    claude = FakeAnthropic([[("end_turn", {})]])
    fg_env.load(SHOP, seed=1, inputs=ONE_SHOPPER).run(
        participants.anthropic(claude, "m", extra={"temperature": 0.2, "metadata": {"user_id": "u"}}), rounds=1)
    assert claude.requests[0]["temperature"] == 0.2 and claude.requests[0]["metadata"] == {"user_id": "u"}

    with pytest.raises(ValueError, match="extra cannot set 'tools'"):
        participants.anthropic(claude, "m", extra={"tools": []})
    with pytest.raises(ValueError, match="extra cannot set 'max_tokens'"):
        participants.openai(client, "m", max_tokens=500, extra={"max_tokens": 100})
    legacy = FakeOpenAI([[("bid", json.dumps({"amount": 30}))]])  # a server that only knows the older field
    fg_env.run(AUCTION,
               {"ann": participants.openai(legacy, "m", extra={"max_tokens": 100}), "bo": "idle", "cy": "idle"}, seed=1)
    assert legacy.requests[0]["max_tokens"] == 100 and "max_completion_tokens" not in legacy.requests[0]


def test_fg_env_run_raises_for_a_failed_run_and_env_run_returns_it():
    def broken(wake):
        raise KeyError("oops")

    with pytest.raises(fg_env.RunError, match="participant for shopper_1 raised KeyError") as info:
        fg_env.run(SHOP, broken, seed=1, inputs=ONE_SHOPPER)
    assert info.value.result.status == "failed"
    result = fg_env.load(SHOP, seed=1, inputs=ONE_SHOPPER).run(broken)
    assert result.status == "failed" and "KeyError" in result.error


@pytest.mark.parametrize("name, fix", [
    ("end_turn", "'end_turn_action'"), ("inspect", "'inspect_action'"), ("look", "'look_action'"),
    ("buy item", "'buy_item'"), ("café", "'cafe'"), ("x" * 70, "'" + "x" * 64 + "'"),
])
def test_check_refuses_action_names_a_model_could_never_call(name, fix):
    contract = copy.deepcopy(SHOP)
    contract["actions"][name] = contract["actions"].pop("review")
    [issue] = [i for i in fg_env.check(contract, rounds=0) if i.path == f"actions.{name}" and i.severity == "error"]
    assert fix in issue.fix


class EmptyThenBidding(FakeOpenAI):
    """Answers with no choices (OpenRouter's hiccup) ``empties`` times, then from the script."""

    def __init__(self, script, empties):
        super().__init__(script)
        self.empties = empties

    def create(self, **request):
        if self.empties:
            self.empties -= 1
            self.requests.append(None)
            return NS(choices=[], usage=None, error={"message": "provider hiccup"})
        return super().create(**request)


def test_an_openai_reply_with_no_choices_is_retried_and_then_forfeits_the_turn_instead_of_losing_it_silently():
    bids = [[("bid", json.dumps({"amount": 30}))]]
    retried = EmptyThenBidding(bids, empties=2)
    result = fg_env.run(AUCTION, {"ann": participants.openai(retried, "m"), "bo": "idle", "cy": "idle"}, seed=1)
    assert result.outputs["price"] == 30 and result.stats["llm_retries"] == 2 and result.stats["forfeits"] == 0

    lost = EmptyThenBidding(bids, empties=99)
    result = fg_env.load(AUCTION, seed=1).run({"ann": participants.openai(lost, "m", retries=1), "bo": "idle",
                                               "cy": "idle"})
    assert result.stats["forfeits"] >= 1 and "turns_forfeited" in [d["code"] for d in result.diagnostics]
    assert not result.ok


class PromptTooLong(Exception):
    status_code = 400


def test_a_prompt_too_long_for_the_model_is_diagnosed_as_such_not_as_a_failing_provider():
    """No retry can shorten a prompt: the finding says so and points at what the agent reads (audit 9 LLM M1)."""
    client = FailingAnthropic([], [PromptTooLong("prompt is too long: 250000 tokens > 200000 maximum")] * 20)
    result = fg_env.load(SHOP, seed=1, inputs=ONE_SHOPPER).run(participants.anthropic(client, "claude-x"), rounds=1)
    assert result.stats["forfeits"] == result.stats["too_long"] == 1 and result.stats["llm_retries"] == 0
    [found] = [d for d in result.diagnostics if d["code"] == "turns_forfeited"]
    assert "longer than the model's context" in found["message"] and "retries" not in found["fix"]
