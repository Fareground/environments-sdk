"""The authoring agent: an LLM given only the SDK's authoring guide, a brief, and the SDK's own tools.

The loop records every turn to a transcript (``author(...)`` returns it) so ``bench.py --replay`` can re-score the
contracts it wrote without the network.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import fg_env

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
#: Where the key is read from when OPENROUTER_API_KEY is not set. Only the one line is parsed; nothing is copied.
ENV_FILE = Path("/Users/Sandro/Desktop/Projects/fareground/.env")
#: Most contract revisions per brief, and most model calls (reading guides, checking, running count too).
MAX_WRITES = 8
MAX_CALLS = 30
#: Longest tool result sent back to the model.
MAX_RESULT = 12000

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

NUDGE = "No contract is saved yet. Save it with write_contract (keep replies short; put the contract in the tool call)."
#: How often the author is nudged to save when it stops without having saved anything.
MAX_NUDGES = 2
INSTRUCTION = ("\n\nBuild this environment. Save it with write_contract and use the other tools as you see fit. "
               "Reply without calling a tool when you are done.")


def api_key() -> str:
    """The OpenRouter key from the environment, else from the fareground .env line. Never printed or stored."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key and ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.partition("=")[2].strip().strip("'\"")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set (and not found in the fareground .env)")
    return key


def chat(messages: List[Dict[str, Any]], model: str, key: str) -> Dict[str, Any]:
    body = {"model": model, "messages": messages, "tools": [{"type": "function", "function": t} for t in TOOLS],
            "max_tokens": 32000, "usage": {"include": True}}
    request = urllib.request.Request(OPENROUTER, json.dumps(body).encode(), {
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data = json.loads(response.read())
            if "choices" in data:
                return data
            error = str(data.get("error"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            error = str(exc)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"OpenRouter call failed: {error[:300]}")


class Workbench:
    """The author's tools, backed by one contract file in a scratch folder."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="fg-author-")) / "contract.json"
        self.writes: List[Any] = []
        self.guide_parts: List[str] = []
        #: Characters of guide text the author was sent: its starting page, then every guide call it made.
        self.guide_chars = 0

    def call(self, name: str, args: Dict[str, Any]) -> str:
        try:
            return getattr(self, "tool_" + name)(**args)[:MAX_RESULT]
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
            return f"Not valid JSON: {exc}. Nothing saved."
        self.writes.append(data)
        self.path.write_text(json.dumps(data, indent=2))
        return f"Saved ({len(contract.splitlines())} lines)."

    def tool_check(self) -> str:
        if not self.path.exists():
            return "No contract saved yet."
        issues = fg_env.check(str(self.path))
        return "\n".join(map(str, issues)) or "No issues."

    def tool_run(self, seed: int = 1, participants: Optional[Dict[str, str]] = None) -> str:
        result = fg_env.load(str(self.path), seed=seed).run(participants, budget={"seconds": 60})
        return result.summary() + "\noutputs: " + json.dumps(result.outputs, default=str)

    def tool_preview(self, agent: str) -> str:
        shown = fg_env.load(str(self.path), seed=1).preview(agent)
        tools = "\n".join(f"- {t['name']}: {t['description']} {json.dumps(t['input_schema'])}" for t in shown["tools"])
        return f"BRIEF:\n{shown['brief']}\n\nUPDATE:\n{shown['update']}\n\nTOOLS:\n{tools}"

    def tool_guide(self, part: str) -> str:
        self.guide_parts.append(part)
        text = fg_env.guide(part)
        self.guide_chars += len(text[:MAX_RESULT])
        return text


def author(brief: str, model: str, key: str, token_cap: int, label: str = "") -> Dict[str, Any]:
    """Let the model author the brief; return the transcript (messages, contracts written, usage, time).
    Progress goes to stderr, one line per model call, prefixed with ``label``."""
    bench, started = Workbench(), time.time()
    bench.guide_chars = len(fg_env.guide("authoring"))
    messages: List[Dict[str, Any]] = [{"role": "system", "content": fg_env.guide("authoring")},
                                      {"role": "user", "content": brief + INSTRUCTION}]
    usage = {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "calls": 0}
    stop, nudges = "calls", 0
    for _ in range(MAX_CALLS):
        if usage["input_tokens"] + usage["output_tokens"] >= token_cap:
            stop = "token cap"
            break
        try:
            data = chat(messages, model, key)
        except RuntimeError as exc:
            stop = str(exc)
            break
        used = data.get("usage") or {}
        usage["input_tokens"] += used.get("prompt_tokens", 0)
        usage["output_tokens"] += used.get("completion_tokens", 0)
        usage["cost"] += float(used.get("cost") or 0)
        usage["calls"] += 1
        message = data["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls}
                        if calls else {"role": "assistant", "content": message.get("content") or ""})
        print(f"{label} call {usage['calls']}: {[c['function']['name'] for c in calls] or 'done'} "
              f"({usage['input_tokens']}+{usage['output_tokens']} tokens)", file=sys.stderr, flush=True)
        if not calls and not bench.writes and nudges < MAX_NUDGES:
            nudges += 1  # a reply cut off at max_tokens, or an answer with no contract saved: ask once more
            messages.append({"role": "user", "content": NUDGE})
            continue
        if not calls:
            stop = "done"
            break
        for call in calls:
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            text = bench.call(call["function"]["name"], args if isinstance(args, dict) else {})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
    shutil.rmtree(bench.path.parent, ignore_errors=True)
    return {"model": model, "stop": stop, "nudges": nudges, "messages": messages, "writes": bench.writes,
            "guide_parts": bench.guide_parts, "guide_chars": bench.guide_chars, "usage": usage, "wall_seconds": round(time.time() - started, 1)}
