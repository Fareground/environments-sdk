"""``fg_env.author``: a plain-language brief in, a working contract out, written by an LLM with the SDK's own tools.

    result = fg_env.author("A corner shop orders stock every week ...", "anthropic:claude-sonnet-4-5", out="shop.json")
    print(result.summary())

The model starts from ``guide("authoring")`` and works with five tools — ``write_contract``, ``check``, ``run``,
``preview`` and ``guide`` — until it says it is done or the budget runs out. Every contract it saves is checked and
run once; the result keeps the latest one that checks clean and runs, so a later revision that breaks it never
replaces it. A model that stops before any saved contract works is sent back, with the problem, a couple of times.

``model`` is ``"anthropic:<model>"`` or ``"openai:<model>"``, on the official client made from ``ANTHROPIC_API_KEY``
or ``OPENAI_API_KEY``. Any OpenAI-compatible server (OpenRouter, a local server) works through ``openai:``: the
client reads ``OPENAI_BASE_URL``, e.g. ``https://openrouter.ai/api/v1`` with the OpenRouter key as ``OPENAI_API_KEY``.
``client`` passes a client of your own instead.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .api import check, load, parse
from .errors import ContractError
from .guides import guide
from .participants import _PROVIDERS, official_client

__all__ = ["author", "AuthorResult"]

#: What a brief may spend unless ``budget`` says otherwise: model tokens (input + output) and model calls.
DEFAULT_BUDGET = {"tokens": 600_000, "calls": 30}
#: Most contract revisions the model may save.
MAX_WRITES = 8
#: Longest tool result sent back to the model.
MAX_RESULT = 12000
#: How often a model that stops before any saved contract works is sent back.
MAX_NUDGES = 2
#: The reply cap on Anthropic, which requires one: the most a non-streaming call may ask for. OpenAI-compatible
#: servers get none, so a model writes as long a contract as it can.
ANTHROPIC_MAX_TOKENS = 20_000

TOOLS = [
    {"name": "write_contract", "description": "Save the environment contract (the whole JSON object, as text). "
     "Replaces the previous version.", "parameters": {"type": "object", "properties": {
         "contract": {"type": "string", "description": "The contract as JSON text."}}, "required": ["contract"]}},
    {"name": "check", "description": "Check the saved contract: every problem with its path and a fix.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "run", "description": "Run the saved contract once and return its summary and outputs.",
     "parameters": {"type": "object", "properties": {
         "seed": {"type": "integer"},
         "participants": {"type": "object", "description": "Optional map of type or entity id to 'random', "
                          "'idle' or 'policy:<name>'. Default: random agents."}}}},
    {"name": "preview", "description": "Exactly what one agent reads on its next turn: brief, update and tools.",
     "parameters": {"type": "object", "properties": {"agent": {"type": "string", "description": "Entity id."}},
                    "required": ["agent"]}},
    {"name": "guide", "description": "Read one part of the SDK guide, e.g. 'actions', 'effects', 'functions.collections'.",
     "parameters": {"type": "object", "properties": {"part": {"type": "string"}}, "required": ["part"]}},
]

INSTRUCTION = ("\n\nBuild this environment. Save it with write_contract and use the other tools as you see fit. "
               "Reply without calling a tool when you are done.")
NUDGE = "No contract is saved yet. Save it with write_contract (keep replies short; put the contract in the tool call)."
NOT_WORKING = "The saved contract does not work yet: {problem}\nFix it and save it again with write_contract."

Message = Dict[str, Any]


@dataclass
class AuthorResult:
    """What :func:`author` built. ``contract`` is the latest saved contract that checks clean and runs (``ok``), else
    the latest one that parsed (``problem`` says what is wrong with it), else None."""

    contract: Optional[Dict[str, Any]]
    ok: bool
    problem: str
    #: Why the loop ended: "done", "tokens", "calls" (the budget), or "error: <the provider's error>".
    stop: str
    #: Every contract the model saved, in order (the text, for one that was not valid JSON).
    writes: List[Any]
    #: The whole conversation, in OpenAI chat format.
    messages: List[Message]
    #: input_tokens, output_tokens, calls, and cost when the provider reports it (OpenRouter does).
    usage: Dict[str, Any]
    seconds: float
    path: Optional[str] = None

    def summary(self) -> str:
        """What it built — name, agent types, actions, stages, outputs — and what to do next."""
        if self.contract is None:
            return f"no contract saved (stopped: {self.stop})"
        name = self.contract.get("name") or "(unnamed)"
        lines = [f"{'built' if self.ok else 'NOT WORKING'}: {name} — {len(self.writes)} revision(s), stopped: {self.stop}"]
        if not self.ok:
            lines.append(f"  problem: {self.problem}")
        agent = "<agent id>"
        try:
            built = parse(self.contract)
        except ContractError:
            built = None
        if built is not None:
            types = [f"{name} (agent)" if spec.agent else name for name, spec in built.types.items()]
            for label, names in (("types", types), ("actions", list(built.actions)),
                                 ("stages", [stage.name for stage in built.stages]), ("outputs", list(built.outputs))):
                lines.append(f"  {label}: {', '.join(names) or '-'}")
            if self.ok:  # population-made agents too: the built world's first agent
                agent = next((e["id"] for e in load(built, seed=1).entities() if built.types[e["type"]].agent), agent)
        if self.path:
            lines.append(f"next: fg-env preview {self.path} {agent} · fg-env run {self.path} --seed 1"
                         + ("" if self.ok else f" · fg-env check {self.path}"))
        return "\n".join(lines)


def author(brief: str, model: str, *, client: Any = None, out: Optional[str] = None,
           budget: Optional[Mapping[str, int]] = None, progress: Optional[Callable[[str], None]] = None) -> AuthorResult:
    """Have ``model`` (``"anthropic:<model>"`` or ``"openai:<model>"``) write an environment for ``brief``; returns an
    :class:`AuthorResult` (``result.contract``, ``result.ok``, ``result.summary()``).

    ``out`` is where the contract is written (nothing is written when None). ``budget`` caps ``tokens`` (input +
    output) and model ``calls``, by default 600,000 and 30. ``client`` replaces the official client made from the
    environment; ``progress`` is called with one line per model call. A provider error does not raise: the loop stops
    (``result.stop`` says why) and keeps what already works."""
    provider, name = _model(model)
    limits = _budget(budget)
    ask = _ANSWERERS[provider](client if client is not None else official_client(provider, name), name)
    bench, started = _Workbench(), time.time()
    messages: List[Message] = [{"role": "system", "content": guide("authoring")},
                               {"role": "user", "content": brief + INSTRUCTION}]
    usage: Dict[str, Any] = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "calls": 0}
    try:
        stop = _converse(ask, messages, bench, usage, limits, progress)
    finally:
        shutil.rmtree(bench.path.parent, ignore_errors=True)
    contract = bench.best if bench.best is not None else bench.latest
    if out and contract is not None:
        Path(out).write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return AuthorResult(contract, bench.best is not None, "" if bench.best is not None else bench.problem, stop,
                        bench.writes, messages, usage, round(time.time() - started, 1),
                        out if out and contract is not None else None)


def _converse(ask: Callable[[List[Message]], Tuple[Message, Dict[str, Any]]], messages: List[Message],
              bench: "_Workbench", usage: Dict[str, Any], limits: Dict[str, int],
              progress: Optional[Callable[[str], None]]) -> str:
    """The tool loop; returns why it stopped."""
    nudges = 0
    while True:
        if usage["calls"] >= limits["calls"]:
            return "calls"
        if usage["input_tokens"] + usage["output_tokens"] >= limits["tokens"]:
            return "tokens"
        try:
            message, used = ask(messages)
        except Exception as exc:  # the provider's error ends the loop; what already works is kept
            return f"error: {type(exc).__name__}: {exc}"[:500]
        for key, value in used.items():
            usage[key] += value
        usage["calls"] += 1
        messages.append(message)
        calls = message.get("tool_calls") or []
        if progress:
            progress(f"call {usage['calls']}: {', '.join(c['function']['name'] for c in calls) or 'done'} "
                     f"({usage['input_tokens']:,}+{usage['output_tokens']:,} tokens)")
        if not calls:
            if bench.best is not None or nudges >= MAX_NUDGES or len(bench.writes) >= MAX_WRITES:
                return "done"
            nudges += 1  # stopped (or was cut off) before any saved contract works: send it back once more
            messages.append({"role": "user", "content": NOT_WORKING.format(problem=bench.problem)
                             if bench.writes else NUDGE})
            continue
        for call in calls:
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            text = bench.call(call["function"]["name"], args if isinstance(args, dict) else {})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})


def _model(model: str) -> Tuple[str, str]:
    provider, _, name = model.partition(":") if isinstance(model, str) else ("", "", "")
    if provider not in _PROVIDERS or not name:
        raise ValueError(f"model {model!r}: use 'anthropic:<model>' or 'openai:<model>' (any OpenAI-compatible "
                         "server, e.g. OpenRouter, through OPENAI_BASE_URL)")
    return provider, name


def _budget(budget: Optional[Mapping[str, int]]) -> Dict[str, int]:
    limits = {**DEFAULT_BUDGET, **(budget or {})}
    for key, value in limits.items():
        if key not in DEFAULT_BUDGET:
            raise ValueError(f"budget has no {key!r}: use tokens and calls, e.g. {DEFAULT_BUDGET}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"budget {key} must be a whole number ≥ 1, got {value!r}")
    return limits


class _Workbench:
    """The author's tools, backed by one contract file in a scratch folder. Every saved contract is checked and run
    once; ``best`` is the latest that works, ``latest`` the latest that parsed and ``problem`` what is wrong with it."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="fg-author-")) / "contract.json"
        self.writes: List[Any] = []
        self.best: Optional[Dict[str, Any]] = None
        self.latest: Optional[Dict[str, Any]] = None
        self.problem = ""

    def call(self, name: str, args: Dict[str, Any]) -> str:
        try:
            return str(getattr(self, "tool_" + name)(**args))[:MAX_RESULT]
        except (TypeError, AttributeError) as exc:
            return f"Bad tool call: {exc}"
        except Exception as exc:  # the SDK's own errors are what the author reads
            return f"{type(exc).__name__}: {exc}"[:MAX_RESULT]

    def tool_write_contract(self, contract: str) -> str:
        if len(self.writes) >= MAX_WRITES:
            return f"Revision limit reached ({MAX_WRITES}); the last saved contract is final."
        try:
            data = json.loads(contract)
        except json.JSONDecodeError as exc:
            self.writes.append(contract)
            self.problem = f"the last write was not valid JSON ({exc})"
            return f"Not valid JSON: {exc}. Nothing saved."
        if not isinstance(data, dict):
            self.writes.append(contract)
            self.problem = "the last write was not a JSON object"
            return "Not a JSON object: a contract is one object {...}. Nothing saved."
        self.writes.append(data)
        self.path.write_text(json.dumps(data, indent=2))
        self.latest, self.problem = data, self._problem()
        if not self.problem:
            self.best = data
        return f"Saved ({len(contract.splitlines())} lines)."

    def _problem(self) -> str:
        """What stops the saved contract from working: its first check error, or the error one run raises."""
        try:
            errors = [str(i) for i in check(str(self.path)) if i.severity == "error"]
            if errors:
                return errors[0] + (f" (and {len(errors) - 1} more: call check)" if len(errors) > 1 else "")
            load(str(self.path), seed=1).run(None, budget={"seconds": 60})
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"[:MAX_RESULT]
        return ""

    def tool_check(self) -> str:
        if not self.path.exists():
            return "No contract saved yet."
        return "\n".join(map(str, check(str(self.path)))) or "No issues."

    def tool_run(self, seed: int = 1, participants: Optional[Dict[str, str]] = None) -> str:
        result = load(str(self.path), seed=seed).run(participants, budget={"seconds": 60})
        return result.summary() + "\noutputs: " + json.dumps(result.outputs, default=str)

    def tool_preview(self, agent: str) -> str:
        shown = load(str(self.path), seed=1).preview(agent)
        tools = "\n".join(f"- {t['name']}: {t['description']} {json.dumps(t['input_schema'])}" for t in shown["tools"])
        return f"BRIEF:\n{shown['brief']}\n\nUPDATE:\n{shown['update']}\n\nTOOLS:\n{tools}"

    def tool_guide(self, part: str) -> str:
        return guide(part)


