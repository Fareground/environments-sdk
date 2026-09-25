"""Every odd shape a provider or proxy gives a reply is read the same way by all three readers of replies — the LLM
participants, the hosts' adapters and the author loop — and none of them raises on it (audit 13 agentif A2, B1).

The shapes: objects or plain dicts; content as text, nothing, a list of text parts (dicts or objects) or one part; a
tool call whose name is missing or not text; arguments as JSON text, an object, a list, nothing or broken text; an
Anthropic tool input as an object, JSON text, broken text or nothing. A participant's run plays on (the engine refuses
what it cannot read), a host answers or raises the host's own error (``HostError``: the run marks the request
unanswered), and ``fg_env.author`` returns its result.
"""
import itertools
import json
import os
from types import SimpleNamespace as NS

import pytest

import fg_env
from fg_env import participants
from fg_env.host import adapters
from fg_env.host.protocols import HostError

CONTENTS = [None, "", "Thinking.", [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
            [NS(type="text", text="a")], {"type": "text", "text": "one part"}, [{"type": "image_url"}], 7]
NAMES = ["buy", None, 3, "", "no_such_tool"]
ARGUMENTS = ['{"offer": "espresso", "qty": 1}', {"offer": "espresso", "qty": 1}, [1, 2], None, "{not json", 5]
INPUTS = [{"offer": "espresso", "qty": 1}, '{"offer": "espresso", "qty": 1}', "{broken", None, [1]]
#: Every shape, and a sample of their combinations (all of them in the slow tier).
OPENAI = [(content, name, arguments) for content, name, arguments in itertools.product(CONTENTS, NAMES, ARGUMENTS)]
ANTHROPIC = [(content, name, given) for content, name, given in itertools.product(CONTENTS, NAMES, INPUTS)]


def _openai_reply(content, name, arguments, as_dict):
    call = {"id": "c1", "type": "function", "function": {"name": name, "arguments": arguments}}
    message = {"role": "assistant", "content": content, "tool_calls": [call] if name != "" else None}
    reply = {"choices": [{"message": message, "finish_reason": "tool_calls"}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 2}}
    return reply if as_dict else json.loads(json.dumps(reply, default=str), object_hook=lambda d: NS(**d))


def _anthropic_reply(content, name, given, as_dict):
    blocks = [content] if content is not None and not isinstance(content, list) else list(content or [])
    blocks = [vars(block) if isinstance(block, NS) else block for block in blocks]
    if name != "":
        blocks.append({"type": "tool_use", "id": "t1", "name": name, "input": given})
    reply = {"content": blocks, "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 2}}
    return reply if as_dict else json.loads(json.dumps(reply, default=str), object_hook=lambda d: NS(**d))


class _Replies:
    """A client of either provider answering every request with ``reply``, then (for a participant's next step) a
    plain text reply."""

    def __init__(self, reply, done):
        self.reply, self.done, self.calls = reply, done, 0
        self.chat = NS(completions=self)
        self.messages = self

    def create(self, **request):
        self.calls += 1
        return self.reply if self.calls == 1 else self.done


SHOP = {"name": "Shop", "clock": {"rounds": 1},
        "types": {"p": {"agent": True, "props": {"cash": 10, "cups": 0}}}, "entities": {"a": {"type": "p"}},
        "actions": {"buy": {"by": "p", "params": {"offer": {"type": "enum", "values": ["espresso", "latte"]},
                                                  "qty": {"type": "int", "min": 1, "max": 3}},
                            "do": ["$actor.cups += $params.qty"]}}}
_OPENAI_DONE = {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
_ANTHROPIC_DONE = {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn",
                   "usage": {"input_tokens": 1, "output_tokens": 1}}


def _cases(cases, step):
    """Every ``step``-th case, or every one in the slow tier."""
    return cases if os.environ.get("FG_ENV_SLOW") else cases[::step]


@pytest.mark.parametrize("as_dict", [True, False])
@pytest.mark.parametrize("shape", _cases(OPENAI, 7), ids=str)
def test_an_openai_participant_plays_on_whatever_the_reply_holds(shape, as_dict):
    client = _Replies(_openai_reply(*shape, as_dict), _OPENAI_DONE)
    result = fg_env.run(SHOP, {"a": participants.openai(client, "m", max_steps=2)}, seed=1)
    assert result.status == "completed", result.error


@pytest.mark.parametrize("as_dict", [True, False])
@pytest.mark.parametrize("shape", _cases(ANTHROPIC, 7), ids=str)
def test_an_anthropic_participant_plays_on_whatever_the_reply_holds(shape, as_dict):
    client = _Replies(_anthropic_reply(*shape, as_dict), _ANTHROPIC_DONE)
    result = fg_env.run(SHOP, {"a": participants.anthropic(client, "m", max_steps=2)}, seed=1)
    assert result.status == "completed", result.error


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("content", CONTENTS, ids=str)
def test_a_host_answers_or_raises_its_own_error_whatever_the_reply_holds(provider, content):
    for as_dict in (True, False):
        text = '{"scores": {"quality": 7}, "rationale": "Clear."}'
        shaped = [{"type": "text", "text": text}] if content == [{"type": "image_url"}] else content
        reply = (_openai_reply(shaped, "", None, as_dict) if provider == "openai"
                 else _anthropic_reply(shaped, "", None, as_dict))
        host = getattr(adapters, provider)(_Replies(reply, reply), "m", retries=0)
        try:
            host.judge({"text": "x", "criteria": []})
        except HostError:
            pass  # no JSON in it: the run marks the request unanswered (host_unusable) and goes on


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("name, arguments", _cases([(name, arguments) for name in NAMES for arguments in ARGUMENTS],
                                                   3), ids=str)
def test_the_author_returns_whatever_a_tool_call_holds(provider, name, arguments):
    tool, given = ("write_contract", {"contract": SHOP}) if name == "buy" else (name, arguments)
    if provider == "openai":
        reply = _openai_reply(None, tool, given, True)
    else:
        reply = _anthropic_reply(None, tool, given, True)
    lines = []
    result = fg_env.author("A coffee shop", f"{provider}:m", client=_Replies(reply, reply), budget={"calls": 2},
                           progress=lines.append)
    assert result.stop is not None
