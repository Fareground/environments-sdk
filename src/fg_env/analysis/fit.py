"""Fitting patterns to data: ``fg_env.analysis.fit_patterns(contract, data_dir=...)``.

Every pattern that declares ``fit`` is estimated from its rows, in order (a pattern listed in another's ``adjust`` is
fitted first, so the other is fitted on what is left). The estimates are written back into a copy of the contract as
inputs — ``<pattern>_<parameter>`` (or a ``<pattern>_fit`` table, one row per key) — and the pattern's parameters
read them, so the fitted world is inspectable, sweepable and reproducible. Standard errors are written beside them
and become the pattern's ``uncertainty``, scaled by the input ``parameter_uncertainty`` (1 samples estimation
uncertainty every run; 0 uses the estimates as they are). What each kind estimates and assumes is in
:mod:`.fit_estimators` and :mod:`.fit_joint`.
"""
from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..api import ContractLike, _read, contract_source, default_data_dir, load
from ..contract import Contract
from ..errors import ContractError, Issue
from ..expr import ExprError, Scope, compile_expr
from ..patterns import timebase as tb
from ..patterns.base import FitFactor, PatternConfig
from ..patterns.expand import validated
from ..patterns.runtime import key_text

__all__ = ["fit_patterns", "FitResult", "PatternFit", "Row", "Problem", "Estimate", "UNCERTAINTY_INPUT"]

#: The input scaling every fitted standard error.
UNCERTAINTY_INPUT = "parameter_uncertainty"


@dataclass
class Row:
    """One observation: when, what was seen, whose it is, its drivers, and whether it is only a lower bound."""

    t: float
    y: float
    key: str | None
    x: dict[str, float]
    censored: bool
    raw: Mapping[str, Any]


@dataclass
class Estimate:
    """What one fit found for one key: parameter values, their standard errors, how, and how well."""

    params: dict[str, Any]
    errors: dict[str, Any] = field(default_factory=dict)
    method: str = ""
    assumed: list[str] = field(default_factory=list)
    observed: list[float] = field(default_factory=list)
    predicted: list[float] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class PatternFit:
    """The report for one fitted pattern."""

    pattern: str
    kind: str
    method: str
    n: int
    keys: int
    rmse: float | None
    mape: float | None
    r2: float | None
    estimated: list[str]
    assumed: list[str]
    params: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        quality = ", ".join(part for part in (
            f"RMSE {self.rmse:.4g}" if self.rmse is not None else "",
            f"MAPE {self.mape:.1%}" if self.mape is not None else "",
            f"R² {self.r2:.3f}" if self.r2 is not None else "") if part)
        shown = json.dumps(self.params, default=lambda v: round(v, 4) if isinstance(v, float) else str(v))
        who = f" over {self.keys} keys" if self.keys > 1 else ""
        text = f"{self.pattern} ({self.kind}): {self.method}, {self.n} rows{who}; {quality or 'no error measure'}"
        text += (f"\n  estimated {', '.join(self.estimated) or 'nothing'}; assumed "
                 f"{', '.join(self.assumed) or 'nothing'}")
        text += f"\n  {shown if len(shown) < 400 else shown[:397] + '…'}"
        return text + "".join(f"\n  note: {note}" for note in self.notes)


@dataclass
class FitResult:
    """The fitted contract (data, like the one given), a report per pattern, and the estimates as priors."""

    contract: dict[str, Any]
    fits: list[PatternFit]
    #: Every fitted number input with a standard error as ``{input: {"dist": "normal", "mean", "sd"}}`` — the form
    #: ``uncertainty=`` takes on experiment, sweep, backtest and validate. The contract already draws these (and the
    #: per-key and list parameters, which have no input of their own) through each pattern's ``uncertainty``; pass
    #: the input ``parameter_uncertainty: 0`` alongside so they are not drawn twice.
    priors: dict[str, dict[str, Any]] = field(default_factory=dict)

    def report(self) -> str:
        return "\n".join(fit.line() for fit in self.fits) or "no pattern declares `fit`"

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.contract, indent=2, ensure_ascii=False) + "\n")


