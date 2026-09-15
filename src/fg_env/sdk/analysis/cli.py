"""Command line for analysis: sweep, sensitivity, calibrate, backtest, checks, highlights.

Wire into the ``fg-env`` parser with ``add_analysis_commands(sub)``. Every command prints a
plain-text report, or JSON with ``--json``; mistakes a user can make become one-line errors
with exit status 1 (2 for a run that breaks), never a traceback.
"""
from __future__ import annotations

import argparse
import functools
import json
import sys
from typing import Any, Callable, Dict, List, Optional

from ..errors import ContractError, InputError, RunError
from ..expr import ExprError

__all__ = ["add_analysis_commands"]


class _UsageError(ValueError):
    """A command-line mistake."""


def _value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _split(item: str, flag: str) -> tuple:
    if "=" not in item:
        raise _UsageError(f"{flag} expects NAME=VALUE, got {item!r}")
    name, value = item.split("=", 1)
    return name.strip(), value.strip()


def _json_file(path: str, flag: str) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise _UsageError(f"cannot read {flag} {path}: {exc.strerror or exc}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _UsageError(f"{flag} {path} is not valid JSON: {exc}") from None


def _inputs(args: argparse.Namespace) -> Dict[str, Any]:
    inputs: Dict[str, Any] = {}
    if getattr(args, "inputs_file", None):
        loaded = _json_file(args.inputs_file, "--inputs-file")
        if not isinstance(loaded, dict):
            raise _UsageError("--inputs-file must hold a JSON object of name → value")
        inputs.update(loaded)
    for item in getattr(args, "input", None) or []:
        name, value = _split(item, "--input")
        inputs[name] = _value(value)
    return inputs


def _participants(items: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    out: Dict[str, Any] = {}
    for item in items:
        key, _, value = item.rpartition("=")
        out[key or "*"] = value
    return out


def _range(text: str, flag: str, steps: bool) -> Dict[str, Any]:
    """``LOW:HIGH[:STEPS][:log]`` → ``{low, high, steps?, log?}``."""
    parts = text.split(":")
    log = parts[-1] == "log"
    if log:
        parts = parts[:-1]
    if len(parts) not in ((2, 3) if steps else (2,)):
        raise _UsageError(f"{flag} range must be LOW:HIGH{'[:STEPS]' if steps else ''}[:log], got {text!r}")
    try:
        spec: Dict[str, Any] = {"low": float(parts[0]), "high": float(parts[1])}
        if len(parts) == 3:
            spec["steps"] = int(parts[2])
    except ValueError:
        raise _UsageError(f"{flag} range must hold numbers, got {text!r}") from None
    if log:
        spec["log"] = True
    return spec


def _sweep_params(items: Optional[List[str]]) -> Dict[str, Any]:
    if not items:
        raise _UsageError("give at least one --param NAME=V1,V2,… or NAME=LOW:HIGH:STEPS")
    params: Dict[str, Any] = {}
    for item in items:
        name, value = _split(item, "--param")
        if ":" in value and "," not in value:
            params[name] = _range(value, "--param", steps=True)
        else:
            params[name] = [_value(v) for v in value.split(",")]
    return params


def _named_ranges(items: Optional[List[str]], flag: str) -> Dict[str, Optional[Dict[str, Any]]]:
    if not items:
        raise _UsageError(f"give at least one {flag} NAME or NAME=LOW:HIGH")
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for item in items:
        if "=" in item:
            name, value = _split(item, flag)
            out[name] = _range(value, flag, steps=False)
        else:
            out[item.strip()] = None
    return out


def _emit(result: Any, as_json: bool) -> None:
    print(json.dumps(result.to_dict(), indent=2, default=str, ensure_ascii=False) if as_json else result.report())


def _guarded(command: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    @functools.wraps(command)
    def run(args: argparse.Namespace) -> int:
        try:
            return command(args)
        except (ContractError, InputError) as exc:
            for issue in exc.issues:
                print(f"error: {issue}", file=sys.stderr)
            return 1
        except (RunError, ExprError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except (ValueError, KeyError, TypeError, OSError) as exc:
            message = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
            print(f"error: {message}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return 130

    return run


def cmd_sweep(args: argparse.Namespace) -> int:
    from .sweep import sweep

    result = sweep(args.file, _sweep_params(args.param), runs=args.runs, outputs=args.output or None,
                   arms=args.arm or None, inputs=_inputs(args), design=args.design, samples=args.samples,
                   participants=_participants(args.agent), rounds=args.rounds, seed=args.seed, workers=args.workers,
                   data_dir=args.data_dir)
    _emit(result, args.json)
    return 0


def cmd_sensitivity(args: argparse.Namespace) -> int:
    from .sensitivity import sensitivity

    result = sensitivity(args.file, _named_ranges(args.vary, "--vary"), args.output, method=args.method,
                         runs=args.runs, baseline=_inputs(args), delta=args.delta, trajectories=args.trajectories,
                         levels=args.levels, samples=args.samples, arm=args.arm,
                         participants=_participants(args.agent), rounds=args.rounds, seed=args.seed,
                         workers=args.workers, data_dir=args.data_dir)
    _emit(result, args.json)
    return 0


def _held_out_cases(text: Optional[str]) -> Any:
    """``--test``: a share of the cases (``0.25``) or comma-separated case names."""
    if text is None:
        return None
    try:
        share = float(text)
    except ValueError:
        return [name.strip() for name in text.split(",") if name.strip()]
    return share if 0 < share < 1 else [text]


def cmd_calibrate(args: argparse.Namespace) -> int:
    from .calibrate import calibrate

    targets: Dict[str, Any] = {}
    if args.targets_file:
        loaded = _json_file(args.targets_file, "--targets-file")
        if not isinstance(loaded, dict):
            raise _UsageError("--targets-file must hold a JSON object of name → target")
        targets.update(loaded)
    for item in args.target or []:
        name, value = _split(item, "--target")
        targets[name] = _value(value)
    goal: Any = targets
    if args.cases:
        if targets:
            raise _UsageError("give --cases or --target/--targets-file, not both")
        goal = _json_file(args.cases, "--cases")
        if not isinstance(goal, list):
            raise _UsageError("--cases must hold a JSON list of {name, inputs, targets}")
    params = {name: spec or {} for name, spec in _named_ranges(args.param, "--param").items()}
    result = calibrate(args.file, goal, params, runs=args.runs, budget=args.budget, holdout=args.holdout,
                       method=args.method, inputs=_inputs(args), arm=args.arm, participants=_participants(args.agent),
                       rounds=args.rounds, seed=args.seed, workers=args.workers, test=_held_out_cases(args.test),
                       folds=args.folds, data_dir=args.data_dir)
    _emit(result, args.json)
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest import backtest

    cases = _json_file(args.cases, "--cases")
    if not isinstance(cases, list):
        raise _UsageError("--cases must hold a JSON list of {inputs, outcome, name?}")
    result = backtest(args.file, cases, args.output, runs=args.runs, threshold=args.threshold, arm=args.arm,
                      participants=_participants(args.agent), rounds=args.rounds, seed=args.seed, workers=args.workers,
                      test=_held_out_cases(args.test), folds=args.folds, data_dir=args.data_dir)
    _emit(result, args.json)
    return 0


def cmd_checks(args: argparse.Namespace) -> int:
    from .checks import behavior_checks

    report = behavior_checks(args.file, runs=args.runs, rounds=args.rounds, seed=args.seed,
                             participants=_participants(args.agent) or "random", inputs=_inputs(args),
                             workers=args.workers, data_dir=args.data_dir)
    _emit(report, args.json)
    return 0 if report.ok else 1


def cmd_highlights(args: argparse.Namespace) -> int:
    from ..api import load
    from .highlights import highlights, narrative

    env = load(args.file, inputs=_inputs(args), seed=args.seed, arm=args.arm, data_dir=args.data_dir)
    result = env.run(_participants(args.agent), rounds=args.rounds)
    moments = highlights(result, top=args.top)
    if args.json:
        payload: Dict[str, Any] = {"seed": result.seed, "highlights": [h.to_dict() for h in moments]}
        if args.narrative:
            payload["narrative"] = narrative(result)
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
    elif args.narrative:
        print(narrative(result))
    else:
        for rank, moment in enumerate(moments, 1):
            print(f"{rank}. [{moment.kind}, score {moment.score:.2f}] {moment.text}")
    return 0 if result.status != "failed" else 2


def _run_options(parser: argparse.ArgumentParser, seed_default: Optional[int] = 0, workers: bool = True) -> None:
    parser.add_argument("file", help="contract JSON file")
    parser.add_argument("--seed", type=int, default=seed_default, help="base seed (runs derive theirs from it)")
    parser.add_argument("--input", action="append", metavar="NAME=VALUE", help="fix an input (JSON value or text)")
    parser.add_argument("--inputs-file", help="JSON file of inputs")
    parser.add_argument("--agent", action="append", metavar="[TYPE_OR_ID=]PARTICIPANT",
                        help="random | idle | policy:<name>, optionally for one type or entity")
    parser.add_argument("--rounds", type=int, help="stop each run after this many rounds")
    parser.add_argument("--data-dir", help="folder input data files are read from (default: the contract's folder)")
    if workers:
        parser.add_argument("--workers", type=int, default=1, help="parallel processes")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")


def _holdout_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test", metavar="SHARE|NAMES", help="hold out cases: a share (0.25) or comma-separated case names")
    group.add_argument("--folds", type=int, help="k-fold cross-validation over the cases")


def add_analysis_commands(sub: Any) -> None:
    """Register the analysis commands on an ``argparse`` sub-parser collection."""
    p = sub.add_parser("sweep", help="run a grid or Latin hypercube of inputs and show main effects")
    _run_options(p)
    p.add_argument("--param", action="append", metavar="NAME=V1,V2|NAME=LOW:HIGH:STEPS[:log]", help="input to sweep")
    p.add_argument("--runs", type=int, default=5, help="seeded runs per cell")
    p.add_argument("--output", action="append", help="output to measure (default: every numeric output)")
    p.add_argument("--arm", action="append", help="arm to add as a factor")
    p.add_argument("--design", choices=("factorial", "lhs"), default="factorial")
    p.add_argument("--samples", type=int, help="Latin hypercube points (design lhs)")
    p.set_defaults(func=_guarded(cmd_sweep))

    p = sub.add_parser("sensitivity", help="rank inputs by their influence on an output")
    _run_options(p)
    p.add_argument("--output", required=True, help="output or metric to explain")
    p.add_argument("--vary", action="append", metavar="NAME[=LOW:HIGH]", help="input to rank")
    p.add_argument("--method", choices=("oat", "morris", "sobol"), default="oat")
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--arm")
    p.add_argument("--delta", type=float, default=0.1, help="oat: perturbation as a share of the baseline")
    p.add_argument("--trajectories", type=int, default=6, help="morris: number of trajectories")
    p.add_argument("--levels", type=int, default=4, help="morris: grid levels")
    p.add_argument("--samples", type=int, default=20, help="sobol: Latin hypercube points")
    p.set_defaults(func=_guarded(cmd_sensitivity))

    from .facts import describe_statistics

    p = sub.add_parser("calibrate", help="fit inputs so outputs or metrics match targets",
                       formatter_class=argparse.RawDescriptionHelpFormatter,
                       epilog='statistics for {"stat": …, "of": metric, "value": …} targets:\n' + describe_statistics())
    _run_options(p)
    p.add_argument("--target", action="append", metavar="NAME=VALUE", help="a number, a JSON list, or a JSON spec")
    p.add_argument("--targets-file", help="JSON object of targets")
    p.add_argument("--param", action="append", metavar="NAME[=LOW:HIGH[:log]]", help="input to fit")
    p.add_argument("--runs", type=int, default=5, help="seeded runs per evaluated point")
    p.add_argument("--budget", type=int, default=30, help="most points to evaluate")
    p.add_argument("--holdout", type=int, help="held-out validation runs (default: --runs)")
    p.add_argument("--method", choices=("auto", "bisection", "golden", "nelder_mead", "cross_entropy"), default="auto")
    p.add_argument("--arm")
    p.add_argument("--cases", help="JSON list of cases {name, inputs, targets} fitted together (instead of --target)")
    _holdout_options(p)
    p.set_defaults(func=_guarded(cmd_calibrate))

    p = sub.add_parser("backtest", help="score the contract's forecasts against known outcomes")
    _run_options(p)
    p.add_argument("--cases", required=True, help="JSON list of {inputs, outcome, name?}")
    p.add_argument("--output", required=True, help="output that forecasts the outcome")
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--threshold", type=float, help="forecast the event 'output > threshold'")
    p.add_argument("--arm")
    _holdout_options(p)
    p.set_defaults(func=_guarded(cmd_backtest))

    p = sub.add_parser("checks", help="play the contract with random agents and report what looks broken "
                                      "(exit status 1 when runs fail)")
    _run_options(p)
    p.add_argument("--runs", type=int, default=4)
    p.set_defaults(func=_guarded(cmd_checks))

    p = sub.add_parser("highlights", help="the notable moments of one run")
    _run_options(p, seed_default=0, workers=False)
    p.add_argument("--arm")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--narrative", action="store_true", help="print a compact timeline instead")
    p.set_defaults(func=_guarded(cmd_highlights))
