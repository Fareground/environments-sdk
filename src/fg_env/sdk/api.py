"""The public entry points: parse, check, load, run."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from .check import check_contract, parse_contract
from .contract import Contract
from .errors import ContractError, Issue, RunError
from .inputs import resolve_inputs
from .measure import RunResult
from .runtime import Env
from .seeds import mint_seed

__all__ = ["ContractLike", "parse", "check", "load", "run", "apply_arm"]

ContractLike = Union[Contract, Mapping[str, Any], str, "os.PathLike[str]"]


_SOURCES = "pass a path to a contract file, a dict, JSON text, or a Contract"
#: Longest path or text quoted back in an error.
_SHOWN = 120


def _read(source: ContractLike) -> Any:
    """The contract data behind ``source``.

    A string is JSON text when it starts (after whitespace) with ``{`` or ``[`` and a file path
    otherwise: a contract is a JSON object, so contract text cannot start any other way."""
    if isinstance(source, (Contract, Mapping)):
        return source
    if isinstance(source, str) and source.lstrip().startswith(("{", "[")):
        return _json(source, "(json text)")
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        return _json(_file_text(path), _shown(str(path)))
    raise ContractError([Issue("(contract)", f"cannot read a contract from {type(source).__name__}", _SOURCES)])


def _shown(text: str) -> str:
    return text if len(text) <= _SHOWN else text[:_SHOWN - 3] + "..."


def _file_text(path: Path) -> str:
    shown = _shown(str(path))
    try:
        exists, is_dir = path.exists(), path.is_dir()
    except (OSError, ValueError):  # a name too long for the filesystem, or one holding a NUL
        exists = is_dir = False
    if not exists:
        if path.suffix or os.sep in str(path) or (os.altsep and os.altsep in str(path)):
            raise ContractError([Issue("(contract)", f"file not found: '{shown}'", "check the path")])
        raise ContractError([Issue("(contract)", f"'{shown}' is neither an existing file nor JSON text",
                                   "JSON text must be an object starting with '{'; otherwise " + _SOURCES)])
    if is_dir:
        raise ContractError([Issue("(contract)", f"'{shown}' is a directory, not a contract file",
                                   "pass the path of the .json file")])
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ContractError([Issue("(contract)", f"cannot read '{shown}': {exc.strerror or exc}",
                                   "check that the file is readable")]) from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ContractError([Issue(shown, f"is not UTF-8 text (invalid byte at position {exc.start})",
                                   "save the contract as UTF-8 JSON")]) from None


def _json(text: str, where: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError([Issue(where, f"not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}")]) from None
    except (ValueError, RecursionError) as exc:  # nested too deeply, or a number too long to read
        raise ContractError([Issue(where, f"cannot read this JSON: {str(exc)[:_SHOWN] or 'nested too deeply'}")]) from None


def parse(source: ContractLike) -> Contract:
    """Read and structurally validate a contract (dict, path, JSON text or :class:`Contract`)."""
    return parse_contract(_read(source))


def _without_unknown_fields(data: Any, issues: List[Issue]) -> Any:
    """Drop fields reported as unknown so the rest of the contract can still be checked."""
    unknown = [i.path for i in issues if i.message.endswith("is not a field here")]
    if len(unknown) != len(issues):
        return None
    data = copy.deepcopy(data)
    for path in unknown:
        node, parts = data, path.replace("[", ".[").split(".")
        for part in parts[:-1]:
            key: Any = int(part[1:-1]) if part.startswith("[") else part
            try:
                node = node[key]
            except (KeyError, IndexError, TypeError):
                return None
        if isinstance(node, dict):
            node.pop(parts[-1], None)
    return data


def _check_all(source: ContractLike) -> tuple[Optional[Contract], List[Issue]]:
    data = _read(source)
    try:
        contract = parse_contract(data)
    except ContractError as exc:
        structural = exc.issues + exc.warnings
        cleaned = _without_unknown_fields(data, exc.issues) if isinstance(data, Mapping) else None
        if cleaned is None:
            return None, structural
        try:
            partial = parse_contract(cleaned)
        except ContractError:
            return None, structural
        semantic = check_contract(partial)
        return None, structural + semantic
    return contract, check_contract(contract)


def check(source: ContractLike, rounds: int = 0, seed: int = 0) -> List[Issue]:
    """Every problem in a contract, errors first then warnings. Never raises for contract problems.

    With ``rounds > 0`` a clean contract is also built and played for that many rounds with
    default participants, so problems that only appear with real values (sampling, first
    turns, views) are reported the same way.
    """
    contract, issues = _check_all(source)
    errors = [i for i in issues if i.severity == "error"]
    warnings_from_smoke: List[Issue] = []
    if rounds > 0 and contract is not None and not errors:
        try:
            result = load(contract, seed=seed).run(_smoke_participant(seed), rounds=rounds)
            if result.status == "failed":
                errors.append(_run_issue(result.error or "the run failed"))
            for problem in result.output_issues:
                warnings_from_smoke.append(Issue(problem["path"], f"{problem['message']} after {rounds} smoke round(s)",
                                                 "fine if it only has a value later in a run; otherwise guard it", "warning"))
        except ContractError as exc:
            errors.extend(exc.issues)
        except RunError as exc:
            errors.append(_run_issue(str(exc), exc.path))
    return errors + [i for i in issues if i.severity != "error"] + warnings_from_smoke


def _smoke_participant(seed: int) -> Any:
    """Reads everything an agent would read (brief, update, tools), then acts at random,
    so a smoke run exercises every view and template, not just the rules."""
    from .participants import RandomAgent

    random_agent = RandomAgent(seed)

    def participant(wake: Any) -> None:
        wake.brief
        wake.update
        random_agent(wake)

    return participant


def _run_issue(message: str, path: Optional[str] = None) -> Issue:
    if path is None and ": " in message:
        head, _, rest = message.partition(": ")
        if " " not in head:
            path, message = head, rest
    return Issue(path or "(run)", message, "fix the rule at this path (found by a smoke run)")


def apply_arm(contract: Contract, arm: str) -> Contract:
    """The contract with an arm's patch deep-merged in."""
    if arm not in contract.arms:
        raise ContractError([Issue("arm", f"'{arm}' is not a declared arm",
                                   f"arms: {', '.join(contract.arms) or 'none declared'}")])
    patch = contract.arms[arm].patch
    if not patch:
        return contract
    return parse_contract(_merge(contract_source(contract), patch))


def contract_source(contract: Contract) -> Dict[str, Any]:
    """The contract as written (mechanisms unexpanded), for re-parsing with changes."""
    if contract._source is not None:
        return copy.deepcopy(contract._source)
    return contract.model_dump(by_alias=True, exclude_unset=True)


def _merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for key, value in patch.items():
            out[key] = _merge(base.get(key), value) if key in base else copy.deepcopy(value)
        return out
    return copy.deepcopy(patch)


def load(source: ContractLike, *, inputs: Optional[Mapping[str, Any]] = None, seed: Optional[int] = None,
         arm: Optional[str] = None, strict: bool = False, parallel: int = 8) -> Env:
    """Check a contract and build a runnable :class:`Env`.

    Errors raise :class:`ContractError` listing every problem with a fix; ``strict=True``
    also rejects warnings. ``seed`` defaults to a fresh one (readable as ``env.seed``).
    """
    contract, issues = _check_all(source)
    blocking = [i for i in issues if i.severity == "error" or strict]
    if blocking or contract is None:
        raise ContractError(blocking or issues)
    merged: Dict[str, Any] = {}
    if arm is not None:
        contract = apply_arm(contract, arm)
        if contract.arms[arm].patch:
            issues = check_contract(contract)
            errors = [i for i in issues if i.severity == "error"]
            if errors:
                raise ContractError(errors, title=f"arm '{arm}' makes the contract invalid")
        merged.update(contract.arms[arm].inputs)
    merged.update(inputs or {})
    resolved = resolve_inputs(contract, merged)
    return Env(contract, resolved, mint_seed() if seed is None else seed, arm, parallel)


def run(source: ContractLike, participants: Any = None, *, inputs: Optional[Mapping[str, Any]] = None,
        seed: Optional[int] = None, arm: Optional[str] = None, rounds: Optional[int] = None,
        on_event: Any = None, strict: bool = False) -> RunResult:
    """Load and run in one call: ``fg_env.run("shop.json", {"buyer": "policy:thrifty"}, seed=1)``."""
    env = load(source, inputs=inputs, seed=seed, arm=arm, strict=strict)
    return env.run(participants, rounds=rounds, on_event=on_event)
