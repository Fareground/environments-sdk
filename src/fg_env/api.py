"""The public entry points: parse, check, load, run."""
from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Union

from .assets.catalog import resolve_assets
from .checks import check_contract, parse_contract
from .checks.smoke import run_issue, smoke_issues
from .contract import Contract
from .contract.inputs import resolve_inputs
from .contract.macros import expand_macros
from .contract.normalize import normalize
from .errors import ContractError, Issue, RunError
from .expr import ExprError
from .runtime.calibration import calibrate_at_load
from .runtime.env import Env
from .runtime.measure import RunResult
from .sampling.seeds import mint_seed

__all__ = ["ContractLike", "DataDir", "parse", "located", "check", "load", "run", "apply_arm", "expand"]

ContractLike = Union[Contract, Mapping[str, Any], str, "os.PathLike[str]"]


_SOURCES = "pass a path to a contract file, a dict, JSON text, or a Contract"
#: Longest path or text quoted back in an error.
_SHOWN = 120


def _read(source: ContractLike) -> Any:
    """The contract data behind ``source``.

    A string is JSON text when it starts (after whitespace) with ``{`` or ``[`` and a file path
    otherwise: a contract is a JSON object, so contract text cannot start any other way."""
    if isinstance(source, Contract):
        return source
    if isinstance(source, Mapping):
        return _with_imports(source, Path.cwd(), ())
    if isinstance(source, str) and source.lstrip().startswith(("{", "[")):
        return _with_imports(_json(source, "(json text)"), Path.cwd(), ())
    if isinstance(source, (str, os.PathLike)):
        path = Path(source)
        return _with_imports(_json(_file_text(path), _shown(str(path))), path.parent, (path.resolve(),))
    raise ContractError([Issue("(contract)", f"cannot read a contract from {type(source).__name__}", _SOURCES)])


#: Deepest chain of imports, and most imported files, one contract may use.
MAX_IMPORT_DEPTH = 16
MAX_IMPORTS = 64


def _with_imports(data: Any, folder: Path, stack: tuple[Path, ...]) -> Any:
    """``data`` with its macros expanded and its ``imports`` merged in (unchanged when it has neither)."""
    data = normalize(expand_macros(data))[0]
    if not isinstance(data, Mapping) or "imports" not in data:
        return data
    return _resolve_imports(data, folder, folder.resolve(), stack, [0], "imports")