class Problem:
    """One pattern's fit: its config, its rows, and the world as fitted so far (to read other patterns)."""

    def __init__(self, name: str, cfg: Any, rows: list[Row], env: Any):
        #: The pattern's config — typed loosely, because each estimator reads the fields of its own kind.
        self.cfg: Any = cfg
        self.name, self.rows, self.env = name, rows, env

    @property
    def clock(self) -> Any:
        return tb.calendar_of(self.env.world)

    def fail(self, message: str) -> ContractError:
        return ContractError([Issue(f"patterns.{self.name}.fit", message)])

    def pattern(self, name: str, key: str | None, t: float, args: tuple[Any, ...] = ()) -> Any:
        runtime = self.env.world.patterns
        return runtime.evaluate(name, key if runtime.configs[name].keyed else None, list(args), t,
                                f"patterns.{self.name}.fit")

    def adjustment(self, row: Row) -> float:
        """The product of the patterns in ``adjust`` for this row (1 without any)."""
        total = 1.0
        for name in self.cfg.fit.adjust if self.cfg.fit else []:
            value = self.pattern(name, row.key, row.t)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise self.fail(f"'{name}' gave {value!r} at t = {row.t:g}; adjust patterns must give numbers above 0")
            total *= value
        return total

    def current(self, field_name: str, key: str | None) -> Any:
        """A parameter's value as the contract has it now (for what a fit assumes)."""
        return self.env.world.patterns.param(self.name, key, field_name, f"patterns.{self.name}")


def fit_patterns(contract: ContractLike, *, data_dir: str | Path | None = None,
                 inputs: Mapping[str, Any] | None = None) -> FitResult:
    """Estimate every pattern that declares ``fit`` and return the contract with the estimates written back.

    Data files are read from ``data_dir`` (default: the contract file's folder). ``inputs`` are used while fitting
    (e.g. which history table to read) and are not written into the result."""
    source = contract_source(contract) if isinstance(contract, Contract) else _read(contract)
    if not isinstance(source, Mapping):
        raise ContractError([Issue("(contract)", "a contract is a JSON object")])
    folder = default_data_dir(contract, data_dir)
    fitted = copy.deepcopy(dict(source))
    env = load(fitted, data_dir=folder, seed=0, inputs=dict(inputs or {}))
    configs = {name: cfg for name, spec in (fitted.get("patterns") or {}).items()
               for cfg in [validated(name, spec)[0]] if cfg is not None}
    order = _order(configs)
    covered = {factor for cfg in configs.values() if cfg.fit and isinstance(cfg.fit.x, dict) for factor in cfg.fit.x}
    covered |= {cfg.fit.noise for cfg in configs.values() if cfg.fit and cfg.fit.noise}
    reports: list[PatternFit] = []
    priors: dict[str, dict[str, Any]] = {}
    for index, name in enumerate(order):
        cfg = configs[name]
        if name in covered:
            reports.append(_skipped(name, cfg, "fitted together with the product that names it"))
            continue
        problem = Problem(name, cfg, _rows(name, cfg, env), env)
        updates, report = _estimate(problem, configs)
        for pattern, per_key in updates.items():
            data = cfg.fit.data if cfg.fit else "data"
            priors.update(_write_back(fitted, pattern, configs[pattern], per_key, env, data))
        reports.append(report)
        if index < len(order) - 1:  # later fits read the estimates written so far
            env = load(fitted, data_dir=folder, seed=0, inputs=dict(inputs or {}))
            configs = {n: validated(n, spec)[0] or configs[n] for n, spec in fitted["patterns"].items()}
    return FitResult(fitted, reports, priors)


def _estimate(problem: Problem,
              configs: dict[str, PatternConfig]) -> tuple[dict[str, dict[str | None, Estimate]], PatternFit]:
    from . import fit_estimators, fit_joint  # both build on this module's types

    cfg = problem.cfg
    if cfg.kind == "product":
        return fit_joint.fit_product(problem, configs)
    estimator = fit_estimators.ESTIMATORS.get(cfg.kind)
    if estimator is None:
        raise problem.fail(f"a {cfg.kind} pattern cannot be fitted from rows",)
    groups: dict[str | None, list[Row]] = {}
    for row in problem.rows:
        groups.setdefault(row.key if cfg.keyed else None, []).append(row)
    per_key: dict[str | None, Estimate] = {}
    notes: list[str] = []
    for key, rows in groups.items():
        try:
            per_key[key] = estimator(Problem(problem.name, cfg, rows, problem.env), key)
        except ValueError as exc:
            if not cfg.keyed:
                raise problem.fail(str(exc)) from None
            notes.append(f"key '{key}' kept its declared parameters: {exc}")
    if not per_key:
        raise problem.fail("no key had rows enough to fit: " + "; ".join(notes))
    return {problem.name: per_key}, summarise(problem.name, cfg, per_key, notes)


def summarise(name: str, cfg: PatternConfig, per_key: Mapping[str | None, Estimate], notes: list[str]) -> PatternFit:
    observed = [v for est in per_key.values() for v in est.observed]
    predicted = [v for est in per_key.values() for v in est.predicted]
    first = next(iter(per_key.values()))
    shown = first.params if len(per_key) == 1 else {str(k): est.params for k, est in list(per_key.items())[:3]}
    return PatternFit(name, cfg.kind, first.method, len(observed) or sum(1 for _ in per_key), len(per_key),
                      *quality(observed, predicted), sorted(first.params), sorted(first.assumed), shown,
                      [*dict.fromkeys(note for est in per_key.values() for note in est.notes), *notes])


