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

Rows marked ``censored`` (demand went unmet: sales capped by a stockout) are observed only as a lower bound: the fit
replaces each by its expected value given it was more than what was sold and refits, until nothing changes
(expectation–maximisation). A stockout that sold nothing still says demand was at least one, which is most of what it
tells about a slow seller. With ``fit.noise`` naming a counts pattern, the dispersion is estimated around the fitted
means — censored rows through their expected spread given that — and the fit is repeated with it.

Standard errors come from each row's observed information (a censored row's less what its censoring leaves unknown).
A profile's and a scale's errors are those of the profile rescaled to average 1 and of the level it leaves, carried
through every slot's coefficient — not the first slot's, which the coefficients are measured from. Parameters whose
drivers move together in the history (promotions that are always price cuts, promotions timed with the season or the
trend) are named in a note with how much the overlap widens their errors and their 95% ranges: the errors carry it,
and the note says why they are wide.
"""
from __future__ import annotations

import math
from typing import Any

from ..expr import ExprError, Scope, compile_expr
from . import timebase as tb
from .base import KINDS
from .compose import operand_names
from .fit import Estimate, PatternFit, Problem, Row, factor_spec, quality
from .numeric import Design, count_regression, dispersion
from .runtime import key_text

__all__ = ["fit_product"]

_REFITS = 3
#: A parameter is reported as overlapping others when their shared movement makes its variance this many times what
#: it would be were its driver unrelated to theirs — its standard error doubled.
_OVERLAP = 4.0


class _Term:
    """A block of design columns belonging to one pattern parameter (per key of that pattern)."""

    def __init__(self, pattern: str, field: str, keys: list[str | None], width: int):
        self.pattern, self.field, self.keys, self.width = pattern, field, keys, width
        self.positions = {key: position for position, key in enumerate(keys)}
        self.start = 0

    def index(self, key: str | None, slot: int = 0) -> int:
        return self.start + self.positions[key] * self.width + slot


def fit_product(problem: Problem, configs: dict[str, Any]) -> tuple[dict[str, dict[str | None, Estimate]], PatternFit]:
    cfg = problem.cfg
    fit = cfg.fit
    assert fit is not None
    rows = problem.rows
    keys: list[str | None] = list(sorted({row.key for row in rows}, key=str)) if cfg.keyed else [None]
    if cfg.keyed and fit.key is None:
        raise problem.fail("a keyed product fits from rows with a key: set `fit.key`")
    factor_keys: dict[str, list[str | None]] = {}
    row_keys: list[dict[str, str | None]] = []
    by_key: dict[str | None, dict[str, str | None]] = {}  # factor keys read only the row's key and table row
    for row in rows:
        mapping = by_key.get(row.key)
        if mapping is None:
            mapping = by_key[row.key] = {name: _factor_key(problem, configs, index, name, row)
                                         for index, name in enumerate(operand_names(cfg))}
            for factor in (fit.x or {}) if isinstance(fit.x, dict) else {}:
                mapping[factor] = _response_key(problem, configs, factor, row)
            for name, key in mapping.items():
                factor_keys.setdefault(name, [])
                if key not in factor_keys[name]:
                    factor_keys[name].append(key)
        row_keys.append(mapping)
    base_keys = _term_keys(cfg, "scale", keys)
    terms: list[_Term] = []
    assumed: list[str] = []
    notes: list[str] = []
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
    for t in terms:
        t.start = width
        width += t.width * len(t.keys)
    dips = _promotion_dips(problem, configs, responses, rows, row_keys)
    sparse: list[list[tuple[int, float]]] = []
    offsets: list[float] = []
    groups: list[int] = []
    base_index = {key: position for position, key in enumerate(base_keys)}
    origins = {term.pattern: tb.to_t(problem.clock,
                                     problem.env.world.patterns.param(term.pattern, None, "origin", term.pattern))
               for term in terms if term.field == "rate"}
    slot_at: dict[tuple[str, float], int] = {}
    for row, mapping in zip(rows, row_keys):
        columns: dict[int, float] = {}
        offset = 0.0
        groups.append(base_index[row.key if base_keys != [None] else None])
        for name in operand_names(cfg):
            term = next((t for t in terms if t.pattern == name), None)
            other = configs[name]
            if term is None:
                offset += math.log(_positive(problem, name, problem.pattern(name, mapping[name], row.t)))
            elif term.field == "profile":
                slot = slot_at.get((name, row.t))
                if slot is None:
                    slot = slot_at[(name, row.t)] = tb.slot(problem.clock, row.t, other.period, term.width + 1)
                if slot:
                    columns[term.index(_at(term, mapping[name]), slot - 1)] = 1.0
            else:
                columns[term.index(None)] = row.t - origins[name]
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
        offset += dips[len(sparse)]
        sparse.append([(j, v) for j, v in sorted(columns.items()) if v])
        offsets.append(offset)
    design = Design(sparse, width, groups, max(groups) + 1 if groups else 0)
    ys = [row.y for row in rows]
    censored = [row.censored for row in rows]
    k: float | None = None
    try:
        result = count_regression(design, ys, offset=offsets, censored=censored, errors=not fit.noise)
    except ValueError as exc:
        raise problem.fail(_confounded(terms, design, str(exc))) from None
    if fit.noise:
        for refit in range(_REFITS):
            k = dispersion(ys, result.means, censored, fitted=design.width + design.count)
            result = count_regression(design, ys, offset=offsets, censored=censored, k=k, start=result,
                                      errors=refit == _REFITS - 1)
    updates, estimates = _estimates(problem, terms, base_keys, result, rows, row_keys)
    notes.extend(_overlaps(terms, result, updates))
    if fit.noise:
        noise = Estimate({"dispersion": k if k is not None else 1e6}, {}, "method of moments around the fitted means",
                         ["dist"])
        updates[fit.noise] = {None: noise}
        notes.append(f"dispersion {k:.3g}" if k is not None else "no over-dispersion found (dispersion set very large)")
    uncensored = [(y, m) for y, m, c in zip(ys, result.means, censored) if not c]
    rmse, mape, r2 = quality([y for y, _ in uncensored], [m for _, m in uncensored])
    if any(censored):
        notes.append(f"{sum(censored)} censored row(s) fitted as lower bounds (EM, {result.iterations} iterations)")
    report = PatternFit(problem.name, cfg.kind,
                        "joint count regression (log link" + (", censored EM" if any(censored) else "") + ")",
                        len(rows), len(keys), rmse, mape, r2, estimates, assumed,
                        {name: _shown(per_key) for name, per_key in updates.items()}, notes)
    return updates, report


def _estimates(problem: Problem, terms: list[_Term], base_keys: list[str | None], result: Any, rows: list[Row],
               row_keys: list[dict[str, str | None]]) -> tuple[dict[str, dict[str | None, Estimate]], list[str]]:
    coef, se, covariance = result.coef, result.se, result.covariance
    updates: dict[str, dict[str | None, Estimate]] = {}
    level_shift: dict[tuple[str, str | None], float] = {}
    level_gradient: dict[tuple[str, str | None], dict[int, float]] = {}
    names: list[str] = []
    for term in terms:
        for key in term.keys:
            if term.field == "profile":
                columns = [term.index(key, i) for i in range(term.width)]
                raw = [1.0] + [math.exp(coef[j]) for j in columns]
                mean = sum(raw) / len(raw)
                profile = [v / mean for v in raw]
                level_shift[(term.pattern, key)] = math.log(mean)
                # the profile averages 1, so a slot's log is its coefficient less the log of the mean, and its error
                # carries every slot's — the first slot's too, the reference the coefficients are measured from
                gradient = level_gradient[(term.pattern, key)] = {j: p / len(raw) for j, p in zip(columns, profile[1:])}
                errors = [p * math.sqrt(_variance({j: (slot == i + 1) - g for i, (j, g) in enumerate(gradient.items())},
                                                  covariance)) for slot, p in enumerate(profile)]
                estimate = Estimate({"profile": profile}, {"profile": errors}, "joint count regression",
                                    ["period", "form"])
            else:
                estimate = Estimate({term.field: coef[term.index(key)]}, {term.field: se[term.index(key)]},
                                    "joint count regression", _assumed(term.field))
            updates.setdefault(term.pattern, {})[key] = estimate
        names.append(f"{term.pattern}.{term.field}")
    shift_by_key: dict[str | None, tuple[float, dict[int, float]]] = {}
    by_pattern = {term.pattern: term for term in terms}
    for mapping, row in zip(row_keys, rows):
        base = row.key if base_keys != [None] else None
        if base not in shift_by_key:
            levels = [(name, _at(by_pattern[name], key)) for name, key in mapping.items() if name in by_pattern]
            shift_by_key[base] = (sum(level_shift.get(level, 0.0) for level in levels),
                                  {j: g for level in levels for j, g in level_gradient.get(level, {}).items()})
    scale: dict[str | None, Estimate] = {}
    for group, key in enumerate(base_keys):
        shift, gradient = shift_by_key.get(key, (0.0, {}))
        value = math.exp(result.intercepts[group] + shift)
        # the scale is the level where the profiles average 1, not the first slot's level the intercept measures
        crossed = result.intercept_covariance[group]
        variance = (result.intercept_se[group] ** 2 + 2 * sum(g * crossed[j] for j, g in gradient.items())
                    + _variance(gradient, covariance))
        scale[key] = Estimate({"scale": value}, {"scale": value * math.sqrt(max(0.0, variance))},
                              "joint count regression")
    updates[problem.name] = scale
    return updates, [f"{problem.name}.scale", *names]


def _overlaps(terms: list[_Term], result: Any, updates: dict[str, dict[str | None, Estimate]]) -> list[str]:
    """Parameters the history can hardly tell apart — promotions that are always price cuts, promotions timed with the
    season or the trend — named together, with how much their overlap widens each one's standard error. The errors
    already carry it; the note says why they are wide and what data would narrow them."""
    covariance, inflation = result.covariance, result.inflation
    columns = [(term.index(key, slot), term, key) for term in terms for key in term.keys for slot in range(term.width)]
    widest: dict[str, tuple[float, int, _Term, str | None]] = {}  # each parameter's most inflated column
    for j, term, key in columns:
        name = _label(term, key)
        if inflation[j] > widest.get(name, (0.0,))[0]:
            widest[name] = (inflation[j], j, term, key)
    groups: list[dict[str, float]] = []  # parameters that overlap, each with its strongest correlation
    for name, (factor, j, term, _) in widest.items():
        pairs = [(abs(covariance[j][other]) / math.sqrt(covariance[j][j] * covariance[other][other]), _label(t, k))
                 for other, t, k in columns if t.pattern != term.pattern and covariance[j][j] > 0
                 and covariance[other][other] > 0]
        if factor < _OVERLAP or not pairs:
            continue
        correlation, partner = max(pairs, key=lambda pair: pair[0])
        joined = [group for group in groups if name in group or partner in group]
        merged = {member: value for group in joined for member, value in group.items()}
        for member in (name, partner):
            merged[member] = max(merged.get(member, 0.0), correlation)
        groups = [group for group in groups if not any(group is other for other in joined)] + [merged]
    notes = []
    for group in groups:
        widened = [f"{name} ×{math.sqrt(widest[name][0]):.1f}" for name in group if widest[name][0] >= _OVERLAP]
        ranges = [shown for shown in (_range(updates, *widest[name][2:]) for name in group) if shown]
        notes.append(f"{_join(list(group))} move together in this history (estimates correlated up to "
                     f"±{max(group.values()):.2f}), so it can hardly tell them apart: the overlap widens their "
                     f"standard errors ({', '.join(widened)}) and the fitted uncertainty carries that"
                     + (f" — 95% ranges {'; '.join(ranges)}" if ranges else "")
                     + "; history in which they vary on their own would narrow them")
    return notes


def _label(term: _Term, key: str | None) -> str:
    return f"{term.pattern}.{term.field}" + (f" ({key})" if key is not None else "")


def _range(updates: dict[str, dict[str | None, Estimate]], term: _Term, key: str | None) -> str:
    """A single-number estimate as its 95% range, for a note (a profile has no one number to show)."""
    estimate = updates[term.pattern][key]
    value, error = estimate.params[term.field], estimate.errors.get(term.field)
    if not isinstance(value, float) or not isinstance(error, float):
        return ""
    return f"{_label(term, key)} {value:.3g} ± {1.96 * error:.2g}"


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _variance(gradient: dict[int, float], covariance: list[list[float]]) -> float:
    """The variance of Σ gradient·coefficient: a linearised combination of the fitted coefficients."""
    return max(0.0, sum(a * b * covariance[i][j] for i, a in gradient.items() for j, b in gradient.items()))


def _confounded(terms: list[_Term], design: Design, reason: str) -> str:
    """Which factors the data cannot tell apart: each whose removal makes the design solvable is named."""
    from .numeric import least_squares

    culprits = []
    for term in terms:
        drop = set(range(term.start, term.start + term.width * len(term.keys)))
        kept = {j: position for position, j in enumerate(j for j in range(design.width) if j not in drop)}
        reduced = Design([[(kept[j], v) for j, v in row if j in kept] for row in design.rows], len(kept), design.groups,
                         design.count)
        try:
            least_squares(reduced, [0.0] * len(design.rows))
        except ValueError:
            continue
        culprits.append(f"{term.pattern}.{term.field}")
    if not culprits:
        return f"the data cannot estimate these factors together ({reason})"
    return (f"the data cannot tell {', '.join(culprits)} apart from the other factors: their drivers move together in "
            "these rows (a price that only changes during promotions, a trend over too short a history); fit from rows "
            "where each varies on its own, or give one of them a fixed value")


def _promotion_dips(problem: Problem, configs: dict[str, Any], responses: list[str], rows: list[Row],
                    row_keys: list[dict[str, str | None]]) -> list[float]:
    """The log of each row's post-promotion dip, from the promotions' declared ``dip`` and ``retain``: rows of one key
    in time order carry the remembered promotion pressure exactly as the memory pattern does, so a fitted lift is not
    pulled down by the weeks after a promotion."""
    offsets = [0.0] * len(rows)
    runtime = problem.env.world.patterns
    for name in responses:
        cfg = configs[name]
        if cfg.kind != "promotion":
            continue
        order = sorted(range(len(rows)), key=lambda i: (str(row_keys[i][name]), rows[i].t))
        pressure: dict[str | None, tuple[float, float]] = {}
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


def _term_keys(cfg: Any, field: str, keys: list[str | None]) -> list[str | None]:
    """The keys a parameter is estimated for: each key when the contract gives it per key ($row, $key), else one."""
    raw = getattr(cfg, field)
    text = raw if isinstance(raw, str) else ""
    return keys if cfg.keyed and ("$row" in text or "$key" in text) else [None]


def _at(term: _Term, key: str | None) -> str | None:
    return key if term.keys != [None] else None


def _assumed(field: str) -> list[str]:
    return {"rate": ["start", "origin"], "elasticity": ["reference"], "lift": ["dip", "retain"]}.get(field, [])


def _slots(problem: Problem, cfg: Any, name: str, key: str | None) -> int:
    declared = (cfg.profile if isinstance(cfg.profile, list)
                else problem.env.world.patterns.param(name, key, "profile", name))
    if not isinstance(declared, list) or len(declared) < 2:
        raise problem.fail(f"'{name}' needs a profile with its slots declared (e.g. 12 values) to be fitted")
    return len(declared)


def _factor_key(problem: Problem, configs: dict[str, Any], index: int, name: str, row: Row) -> str | None:
    item = problem.cfg.of[index]
    other = configs[name]
    if not other.keyed:
        return None
    if isinstance(item, str):
        return row.key
    return _evaluate_key(problem, item.key, row, f"of[{index}].key")


def _response_key(problem: Problem, configs: dict[str, Any], name: str, row: Row) -> str | None:
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


def _shown(per_key: dict[str | None, Estimate]) -> Any:
    if list(per_key) == [None]:
        return per_key[None].params
    return ({str(key): est.params for key, est in list(per_key.items())[:2]}
            | ({"…": f"{len(per_key)} keys"} if len(per_key) > 2 else {}))


def _unused(kinds: Any = KINDS) -> None:  # pragma: no cover
    return None
