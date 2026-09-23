"""fg_env.author: a brief → a working contract, with scripted model clients (no network)."""
import json
from types import SimpleNamespace

import pytest

import fg_env
from fg_env.__main__ import main

WORKING = fg_env.new("game")
BROKEN = {**WORKING, "bogus": 1}


def call(name, **args):
    return {"name": name, "args": args}


def write(contract):
    return call("write_contract", contract=contract if isinstance(contract, str) else json.dumps(contract))


class FakeOpenAI:
    """An ``openai.OpenAI()`` look-alike replying from a script: each turn is a list of tool calls, [] = done."""

    def __init__(self, *turns, tokens=100):
        self.turns, self.tokens, self.sent = list(turns), tokens, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, tools):
        self.sent.append([dict(m) for m in messages])
        calls = self.turns.pop(0) if self.turns else []
        tool_calls = [SimpleNamespace(id=f"c{len(self.sent)}-{n}", function=SimpleNamespace(
            name=c["name"], arguments=json.dumps(c["args"]))) for n, c in enumerate(calls)]
        message = SimpleNamespace(content="" if calls else "Done.", tool_calls=tool_calls or None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               usage=SimpleNamespace(prompt_tokens=self.tokens, completion_tokens=10))


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

    assert not result.ok and result.stop == "done" and result.contract == BROKEN
    assert "'bogus' is not a field here" in result.problem and result.usage["calls"] == 4  # sent back twice
    assert result.summary().startswith("NOT WORKING") and f"fg-env check {out}" in result.summary()


def test_a_provider_error_stops_and_keeps_what_works():
    client = FakeOpenAI([write(WORKING)])
    replies = client.create

    def failing(**kwargs):
        if client.sent:
            raise ConnectionError("provider down")
        return replies(**kwargs)

    client.chat.completions.create = failing

    result = fg_env.author("A game.", "openai:m", client=client)

    assert result.stop == "error: ConnectionError: provider down" and result.ok


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
    assert sent[1]["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "t1",
                                                     "content": "Saved (1 lines)."}
    assert sent[1]["system"][0]["text"] == fg_env.guide("authoring")


@pytest.mark.parametrize("model, message", [
    ("claude-x", "use 'anthropic:<model>' or 'openai:<model>'"),
    ("openai:", "use 'anthropic:<model>' or 'openai:<model>'"),
])
def test_bad_model_names_say_how_to_fix_them(model, message):
    with pytest.raises(ValueError, match=message):
        fg_env.author("A game.", model, client=FakeOpenAI())


def test_bad_budget_says_how_to_fix_it():
    with pytest.raises(ValueError, match="use tokens and calls"):
        fg_env.author("A game.", "openai:m", client=FakeOpenAI(), budget={"seconds": 5})


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
    monkeypatch.setattr("fg_env.authoring.official_client", lambda provider, model: client)
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
    assert first == ["No contract saved yet.", "Saved (1 lines)."]
    assert "TOOLS:" in second[0] and second[1] == fg_env.guide("effects")[:12000]
    assert second[2].startswith("Bad tool call") and second[3].startswith("Bad tool call")
