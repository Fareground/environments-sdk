"""Command line for games: ``fg-env conformance`` and ``fg-env playthrough``."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

__all__ = ["add_game_commands"]


def cmd_conformance(args: argparse.Namespace) -> int:
    from ..game.conformance import conformance
    from . import _inputs

    report = conformance(args.file, sims=args.sims, seed=args.seed, inputs=_inputs(args),
                         simultaneous="turn_based" if args.turn_based else "joint", leak_branches=args.leak_branches,
                         max_steps=args.max_steps, resume=not args.no_resume)
    print(json.dumps(report.to_dict(), indent=2, default=str, ensure_ascii=False) if args.json else report.summary())
    return 0 if report.ok else 1


def cmd_playthrough(args: argparse.Namespace) -> int:
    from ..game.playthrough import playthrough, steps_from_text
    from . import _inputs, _UsageError

    steps = None
    if args.steps:
        try:
            with open(args.steps, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            raise _UsageError(f"cannot read --steps {args.steps}: {exc.strerror or exc}") from None
        steps = steps_from_text(text) if not text.lstrip().startswith("[") else json.loads(text)
    text = playthrough(args.file, seed=args.seed, steps=steps, inputs=_inputs(args),
                       simultaneous="turn_based" if args.turn_based else "joint", max_steps=args.max_steps)
    if args.check:
        try:
            with open(args.check, encoding="utf-8") as handle:
                golden = handle.read()
        except OSError as exc:
            raise _UsageError(f"cannot read --check {args.check}: {exc.strerror or exc}") from None
        if golden == text:
            print(f"{args.check}: unchanged")
            return 0
        import difflib

        sys.stdout.writelines(difflib.unified_diff(golden.splitlines(True), text.splitlines(True), args.check, "now"))
        return 1
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text, end="")
    return 0


def add_game_commands(sub: Any) -> None:
    from . import _guarded

    p = sub.add_parser("conformance", help="random playouts that check every decision of a game: legal calls apply, "
                                           "clones, serialization, chance, returns, replay, resume and leaks")
    p.add_argument("file", help="contract JSON file")
    p.add_argument("--sims", type=int, default=20, help="random playouts")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--input", action="append", metavar="NAME=VALUE", help="set an input (JSON value or text)")
    p.add_argument("--inputs-file", help="JSON file of inputs")
    p.add_argument("--turn-based", action="store_true", help="play simultaneous stages one seat at a time")
    p.add_argument("--leak-branches", type=int, default=2, help="changed steps per playout for the leak test")
    p.add_argument("--max-steps", type=int, default=1000, help="longest playout")
    p.add_argument("--no-resume", action="store_true", help="skip the whole-run resume check")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_guarded(cmd_conformance))

    p = sub.add_parser("playthrough", help="play one game and print what every seat reads at every decision, the "
                                           "legal calls, chance and returns (a golden text to diff)")
    p.add_argument("file", help="contract JSON file")
    p.add_argument("--seed", type=int, default=0, help="seed for the game and the random steps")
    p.add_argument("--steps", help="play these steps: a playthrough file or a JSON list of steps")
    p.add_argument("--input", action="append", metavar="NAME=VALUE", help="set an input (JSON value or text)")
    p.add_argument("--inputs-file", help="JSON file of inputs")
    p.add_argument("--turn-based", action="store_true", help="play simultaneous stages one seat at a time")
    p.add_argument("--max-steps", type=int, default=500, help="longest playthrough")
    p.add_argument("--out", help="write the playthrough to this file")
    p.add_argument("--check", metavar="FILE",
                   help="compare with a golden playthrough; print the diff, exit 1 if changed")
    p.set_defaults(func=_guarded(cmd_playthrough))
