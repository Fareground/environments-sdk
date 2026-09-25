"""LLM participant loops against fake provider clients (no network)."""
import copy
import json
import time
from types import SimpleNamespace as NS

from test_runtime import AUCTION, SHOP

import fg_env
from fg_env import participants


class FakeAnthropic:
    """Replays scripted tool calls and records every request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))
        blocks = self.script.pop(0) if self.script else []
        content = [NS(type="tool_use", id=f"call_{len(self.requests)}_{i}", name=name, input=args)
                   for i, (name, args) in enumerate(blocks)]
        if not content:
            content = [NS(type="text", text="done")]
        usage = NS(input_tokens=100, output_tokens=10, cache_read_input_tokens=80, cache_creation_input_tokens=0)
        return NS(content=content, usage=usage)


def test_anthropic_participant_drives_a_turn_with_corrections():
    client = FakeAnthropic([
        [("buy", {"offer": "latte", "qty": 99})],
        [("buy", {"offer": "espresso", "qty": 2})],
        [("end_turn", {})],
    ])
    agent = participants.anthropic(client, "claude-sonnet-5")
    env = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1})
    env.run(agent, rounds=1)
    first = client.requests[0]
    assert "# Corner shop" in first["system"][0]["text"] and "On the shelf" in first["messages"][0]["content"]
    assert "cache_control" not in json.dumps(first)  # a prompt this short: no model caches it
    correction = client.requests[1]["messages"][-1]["content"][0]
    assert correction["is_error"] and "qty must be at most 10" in correction["content"]
    assert client.requests[2]["messages"][-1]["content"][0]["content"] == "You bought 2 × Espresso for $6.00."
    assert env.world.props["revenue"] == 6
    assert agent.usage.calls == 3 and agent.usage.cache_read_tokens == 240


def test_one_tool_list_serves_the_whole_turn_so_each_call_reads_the_one_before_from_the_prompt_cache():
    client = FakeAnthropic([
        [("buy", {"offer": "espresso", "qty": 1})],
        [("buy", {"offer": "latte", "qty": 1})],
        [("end_turn", {})],
    ])
    rules = "House rules: pay at the counter, one queue, no refunds. " * 60  # a prompt long enough to cache
    env = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1})
    env.run(participants.anthropic(client, "claude-x", system=rules), rounds=1)
    first, second, third = client.requests
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert first["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert third["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}  # it follows the latest
    assert sum("cache_control" in json.dumps(m) for m in third["messages"]) == 1
    # Buying made `review` legal: it joins the list (the one change a turn's tools can make); the rest stays as it was.
    names = [[tool["name"] for tool in request["tools"]] for request in client.requests]
    assert "review" not in names[0] and names[1] == names[2] == names[0] + ["review"]
    assert first["tools"] == second["tools"][:-1]


def _cache_marks(request):
    """Where a request places prompt-cache breakpoints: on the system prompt, and on how many messages."""
    return ("cache_control" in request["system"][0], sum("cache_control" in json.dumps(m) for m in request["messages"]))


def test_a_turn_that_ends_on_one_action_writes_no_cache_a_later_call_cannot_read():
    rules = "House rules: bids are final, the highest wins, ties go to the earliest. " * 60
    auction = {**AUCTION, "clock": {"rounds": 3}}
    client = FakeAnthropic([[("bid", {"amount": 10})]] * 3)
    fg_env.run(auction, {"ann": participants.anthropic(client, "claude-x", system=rules), "bo": "idle", "cy": "idle"},
               seed=1)
    # Each turn is one call (bid ends it): no breakpoint on the conversation. The system prompt is cached from the
    # second turn on, once the agent's turn opens with the same tools and brief as its previous one, which it reads.
    assert [_cache_marks(request) for request in client.requests] == [(False, 0), (True, 0), (True, 0)]

    varying = copy.deepcopy(auction)
    varying["actions"]["bid"]["params"]["amount"]["max"] = "$actor.budget - $round"
    client = FakeAnthropic([[("bid", {"amount": 10})]] * 3)
    fg_env.run(varying, {"ann": participants.anthropic(client, "claude-x", system=rules), "bo": "idle", "cy": "idle"},
               seed=1)
    assert [_cache_marks(request) for request in client.requests] == [(False, 0)] * 3  # the tools change every turn


def test_a_tool_that_stops_being_legal_stays_offered_and_the_result_says_it_is_not_available_now():
    client = FakeAnthropic([
        [("buy", {"offer": "espresso", "qty": 1})],
        [("review", {"offer": "espresso", "stars": 5, "text": "Good."})],
        [("review", {"offer": "espresso", "stars": 4, "text": "Again."})],
        [("end_turn", {})],
    ])
    env = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1})
    env.run(participants.anthropic(client, "claude-x"), rounds=1)
    after_review = client.requests[2]["messages"][-1]["content"][0]
    assert after_review["content"].endswith("(Not available now: review.)")
    assert "review" in [tool["name"] for tool in client.requests[2]["tools"]]
    refused = client.requests[3]["messages"][-1]["content"][0]  # the engine still refuses it, saying why
    assert refused["is_error"] and "1 time(s) per round" in refused["content"]


class FakeOpenAI:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = NS(completions=self)

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))
        calls = self.script.pop(0) if self.script else []
        tool_calls = [NS(id=f"c{i}", function=NS(name=name, arguments=args)) for i, (name, args) in enumerate(calls)]
        message = NS(content="", tool_calls=tool_calls or None)
        return NS(choices=[NS(message=message)], usage=NS(prompt_tokens=50, completion_tokens=5))


def test_openai_participant_handles_bad_json_and_bids():
    client = FakeOpenAI([
        [("bid", "{not json")],
        [("bid", json.dumps({"amount": 30}))],
    ])
    agent = participants.openai(client, "gpt-x")
    result = fg_env.run(AUCTION, {"ann": agent, "bo": "idle", "cy": "idle"}, seed=1)
    assert result.ok, result.summary()
    assert result.outputs == {"winner": "Ann", "price": 30}
    tool_messages = [m for m in client.requests[1]["messages"] if m["role"] == "tool"]
    assert "not valid JSON" in tool_messages[0]["content"]
    assert "tool_calls" not in client.requests[1]["messages"][-1] or client.requests[1]["messages"][-1]["tool_calls"]
    assert client.requests[0]["tools"][0]["function"]["name"] == "bid"


class Flaky(Exception):
    def __init__(self, status_code, retry_after=None):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = NS(headers={"retry-after": retry_after} if retry_after is not None else {})


class FailingAnthropic(FakeAnthropic):
    """Raises the queued errors before answering from the script."""

    def __init__(self, script, errors):
        super().__init__(script)
        self.errors = list(errors)

    def create(self, **request):
        if self.errors:
            self.requests.append(None)
            raise self.errors.pop(0)
        return super().create(**request)


def test_a_retry_never_waits_past_the_runs_seconds_budget(monkeypatch):
    """A provider asking to wait 15 s may not hold a run with a 2 s budget: each wait is cut to what is left of it."""
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    client = FailingAnthropic([[("end_turn", {})]], [Flaky(429, retry_after="15")] * 4)
    agent = participants.anthropic(client, "claude-sonnet-5", retries=3)
    fg_env.load(SHOP, seed=1, inputs={"shoppers": 1}).run(agent, rounds=1, budget={"seconds": 2})
    assert sleeps and all(wait <= 2 for wait in sleeps)


def test_transient_provider_errors_are_retried_with_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", sleeps.append)
    client = FailingAnthropic([[("buy", {"offer": "espresso", "qty": 1})], [("end_turn", {})]],
                              [Flaky(429, retry_after="3"), Flaky(529)])
    agent = participants.anthropic(client, "claude-sonnet-5")
    result = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1}).run(agent, rounds=1)
    assert result.status != "failed", result.error
    assert sleeps[0] == 3.0 and 1.0 <= sleeps[1] <= 3.0  # retry-after, else 2 s with jitter
    assert result.stats["llm_retries"] == 2 and agent.usage.retries == 2
    assert result.stats["llm_calls"] == 2 and result.stats["input_tokens"] == 200
    assert result.stats["cache_read_tokens"] == 160


def test_a_model_that_only_talks_is_nudged_once():
    client = FakeAnthropic([[], [("buy", {"offer": "espresso", "qty": 1})], [("end_turn", {})]])
    agent = participants.anthropic(client, "claude-sonnet-5")
    env = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1})
    env.run(agent, rounds=1)
    assert client.requests[1]["messages"][-1]["content"] == (
        "Act only by calling your tools (buy, end_turn). When you have nothing more to do, call end_turn.")
    assert env.world.props["revenue"] == 3

    silent = FakeAnthropic([])
    env = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1})
    env.run(participants.anthropic(silent, "claude-sonnet-5"), rounds=1)
    assert len(silent.requests) == 2  # one nudge, then the turn ends


def test_usage_survives_snapshots_and_resumes():
    client = FakeAnthropic([[("end_turn", {})]] * 4)
    agent = participants.anthropic(client, "claude-sonnet-5")
    env = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1})
    env.run(agent, rounds=1)
    restored = fg_env.Env.restore(env.contract, env.snapshot())
    assert restored.state.stats.llm_calls == 1 and restored.state.stats.input_tokens == 100


def test_a_provider_and_model_name_an_llm_participant_on_the_official_client(tmp_path, monkeypatch, capsys):
    from types import ModuleType

    from fg_env.__main__ import main

    made = []
    sdk = ModuleType("anthropic")
    buy = [("buy", {"offer": "espresso", "qty": 1}), ("end_turn", {})]
    sdk.Anthropic = lambda: made.append(FakeAnthropic([buy] * 40)) or made[-1]
    monkeypatch.setitem(__import__("sys").modules, "anthropic", sdk)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    path = tmp_path / "shop.json"
    path.write_text(json.dumps(SHOP))
    assert main(["run", str(path), "--seed", "1", "--rounds", "1", "--agent", "shopper=anthropic:claude-sonnet-5"]) == 0
    assert made and made[-1].requests[0]["model"] == "claude-sonnet-5"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert main(["run", str(path), "--agent", "shopper=anthropic:claude-sonnet-5"]) == 1
    assert "set ANTHROPIC_API_KEY in the environment" in capsys.readouterr().err
    monkeypatch.setitem(__import__("sys").modules, "openai", None)  # not installed
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert main(["run", str(path), "--agent", "openai:gpt-x"]) == 1
    assert "pip install openai" in capsys.readouterr().err
    assert main(["run", str(path), "--agent", "anthropic:"]) == 1
    assert "'anthropic:<model>'" in capsys.readouterr().err


def test_retries_stop_at_the_turn_deadline_and_no_call_is_made_after_the_run_returns():
    client = FailingAnthropic([], [Flaky(429, retry_after="0.5")] * 50)
    result = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1}).run(
        participants.anthropic(client, "m"), rounds=1, time_limit=0.3)
    made = len(client.requests)
    time.sleep(1.2)  # a retry would have come after the 0.5 s wait; the turn ended at 0.3 s
    # Under load the first call may not even start within 0.3 s; what matters is that none comes after the run returns.
    assert len(client.requests) == made <= 1 and result.stats["timeouts"] == 1


class TooLong(Exception):
    status_code = 400

    def __init__(self):
        super().__init__("prompt is too long: 212000 tokens > 200000 maximum")
        self.response = NS(headers={})


def test_a_prompt_too_long_for_the_model_forfeits_that_turn_not_the_run():
    client = FailingAnthropic([[("buy", {"offer": "espresso", "qty": 1})], [("end_turn", {})]], [TooLong()])
    result = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1}).run(participants.anthropic(client, "m"), rounds=2)
    assert result.status != "failed", result.error
    assert result.stats["forfeits"] == 1 and result.stats["actions"] == 1


def _plain(value):
    """A fake client's response object as the plain dicts some proxies return."""
    if isinstance(value, NS):
        return {key: _plain(item) for key, item in vars(value).items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def test_participants_read_plain_dict_responses_as_they_read_objects():
    """Responses, blocks and tool calls are read as hosts and the author read them (audit 12 agentif M1)."""
    anthropic = FakeAnthropic([[("buy", {"offer": "espresso", "qty": 2})], [("end_turn", {})]])
    plain_anthropic = NS(messages=NS(create=lambda **request: _plain(anthropic.create(**request))))
    agent = participants.anthropic(plain_anthropic, "claude-x")
    env = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1})
    env.run(agent, rounds=1)
    assert env.world.props["revenue"] == 6 and agent.usage.calls == 2 and agent.usage.input_tokens == 200
    openai = FakeOpenAI([[("bid", json.dumps({"amount": 30}))]])
    plain_openai = NS(chat=NS(completions=NS(create=lambda **request: _plain(openai.create(**request)))))
    agent = participants.openai(plain_openai, "gpt-x")
    result = fg_env.run(AUCTION, {"ann": agent, "bo": "idle", "cy": "idle"}, seed=1)
    assert result.ok and result.outputs == {"winner": "Ann", "price": 30}, result.summary()
    assert agent.usage.input_tokens == 50 * agent.usage.calls


class _NoIds(FakeAnthropic):
    """A proxy that drops the ids of the model's tool calls."""

    def create(self, **request):
        response = super().create(**request)
        for block in response.content:
            if block.type == "tool_use":
                block.id = None
        return response


def test_a_tool_call_without_an_id_is_given_one_its_result_answers():
    """(audit 14 agentif LOW-7)"""
    client = _NoIds([[("buy", {"offer": "espresso", "qty": 1})], [("end_turn", {})]])
    result = fg_env.load(SHOP, seed=1, inputs={"shoppers": 1}).run(participants.anthropic(client, "m"), rounds=1)
    assert result.status != "failed", result.error
    answered = [block for message in client.requests[-1]["messages"] if isinstance(message["content"], list)
                for block in message["content"] if isinstance(block, dict) and block.get("type") == "tool_result"]
    assert answered and all(block["tool_use_id"] for block in answered)
