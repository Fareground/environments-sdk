"""Arguments a participant or model writes as text too deeply nested to parse are an invalid call, never a crash of the
run or of an authoring session."""
from test_author import WORKING, FakeOpenAI, tool_replies, write

import fg_env

DEEP = "[" * 10_000


def test_deeply_nested_argument_text_is_an_invalid_call_not_a_crashed_run():
    told = []

    def agent(wake):
        told.append(wake.call("pass", DEEP).text)

    result = fg_env.load("examples/contracts/games/kuhn_poker.json", seed=1).run({"*": agent})
    assert result.status != "failed", result.error
    assert "too deep to read" in told[0]


def test_deeply_nested_tool_arguments_do_not_crash_an_authoring_session():
    unreadable = [{"name": "write_contract", "raw": DEEP}]
    client = FakeOpenAI([write(WORKING)], unreadable, [], [])
    result = fg_env.author("A game.", "openai:m", client=client)
    assert result.ok  # the working revision is kept; the unreadable call was answered, not raised
    assert any("too deep to read" in reply for reply in tool_replies(client))


def test_a_provider_that_reports_no_usage_still_spends_the_token_budget():
    from types import SimpleNamespace

    from fg_env import participants

    class NoUsage:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            call = SimpleNamespace(id=f"c{self.calls}", function=SimpleNamespace(name="end_turn", arguments="{}"))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]),
                                                            finish_reason="stop")], usage=None)

    client = NoUsage()
    result = fg_env.load("examples/contracts/lemonade_stand.json", seed=1).run(
        participants.openai(client, "m"), budget={"tokens": 1000})
    assert result.budget["exhausted"] == "tokens" and client.calls < 20
    assert any(d["code"] == "usage_unreported" for d in result.diagnostics)
