"""``fg-env report``: a plain-language report of a saved run, or of a contract run across its arms."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

__all__ = ["add_report_command"]


def _json(path: str, flag: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {flag} {path}: {exc.strerror or exc}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{flag} {path} is not valid JSON: {exc}") from None


def _requirements(items: list[str] | None) -> dict[str, str] | None:
    if not items:
        return None
    out = {}
    for item in items:
        for op in (">=", "<=", ">", "<"):
            name, found, value = item.partition(op)
            if found:
                out[name.strip()] = f"{op} {value.strip()}"
                break
        else:
            raise ValueError(f"--require expects NAME>=VALUE (or <=, >, <), got {item!r}")
    return out


def cmd_report(args: argparse.Namespace) -> int:
    from ..analysis.validate import validate
    from ..errors import ContractError
    from ..experiments.experiment import experiment
    from ..runtime.measure import RunResult
    from . import report

    try:
        data = _json(args.file, "file")
        saved = isinstance(data, dict) and "status" in data and "outputs" in data
        contract = args.contract if saved else args.file
        inputs = json.loads(Path(args.inputs_file).read_text(encoding="utf-8")) if args.inputs_file else None
        if saved:
            source: Any = RunResult.load(args.file)
        else:
            source = experiment(args.file, runs=args.runs, arms=args.arm or None, seed=args.seed, inputs=inputs,
                                workers=args.workers)
        checked = validate(contract, _json(args.cases, "--cases"), runs=args.runs, seed=args.seed) \
            if args.cases and contract else None
        written = report(source, args.audience, contract=contract, validation=checked, objective=args.objective,
                         require=_requirements(args.require), control=args.control)
    except ContractError as exc:
        for issue in exc.issues:
            print(f"error: {issue}", file=sys.stderr)
        return 1
    except (ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.out:
        written.save(args.out)
    print(written.to_json() if args.json else written.markdown)
    return 0


def add_report_command(sub: Any) -> None:
    p = sub.add_parser("report", help="a plain-language report for a manager or owner: a saved run, or a contract "
                                      "run across its arms")
    p.add_argument("file", help="a contract (runs every arm, or --arm) or a result saved with result.save / run --json")
    p.add_argument("--contract", help="with a saved result: its contract, for names, assumptions and patterns")
    p.add_argument("--audience", choices=("owner", "analyst"), default="owner")
    p.add_argument("--arm", action="append", help="arm to run (default: every declared arm)")
    p.add_argument("--runs", type=int, default=20, help="seeded runs per arm")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--inputs-file", help="JSON file of inputs for every run")
    p.add_argument("--workers", type=int, default=1, help="parallel processes")
    p.add_argument("--objective", help="min:<output> or max:<output>: what the recommendation optimises")
    p.add_argument("--require", action="append", metavar="OUTPUT>=VALUE", help="a bound the recommendation must meet")
    p.add_argument("--control", help="the arm differences are measured against (default: the first)")
    p.add_argument("--cases", help="JSON list of validation cases {name, inputs, actuals}")
    p.add_argument("--out", help="also write the report: .json for JSON, anything else for Markdown")
    p.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    p.set_defaults(func=cmd_report)
