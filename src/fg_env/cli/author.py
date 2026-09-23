"""`fg-env author BRIEF --model <provider>:<model>`: have an LLM write a working contract from a plain-language brief."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

__all__ = ["add_author_command"]


def cmd_author(args: argparse.Namespace) -> int:
    from ..authoring import author

    is_file = os.path.isfile(args.brief)  # False, not an error, for a brief too long to be a file name
    brief = Path(args.brief).read_text(encoding="utf-8") if is_file else args.brief
    out = args.out or (str(Path(args.brief).with_suffix(".json")) if is_file else "env.json")
    if Path(out).exists() and not args.force:
        print(f"error: '{out}' already exists: choose another --out, or --force to replace it", file=sys.stderr)
        return 1
    budget = {"tokens": args.tokens} if args.tokens is not None else None
    result = author(brief, args.model, out=out, budget=budget, progress=lambda line: print(line, file=sys.stderr))
    print(result.summary())
    return 0 if result.ok else 1


def add_author_command(sub: Any) -> None:
    from ..authoring import DEFAULT_BUDGET
    from . import _guarded

    p = sub.add_parser("author", help="have an LLM write a working contract from a plain-language brief")
    p.add_argument("brief", help="the brief: a text file, or the text itself")
    p.add_argument("--model", required=True, help="anthropic:<model> or openai:<model> (the key from ANTHROPIC_API_KEY "
                                                  "or OPENAI_API_KEY; OpenRouter and other OpenAI-compatible servers "
                                                  "through OPENAI_BASE_URL)")
    p.add_argument("--out", help="where to write the contract (default: the brief file's name as .json, or env.json)")
    p.add_argument("--force", action="store_true", help="replace --out if it exists")
    p.add_argument("--tokens", type=int, help=f"most model tokens to spend, input + output (default: {DEFAULT_BUDGET['tokens']})")
    p.set_defaults(func=_guarded(cmd_author))