def quality(observed: list[float], predicted: list[float]) -> tuple[float | None, float | None, float | None]:
    """RMSE, MAPE (rows above 0) and R² of predictions against what was seen."""
    if not observed:
        return None, None, None
    n = len(observed)
    rmse = math.sqrt(sum((o - p) ** 2 for o, p in zip(observed, predicted)) / n)
    positive = [(o, p) for o, p in zip(observed, predicted) if o > 0]
    mape = sum(abs(o - p) / o for o, p in positive) / len(positive) if positive else None
    mean = sum(observed) / n
    total = sum((o - mean) ** 2 for o in observed)
    r2 = 1 - sum((o - p) ** 2 for o, p in zip(observed, predicted)) / total if total > 0 else None
    return rmse, mape, r2


def _skipped(name: str, cfg: PatternConfig, why: str) -> PatternFit:
    return PatternFit(name, cfg.kind, why, 0, 0, None, None, None, [], [], {})


def _order(configs: dict[str, PatternConfig]) -> list[str]:
    """Patterns with ``fit``, each after the patterns its ``adjust`` names."""
    order: list[str] = []
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in order:
            return
        if name in visiting:
            raise ContractError([Issue(f"patterns.{name}.fit.adjust", "fits adjust each other in a cycle: "
                                       + " → ".join([*visiting, name]))])
        visiting.append(name)
        fit = configs[name].fit
        for other in fit.adjust if fit else []:
            if other in configs and configs[other].fit is not None:
                visit(other)
        visiting.pop()
        order.append(name)

    for name, cfg in configs.items():
        if cfg.fit is not None:
            visit(name)
    return order


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{where}: {value!r} is not a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: {value!r} is not a number") from None
    if not math.isfinite(number):
        raise ValueError(f"{where}: {value!r} is not a finite number")
    return number


def _rows(name: str, cfg: PatternConfig, env: Any) -> list[Row]:
    fit = cfg.fit
    assert fit is not None
    path = f"patterns.{name}.fit"
    scope = Scope({"inputs": env.inputs}, env.world)
    try:
        data = compile_expr(fit.data)(scope)
    except ExprError as exc:
        raise ContractError([Issue(f"{path}.data", exc.detail)]) from None
    if not isinstance(data, list) or not all(isinstance(row, Mapping) for row in data):
        raise ContractError([Issue(f"{path}.data", "must give rows (a table input)")])
    keep = compile_expr(fit.where) if fit.where else None
    rows: list[Row] = []
    factors = fit.x if isinstance(fit.x, dict) else ({"x": fit.x} if fit.x else {})
    times: dict[Any, float] = {}  # a history repeats each date once per key: parse each once
    for index, raw in enumerate(data):
        where = f"{path}.data row {index}"
        try:
            if keep is not None and not keep(scope.child(row=raw)):
                continue
            missing = [column for column in [fit.value, fit.time, fit.key, fit.mean, fit.censored,
                                              *(f if isinstance(f, str) else f.column for f in factors.values())]
                       if column is not None and column not in raw]
            if missing:
                raise ValueError(f"{where}: no column '{missing[0]}' (columns: {', '.join(raw)})")
            value = raw[fit.value]
            if value is None or value == "":
                continue
            when = raw[fit.time] if fit.time else 0.0
            cacheable = isinstance(when, (str, int, float)) and not isinstance(when, bool)
            t = times.get(when) if cacheable else None
            if t is None:
                moment = (float(when) if isinstance(when, str)
                          and when.strip().replace(".", "", 1).lstrip("-").isdigit() else when)
                t = tb.to_t(tb.calendar_of(env.world), moment)
                if cacheable:
                    times[when] = t
            x = {factor: _number(raw[spec if isinstance(spec, str) else spec.column], f"{where}, column "
                                 f"'{spec if isinstance(spec, str) else spec.column}'")
                 for factor, spec in factors.items()}
            if fit.mean:
                x["mean"] = _number(raw[fit.mean], f"{where}, column '{fit.mean}'")
            flag = raw[fit.censored] if fit.censored else False
            censored = str(flag).strip().lower() in ("1", "1.0", "true", "yes")
            rows.append(Row(t, _number(value, f"{where}, column '{fit.value}'"),
                            key_text(raw[fit.key]) if fit.key else None, x, censored, raw))
        except (ValueError, ExprError) as exc:
            raise ContractError([Issue(path, getattr(exc, "detail", str(exc)))]) from None
    if not rows:
        raise ContractError([Issue(path, "no rows to fit (check `data` and `where`)")])
    return rows