def _resolve_imports(data: Mapping[str, Any], folder: Path, root: Path, stack: tuple[Path, ...], count: list[int],
                     where: str) -> dict[str, Any]:
    """``data`` (macros already expanded) with its imports merged in. Each file's macros are expanded
    before it is merged, so the importing contract's own entries, generated or written, win."""
    from .mechanisms import merge_sections
    from .registry import MechanismError

    out = copy.deepcopy(dict(data))
    listed = out.pop("imports", None)
    if listed is None:
        return out
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise ContractError([Issue(where, "must be a list of file paths", 'e.g. "imports": ["parts/deck.json"]')])
    for index, relative in enumerate(listed):
        path = f"{where}[{index}]"
        target = (folder / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise ContractError([Issue(path, f"'{_shown(relative)}' is outside the contract's folder",
                                       "keep imported files beside the contract or in a subfolder")]) from None
        if target in stack:
            chain = " → ".join(p.name for p in (*stack, target))
            raise ContractError([Issue(path, f"imports form a cycle: {chain}", "remove one of these imports")])
        count[0] += 1
        if len(stack) >= MAX_IMPORT_DEPTH or count[0] > MAX_IMPORTS:
            raise ContractError([Issue(path, f"too many imports (at most {MAX_IMPORTS} files, {MAX_IMPORT_DEPTH} deep)",
                                       "import fewer, larger files")])
        fragment = _json(_file_text(target), _shown(str(target)))
        if not isinstance(fragment, dict):
            raise ContractError([Issue(path, f"'{_shown(relative)}' must hold a JSON object of contract sections")])
        fragment = _resolve_imports(expand_macros(fragment), target.parent, root, (*stack, target), count,
                                    f"{path}.imports")
        for key in ("fg_env", "name", "description"):
            fragment.pop(key, None)
        try:
            for key in ("space", "physics"):
                if key in fragment:
                    out.setdefault(key, fragment.pop(key))
            merge_sections(out, fragment)
        except MechanismError as exc:
            raise ContractError([Issue(path, f"cannot merge '{_shown(relative)}': {exc}", exc.fix)]) from None
        except (AttributeError, TypeError, ValueError) as exc:
            raise ContractError([Issue(path,
                                       f"cannot merge '{_shown(relative)}': a section has the wrong shape ({exc})",
                                       "compare the imported file with the contract reference")]) from None
    return out


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
        raise ContractError([Issue(where,
                                   f"not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}")]) from None
    except (ValueError, RecursionError) as exc:  # nested too deeply, or a number too long to read
        raise ContractError([Issue(where,
                                   f"cannot read this JSON: {str(exc)[:_SHOWN] or 'nested too deeply'}")]) from None


DataDir = Union[str, "os.PathLike[str]", None]


def parse(source: ContractLike, data_dir: DataDir = None) -> Contract:
    """Read and structurally validate a contract (dict, path, JSON text or :class:`Contract`).

    The contract remembers where its input data files are read from: ``data_dir`` when given, else the
    contract file's folder, so every run, check and analysis of it finds them."""
    return located(parse_contract(_read(source)), default_data_dir(source, data_dir))


def located(contract: Contract, folder: DataDir) -> Contract:
    """``contract`` reading its data files from ``folder`` (a copy when that changes; the original is untouched)."""
    if folder is None or contract._folder == str(folder):
        return contract
    moved = contract.model_copy()
    moved._folder = str(folder)
    return moved


def expand(source: ContractLike, *, mechanisms: bool = False) -> dict[str, Any]:
    """The contract data the engine reads: imports merged and macros expanded (and, with
    ``mechanisms=True``, every mechanism expanded into ordinary sections too; the ``mechanisms`` block stays,
    since the generated effects read their config there, and loading the result again changes nothing).

    Raises :class:`ContractError` for problems found while expanding; ``check`` reports the rest."""
    from .mechanisms import expand_mechanisms

    data = contract_source(source) if isinstance(source, Contract) else _read(source)
    if not isinstance(data, Mapping):
        raise ContractError([Issue("(contract)", f"a contract is a JSON object, got {type(data).__name__}")])
    if not mechanisms:
        return copy.deepcopy(dict(data))
    expanded, issues = expand_mechanisms(data)
    if issues:
        raise ContractError(issues, title="mechanisms cannot be expanded")
    return expanded


def _without_unknown_fields(data: Any, issues: list[Issue]) -> Any:
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


def _check_all(source: ContractLike, data_dir: DataDir = None) -> tuple[Contract | None, list[Issue]]:
    try:
        data = _read(source)
    except ContractError as exc:  # a missing file or text that is not JSON
        return None, exc.issues + exc.warnings
    try:
        contract = located(parse_contract(data), default_data_dir(source, data_dir))
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


def check(source: ContractLike, rounds: int | None = None, seed: int = 0, *, data_dir: DataDir = None,
          hosts: Any = None, inputs: Mapping[str, Any] | None = None) -> list[Issue]:
    """Every problem in a contract, errors first then warnings. Never raises for contract problems: a missing file
    or text that is not JSON is an issue too.

    A contract without errors is also built and played, so problems that only appear with real values (sampling, later
    rounds, views, outputs, a policy's own rules) are reported the same way: once with random agents that read
    everything they are shown, once with agents that choose boundary values (a parameter's least value, zero, its
    greatest), once with every agent idle (a turn that passes without an action, as when a model times out or refuses,
    must not break the rules), then once per declared policy, played by every agent type (a rule whose action a type
    cannot take is skipped for it). An action called in these plays that never once succeeded is reported too. By
    default each play lasts 12 rounds (fewer when the run ends sooner; more to reach the last round a one-off event is
    scheduled for), the same on every machine: a time guard stops only a contract too slow to play, and is reported
    when it does. ``rounds`` plays exactly that many rounds instead (0 checks statically only). Inputs with a
    ``source`` are read from ``data_dir`` (default: the contract file's folder); ``hosts`` answers what the contract
    asks of a host during those plays. ``inputs`` checks a configured scenario without editing its defaults; supplied
    inputs are validated even with ``rounds=0``, and the plays exercise them.
    """
    contract, issues = _check_all(source, data_dir)
    errors = [i for i in issues if i.severity == "error"]
    static = rounds is not None and rounds <= 0
    if inputs is not None and static and contract is not None and not errors:
        try:
            resolve_inputs(contract, inputs, default_data_dir(source, data_dir))
        except ContractError as exc:
            errors.extend(exc.issues)
    warnings_from_smoke: list[Issue] = []
    if not static and contract is not None and not errors:
        built = contract
        try:
            found, warnings_from_smoke = smoke_issues(
                built, lambda: load(built, inputs=inputs, seed=seed, hosts=hosts, calibrate=False), rounds, seed)
            errors.extend(found)
        except ContractError as exc:
            errors.extend(exc.issues)
        except RunError as exc:
            errors.append(run_issue(str(exc)))  # its text starts with its path
    return errors + [i for i in issues if i.severity != "error"] + warnings_from_smoke


def apply_arm(contract: Contract, arm: str) -> Contract:
    """The contract with an arm's patch deep-merged in."""
    if arm not in contract.arms:
        raise ContractError([Issue("arm", f"'{arm}' is not a declared arm",
                                   f"arms: {', '.join(contract.arms) or 'none declared'}")])
    patch = contract.arms[arm].patch
    if not patch:
        return contract
    return located(parse_contract(_merge(contract_source(contract), patch)), contract._folder)


def contract_source(contract: Contract) -> dict[str, Any]:
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


def default_data_dir(source: ContractLike, data_dir: DataDir = None) -> Path | None:
    """Where input data files and assets are read from: ``data_dir`` when given, else the contract file's folder (a
    parsed contract remembers the folder it was read with)."""
    if data_dir is not None:
        return Path(data_dir)
    if isinstance(source, Contract):
        return Path(source._folder) if source._folder is not None else None
    if isinstance(source, os.PathLike) or (isinstance(source, str) and not source.lstrip().startswith(("{", "["))):
        return Path(source).parent
    return None


def load(source: ContractLike, *, inputs: Mapping[str, Any] | None = None, seed: int | None = None,
         arm: str | None = None, strict: bool = False, parallel: int = 8,
         data_dir: DataDir = None, hosts: Any = None, exposures: bool = False, chance: Any = None,
         calibrate: bool = True, events: bool = True) -> Env:
    """Check a contract and build a runnable :class:`Env`.

    Errors raise :class:`ContractError` listing every problem with a fix; ``strict=True`` also rejects warnings.
    ``seed`` defaults to a fresh one (readable as ``env.seed``). Inputs with a ``source`` and the contract's ``assets``
    read their files from ``data_dir`` (default: the contract file's folder). ``hosts`` (a :class:`~fg_env.host.Hosts`
    or a mapping of host name to adapter) answers the judgment the contract asks of a host; build-time host work
    (personas) is done before round 1. ``exposures=True`` records what every agent was shown on every wake
    (``result.exposures``); a contract that calls ``$seen`` records it anyway. ``chance`` decides `chance` effects:
    ``"sampled"`` (the default: drawn from the seeded stream) or a callable given each
    :class:`~fg_env.effects.chance.ChanceNode` that returns the index of the outcome to take (a fixed deal, duplicate
    formats); :func:`fg_env.rl.game` enumerates chance for search. A contract with a ``calibration`` section fits its
    inputs with pilot sessions first (``env.calibration`` is the report); ``calibrate=False`` skips that, as
    ``fg_env.check``'s smoke play does. ``events=False`` keeps no event log, for a big crowd played for many rounds:
    ``result.events`` is empty (``on_event`` still streams every event) and the run forgets each event once no agent's
    news can reach it, so its memory stays flat however long it plays; everything the run does is the same (a contract
    that reads `$events` or `$seen` keeps its log).
    """
    contract, issues = _check_all(source, data_dir)
    blocking = [i for i in issues if i.severity == "error" or strict]
    if blocking or contract is None:
        raise ContractError(blocking or issues)
    merged: dict[str, Any] = {}
    unarmed = contract
    if arm is not None:
        contract = apply_arm(contract, arm)
        if contract.arms[arm].patch:
            issues = check_contract(contract)
            errors = [i for i in issues if i.severity == "error"]
            if errors:
                raise ContractError(errors, title=f"arm '{arm}' makes the contract invalid")
        merged.update(contract.arms[arm].inputs)
    merged.update(inputs or {})
    folder = default_data_dir(contract)
    resolved = resolve_inputs(contract, merged, folder)
    assets = resolve_assets(contract, resolved, folder)
    run_seed = mint_seed() if seed is None else seed
    report = None
    if calibrate and contract.calibration is not None:
        report = calibrate_at_load(contract, merged, resolved, run_seed, arm,
                                   lambda values: Env(contract, dict(values), run_seed, arm, parallel, False, assets))
        if report is not None:
            resolved = resolve_inputs(contract, {**merged, **report["params"]}, folder)
    env = Env(contract, resolved, run_seed, arm, parallel, exposures, assets, events)
    env.calibration = report
    env.origin.unarmed = unarmed
    if chance is not None:
        from .copying.branch import use_chance

        use_chance(env, chance)
    if hosts is not None:
        from .host.api import attach

        attach(env, hosts)
    return env


def run(source: ContractLike, participants: Any = None, *, inputs: Mapping[str, Any] | None = None,
        seed: int | None = None, arm: str | None = None, rounds: int | None = None,
        on_event: Any = None, strict: bool = False, data_dir: DataDir = None,
        hosts: Any = None, time_limit: float | None = None, exposures: bool = False,
        budget: Mapping[str, Any] | None = None, events: bool = True) -> RunResult:
    """Load and run in one call: ``fg_env.run("shop.json", {"buyer": "policy:thrifty"}, seed=1)``.

    A run that fails — a rule that cannot be evaluated, a participant that raises, a model provider that refuses the
    request — raises :class:`RunError` saying what failed and how to fix it; its ``result`` is the failed run.
    (``env.run`` returns a failed run instead, and experiments keep failed runs and carry on.)"""
    env = load(source, inputs=inputs, seed=seed, arm=arm, strict=strict, data_dir=data_dir, hosts=hosts,
               exposures=exposures, events=events)
    try:
        return env.run(participants, rounds=rounds, on_event=on_event, time_limit=time_limit, budget=budget,
                       raise_errors=True)
    except ExprError as exc:
        raise _failed(RunError(str(exc)), env) from exc
    except RunError as exc:
        _failed(exc, env)
        raise


def _failed(error: RunError, env: Env) -> RunError:
    error.result = env.result()
    return error
