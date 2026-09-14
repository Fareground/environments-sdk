"""Command line for the Environment SDK: check, run, preview, experiment, guide, schema."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from .errors import ContractError, InputError, RunError
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
            raise SystemExit(f"{flag} expects name=value, got {item!r}")
        key, value = item.split("=", 1)
        out[key.strip()] = _parse_value(value)
    return out


def _inputs(args: argparse.Namespace) -> Dict[str, Any]:
    inputs: Dict[str, Any] = {}
    if getattr(args, "inputs_file", None):
        with open(args.inputs_file) as handle:
            inputs.update(json.load(handle))
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


def _report_contract_error(exc: ContractError) -> int:
    for issue in exc.issues:
        print(f"error: {issue}", file=sys.stderr)
    return 1


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

    try:
        env = load(args.file, inputs=_inputs(args), seed=args.seed, arm=args.arm)
    except (ContractError, InputError) as exc:
        return _report_contract_error(exc)
    except (RunError, ExprError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = env.run(_participants(args.agent), rounds=args.rounds)
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
        env = load(args.file, inputs=_inputs(args), seed=args.seed, arm=args.arm)
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
    try:
        view = env.preview(args.entity, args.stage)
    except (RunError, ExprError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
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
    from .experiment import experiment

    arms = [a.strip() for a in args.arms.split(",")] if args.arms else None
    try:
        result = experiment(args.file, runs=args.runs, arms=arms, seed=args.seed, inputs=_inputs(args),
                            participants=_participants(args.agent), rounds=args.rounds, workers=args.workers)
    except (ContractError, InputError) as exc:
        return _report_contract_error(exc)
    except (RunError, ExprError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), indent=2, default=str, ensure_ascii=False) if args.json else result.table())
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


def add_commands(sub: Any) -> None:
    p = sub.add_parser("check", help="check a contract and list every problem with its fix")
    p.add_argument("file", help="contract JSON file")
    p.add_argument("--rounds", type=int, default=1,
                   help="also build and play this many rounds with default participants (0 = static check only)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("run", help="run a contract and print the result")
    _common(p, None)
    p.add_argument("--agent", action="append", metavar="[TYPE_OR_ID=]PARTICIPANT",
                   help="random | idle | policy:<name>, optionally for one type or entity")
    p.add_argument("--rounds", type=int, help="stop after this many rounds")
    p.add_argument("--events", action="store_true", help="include the event log")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("preview", help="show exactly what an agent would read and which tools it gets")
    _common(p, 0)
    p.add_argument("entity", help="agent entity id")
    p.add_argument("--stage", help="stage name (default: the first stage where this agent can act)")
    p.add_argument("--rounds", type=int, default=0, help="play this many rounds first, then preview")
    p.add_argument("--agent", action="append", metavar="[TYPE_OR_ID=]PARTICIPANT",
                   help="participants for the rounds played before the preview")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_preview)

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
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_experiment)

    p = sub.add_parser("guide", help="print the contract authoring guide")
    p.add_argument("part", nargs="?", help="one part: overview, model, reference, expressions, functions, "
                                           "templates, effects, patterns, running, checklist")
    p.set_defaults(func=cmd_guide)

    p = sub.add_parser("schema", help="print the contract JSON Schema")
    p.set_defaults(func=cmd_schema)
