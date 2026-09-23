"""Command line for recorded runs and evaluation: trace (reading and replaying) and evaluate."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from .cli import _check_exposures, _guarded, _inputs, _pairs, _UsageError

__all__ = ["add_run_commands", "budget_arg", "save_frames"]

TRACE_VIEWS = ("overview", "turn", "timeline", "search", "invalid", "agent", "replay")


def budget_arg(items: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    """``--budget tokens=100000 --budget on_exhaust=idle`` as a budget mapping (None when not given)."""
    return _pairs(items, "--budget") or None


def save_frames(path: str, result: Any) -> None:
    """``--frames FILE``: the run's spectator frames as JSON."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result.frames, handle, indent=2, ensure_ascii=False, default=str)
        handle.write("\n")
    if not result.frames:
        print(f"note: {path} holds no frames: frames render the contract's spectator views (\"for\": \"spectator\") "
              "at the end of every round", file=sys.stderr)


def cmd_trace(args: argparse.Namespace) -> int:
    from .trace import trace

    recording = trace(args.file)
    view, rest = args.view, list(args.args)
    if view == "replay":
        if len(rest) != 1:
            raise _UsageError("usage: fg-env trace FILE replay CONTRACT [--fallback PARTICIPANT]")
        return _replay(recording, rest[0], args)
    if args.fallback is not None or args.data_dir is not None:
        raise _UsageError("--fallback and --data-dir apply only to fg-env trace FILE replay CONTRACT")
    if view == "search":
        if not rest:
            raise _UsageError("usage: fg-env trace FILE search TEXT")
        shown = recording.search(" ".join(rest))
    elif view == "turn":
        shown = recording.turn(*_turn_args(rest))
    elif view == "agent":
        if len(rest) != 1:
            raise _UsageError("usage: fg-env trace FILE agent AGENT")
        shown = recording.agent(rest[0])
    elif len(rest) > (0 if view == "overview" else 1):
        raise _UsageError(f"usage: fg-env trace FILE {view}{'' if view == 'overview' else ' [AGENT]'}")
    elif view == "overview":
        shown = recording.overview()
    else:
        shown = recording.timeline(*rest) if view == "timeline" else recording.invalid(*rest)
    print(json.dumps(shown.data, indent=2, ensure_ascii=False, default=str) if args.json else shown.text)
    return 0


def _turn_args(rest: List[str]) -> List[Any]:
    usage = "usage: fg-env trace FILE turn WAKE (a number), or turn AGENT ROUND (a number)"
    if len(rest) not in (1, 2):
        raise _UsageError(usage)
    try:
        return [int(rest[0])] if len(rest) == 1 else [rest[0], int(rest[1])]
    except ValueError:
        raise _UsageError(usage) from None


def _replay(recording: Any, contract: str, args: argparse.Namespace) -> int:
    replayed = recording.replay(contract, fallback=args.fallback, data_dir=args.data_dir)
    if args.json:
        print(json.dumps(replayed.to_dict(), indent=2, ensure_ascii=False, default=str))
    else:
        print(("OK: " if replayed.ok else "DIVERGED: ") + replayed.message)
    return 0 if replayed.ok else 1


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .evaluate import evaluate

    _check_exposures(args)
    modes = {name: _share(name, value) for name, value in _pairs(args.mode, "--mode").items()} or None
    result = evaluate(args.file, focal=args.focal, background=args.background, baseline=args.baseline,
                      seats=args.seat, score=args.score, modes=modes, inputs=_inputs(args) or None, arm=args.arm,
                      runs=args.runs, rounds=args.rounds, budget=budget_arg(args.budget), seed=args.seed,
                      workers=args.workers, exposures=args.exposures)
    print(json.dumps(result.to_dict(), indent=2, default=str, ensure_ascii=False) if args.json else result.summary())
    return 0


def _share(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _UsageError(f"--mode expects NAME=SHARE with a share of the seats, like resident=0.75; got {name}={value!r}")
    return float(value)


def add_run_commands(sub: Any) -> None:
    p = sub.add_parser("trace", help="read a recorded run (overview, a turn in full, timeline, search, invalid calls, "
                                     "one agent), or replay it offline against a contract (exit 1 on a divergence)")
    p.add_argument("file", help="result file written with fg-env run --trace (.json or .jsonl)")
    p.add_argument("view", nargs="?", choices=TRACE_VIEWS, default="overview")
    p.add_argument("args", nargs="*", help="turn: WAKE or AGENT ROUND; timeline/invalid: [AGENT]; search: TEXT; "
                                           "agent: AGENT; replay: CONTRACT")
    p.add_argument("--fallback", metavar="PARTICIPANT", help="replay: play on after a divergence with random | idle | "
                                                             "policy:<name>")
    p.add_argument("--data-dir", help="replay: folder input data files are read from (default: the contract's folder)")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.set_defaults(func=_guarded(cmd_trace))

    p = sub.add_parser("evaluate", help="score a focal participant among background agents against a baseline")
    p.add_argument("file", help="contract JSON file, or a suite file {\"scenarios\": [...]}")
    p.add_argument("--seed", type=int, default=0, help="base seed (run i uses the seeds of fg-env experiment)")
    p.add_argument("--input", action="append", metavar="NAME=VALUE", help="set an input (JSON value or text)")
    p.add_argument("--inputs-file", help="JSON file of inputs")
    p.add_argument("--arm", help="experiment arm to apply")
    p.add_argument("--focal", required=True, metavar="PARTICIPANT", help="random | idle | policy:<name> | anthropic:<model> | openai:<model>")
    p.add_argument("--background", metavar="PARTICIPANT", help="plays every other agent (default: type policy)")
    p.add_argument("--baseline", metavar="PARTICIPANT", help="plays the focal seats in the paired runs "
                                                             "(default: the background)")
    p.add_argument("--seat", action="append", metavar="ENTITY_ID", help="a seat the focal participant may take "
                                                                        "(default: every starting agent)")
    p.add_argument("--score", help="output name or expression over $outputs and $seat "
                                   "(default: the game's returns, else the winner)")
    p.add_argument("--mode", action="append", metavar="NAME=SHARE", help="share of the seats focal takes, "
                                                                         "e.g. resident=0.75 (default: all seats)")
    p.add_argument("--runs", type=int, default=10, help="runs per scenario and mode")
    p.add_argument("--rounds", type=int, help="stop each run after this many rounds")
    p.add_argument("--budget", action="append", metavar="NAME=VALUE", help="per-run budget: tokens, calls, "
                                                                           "host_calls, seconds, on_exhaust")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--exposures", action="store_true", help="record what every agent saw in each run (in the "
                                                            "--json output)")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.set_defaults(func=_guarded(cmd_evaluate))
