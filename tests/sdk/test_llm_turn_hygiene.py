"""What real models trip over in a turn: truncated replies, calls after the turn ended, the call budget, reads."""
import json
from types import SimpleNamespace as NS

import fg_env
from fg_env import participants

DUEL = {
    "name": "Duel",
    "clock": {"rounds": 1},
    "world": {"idle": 0},
    "types": {"player": {"agent": True, "props": {"score": 0}}},
    "entities": {"ann": {"type": "player", "name": "Ann"}, "ben": {"type": "player", "name": "Ben"}},
    "actions": {
        "move": {"by": "player", "params": {"to": {"type": "int", "min": 1, "max": 3}},
                 "do": ["$actor.score += $params.to"], "terminal": True},
    },
    "stages": [{"name": "play", "must_act": True, "on_idle": ["$world.idle += 1"]}],
    "views": {"table": {"for": "player", "title": "Players", "of": "player", "show": "{$it}: {score}"}},
}


class FakeOpenAI:
    """Scripted replies: a list of tool calls, or ("length", calls) for a reply cut off at the output limit."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = NS(completions=self)

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))
        step = self.script.pop(0) if self.script else []
        finish, calls = step if isinstance(step, tuple) else ("tool_calls" if step else "stop", step)
        tool_calls = [NS(id=f"c{i}", function=NS(name=name, arguments=json.dumps(args)))
                      for i, (name, args) in enumerate(calls)]
        message = NS(content="", tool_calls=tool_calls or None)
        return NS(choices=[NS(message=message, finish_reason=finish)], usage=NS(prompt_tokens=50, completion_tokens=5))


class FakeAnthropic:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = self

    def create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=str)))
        stop, calls = self.replies.pop(0) if self.replies else ("end_turn", [])
        content = [NS(type="tool_use", id=f"t{i}", name=name, input=args) for i, (name, args) in enumerate(calls)]
        return NS(content=content, stop_reason=stop, usage=NS(input_tokens=10, output_tokens=5))


def test_a_truncated_reply_is_retried_once_and_a_silent_agent_is_reported_not_counted_invalid():
    client = FakeOpenAI([("length", []), ("length", [])])
    env = fg_env.load(DUEL, seed=1, exposures=True)
    result = env.run({"ann": participants.openai(client, "m"), "ben": "random"})
    assert len(client.requests) == 2
    assert client.requests[1]["messages"][-1]["content"].startswith("Your reply was cut off at the output limit")
    assert result.agent_stats["ann"]["truncated"] == 2 and result.stats["truncated"] == 2
    assert result.agent_stats["ann"]["invalid_calls"] == 0 and result.agent_stats["ann"]["calls"] == 0
    wake = next(w for w in result.exposures["wakes"] if w["entity"] == "ann")
    assert wake["usage"]["truncated"] == 2 and wake["calls"] == [] and wake["invalid"] == 0
    assert any(event["text"] == "Ann did not act." for event in result.events)
    assert env.world.props["idle"] == 1


def test_without_retry_a_truncated_reply_ends_the_turn_and_max_tokens_and_effort_are_passed_on():
    client = FakeOpenAI([("length", [])])
    agent = participants.openai(client, "m", max_tokens=900, reasoning_effort="low", retry_truncated=False)
    fg_env.run(DUEL, {"ann": agent, "ben": "random"}, seed=1)
    assert len(client.requests) == 1
    assert client.requests[0]["max_tokens"] == 900 and client.requests[0]["reasoning_effort"] == "low"
    plain = FakeOpenAI([[("move", {"to": 2})]])
    fg_env.run(DUEL, {"ann": participants.openai(plain, "m"), "ben": "random"}, seed=1)
    assert "max_tokens" not in plain.requests[0] and "reasoning_effort" not in plain.requests[0]


def test_the_anthropic_participant_counts_a_reply_stopped_at_max_tokens():
    client = FakeAnthropic([("max_tokens", []), ("tool_use", [("move", {"to": 3})])])
    result = fg_env.run(DUEL, {"ann": participants.anthropic(client, "m"), "ben": "random"}, seed=1)
    assert result.agent_stats["ann"]["truncated"] == 1 and result.agent_stats["ann"]["actions"] == 1
    retry = client.requests[1]["messages"][-1]["content"]
    assert retry[-1]["text"].startswith("Your reply was cut off")  # no empty assistant turn is sent back


def test_the_nudge_names_the_tools_offered_and_never_asks_for_an_end_turn_that_is_not_offered():
    client = FakeOpenAI([[], [("move", {"to": 1})]])
    fg_env.run(DUEL, {"ann": participants.openai(client, "m"), "ben": "random"}, seed=1)
    nudge = client.requests[1]["messages"][-1]["content"]
    assert nudge == "Act only by calling your tools (move, inspect). You must take an action this turn."


def test_calls_left_in_a_reply_after_the_turn_ended_are_not_made_or_counted():
    client = FakeOpenAI([[("move", {"to": 1}), ("inspect", {"id": "ben"}), ("move", {"to": 3})]])
    result = fg_env.run(DUEL, {"ann": participants.openai(client, "m"), "ben": "random"}, seed=1, exposures=True)
    wake = next(w for w in result.exposures["wakes"] if w["entity"] == "ann")
    assert [call["tool"] for call in wake["calls"]] == ["move"]
    assert result.agent_stats["ann"]["calls"] == 1 and result.agent_stats["ann"]["invalid_calls"] == 0


def test_reads_do_not_spend_the_calls_an_agent_needs_to_act():
    contract = {**DUEL, "stages": [{"name": "play", "max_calls": 2}]}
    outcome = {}

    def reader(wake):
        outcome["update"] = wake.update
        outcome["reads"] = [wake.call("inspect", {"id": "ben"}).ok for _ in range(2)]
        outcome["move"] = wake.call("move", {"to": 2})

    fg_env.run(contract, {"ann": reader, "ben": "idle"}, seed=1)
    assert outcome["reads"] == [True, True] and outcome["move"].ok
    assert "You have 2 tool calls this turn (looking at views and inspecting are free)." in outcome["update"]


def test_a_read_past_the_free_allowance_uses_a_call_and_the_last_calls_are_counted_down():
    contract = {**DUEL, "stages": [{"name": "play", "max_calls": 2}]}
    texts = []

    def reader(wake):
        texts.extend(wake.call("inspect", {"id": "ben"}).text for _ in range(3))
        texts.append(wake.call("inspect", {"id": "ben"}).text)

    fg_env.run(contract, {"ann": reader, "ben": "idle"}, seed=1)
    assert "(Calls left: 1.)" in texts[2] and "your turn is over" in texts[3]


def test_inspect_offers_its_ids_finds_a_name_and_suggests_the_closest_id():
    seen = {}

    def agent(wake):
        tool = next(t for t in wake.tools if t.name == "inspect")
        seen["enum"] = tool.input_schema["properties"]["id"]["enum"]
        seen["by_name"] = wake.call("inspect", {"id": "Ben"}).text
        seen["typo"] = wake.call("inspect", {"id": "bem"}).text
        seen["update"] = wake.update
        wake.call("move", {"to": 1})

    fg_env.run(DUEL, {"ann": agent, "ben": "idle"}, seed=1)
    assert seen["enum"] == ["ann", "ben"]
    assert seen["by_name"].startswith("Ben [ben] (player)")
    assert seen["typo"] == "No entity with that id is available to inspect. Did you mean 'ben'?"
    assert "Ben [ben]: 0" in seen["update"] and "Ann: 0" in seen["update"]  # no handle for yourself


def test_many_ids_are_listed_compactly_and_uninspectable_entities_get_no_handle():
    contract = {**DUEL, "types": {**DUEL["types"], "ghost": {"inspect": False}},
                "population": [{"type": "player", "count": 70, "id": "u{$i}", "name": "user{$i}"}],
                "entities": {**DUEL["entities"], "g": {"type": "ghost", "name": "Ghost"}},
                "views": {"table": {"for": "player", "show": "{$entity('g')} and {$entity('u2')}"}}}
    seen = {}

    def agent(wake):
        tool = next(t for t in wake.tools if t.name == "inspect")
        seen["schema"], seen["description"], seen["update"] = tool.input_schema, tool.description, wake.update
        wake.call("move", {"to": 1})

    fg_env.run(contract, {"ann": agent, "*": "idle"}, seed=1, rounds=1)
    assert "enum" not in seen["schema"]["properties"]["id"]
    assert "Ids: ann, ben, u1–u70." in seen["description"]
    assert "Ghost and user2 [u2]" in seen["update"]


def test_compact_ids_keep_order_and_cut_a_long_listing():
    from fg_env.sdk.reads import compact_ids

    assert compact_ids(["p1", "p2", "p3", "x", "p5", "p6", "a07"]) == "p1–p3, x, p5, p6, a07"
    assert compact_ids([f"k{i}" for i in range(0, 200, 2)]).endswith(" and 40 more")
