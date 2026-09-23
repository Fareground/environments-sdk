"""`fg-env new <template> [file]`: write a ready-to-run contract to start from."""
from __future__ import annotations

import argparse
import sys
from typing import Any

__all__ = ["add_new_command"]


def cmd_new(args: argparse.Namespace) -> int:
    from ..errors import ContractError
    from ..scaffold import TEMPLATES, new

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
    print(f"next: fg-env check {path} · fg-env preview {path} <agent id> · fg-env run {path} --seed 1")
    return 0


def add_new_command(sub: Any) -> None:
    from ..scaffold import TEMPLATES

    p = sub.add_parser("new", help="write a ready-to-run contract from a template: " + ", ".join(TEMPLATES))
    p.add_argument("template", help=" | ".join(f"{name} ({about})" for name, (about, _) in TEMPLATES.items()))
    p.add_argument("file", nargs="?", help="where to write it (default: <template>.json)")
    p.add_argument("--force", action="store_true", help="replace the file if it exists")
    p.set_defaults(func=cmd_new)
