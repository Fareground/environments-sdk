"""A model call whose reply reports no token usage (or not as whole numbers) still costs something: participants,
hosts and the authoring loop all count the size of what was sent instead and say the count is an estimate, so a
token budget binds (audit 9 LLM M3, author M5, LOWs)."""
import json
from types import SimpleNamespace

from test_author import WORKING

import fg_env
from fg_env import host, participants


def _text(text, usage=None):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage)


def test_a_participants_usage_that_is_not_whole_numbers_counts_as_unreported():
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="end_turn", arguments="{}"))
    usage = SimpleNamespace(prompt_tokens="12", completion_tokens=3)
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]),
                                                     finish_reason="stop")], usage=usage)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: reply)))
    result = fg_env.load("examples/contracts/lemonade_stand.json", seed=1).run(participants.openai(client, "m"),
                                                                                rounds=1)
    assert result.stats["unreported_usage"] > 0 and result.stats["input_tokens"] > 0
    assert any(d["code"] == "usage_unreported" for d in result.diagnostics)


def test_a_host_whose_replies_carry_no_usage_still_spends_the_token_budget():
    answer = json.dumps({"narration": "Nothing much happens.", "effects": []})
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: _text(answer)))
    gm = host.adapters.anthropic(client, "m")
    env = host.load("examples/contracts/host/tavern_gm.json", hosts={"game_master": gm}, seed=1)
    result = env.run("random", budget={"tokens": 500})
    assert result.budget["exhausted"] == "tokens" and not result.ok
    assert "usage_unreported" in [d["code"] for d in result.diagnostics]


def test_an_authoring_session_estimates_what_an_unreported_call_spent_and_says_so():
    def create(**request):
        blocks = [SimpleNamespace(type="tool_use", id=f"t{len(request['messages'])}", name="check", input={})]
        if len(request["messages"]) == 1:
            blocks = [SimpleNamespace(type="tool_use", id="t1", name="write_contract",
                                      input={"contract": json.dumps(WORKING)})]
        return SimpleNamespace(content=blocks, stop_reason="tool_use")  # no usage at all

    result = fg_env.author("A game.", "anthropic:m", client=SimpleNamespace(messages=SimpleNamespace(create=create)),
                           budget={"tokens": 20_000, "calls": 30})
    assert result.stop == "tokens" and result.usage["unreported"] == result.usage["calls"]
    assert "came back without usage" in result.summary()
