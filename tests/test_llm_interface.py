"""What an LLM participant and an LLM host send and are told: budgets that keep turns parallel, previews that never
call a model, host failures that say how to fix them, and tool text without noise (fake clients, no network)."""
import copy
import json
import threading
import time
from types import SimpleNamespace as NS

from test_host_tape import PITCH, _Anthropic, _message, pitcher
from test_llm_failures import EmptyThenBidding
from test_llm_participants import FakeAnthropic, FakeOpenAI
from test_runtime import AUCTION, SHOP

import fg_env
from fg_env import host, participants


class MeetingBidders:
    """An Anthropic client counting how many calls are under way at once. Until three have been, each call waits for
    the others to arrive (never on a clock), so parallel calls always meet and serial ones never do."""

    def __init__(self):
        self.arrived = threading.Condition()
        self.active = self.most = 0
        self.messages = self

    def create(self, **request):
        with self.arrived:
            self.active += 1
            self.most = max(self.most, self.active)
            self.arrived.notify_all()
            self.arrived.wait_for(lambda: self.most >= 3, timeout=10)
            self.active -= 1
        usage = NS(input_tokens=100, output_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return NS(content=[NS(type="tool_use", id="c1", name="bid", input={"amount": 10})], usage=usage)


def test_a_token_budget_far_from_its_limit_keeps_parallel_turns_parallel():
    client = MeetingBidders()
    bidders = {name: participants.anthropic(client, "m") for name in ("ann", "bo", "cy")}
    result = fg_env.load(AUCTION, seed=1).run(bidders, budget={"tokens": 10**9})
    assert result.ok, result.summary()
    assert client.most == 3  # each first call holds the size of its prompt, not all that is left


def test_a_preview_plays_earlier_turns_without_calling_a_model(monkeypatch):
    client = FakeAnthropic([[("buy", {"offer": "espresso", "qty": 1}), ("end_turn", {})]] * 20)
    monkeypatch.setattr("fg_env.participants.llm.official_client", lambda provider, model: client)
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


def test_a_host_still_failing_after_its_retries_says_so_without_asking_again_and_the_run_goes_on(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)

    class Overloaded(Exception):
        status_code = 529

    client = _Anthropic([Overloaded("busy")] * 5)
    env = host.load(PITCH, hosts={"judge": host.adapters.anthropic(client, "claude-x", retries=1)}, seed=1)
    result = host.run(env, pitcher)
    assert result.status == "completed" and len(client.requests) == 4  # a try and a retry a pitch, no correction
    assert "host_unusable" in result.degraded
    assert any("still failed after 1 retry with Overloaded (HTTP 529): busy" in d["message"]
               for d in result.diagnostics if d["code"] == "host_unusable")


def test_host_retries_never_wait_past_the_deadline_of_the_turn_that_asked():
    class RateLimited(Exception):
        status_code = 429
        response = NS(headers={"retry-after": "30"})

    client = _Anthropic([RateLimited("slow down")] * 3)
    env = host.load(PITCH, hosts={"judge": host.adapters.anthropic(client, "claude-x")}, seed=1)
    started = time.monotonic()
    result = env.run(pitcher, rounds=1, time_limit=2)
    assert time.monotonic() - started < 5 and len(client.requests) == 1
    assert result.status != "failed" and any("The turn's time ran out before another try." in d["message"]
                                             for d in result.diagnostics)


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


def _judged(models=None, **panel):
    answer = _message(json.dumps({"scores": {"quality": 7}, "rationale": "fine"}))
    client = _Anthropic([answer] * 4)
    contract = {**PITCH, "mechanisms": {"panel": {**PITCH["mechanisms"]["panel"], **panel}}}
    judge = host.adapters.anthropic(client, "claude-host", models=models)
    host.run(host.load(contract, hosts={"judge": judge}, seed=1), pitcher)
    return {request["model"] for request in client.requests}


def test_the_hosts_own_model_answers_whatever_model_the_contract_names():
    assert _judged(model="claude-opus-9-most-expensive") == {"claude-host"}


def test_a_contract_model_hint_picks_a_model_only_through_the_hosts_own_map():
    assert _judged({"strong": "claude-big"}, model="strong") == {"claude-big"}
    assert _judged({"strong": "claude-big"}, model="cheap") == {"claude-host"}


def test_check_flags_a_raw_model_id_in_a_host_mechanism():
    contract = {**PITCH, "mechanisms": {"panel": {**PITCH["mechanisms"]["panel"], "model": "gpt-4o"}}}
    [found] = [i for i in fg_env.check(contract, rounds=0) if i.path == "mechanisms.panel.model"]
    assert found.severity == "warning" and "'gpt-4o' reads as a provider's model id" in found.message
    named = {**PITCH, "mechanisms": {"panel": {**PITCH["mechanisms"]["panel"], "model": "strong"}}}
    assert not [i for i in fg_env.check(named, rounds=0) if i.path == "mechanisms.panel.model"]


def test_each_provider_request_times_out_with_the_turn_that_made_it():
    timed = FakeAnthropic([[("buy", {"offer": "espresso", "qty": 1}), ("end_turn", {})]])
    fg_env.load(SHOP, seed=1, inputs={"shoppers": 1}).run(participants.anthropic(timed, "m"), rounds=1,
                                                           time_limit=30)
    assert 0 < timed.requests[0]["timeout"] <= 30
    free = FakeAnthropic([[("buy", {"offer": "espresso", "qty": 1}), ("end_turn", {})]])
    fg_env.load(SHOP, seed=1, inputs={"shoppers": 1}).run(participants.anthropic(free, "m"), rounds=1)
    assert free.requests[0]["timeout"] == 600


def test_a_host_request_times_out_with_the_turn_that_asked():
    client = _Anthropic([_message(json.dumps({"scores": {"quality": 7}, "rationale": "fine"}))] * 2)
    env = host.load(PITCH, hosts={"judge": host.adapters.anthropic(client, "claude-host")}, seed=1)
    host.run(env, pitcher, time_limit=30)
    assert all(0 < request["timeout"] <= 30 for request in client.requests)


def test_a_list_of_entities_with_too_few_choices_is_not_offered_and_no_enum_is_ever_empty():
    """A list needing an entity that nobody may choose could never succeed, and `enum: []` is a schema a provider may
    refuse (audit 9 LLM M4)."""
    MINE = {"type": "entity", "of": "item", "where": "$it.holder == $actor.id"}  # noqa: N806
    contract = {"name": "Edge", "clock": {"rounds": 1},
                "types": {"p": {"agent": True}, "item": {"props": {"holder": ""}}},
                "entities": {"a": {"type": "p"}, "i1": {"type": "item", "props": {"holder": "zzz"}}},
                "actions": {"many": {"by": "p", "do": [], "params": {"its": {"type": "list", "items": MINE,
                                                                             "min_items": 1}}},
                            "some": {"by": "p", "do": [], "params": {"its": {"type": "list", "items": MINE}}}}}
    tools = {tool["name"]: tool for tool in fg_env.load(contract, seed=1).preview("a")["tools"]}
    assert "many" not in tools
    assert "enum" not in tools["some"]["input_schema"]["properties"]["its"]["items"]