def _openai(client: Any, model: str) -> Callable[[List[Message]], Tuple[Message, Dict[str, Any]]]:
    tools = [{"type": "function", "function": tool} for tool in TOOLS]

    def ask(messages: List[Message]) -> Tuple[Message, Dict[str, Any]]:
        response = client.chat.completions.create(model=model, messages=messages, tools=tools)
        reply = response.choices[0].message
        calls = [{"id": c.id, "type": "function", "function": {"name": c.function.name,
                                                               "arguments": c.function.arguments}}
                 for c in reply.tool_calls or []]
        message: Message = {"role": "assistant", "content": reply.content or ""}
        if calls:
            message["tool_calls"] = calls
        used = response.usage
        return message, {"input_tokens": getattr(used, "prompt_tokens", 0) or 0,
                         "output_tokens": getattr(used, "completion_tokens", 0) or 0,
                         "cost": float(getattr(used, "cost", 0) or 0)}

    return ask


def _anthropic(client: Any, model: str) -> Callable[[List[Message]], Tuple[Message, Dict[str, Any]]]:
    tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in TOOLS]

    def ask(messages: List[Message]) -> Tuple[Message, Dict[str, Any]]:
        system = [{"type": "text", "text": messages[0]["content"], "cache_control": {"type": "ephemeral"}}]
        response = client.messages.create(model=model, system=system, messages=_to_anthropic(messages[1:]),
                                          tools=tools, max_tokens=ANTHROPIC_MAX_TOKENS)
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [{"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.input)}}
                 for b in response.content if b.type == "tool_use"]
        message: Message = {"role": "assistant", "content": text}
        if calls:
            message["tool_calls"] = calls
        used = response.usage
        return message, {"input_tokens": used.input_tokens + (getattr(used, "cache_read_input_tokens", 0) or 0)
                         + (getattr(used, "cache_creation_input_tokens", 0) or 0),
                         "output_tokens": used.output_tokens}

    return ask


def _to_anthropic(messages: List[Message]) -> List[Message]:
    """The OpenAI-format conversation (after the system message) as Anthropic messages."""
    out: List[Message] = []
    for m in messages:
        if m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant":
            blocks: List[Dict[str, Any]] = [{"type": "text", "text": m["content"]}] if m["content"] else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                        "input": json.loads(c["function"]["arguments"] or "{}")} for c in m.get("tool_calls", [])]
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no reply)"}]})
        else:
            out.append({"role": "user", "content": m["content"]})
    return out


_ANSWERERS = {"openai": _openai, "anthropic": _anthropic}
