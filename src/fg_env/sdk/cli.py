"""Command line for the Environment SDK: check, run, preview, experiment, guide, schema (trace and
evaluate are in :mod:`fg_env.sdk.cli_runs`)."""
from __future__ import annotations

import argparse
import functools
import json
import sys
from typing import Any, Callable, Dict, List, Optional

from .errors import ContractError, InputError, RunError, SnapshotError
from .expr import ExprError

__all__ = ["add_commands"]


def _parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _pairs(items: Optional[List[str]], flag: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for item in items or []:
        if "=" not in item:
            raise _UsageError(f"{flag} expects name=value, got {item!r}")
        key, value = item.split("=", 1)
        out[key.strip()] = _parse_value(value)
    return out


class _UsageError(Exception):
    """A command-line mistake: reported as one line, exit status 1."""


def _inputs(args: argparse.Namespace) -> Dict[str, Any]:
    inputs: Dict[str, Any] = {}
    if getattr(args, "inputs_file", None):
        try:
            with open(args.inputs_file, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except OSError as exc:
            raise _UsageError(f"cannot read --inputs-file {args.inputs_file}: {exc.strerror or exc}") from None
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _UsageError(f"--inputs-file {args.inputs_file} is not valid JSON: {exc}") from None
        if not isinstance(loaded, dict):
            raise _UsageError(f"--inputs-file {args.inputs_file} must hold a JSON object of name → value")
        inputs.update(loaded)
    inputs.update(_pairs(getattr(args, "input", None), "--input"))
    return inputs


def _participants(items: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    out: Dict[str, Any] = {}
    for item in items:
        key, _, value = item.rpartition("=")
        out[key or "*"] = value
    return out


def _check_exposures(args: argparse.Namespace) -> None:
    """``--exposures`` records into the result, so the result must go somewhere."""
    if args.exposures and not (args.json or getattr(args, "trace", None)):
        where = "add --json to print it" + (", or --trace FILE to save it" if hasattr(args, "trace") else "")
        raise _UsageError(f"--exposures records what every agent saw into the result: {where}")


def _report_contract_error(exc: ContractError) -> int:
    for issue in exc.issues:
        print(f"error: {issue}", file=sys.stderr)
    return 1


def _guarded(command: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    """Every failure a user can cause becomes a message and an exit status, never a traceback."""

    @functools.wraps(command)
    def run(args: argparse.Namespace) -> int:
        try:
            return command(args)
        except (ContractError, InputError) as exc:
            return _report_contract_error(exc)
        except (RunError, ExprError, SnapshotError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except (_UsageError, ValueError, KeyError, OSError) as exc:
            message = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
            print(f"error: {message}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return 130

    return run


def cmd_check(args: argparse.Namespace) -> int:
    from .api import check

    issues = check(args.file, rounds=args.rounds)
    if args.json:
        print(json.dumps([i.to_dict() for i in issues], indent=2, ensure_ascii=False))
    else:
        for issue in issues:
            print(("error: " if issue.severity == "error" else "warning: ") + f"{issue.path}: {issue.message}"
                  + (f" → {issue.fix}" if issue.fix else ""))
        errors = sum(1 for i in issues if i.severity == "error")
        print("contract OK" if not errors else f"{errors} error(s)", file=sys.stderr if errors else sys.stdout)
    return 1 if any(i.severity == "error" for i in issues) else 0


def cmd_run(args: argparse.Namespace) -> int:
    from .api import load
    from .cli_runs import budget_arg, save_frames

    _check_exposures(args)
    try:
        env = load(args.file, inputs=_inputs(args), seed=args.seed, arm=args.arm, data_dir=args.data_dir,
                   exposures=bool(args.trace or args.exposures))
    except (ContractError, InputError) as exc:
        return _report_contract_error(exc)
    except (RunError, ExprError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = env.run(_participants(args.agent), rounds=args.rounds, budget=budget_arg(args.budget))
    if args.trace:
        result.save(args.trace)
    if args.frames:
        save_frames(args.frames, result)
    if args.json:
        print(result.to_json(events=args.events))
    else:
        print(result.summary())
        stats = result.stats
        tokens = f", ~{stats['avg_update_tokens']} update tokens per read turn" if stats["update_reads"] else ""
        print(f"agents: {stats['wakes']} turns, {stats['actions']} actions, {stats['invalid_calls']} invalid calls{tokens}")
        if args.events:
            for event in result.events:
                if event.get("text"):
                    print(f"  r{event['round']} {event['kind']}: {event['text']}")
    return 0 if result.status in ("completed", "ended", "stopped") else 2


def cmd_preview(args: argparse.Namespace) -> int:
    from .api import load

    try:
        env = load(args.file, inputs=_inputs(args), seed=args.seed, arm=args.arm, data_dir=args.data_dir)
        if args.rounds:
            played = env.run(_participants(args.agent), rounds=args.rounds)
            if played.status == "failed":
                print(f"error: {played.error}", file=sys.stderr)
                return 2
    except (ContractError, InputError) as exc:
        return _report_contract_error(exc)
    except (RunError, ExprError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.entity not in env.world.entities:
        agents = [e.id for e in env.world.entities.values() if env.contract.types[e.entity_type].agent]
        print(f"no entity '{args.entity}' (agents: {', '.join(agents[:20])})", file=sys.stderr)
        return 1
    view = env.preview(args.entity, args.stage)
    if args.json:
        print(json.dumps(view, indent=2, ensure_ascii=False))
        return 0
    print("=== brief ===\n" + view["brief"])
    print("\n=== update ===\n" + view["update"])
    print("\n=== tools ===")
    for tool in view["tools"]:
        print(f"- {tool['name']}: {tool['description']}  {json.dumps(tool['input_schema']['properties'])}")
    tokens = view["tokens"]
    print(f"\n~tokens: brief {tokens['brief']}, update {tokens['update']}, tools {tokens['tools']}")
    return 0


def cmd_experiment(args: argparse.Namespace) -> int:
    from .cli_runs import budget_arg
    from .experiment import experiment

    _check_exposures(args)
    arms = [a.strip() for a in args.arms.split(",")] if args.arms else None
    try:
        result = experiment(args.file, runs=args.runs, arms=arms, seed=args.seed, inputs=_inputs(args),
                            participants=_participants(args.agent), rounds=args.rounds, workers=args.workers,
                            data_dir=args.data_dir, budget=budget_arg(args.budget), exposures=args.exposures)
    except (ContractError, InputError) as exc:
        return _report_contract_error(exc)
    except (RunError, ExprError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), indent=2, default=str, ensure_ascii=False) if args.json else result.table())
    return 0


def cmd_tournament(args: argparse.Namespace) -> int:
    from .cli_runs import budget_arg
    from .tournament import tournament

    _check_exposures(args)
    entrants: Dict[str, Any] = {}
    for item in args.entrant or []:
        name, sep, participant = (part.strip() for part in item.partition("="))
        if not sep or not name or not participant:
            raise _UsageError(f"--entrant expects NAME=PARTICIPANT, got {item!r}")
        if name in entrants:
            raise _UsageError(f"entrant '{name}' is given twice")
        entrants[name] = participant
    result = tournament(args.file, entrants, seats=args.seat, pairing=args.pairing, games=args.games, score=args.score,
                        rating=args.rating, swiss_rounds=args.swiss_rounds, others=args.others, inputs=_inputs(args),
                        arm=args.arm, rounds=args.rounds, seed=args.seed, workers=args.workers, data_dir=args.data_dir,
                        budget=budget_arg(args.budget), exposures=args.exposures)
    print(json.dumps(result.to_dict(), indent=2, default=str, ensure_ascii=False) if args.json else result.summary())
    return 0


def _description(args: argparse.Namespace) -> Any:
    from .describe import describe

    return describe(args.file, inputs=_inputs(args), arm=args.arm, data_dir=args.data_dir)


def cmd_describe(args: argparse.Namespace) -> int:
    description = _description(args)
    if args.json:
        print(json.dumps(description.to_dict(), indent=2, default=str, ensure_ascii=False))
    else:
        print(description.markdown, end="")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    description = _description(args)
    print(json.dumps(description.metadata, indent=2, default=str, ensure_ascii=False) if args.json else description.info())
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    from .bench import bench, bench_table

    results = bench(args.files, rounds=args.rounds, seed=args.seed, inputs=_inputs(args))
    print(json.dumps([r.to_dict() for r in results], indent=2) if args.json else bench_table(results))
    return 0 if all(r.status != "failed" for r in results) else 2


def cmd_expand(args: argparse.Namespace) -> int:
    from .api import expand

    print(json.dumps(expand(args.file, mechanisms=args.mechanisms), indent=2, ensure_ascii=False))
    return 0


def cmd_guide(args: argparse.Namespace) -> int:
    from .guide import guide

    print(guide(args.part))
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from .guide import schema

    print(json.dumps(schema(), indent=2, ensure_ascii=False))
    return 0


def _common(parser: argparse.ArgumentParser, seed_default: Optional[int]) -> None:
    parser.add_argument("file", help="contract JSON file")
    parser.add_argument("--seed", type=int, default=seed_default, help="run seed")
    parser.add_argument("--input", action="append", metavar="NAME=VALUE", help="set an input (JSON value or text)")
    parser.add_argument("--inputs-file", help="JSON file of inputs")
    parser.add_argument("--arm", help="experiment arm to apply")
    parser.add_argument("--data-dir", help="folder input data files are read from (default: the contract's folder)")


def _batch_flags(parser: argparse.ArgumentParser, what: str) -> None:
    parser.add_argument("--budget", action="append", metavar="NAME=VALUE",
                        help=f"budget for each {what}: tokens, calls, host_calls, seconds; on_exhaust=end|idle")
    parser.add_argument("--exposures", action="store_true",
                        help=f"record what every agent saw in each {what} (in the --json output)")


def add_commands(sub: Any) -> None:
    p = sub.add_parser("check", help="check a contract and list every problem with its fix")
    p.add_argument("file", help="contract JSON file")
    p.add_argument("--rounds", type=int, default=1,
                   help="also build and play this many rounds with default participants (0 = static check only)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_guarded(cmd_check))

    p = sub.add_parser("run", help="run a contract and print the result")
    _common(p, None)
    p.add_argument("--agent", action="append", metavar="[TYPE_OR_ID=]PARTICIPANT",
                   help="random | idle | policy:<name>, optionally for one type or entity")
    p.add_argument("--rounds", type=int, help="stop after this many rounds")
    p.add_argument("--events", action="store_true", help="include the event log")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.add_argument("--trace", metavar="FILE", help="record what every agent saw and did, and save the result to FILE "
                                                   "(.json or .jsonl) for fg-env trace and fg-env replay")
    p.add_argument("--budget", action="append", metavar="NAME=VALUE",
                   help="cap the run: tokens, calls, host_calls, seconds; on_exhaust=end|idle")
    p.add_argument("--exposures", action="store_true",
                   help="record what every agent saw and did (in the --json output; --trace records it too)")
    p.add_argument("--frames", metavar="FILE", help="save the spectator frames (views for spectators, one per round) "
                                                    "to FILE as JSON")
    p.set_defaults(func=_guarded(cmd_run))

    p = sub.add_parser("preview", help="show exactly what an agent would read and which tools it gets")
    _common(p, 0)
    p.add_argument("entity", help="agent entity id")
    p.add_argument("--stage", help="stage name (default: the first stage where this agent can act)")
    p.add_argument("--rounds", type=int, default=0, help="play this many rounds first, then preview")
    p.add_argument("--agent", action="append", metavar="[TYPE_OR_ID=]PARTICIPANT",
                   help="participants for the rounds played before the preview")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_guarded(cmd_preview))

    p = sub.add_parser("experiment", help="run arms × N seeded runs and compare outputs")
    p.add_argument("file", help="contract JSON file")
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--arms", help="comma-separated arm names (default: all declared)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--input", action="append", metavar="NAME=VALUE")
    p.add_argument("--inputs-file")
    p.add_argument("--agent", action="append", metavar="[TYPE_OR_ID=]PARTICIPANT")
    p.add_argument("--rounds", type=int)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--data-dir", help="folder input data files are read from (default: the contract's folder)")
    p.add_argument("--json", action="store_true")
    _batch_flags(p, "run")
    p.set_defaults(func=_guarded(cmd_experiment))

    p = sub.add_parser("tournament", help="play entrants against each other in the contract's seats and rate them")
    _common(p, 0)
    p.add_argument("--entrant", action="append", metavar="NAME=PARTICIPANT",
                   help="an entrant: random | idle | policy:<name> (give at least two)")
    p.add_argument("--seat", action="append", metavar="ENTITY_ID", help="a seat (default: every starting agent)")
    p.add_argument("--pairing", choices=("round_robin", "all_play_all", "swiss"), default="round_robin")
    p.add_argument("--games", type=int, default=1, help="games per seating (game g shares its seed across seatings)")
    p.add_argument("--score", help="output name or expression over $outputs and $seat (default: the winner)")
    p.add_argument("--rating", choices=("elo", "glicko2"), default="elo", help="rating the standings are ranked by")
    p.add_argument("--swiss-rounds", type=int, help="Swiss rounds (default: log2 of the entrants, rounded up)")
    p.add_argument("--others", metavar="PARTICIPANT", help="participant for agents without a seat")
    p.add_argument("--rounds", type=int, help="stop each game after this many rounds")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    _batch_flags(p, "game")
    p.set_defaults(func=_guarded(cmd_tournament))

    for name, text, command in (
            ("describe", "write an ODD-protocol description of a contract (markdown; --json adds the game metadata)",
             cmd_describe),
            ("info", "derived game metadata: turns, chance, information, players, length, action space", cmd_info)):
        p = sub.add_parser(name, help=text)
        p.add_argument("file", help="contract JSON file")
        p.add_argument("--input", action="append", metavar="NAME=VALUE", help="set an input (JSON value or text)")
        p.add_argument("--inputs-file", help="JSON file of inputs")
        p.add_argument("--arm", help="describe the contract with this arm applied")
        p.add_argument("--data-dir", help="folder input data files are read from (default: the contract's folder)")
        p.add_argument("--json", action="store_true", help="print JSON")
        p.set_defaults(func=_guarded(command))

    p = sub.add_parser("bench", help="time contracts (default: the reference models): ms per round, rounds per "
                                     "second and time per phase")
    p.add_argument("files", nargs="*", help="contract JSON files (default: the reference models in examples/contracts)")
    p.add_argument("--rounds", type=int, help="rounds to time (default: each contract's own length)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--input", action="append", metavar="NAME=VALUE", help="set an input wherever it is declared")
    p.add_argument("--inputs-file", help="JSON file of inputs")
    p.add_argument("--json", action="store_true", help="print JSON")
    p.set_defaults(func=_guarded(cmd_bench))
    from .cli_runs import add_run_commands

    add_run_commands(sub)

    p = sub.add_parser("expand", help="print the contract as the engine reads it: imports merged, macros expanded")
    p.add_argument("file", help="contract JSON file")
    p.add_argument("--mechanisms", action="store_true", help="also expand every mechanism into ordinary sections")
    p.set_defaults(func=_guarded(cmd_expand))

    p = sub.add_parser("guide", help="print the contract authoring guide")
    p.add_argument("part", nargs="?", help="one part: overview, model, reference, expressions, macros, functions, "
                                           "templates, effects, patterns, mechanisms, running, checklist")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("schema", help="print the contract JSON Schema")
    p.set_defaults(func=cmd_schema)
