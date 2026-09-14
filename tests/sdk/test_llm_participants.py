"""LLM participant loops against fake provider clients (no network)."""
import json
from types import SimpleNamespace as NS

import fg_env
from fg_env import participants

from test_runtime import AUCTION, SHOP


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
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "# Corner shop" in first["system"][0]["text"]
    assert "On the shelf" in first["messages"][0]["content"]
    correction = client.requests[1]["messages"][-1]["content"][0]
    assert correction["is_error"] and "qty must be at most 10" in correction["content"]
    assert client.requests[2]["messages"][-1]["content"][0]["content"] == "You bought 2 × Espresso for $6.00."
    assert env.world.props["revenue"] == 6
    assert agent.usage.calls == 3 and agent.usage.cache_read_tokens == 240


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
    assert client.requests[0]["tools"][0]["function"]["name"] == "bid"
