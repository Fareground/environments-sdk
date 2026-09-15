"""Fitting a product jointly: base × factors × responses, estimated together so no factor absorbs another's effect.

The model is log E[value] = log base(key) + Σ factor terms, fitted as a count regression with a log link (Poisson
quasi-likelihood; negative binomial once its dispersion is known), so demand noise, zeros and stockouts are handled
the way counts behave. Terms by kind:

* the product's ``scale`` — one base per key of the product (or one overall), absorbed as group intercepts;
* a ``seasonal`` factor with a ``profile`` — one term per slot but the first, per key of the factor; the profile is
  then rescaled to average 1 and the bases take up the level;
* an ``exponential`` ``trend`` — its rate (start and origin assumed);
* an ``elasticity`` (constant) named in ``fit.x`` — its elasticity per key, on log(price / reference);
* a ``promotion`` (exponential) named in ``fit.x`` — its lift per key, on the promotion intensity; its declared dip
  and retain are applied to the weeks after each promotion (assumed, not estimated);
* any other factor — held at its declared values (an offset), and reported as assumed.

Rows marked ``censored`` (sales capped by a stockout) are observed only as a lower bound: the fit replaces each by
its expected value given it was at least what was sold and refits, until nothing changes (expectation–maximisation).
With ``fit.noise`` naming a counts pattern, the dispersion is estimated around the fitted means — censored rows
through their expected spread given what was sold — and the fit is repeated with it.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from ..expr import ExprError, Scope, compile_expr
from . import timebase as tb
from .base import KINDS
from .compose import operand_names
from .fit import Estimate, PatternFit, Problem, Row, factor_spec, quality
from .numeric import count_regression, dispersion
from .runtime import key_text

__all__ = ["fit_product"]

_REFITS = 3


class _Term:
    """A block of design columns belonging to one pattern parameter (per key of that pattern)."""

    def __init__(self, pattern: str, field: str, keys: List[Optional[str]], width: int):
        self.pattern, self.field, self.keys, self.width = pattern, field, keys, width
        self.start = 0

    def index(self, key: Optional[str], slot: int = 0) -> int:
        return self.start + self.keys.index(key) * self.width + slot


def fit_product(problem: Problem, configs: Dict[str, Any]) -> Tuple[Dict[str, Dict[Optional[str], Estimate]], PatternFit]:
    cfg = problem.cfg
    fit = cfg.fit
    assert fit is not None
    rows = problem.rows
    keys: List[Optional[str]] = list(sorted({row.key for row in rows}, key=str)) if cfg.keyed else [None]
    if cfg.keyed and fit.key is None:
        raise problem.fail("a keyed product fits from rows with a key: set `fit.key`")
    factor_keys: Dict[str, List[Optional[str]]] = {}
    row_keys: List[Dict[str, Optional[str]]] = []
    for row in rows:
        mapping = {name: _factor_key(problem, configs, index, name, row) for index, name in enumerate(operand_names(cfg))}
        for factor in (fit.x or {}) if isinstance(fit.x, dict) else {}:
            mapping[factor] = _response_key(problem, configs, factor, row)
        row_keys.append(mapping)
        for name, key in mapping.items():
            factor_keys.setdefault(name, [])
            if key not in factor_keys[name]:
                factor_keys[name].append(key)
    base_keys = _term_keys(cfg, "scale", keys)
    terms: List[_Term] = []
    assumed: List[str] = []
    notes: List[str] = []
    for name in operand_names(cfg):
        other = configs[name]
        if other.kind == "seasonal" and other.profile is not None and other.form == "multiply":
            slots = _slots(problem, other, name, factor_keys[name][0])
            terms.append(_Term(name, "profile", _term_keys(other, "profile", factor_keys[name]), slots - 1))
        elif other.kind == "trend" and other.form == "exponential" and not other.keyed:
            terms.append(_Term(name, "rate", [None], 1))
        else:
            assumed.append(f"{name} (held at its declared values)")
    responses = list(fit.x) if isinstance(fit.x, dict) else []
    for name in responses:
        other = configs.get(name)
        if other is None:
            raise problem.fail(f"`fit.x` names '{name}', which is not a pattern")
        if other.kind == "elasticity" and other.form == "constant":
            terms.append(_Term(name, "elasticity", _term_keys(other, "elasticity", factor_keys[name]), 1))
        elif other.kind == "promotion" and other.form == "exponential":
            terms.append(_Term(name, "lift", _term_keys(other, "lift", factor_keys[name]), 1))
        else:
            assumed.append(f"{name} (a {other.kind} is held at its declared values in a product fit)")
    width = 0
    for term in terms:
        term.start = width
        width += term.width * len(term.keys)
    dips = _promotion_dips(problem, configs, responses, rows, row_keys)
    design: List[List[float]] = []
    offsets: List[float] = []
    groups: List[int] = []
    for row, mapping in zip(rows, row_keys):
        columns = [0.0] * width
        offset = 0.0
        groups.append(base_keys.index(row.key if base_keys != [None] else None))
        for name in operand_names(cfg):
            term = next((t for t in terms if t.pattern == name), None)
            other = configs[name]
            if term is None:
                offset += math.log(_positive(problem, name, problem.pattern(name, mapping[name], row.t)))
            elif term.field == "profile":
                slot = tb.slot(problem.clock, row.t, other.period, term.width + 1)
                if slot:
                    columns[term.index(_at(term, mapping[name]), slot - 1)] = 1.0
            else:
                columns[term.index(None)] = row.t - tb.to_t(problem.clock, problem.env.world.patterns.param(name, None, "origin", name))
        for name in responses:
            term = next((t for t in terms if t.pattern == name), None)
            driver = row.x[name]
            if term is None:
                offset += math.log(_positive(problem, name, problem.pattern(name, mapping[name], row.t, (driver,))))
            elif term.field == "elasticity":
                reference = problem.env.world.patterns.param(name, mapping[name], "reference", name)
                if driver <= 0:
                    raise problem.fail(f"a price of {driver:g} at t = {row.t:g}: prices must be above 0")
                columns[term.index(_at(term, mapping[name]))] = math.log(driver / reference)
            else:
                columns[term.index(_at(term, mapping[name]))] = driver
        offset += dips[len(design)]
        design.append(columns)
        offsets.append(offset)
    ys = [row.y for row in rows]
    censored = [row.censored for row in rows]
    k: Optional[float] = None
    try:
        result = count_regression(design, ys, offset=offsets, censored=censored, groups=groups)
    except ValueError as exc:
        raise problem.fail(_confounded(terms, design, groups, str(exc))) from None
    if fit.noise:
        for _ in range(_REFITS):
            k = dispersion(ys, result.means, censored)
            result = count_regression(design, ys, offset=offsets, censored=censored, k=k, groups=groups)
    updates, estimates = _estimates(problem, terms, base_keys, result, rows, row_keys)
    if fit.noise:
        noise = Estimate({"dispersion": k if k is not None else 1e6}, {}, "method of moments around the fitted means", ["dist"])
        updates[fit.noise] = {None: noise}
        notes.append(f"dispersion {k:.3g}" if k is not None else "no over-dispersion found (dispersion set very large)")
    uncensored = [(y, m) for y, m, c in zip(ys, result.means, censored) if not c]
    rmse, mape, r2 = quality([y for y, _ in uncensored], [m for _, m in uncensored])
    if any(censored):
        notes.append(f"{sum(censored)} censored row(s) fitted as lower bounds (EM, {result.iterations} iterations)")
    report = PatternFit(problem.name, cfg.kind, "joint count regression (log link" + (", censored EM" if any(censored) else "") + ")",
                        len(rows), len(keys), rmse, mape, r2, estimates, assumed,
                        {name: _shown(per_key) for name, per_key in updates.items()}, notes)
    return updates, report


def _estimates(problem: Problem, terms: List[_Term], base_keys: List[Optional[str]], result: Any, rows: List[Row],
               row_keys: List[Dict[str, Optional[str]]]) -> Tuple[Dict[str, Dict[Optional[str], Estimate]], List[str]]:
    coef, se = result.coef, result.se
    updates: Dict[str, Dict[Optional[str], Estimate]] = {}
    level_shift: Dict[Tuple[str, Optional[str]], float] = {}
    names: List[str] = []
    for term in terms:
        for key in term.keys:
            if term.field == "profile":
                logs = [0.0] + [coef[term.index(key, i)] for i in range(term.width)]
                errors = [0.0] + [se[term.index(key, i)] for i in range(term.width)]
                raw = [math.exp(v) for v in logs]
                mean = sum(raw) / len(raw)
                profile = [v / mean for v in raw]
                level_shift[(term.pattern, key)] = math.log(mean)
                estimate = Estimate({"profile": profile}, {"profile": [p * e for p, e in zip(profile, errors)]},
                                    "joint count regression", ["period", "form"])
            else:
                estimate = Estimate({term.field: coef[term.index(key)]}, {term.field: se[term.index(key)]},
                                    "joint count regression", _assumed(term.field))
            updates.setdefault(term.pattern, {})[key] = estimate
        names.append(f"{term.pattern}.{term.field}")
    shift_by_key: Dict[Optional[str], float] = {}
    by_pattern = {term.pattern: term for term in terms}
    for mapping, row in zip(row_keys, rows):
        shift = sum(level_shift.get((name, _at(by_pattern[name], key)), 0.0) for name, key in mapping.items() if name in by_pattern)
        shift_by_key.setdefault(row.key if base_keys != [None] else None, shift)
    scale: Dict[Optional[str], Estimate] = {}
    for group, key in enumerate(base_keys):
        value = math.exp(result.intercepts[group] + shift_by_key.get(key, 0.0))
        scale[key] = Estimate({"scale": value}, {"scale": value * result.intercept_se[group]}, "joint count regression")
    updates[problem.name] = scale
    return updates, [f"{problem.name}.scale", *names]


def _confounded(terms: List[_Term], design: List[List[float]], groups: List[int], reason: str) -> str:
    """Which factors the data cannot tell apart: each whose removal makes the design solvable is named."""
    from .numeric import least_squares

    culprits = []
    for term in terms:
        drop = set(range(term.start, term.start + term.width * len(term.keys)))
        kept = [j for j in range(len(design[0]) if design else 0) if j not in drop]
        try:
            least_squares([[row[j] for j in kept] for row in design], [0.0] * len(design), groups=groups)
        except ValueError:
            continue
        culprits.append(f"{term.pattern}.{term.field}")
    if not culprits:
        return f"the data cannot estimate these factors together ({reason})"
    return (f"the data cannot tell {', '.join(culprits)} apart from the other factors: their drivers move together in "
            "these rows (a price that only changes during promotions, a trend over too short a history); fit from rows "
            "where each varies on its own, or give one of them a fixed value")


def _promotion_dips(problem: Problem, configs: Dict[str, Any], responses: List[str], rows: List[Row],
                    row_keys: List[Dict[str, Optional[str]]]) -> List[float]:
    """The log of each row's post-promotion dip, from the promotions' declared ``dip`` and ``retain``: rows of one key in
    time order carry the remembered promotion pressure exactly as the memory pattern does, so a fitted lift is not
    pulled down by the weeks after a promotion."""
    offsets = [0.0] * len(rows)
    runtime = problem.env.world.patterns
    for name in responses:
        cfg = configs[name]
        if cfg.kind != "promotion":
            continue
        order = sorted(range(len(rows)), key=lambda i: (str(row_keys[i][name]), rows[i].t))
        pressure: Dict[Optional[str], Tuple[float, float]] = {}
        for index in order:
            key = row_keys[index][name]
            dip = float(runtime.param(name, key, "dip", name))
            retain = 0.5 ** (1 / float(runtime.param(name, key, "half_life", name))) if cfg.half_life is not None \
                else float(runtime.param(name, key, "retain", name))
            every = float(cfg.every) if cfg.every is not None else float(problem.clock.step)
            row, x = rows[index], rows[index].x[name]
            if key in pressure:
                carried, last = pressure[key]
                carried *= retain ** max(0.0, (row.t - last) / every)
            else:
                carried = 0.0
            if x <= 0 and dip:
                offsets[index] += math.log(max(1e-9, 1 - dip * min(1.0, carried)))
            pressure[key] = (carried + x, row.t)
    return offsets


def _term_keys(cfg: Any, field: str, keys: List[Optional[str]]) -> List[Optional[str]]:
    """The keys a parameter is estimated for: each key when the contract gives it per key ($row, $key), else one."""
    raw = getattr(cfg, field)
    text = raw if isinstance(raw, str) else ""
    return keys if cfg.keyed and ("$row" in text or "$key" in text) else [None]


def _at(term: _Term, key: Optional[str]) -> Optional[str]:
    return key if term.keys != [None] else None


def _assumed(field: str) -> List[str]:
    return {"rate": ["start", "origin"], "elasticity": ["reference"], "lift": ["dip", "retain"]}.get(field, [])


def _slots(problem: Problem, cfg: Any, name: str, key: Optional[str]) -> int:
    declared = cfg.profile if isinstance(cfg.profile, list) else problem.env.world.patterns.param(name, key, "profile", name)
    if not isinstance(declared, list) or len(declared) < 2:
        raise problem.fail(f"'{name}' needs a profile with its slots declared (e.g. 12 values) to be fitted")
    return len(declared)


def _factor_key(problem: Problem, configs: Dict[str, Any], index: int, name: str, row: Row) -> Optional[str]:
    item = problem.cfg.of[index]
    other = configs[name]
    if not other.keyed:
        return None
    if isinstance(item, str):
        return row.key
    return _evaluate_key(problem, item.key, row, f"of[{index}].key")


def _response_key(problem: Problem, configs: Dict[str, Any], name: str, row: Row) -> Optional[str]:
    spec = factor_spec(problem.cfg.fit, name)
    other = configs.get(name)
    if other is None or not other.keyed:
        return None
    if spec.key is None:
        return row.key
    return _evaluate_key(problem, spec.key, row, f"fit.x.{name}.key")


def _evaluate_key(problem: Problem, source: str, row: Row, where: str) -> str:
    runtime = problem.env.world.patterns
    table_row = runtime.row(problem.name, row.key, problem.name) if problem.cfg.table is not None and row.key else None
    scope = Scope({"inputs": problem.env.inputs, "key": row.key, "row": table_row}, problem.env.world)
    try:
        return key_text(compile_expr(source)(scope))
    except (ExprError, ValueError) as exc:
        raise problem.fail(f"`{where}`: {getattr(exc, 'detail', exc)}") from None


def _positive(problem: Problem, name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise problem.fail(f"'{name}' gave {value!r}; a factor of a product must be above 0 to be fitted")
    return float(value)


def _shown(per_key: Dict[Optional[str], Estimate]) -> Any:
    if list(per_key) == [None]:
        return per_key[None].params
    return {str(key): est.params for key, est in list(per_key.items())[:2]} | ({"…": f"{len(per_key)} keys"} if len(per_key) > 2 else {})


def _unused(kinds: Any = KINDS) -> None:  # pragma: no cover
    return None
