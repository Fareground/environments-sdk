"""`fg-env author BRIEF --model <provider>:<model>`: have an LLM write a working contract from a plain-language brief.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["add_author_command"]


def cmd_author(args: argparse.Namespace) -> int:
    from ..authoring.author import author

    is_file = os.path.isfile(args.brief)  # False, not an error, for a brief too long to be a file name
    if not is_file and _names_a_file(args.brief):
        print(f"error: no file '{args.brief}': give the path of a brief file that exists, or the brief itself in "
              "quotes", file=sys.stderr)
        return 1
    brief = Path(args.brief).read_text(encoding="utf-8") if is_file else args.brief
    out = args.out or (str(Path(args.brief).with_suffix(".json")) if is_file else "env.json")
    if Path(out).exists() and not args.force:
        print(f"error: '{out}' already exists: choose another --out, or --force to replace it", file=sys.stderr)
        return 1
    budget = {key: value for key, value in (("tokens", args.tokens), ("calls", args.calls), ("seconds", args.seconds))
              if value is not None}
    result = author(brief, args.model, out=out, budget=budget, progress=lambda line: print(line, file=sys.stderr))
    print(result.summary())
    # a session the provider ended is a failure to report, even when an earlier revision works and is kept
    return 0 if result.ok and not result.stop.startswith(("error", "refused")) else 1


def _names_a_file(text: str) -> bool:
    """Whether ``text`` reads as a file's path rather than a brief: one word with a folder or an extension in it."""
    return not any(ch.isspace() for ch in text) and (os.sep in text or "/" in text or bool(Path(text).suffix))


def add_author_command(sub: Any) -> None:
    from ..authoring.author import DEFAULT_BUDGET
    from ..runtime.budget import CACHED_WEIGHT
    from . import _guarded

    p = sub.add_parser("author", help="have an LLM write a working contract from a plain-language brief")
    p.add_argument("brief", help="the brief: a text file, or the text itself")
    p.add_argument("--model", required=True, help="anthropic:<model> or openai:<model> (the key from ANTHROPIC_API_KEY "
                                                  "or OPENAI_API_KEY; OpenRouter and other OpenAI-compatible servers "
                                                  "through OPENAI_BASE_URL)")
    p.add_argument("--out", help="where to write the contract (default: the brief file's name as .json, or env.json)")
    p.add_argument("--force", action="store_true", help="replace --out if it exists")
    p.add_argument("--tokens", type=int, help="most model tokens to spend: input, output and cache writes, a cache "
                                              f"read counting {CACHED_WEIGHT:g} of one "
                                              f"(default: {DEFAULT_BUDGET['tokens']})")
    p.add_argument("--calls", type=int, help=f"most model calls to make (default: {DEFAULT_BUDGET['calls']})")
    p.add_argument("--seconds", type=float,
                   help=f"most wall-clock seconds to take (default: {DEFAULT_BUDGET['seconds']})")
    p.set_defaults(func=_guarded(cmd_author))
