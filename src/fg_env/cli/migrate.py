"""`fg-env migrate FILE... [--write]`: show, or save, contracts written in an earlier form of the language in the
current form (the same rewrites loading them makes)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

__all__ = ["add_migrate_command"]


def cmd_migrate(args: argparse.Namespace) -> int:
    from ..api import migrate
    from ..contract.layout import dumps
    from ..errors import ContractError

    failed = 0
    for name in args.files:
        try:
            current, notes = migrate(name)
        except ContractError as exc:
            failed += 1
            for issue in exc.issues:
                print(f"error: {name}: {issue}", file=sys.stderr)
            continue
        if args.write or len(args.files) > 1:
            if args.write and notes:  # a current file keeps its own layout
                Path(name).write_text(dumps(current), encoding="utf-8")
            done = "wrote the current form" if args.write else "to rewrite"
            print(f"{name}: " + (f"{done} ({len(notes)} rewrite(s))" if notes else "already current"))
        else:
            print(dumps(current), end="")
        if not args.quiet:
            for note in notes:
                print(f"  {note}", file=sys.stderr)
    return 1 if failed else 0


def add_migrate_command(sub: Any) -> None:
    p = sub.add_parser("migrate", help="rewrite a contract written in an earlier form of the language in the current "
                                       "form: prints it (and each rewrite), or saves it with --write")
    p.add_argument("files", nargs="+", metavar="FILE", help="contract JSON file(s); imported files are migrated on "
                                                             "their own, so name them too")
    p.add_argument("--write", action="store_true", help="save the current form over each file")
    p.add_argument("-q", "--quiet", action="store_true", help="do not list the rewrites")
    p.set_defaults(func=cmd_migrate)
