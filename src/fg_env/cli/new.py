"""`fg-env new <template> [file]` / `fg-env new --engine <id> [file]`: write a ready-to-run contract to start from;
`fg-env engines` lists the engines."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

__all__ = ["add_new_command"]


def cmd_new(args: argparse.Namespace) -> int:
    from ..errors import ContractError
    from ..authoring.scaffold import TEMPLATES, new

    if args.engine:
        return _clone(args)
    if args.template is None:
        print("error: name a template (" + ", ".join(TEMPLATES) + ") or an engine with --engine <id> (fg-env engines)",
              file=sys.stderr)
        return 1
    path = args.file or f"{args.template}.json"
    try:
        new(args.template, path, overwrite=args.force)
    except ContractError as exc:
        for issue in exc.issues:
            print(f"error: {issue}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(f"error: {exc} (--force replaces it)", file=sys.stderr)
        return 1
    print(f"wrote {path}: {TEMPLATES[args.template][0]}")
    return _next(path)


def _clone(args: argparse.Namespace) -> int:
    """With --engine the one positional is the file: `fg-env new --engine market cafes.json`."""
    from ..engines import EngineNotFound, clone, get

    if args.file is not None:
        print(f"error: with --engine give only the file to write, not {args.template!r} and {args.file!r}", file=sys.stderr)
        return 1
    path = Path(args.template or f"{args.engine}.json")
    try:
        engine = get(args.engine)
        clone(args.engine, path, name=path.stem.replace("_", " ").replace("-", " ").strip().capitalize() or None,
              overwrite=args.force)
    except EngineNotFound as exc:
        print(f"error: {exc.args[0]}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(f"error: {exc} (--force replaces it)", file=sys.stderr)
        return 1
    print(f"wrote {path}: the {engine.id} engine — {engine.summary}")
    return _next(str(path))


def _next(path: str) -> int:
    print(f"next: fg-env check {path} · fg-env preview {path} <agent id> · fg-env run {path} --seed 1")
    return 0


def cmd_engines(args: argparse.Namespace) -> int:
    from ..engines import list_engines

    engines = list_engines(available=True)
    width = max(len(engine.id) for engine in engines)
    for engine in engines:
        print(f"{engine.id:<{width}}  {engine.summary}")
    print("\nstart from one: fg-env new --engine <id> my_env.json (a complete contract to edit, with coded participants)")
    return 0


def add_new_command(sub: Any) -> None:
    from ..authoring.scaffold import TEMPLATES

    p = sub.add_parser("new", help="write a ready-to-run contract from a template (" + ", ".join(TEMPLATES) + ") or, with "
                                   "--engine, from an engine (fg-env engines lists them)")
    p.add_argument("template", nargs="?", help=" | ".join(f"{name} ({about})" for name, (about, _) in TEMPLATES.items()))
    p.add_argument("file", nargs="?", help="where to write it (default: <template>.json)")
    p.add_argument("--engine", metavar="ID", help="start from an engine instead: fg-env new --engine <id> [file]")
    p.add_argument("--force", action="store_true", help="replace the file if it exists")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("engines", help="list the engines: complete, runnable scenarios to start from with fg-env new --engine")
    p.set_defaults(func=cmd_engines)
