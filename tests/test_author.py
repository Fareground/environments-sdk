"""fg_env.author: a brief → a working contract, with scripted model clients (no network)."""
import json
from types import SimpleNamespace

import pytest

import fg_env
from fg_env.__main__ import main

WORKING = fg_env.new("duel")
BROKEN = {**WORKING, "bogus": 1}


def call(name, **args):
    return {"name": name, "args": args}


def write(contract):
    return call("write_contract", contract=contract if isinstance(contract, str) else json.dumps(contract))


def edit(*edits):
    return call("edit_contract", edits=[{"path": path, **({} if value is ... else {"value": value})}
                                        for path, value in edits])


class Cut(list):
    """A turn the provider cut off at the output limit: its last call's arguments stop half-way."""


class ProviderError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code, self.response = status, SimpleNamespace(headers={})


#: A turn on which the provider sends a response with no choices (OpenRouter does, now and then).
EMPTY = "empty"


class FakeOpenAI:
    """An ``openai.OpenAI()`` look-alike replying from a script: each turn is a list of tool calls ([] = done), a
    :class:`Cut` list, :data:`EMPTY`, or an exception to raise. ``cached`` of each call's ``tokens`` are cache reads."""

    def __init__(self, *turns, tokens=100, cost=None, cached=None):
        self.turns, self.tokens, self.cost, self.cached, self.sent = list(turns), tokens, cost, cached, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, tools):
        self.sent.append([dict(m) for m in messages])
        calls = self.turns.pop(0) if self.turns else []
        if isinstance(calls, BaseException):
            raise calls
        if calls == EMPTY:
            return SimpleNamespace(choices=[], usage=None)
        arguments = [c.get("raw") or json.dumps(c["args"]) for c in calls]
        if isinstance(calls, Cut) and arguments:
            arguments[-1] = arguments[-1][:len(arguments[-1]) // 2]
        tool_calls = [SimpleNamespace(id=f"c{len(self.sent)}-{n}", function=SimpleNamespace(
            name=c["name"], arguments=text)) for n, (c, text) in enumerate(zip(calls, arguments))]
        message = SimpleNamespace(content="" if calls else "Done.", tool_calls=tool_calls or None)
        usage = SimpleNamespace(prompt_tokens=self.tokens, completion_tokens=10)
        if self.cost is not None:
            usage.cost = self.cost
        if self.cached is not None:
            usage.prompt_tokens_details = SimpleNamespace(cached_tokens=self.cached)
        return SimpleNamespace(choices=[SimpleNamespace(message=message,
                                                        finish_reason="length" if isinstance(calls, Cut) else "stop")],
                               usage=usage)


def tool_replies(client):
    """Every tool result the model read, in order."""
    return [m["content"] for m in client.sent[-1] if m["role"] == "tool"]


#: A contract that checks clean but fails in round 15, after check's smoke rounds.
LATE_CRASH = {"name": "Late crash", "clock": {"rounds": 20},
              "world": {"table": {"type": "map", "default": {"a": 1}}, "x": 0},
              "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
              "actions": {"wait": {"by": "p", "do": ["$world.x += 1"]}}, "stages": [{"name": "s"}],
              "events": [{"at": 15, "do": ["$world.x = $world.table[$text($round)]"]}], "outputs": {"x": "$world.x"}}
#: A contract that works while agents act, and fails when none does.
IDLE_CRASH = {"name": "Idle crash", "clock": {"rounds": 3},
              "world": {"moves": {"type": "map", "default": {}}, "n": 0},
              "types": {"p": {"agent": True}}, "entities": {"a": {"type": "p"}},
              "actions": {"move": {"by": "p", "do": ["$world.moves[$text($round)] = 1"]}}, "stages": [{"name": "s"}],
              "events": [{"at": 3, "do": ["$world.n = $world.moves['1']"]}], "outputs": {"n": "$world.n"}}


def test_happy_path_writes_the_working_contract_and_summarises_it(tmp_path):
    out = tmp_path / "game.json"
    client = FakeOpenAI([write(BROKEN)], [call("check")], [write(WORKING)], [call("run", seed=2)], [])

    result = fg_env.author("A two-player game.", "openai:some-model", client=client, out=str(out))

    assert result.ok and result.stop == "done" and result.contract == WORKING
    assert json.loads(out.read_text()) == WORKING
    assert result.usage["calls"] == 5 and len(result.writes) == 2
    assert "'bogus' is not a field here" in client.sent[2][-1]["content"]  # the check tool answered the model
    summary = result.summary()
    assert summary.startswith("built:") and "actions:" in summary and "outputs:" in summary
    assert f"fg-env run {out} --seed 1" in summary


def test_a_regressing_last_write_never_replaces_the_working_contract(tmp_path):
    out = tmp_path / "env.json"
    client = FakeOpenAI([write(WORKING)], [write(BROKEN)], [])

    result = fg_env.author("A game.", "openai:m", client=client, out=str(out))

    assert result.ok and result.contract == WORKING and result.writes[-1] == BROKEN
    assert json.loads(out.read_text()) == WORKING


def test_budget_stops_the_loop_and_keeps_what_works():
    client = FakeOpenAI([write(WORKING)], [call("check")], [call("check")], [call("check")], tokens=1000)

    by_tokens = fg_env.author("A game.", "openai:m", client=client, budget={"tokens": 2000})
    by_calls = fg_env.author("A game.", "openai:m", client=FakeOpenAI([write(WORKING)], [call("check")]),
                             budget={"calls": 1})

    assert (by_tokens.stop, by_tokens.usage["calls"], by_tokens.ok) == ("tokens", 2, True)
    assert (by_calls.stop, by_calls.usage["calls"], by_calls.contract) == ("calls", 1, WORKING)


def test_a_write_that_is_not_an_object_is_refused():
    client = FakeOpenAI([write("[1, 2]")], [write(WORKING)], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert client.sent[1][-1]["content"].startswith("Not a JSON object")
    assert result.ok and result.writes[0] == "[1, 2]"


def test_invalid_json_is_reported_and_the_model_is_sent_back_until_it_works():
    client = FakeOpenAI([write("{not json")], [], [write(WORKING)], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert client.sent[1][-1]["content"].startswith("Not valid JSON")
    assert "not valid JSON" in client.sent[2][-1]["content"]  # stopping early sent it back with the problem
    assert result.ok and result.writes[0] == "{not json" and result.contract == WORKING


def test_a_model_that_never_gets_it_working_returns_the_latest_contract_and_its_problem(tmp_path):
    out = tmp_path / "env.json"
    result = fg_env.author("A game.", "openai:m", client=FakeOpenAI([write(BROKEN)], [], [], []), out=str(out))

    assert not result.ok and result.stop == "gave_up" and result.contract == BROKEN
    assert "'bogus' is not a field here" in result.problem and result.usage["calls"] == 4  # sent back twice
    # `out` only ever holds a contract that works: this one is written beside it, named for what it is.
    drafted = tmp_path / "env.not-working.json"
    assert not out.exists() and json.loads(drafted.read_text()) == BROKEN and result.path == str(drafted)
    assert result.summary().startswith("NOT WORKING") and f"fg-env check {drafted}" in result.summary()


def test_a_provider_error_that_persists_stops_and_keeps_what_works(monkeypatch):
    waits = []
    monkeypatch.setattr("fg_env.authoring.author.time.sleep", waits.append)
    client = FakeOpenAI([write(WORKING)])
    replies = client.create

    def failing(**kwargs):
        if client.sent:
            raise ConnectionError("provider down")
        return replies(**kwargs)

    client.chat.completions.create = failing

    result = fg_env.author("A game.", "openai:m", client=client)

    assert result.stop == "error: ConnectionError: provider down" and result.ok
    assert waits == [1.0, 2.0, 4.0, 8.0]  # retried with backoff first


def test_anthropic_client_gets_anthropic_messages():
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        blocks = [SimpleNamespace(type="tool_use", id="t1", name="write_contract",
                                  input={"contract": json.dumps(WORKING)})] if len(sent) == 1 else \
            [SimpleNamespace(type="text", text="Done.")]
        return SimpleNamespace(content=blocks, usage=SimpleNamespace(input_tokens=5, output_tokens=5))

    result = fg_env.author("A game.", "anthropic:m", client=SimpleNamespace(messages=SimpleNamespace(create=create)))

    assert result.ok and result.stop == "done"
    assert sent[1]["messages"][1]["content"][0]["type"] == "tool_use"
    assert sent[1]["messages"][2]["content"][0]["content"].startswith("Saved revision 1: it works")
    assert sent[1]["system"][0]["text"] == fg_env.guide("authoring")


@pytest.mark.parametrize("model, message", [
    ("claude-x", "use 'anthropic:<model>' or 'openai:<model>'"),
    ("openai:", "use 'anthropic:<model>' or 'openai:<model>'"),
])
def test_bad_model_names_say_how_to_fix_them(model, message):
    with pytest.raises(ValueError, match=message):
        fg_env.author("A game.", model, client=FakeOpenAI())


def test_bad_budget_says_how_to_fix_it():
    with pytest.raises(ValueError, match="use tokens, calls and seconds"):
        fg_env.author("A game.", "openai:m", client=FakeOpenAI(), budget={"minutes": 5})


def test_cli_refuses_to_overwrite_and_needs_a_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    existing = tmp_path / "env.json"
    existing.write_text("{}")

    assert main(["author", "A game.", "--model", "openai:m", "--out", str(existing)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert main(["author", "A game.", "--model", "openai:m", "--out", str(tmp_path / "new.json")]) == 1
    assert "set OPENAI_API_KEY" in capsys.readouterr().err
    assert main(["author", "A game. " * 100, "--model", "openai:m", "--out", str(tmp_path / "new.json")]) == 1
    assert "set OPENAI_API_KEY" in capsys.readouterr().err  # a long brief is text, not a file name


def test_cli_authors_from_a_brief_file(tmp_path, monkeypatch, capsys):
    brief = tmp_path / "shop.md"
    brief.write_text("A two-player game.")
    client = FakeOpenAI([write(WORKING)], [])
    monkeypatch.setattr("fg_env.authoring.author.official_client", lambda provider, model: client)
    monkeypatch.chdir(tmp_path)

    assert main(["author", str(brief), "--model", "openai:m"]) == 0
    assert json.loads((tmp_path / "shop.json").read_text()) == WORKING
    assert "A two-player game." in client.sent[0][1]["content"]
    output = capsys.readouterr()
    assert output.out.startswith("built:") and "call 1: write_contract" in output.err


def test_preview_guide_and_bad_tool_calls_answer_the_model():
    client = FakeOpenAI([call("check"), write(WORKING)], [call("preview", agent="north"), call("guide", part="effects"),
                                                           call("preview"), call("launch")], [])

    fg_env.author("A game.", "openai:m", client=client)

    first, second = [m["content"] for m in client.sent[1][-2:]], [m["content"] for m in client.sent[2][-4:]]
    assert first[0] == "No contract saved yet." and first[1].startswith("Saved revision 1: it works")
    assert "TOOLS:" in second[0] and second[1] == fg_env.guide("effects")[:12000]
    assert second[2] == "Bad tool call: preview: it needs agent. It takes: agent."
    assert second[3].startswith("Bad tool call: there is no tool 'launch'; the tools are write_contract, edit_contract")


def test_tool_arguments_that_are_not_a_json_object_are_named():
    client = FakeOpenAI([{"name": "guide", "raw": "{part"}, {"name": "check", "raw": "[]"}], [])

    fg_env.author("A game.", "openai:m", client=client)

    assert tool_replies(client)[0].startswith("Bad tool call: guide: its arguments are not valid JSON")
    assert tool_replies(client)[1] == "Bad tool call: check: its arguments must be a JSON object. It takes: nothing."


def test_a_contract_that_fails_after_the_smoke_rounds_is_not_built():
    client = FakeOpenAI([write(LATE_CRASH)], [], [], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert not result.ok and result.stop == "gave_up"
    assert result.problem.startswith("a run with random agents (seed 1) failed in round 15: events[0].do[0]")
    assert tool_replies(client)[0].startswith("Saved revision 1, but it does not work yet: a run with random agents")


def test_a_contract_that_fails_when_agents_do_not_act_is_not_built():
    result = fg_env.author("A game.", "openai:m", client=FakeOpenAI([write(IDLE_CRASH)], [], [], []))

    # check's own pass with agents that never act already reports it, before the author's runs.
    assert not result.ok and "agents that never act" in result.problem and "events[0].do[0]" in result.problem


def test_a_dropped_last_revision_is_named_in_the_reply_and_the_summary():
    client = FakeOpenAI([write(WORKING)], [write(BROKEN)], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert result.ok and result.contract == WORKING and result.working == [1]
    assert tool_replies(client)[1].startswith("Saved revision 2, but it does not work yet: bogus: 'bogus' is not a "
                                              "field")
    assert "kept revision 1 of 2; revision 2 did not work: bogus: 'bogus'" in result.summary()


def test_a_write_cut_off_at_the_output_limit_is_named_and_does_not_use_a_revision(monkeypatch):
    monkeypatch.setattr("fg_env.authoring.workbench.MAX_REVISIONS", 1)
    client = FakeOpenAI(Cut([call("check"), write(WORKING)]), [], [write(WORKING)], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    cut = tool_replies(client)[1]
    assert tool_replies(client)[0] == "No contract saved yet."  # the complete call before the cut one still runs
    assert cut.startswith("Your write_contract call was cut off at the output limit, so nothing was saved")
    assert "edit_contract" in cut
    assert "cut off at the output limit" in client.sent[2][-1]["content"]  # stopping then sent it back with that
    assert result.ok and result.usage["truncated"] == 1 and result.writes[0].startswith('{"contract": "{')
    assert result.messages[2]["tool_calls"][1]["function"]["arguments"] == "{}"  # what the provider is sent back


def test_a_model_that_stops_after_a_cut_off_write_gets_a_plain_summary():
    result = fg_env.author("A game.", "openai:m", client=FakeOpenAI(Cut([write(WORKING)]), [], [], []))

    assert (result.ok, result.stop, result.contract) == (False, "gave_up", None)
    assert "cut off at the output limit" in result.summary()


def test_anthropic_max_tokens_mid_write_is_a_cut_off_write():
    def create(**kwargs):
        blocks = [SimpleNamespace(type="tool_use", id="t1", name="write_contract", input={})]
        return SimpleNamespace(content=blocks, stop_reason="max_tokens",
                               usage=SimpleNamespace(input_tokens=5, output_tokens=20000))

    result = fg_env.author("A game.", "anthropic:m", client=SimpleNamespace(messages=SimpleNamespace(create=create)),
                           budget={"calls": 1})

    assert result.messages[-1]["content"].startswith("Your write_contract call was cut off at the output limit")


def test_edit_contract_changes_parts_of_the_saved_contract():
    client = FakeOpenAI([edit(("name", "x"))], [write(WORKING)],
                        [edit(("name", "Pile"), ("entities.east", {"type": "player"}), ("brief.rules", ...),
                              ("actions.take.do[3]", "$world.stones -= 0"))],
                        [edit(("clock.nope.deeper", 1))], [edit(("actions.take.do[9]", 1))], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    replies = tool_replies(client)
    assert replies[0] == "No contract saved yet: save one with write_contract first."
    assert replies[2].startswith("Saved revision 2: it works")
    assert replies[3] == "edits[0]: clock.nope.deeper: the contract has no clock.nope. Nothing saved."
    assert replies[4].startswith("edits[0]: actions.take.do[9]: actions.take.do has 4 item(s): use an index below 4, "
                                 "or 4 to add one. Nothing saved.")
    kept = result.contract
    assert kept["name"] == "Pile" and "east" in kept["entities"] and "rules" not in kept["brief"]
    assert kept["actions"]["take"]["do"][-1] == "$world.stones -= 0" and WORKING["name"] == "Take the last stone"


def test_the_summary_says_when_the_kept_revision_changed_what_the_environment_is():
    renamed = {**WORKING, "name": "Stones", "outputs": {"score": "1"}}
    renamed["actions"] = {**WORKING["actions"], "take": {**WORKING["actions"]["take"], "description": "Take stones."}}
    client = FakeOpenAI([write(WORKING)], [write(renamed)], [write(renamed)], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert result.working == [1, 2] and result.kept == 2
    assert ("changed since revision 1, the first that worked: name 'Take the last stone' → 'Stones'; "
            "actions ~take; outputs -winner +score") in result.summary()


def test_retryable_provider_errors_are_retried_and_others_stop(monkeypatch):
    waits = []
    monkeypatch.setattr("fg_env.authoring.author.time.sleep", waits.append)

    busy = fg_env.author("A game.", "openai:m", client=FakeOpenAI(ProviderError(429), ProviderError(529),
                                                                  [write(WORKING)], []))
    refused = fg_env.author("A game.", "openai:m", client=FakeOpenAI(ProviderError(401)))

    assert busy.ok and busy.stop == "done" and waits == [1.0, 2.0]
    assert refused.stop == "error: ProviderError: HTTP 401" and len(waits) == 2


def test_anthropic_caches_the_conversation_so_far():
    sent = []

    def create(**kwargs):
        sent.append(kwargs)
        blocks = [SimpleNamespace(type="tool_use", id=f"t{len(sent)}", name="check", input={})]
        return SimpleNamespace(content=blocks, stop_reason="tool_use",
                               usage=SimpleNamespace(input_tokens=5, output_tokens=5))

    result = fg_env.author("A game.", "anthropic:m", client=SimpleNamespace(messages=SimpleNamespace(create=create)),
                           budget={"calls": 2})

    marked = [[b for m in s["messages"] for b in m["content"] if "cache_control" in b] for s in sent]
    assert [len(m) for m in marked] == [1, 1]
    assert sent[1]["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}  # the latest tool result
    assert "cost" not in result.usage  # Anthropic reports no cost


def test_cost_is_reported_only_when_the_provider_reports_it():
    priced = fg_env.author("A game.", "openai:m", client=FakeOpenAI([write(WORKING)], [], cost=0.25))

    assert priced.usage["cost"] == 0.5 and "$0.50" in priced.summary()
    assert "cost" not in fg_env.author("A game.", "openai:m", client=FakeOpenAI([write(WORKING)], [])).usage


def test_invalid_writes_do_not_use_up_the_revisions(monkeypatch):
    monkeypatch.setattr("fg_env.authoring.workbench.MAX_REVISIONS", 2)
    client = FakeOpenAI([write("{no")], [write("{no")], [write("{no")], [write(BROKEN)], [write(WORKING)], [])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert result.ok and result.working == [2] and len(result.writes) == 5


def test_running_out_of_revisions_without_a_working_contract_stops(monkeypatch):
    monkeypatch.setattr("fg_env.authoring.workbench.MAX_REVISIONS", 2)
    client = FakeOpenAI([write(BROKEN)], [write({**BROKEN, "name": "Again"})], [call("check")])

    result = fg_env.author("A game.", "openai:m", client=client)

    assert (result.ok, result.stop, result.usage["calls"]) == (False, "revisions", 2)


def test_cli_caps_model_calls(tmp_path, monkeypatch, capsys):
    client = FakeOpenAI([write(WORKING)], [call("check")], [call("check")])
    monkeypatch.setattr("fg_env.authoring.author.official_client", lambda provider, model: client)

    assert main(["author", "A game.", "--model", "openai:m", "--out", str(tmp_path / "g.json"), "--calls", "2"]) == 0
    assert "stopped: calls" in capsys.readouterr().out and len(client.sent) == 2
