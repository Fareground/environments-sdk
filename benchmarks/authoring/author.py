"""The authoring agent the benchmark measures: :func:`fg_env.author` on OpenRouter — the SDK's own loop, guide and
tools, so the benchmark scores exactly what users run.

``author(...)`` returns the transcript (messages, contracts written, usage, time) so ``bench.py --replay`` can
re-score the contracts it wrote without the network. Needs the ``openai`` package.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import fg_env

OPENROUTER = "https://openrouter.ai/api/v1"
#: Where the key is read from when OPENROUTER_API_KEY is not set. Only the one line is parsed; nothing is copied.
ENV_FILE = Path("/Users/Sandro/Desktop/Projects/fareground/.env")


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


def author(brief: str, model: str, key: str, token_cap: int, label: str = "") -> Dict[str, Any]:
    """Let ``model`` (an OpenRouter id) author the brief; return the transcript. Progress goes to stderr, one line
    per model call, prefixed with ``label``."""
    import openai

    result = fg_env.author(brief, f"openai:{model}", client=openai.OpenAI(base_url=OPENROUTER, api_key=key),
                           budget={"tokens": token_cap},
                           progress=lambda line: print(f"{label} {line}", file=sys.stderr, flush=True))
    guide_calls = [c for m in result.messages for c in m.get("tool_calls") or [] if c["function"]["name"] == "guide"]
    guide_ids = {c["id"] for c in guide_calls}
    #: Characters of guide text the author was sent: its starting page, then every guide call it made.
    guide_chars = len(result.messages[0]["content"]) + sum(len(m["content"]) for m in result.messages
                                                            if m.get("tool_call_id") in guide_ids)
    return {"model": model, "stop": result.stop, "messages": result.messages, "writes": result.writes,
            "guide_parts": [_part(c) for c in guide_calls], "guide_chars": guide_chars, "usage": result.usage,
            "wall_seconds": result.seconds}


def _part(call: Dict[str, Any]) -> str:
    try:
        return str(json.loads(call["function"]["arguments"] or "{}").get("part"))
    except (json.JSONDecodeError, AttributeError):
        return "?"