def factor_spec(fit: Any, factor: str) -> FitFactor:
    spec = fit.x[factor]
    return spec if isinstance(spec, FitFactor) else FitFactor(column=spec)


# ---------------------------------------------------------------------------
# Writing estimates back into the contract
# ---------------------------------------------------------------------------


def _error_expr(reference: str, value: Any) -> str:
    scale = f"$inputs.{UNCERTAINTY_INPUT}"
    return f"$map({reference}, $it * {scale})" if isinstance(value, list) else f"{reference} * {scale}"


def _usable(error: Any) -> bool:
    values = error if isinstance(error, list) else [error]
    return bool(values) and all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0 for v in values)


def _write_back(contract: dict[str, Any], name: str, cfg: PatternConfig, per_key: Mapping[str | None, Estimate],
                env: Any, data: str) -> dict[str, dict[str, Any]]:
    """Write one pattern's estimates into the contract; returns its number inputs with errors as normal priors."""
    spec = contract["patterns"][name]
    priors: dict[str, dict[str, Any]] = {}
    inputs = contract.setdefault("inputs", {})
    fields = sorted({f for est in per_key.values() for f in est.params})
    with_errors = sorted({f for est in per_key.values() for f, e in est.errors.items() if _usable(e)})
    if with_errors:
        inputs.setdefault(UNCERTAINTY_INPUT, {
            "type": "number", "default": 1, "min": 0,
            "description": "Scales the standard errors of fitted pattern parameters: 1 draws each run's parameters "
                           "around their estimates, 0 uses the estimates as they are."})
    method = next(iter(per_key.values())).method
    about = f"fitted by fg_env.analysis.fit_patterns from {data} ({method})"
    uncertainty = dict(spec.get("uncertainty") or {})
    if list(per_key) == [None]:
        est = per_key[None]
        for f in fields:
            inputs[f"{name}_{f}"] = _input(est.params[f], f"Pattern '{name}', {f}: {about}.")
            spec[f] = f"$inputs.{name}_{f}"
            if f in with_errors and _usable(est.errors.get(f)):
                inputs[f"{name}_{f}_se"] = _input(est.errors[f], f"Standard error of {name}_{f}.")
                uncertainty[f] = _error_expr(f"$inputs.{name}_{f}_se", est.params[f])
                if not isinstance(est.params[f], list):
                    priors[f"{name}_{f}"] = {"dist": "normal", "mean": est.params[f], "sd": est.errors[f]}
    else:
        rows = _table_rows(name, cfg, per_key, fields, with_errors, env)
        column = cfg.column or "key"
        inputs[f"{name}_fit"] = {"type": "table", "default": rows,
                                 "description": f"Pattern '{name}', one row per key: {', '.join(fields)} {about}."}
        spec["table"], spec["column"] = f"$inputs.{name}_fit", column
        for f in fields:
            spec[f] = f"$row.{f}"
            if f in with_errors:
                sample = next(est.params[f] for est in per_key.values() if f in est.params)
                uncertainty[f] = _error_expr(f"$row.{f}_se", sample)
    if uncertainty:
        spec["uncertainty"] = uncertainty
    return priors


def _input(value: Any, description: str) -> dict[str, Any]:
    return {"type": "list" if isinstance(value, list) else "number", "default": value, "description": description}


def _table_rows(name: str, cfg: PatternConfig, per_key: Mapping[str | None, Estimate], fields: list[str],
                with_errors: list[str], env: Any) -> list[dict[str, Any]]:
    """One row per key: the pattern's own table row (when it has one), with the estimates in their columns; keys
    without an estimate keep the values the contract gave them."""
    runtime = env.world.patterns
    where = f"patterns.{name}"
    column = cfg.column or "key"
    keys = list(runtime.keys(name, where)) if cfg.keyed else []
    keys += [key for key in per_key if key is not None and key not in keys]
    rows = []
    for key in keys:
        row: dict[str, Any] = dict(runtime.row(name, key, where) or {}) if cfg.table is not None and key in (
            runtime._table(name, where) if cfg.table is not None else {}) else {}
        row[column] = key
        est = per_key.get(key)
        for f in fields:
            if est is not None and f in est.params:
                row[f] = est.params[f]
            else:
                row[f] = runtime.param(name, key, f, where)
            if f in with_errors:
                error = est.errors.get(f) if est is not None else None
                row[f"{f}_se"] = error if _usable(error) else ([0.0] * len(row[f]) if isinstance(row[f], list) else 0.0)
        rows.append(row)
    return rows
