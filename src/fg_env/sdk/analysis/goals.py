"""What an optimiser aims for: objectives and constraints over a candidate's runs, with intervals.

Objectives are written ``"maximise margin"``, ``"minimise staffing_cost"`` or ``"maximise p10 of margin"`` (risk
averse: the 10th percentile over runs); the statistic is ``mean`` (the default), ``median`` or ``p1``…``p99``.
Constraints are ``"fill_rate >= 0.95"`` (the mean over runs), ``"p10 of fill_rate >= 0.9"`` (a statistic over runs)
or ``"sl >= 0.8 in 90% of runs"`` (the share of runs where it holds). A measure is an output or a metric (its final
value) by name, or an expression over ``$outputs``, ``$metrics`` and ``$inputs``: ``"maximise $outputs.revenue -
$outputs.holding_cost"``.

Every value comes with a 95% interval: Student-t for a mean, Wilson for a share of runs, a seeded bootstrap for a
percentile. Candidates run on the same seeds, so :func:`paired` compares two of them run by run.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..expr import ExprError, Scope, compile_expr, evaluate
from ..measure import RunResult
from . import runner
from .stats import estimate, mean, numeric, quantile, wilson

__all__ = ["Stat", "Measure", "Objective", "Constraint", "Assessment", "parse_objectives", "parse_constraints",
           "assess", "rank", "paired", "dominates"]

#: Bootstrap resamples behind a percentile's interval.
_BOOTSTRAP = 200
#: Most objectives a Pareto frontier is traced for.
MAX_OBJECTIVES = 3
_OBJECTIVE = re.compile(r"^\s*(maximi[sz]e|max|minimi[sz]e|min)\s+(.+?)\s*$", re.IGNORECASE)
_STAT = re.compile(r"^\s*(mean|median|p(\d{1,2}))\s+of\s+(.+?)\s*$", re.IGNORECASE)
_SHARE = re.compile(r"^(.*\S)\s+in\s+(\d+(?:\.\d+)?)\s*%\s+of\s+runs\s*$", re.IGNORECASE)
_OPERATORS = (">=", "<=", ">", "<")
_COMPARE = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}

Key = Tuple[int, float]


@dataclass(frozen=True)
class Stat:
    """How runs are summarised: ``mean``, ``median`` or a percentile ``pNN``."""

    name: str
    q: Optional[float] = None

    @classmethod
    def parse(cls, text: str) -> "Stat":
        word = text.lower()
        if word == "mean":
            return cls("mean")
        if word == "median":
            return cls("median", 0.5)
        if re.fullmatch(r"p\d{1,2}", word) and 1 <= int(word[1:]) <= 99:
            return cls(word, int(word[1:]) / 100)
        raise ValueError(f"unknown statistic '{text}': use mean, median or p1…p99")

    def of(self, values: Sequence[float]) -> float:
        return mean(values) if self.q is None else quantile(values, self.q)

    def interval(self, values: Sequence[float], rng: random.Random) -> Tuple[Optional[float], Optional[float]]:
        if len(values) < 2:
            return None, None
        if self.q is None:
            e = estimate(values)
            return e.low, e.high
        draws = sorted(self.of([values[rng.randrange(len(values))] for _ in values]) for _ in range(_BOOTSTRAP))
        return quantile(draws, 0.025), quantile(draws, 0.975)


@dataclass(frozen=True)
class Measure:
    """An output or metric by name, or an expression over ``$outputs``, ``$metrics`` and ``$inputs``."""

    text: str
    named: Optional[Tuple[str, str]] = None

    @classmethod
    def parse(cls, contract: Any, text: str, where: str) -> "Measure":
        text = text.strip()
        if "$" not in text:
            try:
                return cls(text, runner.resolve_measure(contract, text))
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}; or write an expression such as $outputs.x - $outputs.y") from None
        try:
            compile_expr(text)
        except ExprError as exc:
            raise ValueError(f"{where}: {exc}") from None
        return cls(text)

    def per_run(self, runs: Sequence[RunResult]) -> List[Optional[float]]:
        """One number per run (``None`` for a failed run); raises when completed runs give no number at all."""
        out: List[Optional[float]] = []
        shown = None
        for r in runs:
            if r.status == "failed":
                out.append(None)
                continue
            raw = runner.raw_value(r, self.named) if self.named else self._evaluate(r)
            number = numeric(raw)
            shown = shown if number is not None else raw
            out.append(number)
        if shown is not None and all(v is None for v in out):
            raise ValueError(f"'{self.text}' gives {type(shown).__name__} {str(shown)[:60]!r}, not a number; "
                             "pick a numeric output or write an expression such as $outputs.by_key.x")
        return out

    def _evaluate(self, r: RunResult) -> Any:
        try:
            return evaluate(self.text, Scope({"outputs": r.outputs, "metrics": r.metrics, "inputs": r.inputs}))
        except ExprError as exc:
            raise ValueError(f"'{self.text}': {exc}") from None


@dataclass(frozen=True)
class Objective:
    sense: int  # +1 maximise, -1 minimise
    stat: Stat
    measure: Measure

    @property
    def text(self) -> str:
        verb = "maximise" if self.sense > 0 else "minimise"
        return f"{verb} {self.stat.name} of {self.measure.text}"


@dataclass(frozen=True)
class Constraint:
    text: str
    measure: Measure
    op: str
    bound: float
    stat: Stat
    share: Optional[float] = None  # the share of runs it must hold in; None = a statistic over runs

    def holds(self, value: float) -> bool:
        return bool(_COMPARE[self.op](value, self.bound))


def parse_objectives(contract: Any, objective: Any) -> List[Objective]:
    items = [objective] if isinstance(objective, str) else objective
    if not isinstance(items, Sequence) or isinstance(items, str) or not items or len(items) > MAX_OBJECTIVES \
            or not all(isinstance(i, str) for i in items):
        raise ValueError(f"objective must be text like 'maximise margin', or a list of 2 to {MAX_OBJECTIVES} for a "
                         f"Pareto frontier; got {objective!r}")
    out = []
    for text in items:
        match = _OBJECTIVE.match(text)
        if not match:
            raise ValueError(f"objective '{text}': start with maximise or minimise, e.g. 'maximise margin' or "
                             "'minimise p90 of cost'")
        sense = 1 if match.group(1).lower().startswith("max") else -1
        stat, rest = _stat_prefix(match.group(2))
        out.append(Objective(sense, stat, Measure.parse(contract, rest, f"objective '{text}'")))
    return out


def parse_constraints(contract: Any, constraints: Any) -> List[Constraint]:
    items = [constraints] if isinstance(constraints, str) else list(constraints or [])
    return [_constraint(contract, text) for text in items]


def _constraint(contract: Any, text: Any) -> Constraint:
    if not isinstance(text, str):
        raise ValueError(f"a constraint is text like 'fill_rate >= 0.95' or 'sl >= 0.8 in 90% of runs', got {text!r}")
    body, share = text, None
    match = _SHARE.match(text)
    if match:
        body, percent = match.group(1), float(match.group(2))
        if not 0 < percent <= 100:
            raise ValueError(f"constraint '{text}': the share of runs must be above 0% and at most 100%")
        share = percent / 100
    left, op, right = _split_comparison(body, text)
    try:
        bound = float(right)
    except ValueError:
        raise ValueError(f"constraint '{text}': the right side must be a number (move expressions to the left), "
                         f"got {right!r}") from None
    stat, rest = _stat_prefix(left)
    if share is not None and stat.name != "mean":
        raise ValueError(f"constraint '{text}': give a statistic or a share of runs, not both")
    return Constraint(text.strip(), Measure.parse(contract, rest, f"constraint '{text}'"), op, bound, stat, share)


def _stat_prefix(text: str) -> Tuple[Stat, str]:
    match = _STAT.match(text)
    return (Stat.parse(match.group(1)), match.group(3)) if match else (Stat("mean"), text)


def _split_comparison(body: str, text: str) -> Tuple[str, str, str]:
    """The last top-level comparison: ``left op right`` (brackets and quotes skipped)."""
    depth, quote, found = 0, "", None
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch in "<>":
            op = body[i:i + 2] if body[i + 1:i + 2] == "=" else ch
            found = (i, op)
            i += len(op) - 1
        i += 1
    if found is None or not body[:found[0]].strip() or not body[found[0] + len(found[1]):].strip():
        raise ValueError(f"constraint '{text}': write it as '<output or $expression> >= <number>' "
                         f"(operators {', '.join(_OPERATORS)}; add 'in 90% of runs' for a share)")
    at, op = found
    return body[:at].strip(), op, body[at + len(op):].strip()


@dataclass(frozen=True)
class Assessment:
    """One candidate on one set of seeds: objective and constraint values with intervals."""

    objectives: Tuple[Dict[str, Any], ...]
    constraints: Tuple[Dict[str, Any], ...]
    senses: Tuple[int, ...]
    runs: int
    failed: int

    @property
    def usable(self) -> bool:
        return all(o["value"] is not None for o in self.objectives) and all(
            c["value"] is not None for c in self.constraints)

    @property
    def feasible(self) -> bool:
        return self.usable and all(c["met"] for c in self.constraints)

    @property
    def violation(self) -> float:
        """How far the constraints are from being met, each shortfall on its own scale, added up."""
        return math.fsum(c["violation"] for c in self.constraints if c["violation"] is not None)

    def to_dict(self) -> Dict[str, Any]:
        return {"objectives": list(self.objectives), "constraints": list(self.constraints), "feasible": self.feasible,
                "runs": self.runs, "failed": self.failed}


def assess(objectives: Sequence[Objective], constraints: Sequence[Constraint], runs: Sequence[RunResult],
           rng: random.Random) -> Assessment:
    rows = []
    for objective in objectives:
        values = [v for v in objective.measure.per_run(runs) if v is not None]
        low, high = objective.stat.interval(values, rng)
        rows.append({"objective": objective.text, "value": objective.stat.of(values) if values else None, "low": low,
                     "high": high, "n": len(values)})
    checks = [_check(c, runs, rng) for c in constraints]
    return Assessment(tuple(rows), tuple(checks), tuple(o.sense for o in objectives), len(runs),
                      sum(1 for r in runs if r.status == "failed"))


def _check(c: Constraint, runs: Sequence[RunResult], rng: random.Random) -> Dict[str, Any]:
    values = [v for v in c.measure.per_run(runs) if v is not None]
    row: Dict[str, Any] = {"constraint": c.text, "bound": c.bound, "op": c.op, "n": len(values)}
    if not values:
        return {**row, "value": None, "low": None, "high": None, "met": False, "shortfall": None, "violation": None}
    if c.share is not None:
        held = sum(1 for v in values if c.holds(v))
        share = held / len(values)
        low, high = wilson(held, len(values))
        shortfall = max(0.0, c.share - share)
        return {**row, "share_needed": c.share, "value": share, "low": low, "high": high, "met": share >= c.share,
                "shortfall": shortfall, "violation": shortfall}
    value = c.stat.of(values)
    low, high = c.stat.interval(values, rng)
    met = c.holds(value)
    shortfall = 0.0 if met else abs(c.bound - value)
    scale = abs(c.bound) or 1.0
    return {**row, "stat": c.stat.name, "value": value, "low": low, "high": high, "met": met, "shortfall": shortfall,
            "violation": 0.0 if met else max(shortfall / scale, 1e-12)}


def rank(a: Assessment) -> Key:
    """Order for one objective: feasible candidates by the objective, then the rest by how far they miss."""
    if not a.usable:
        return (2, 0.0)
    if not a.feasible:
        return (1, a.violation)
    return (0, -a.senses[0] * a.objectives[0]["value"])


def dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    """``a`` is at least as good as ``b`` everywhere and better somewhere (values already signed so larger is better)."""
    return all(x >= y for x, y in zip(a, b)) and any(x > y for x, y in zip(a, b))


def paired(objective: Objective, runs_a: Sequence[RunResult], runs_b: Sequence[RunResult], rng: random.Random,
           signed: bool = True) -> Optional[Dict[str, Any]]:
    """``a`` minus ``b`` on the objective, run by run on shared seeds, with a 95% interval (``None`` without pairs).

    ``signed`` turns the difference so that positive means ``a`` is better; otherwise it is the raw change."""
    sign = objective.sense if signed else 1
    pairs = [(x, y) for x, y in zip(objective.measure.per_run(runs_a), objective.measure.per_run(runs_b))
             if x is not None and y is not None]
    if not pairs:
        return None
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    point = sign * (objective.stat.of(xs) - objective.stat.of(ys))
    if len(pairs) < 2:
        low = high = None
    elif objective.stat.q is None:
        e = estimate([sign * (x - y) for x, y in pairs])
        low, high = e.low, e.high
    else:
        draws = []
        for _ in range(_BOOTSTRAP):
            picks = [rng.randrange(len(pairs)) for _ in pairs]
            draws.append(sign * (objective.stat.of([xs[i] for i in picks]) - objective.stat.of([ys[i] for i in picks])))
        draws.sort()
        low, high = quantile(draws, 0.025), quantile(draws, 0.975)
    clear = low is not None and high is not None and (low > 0 or high < 0)
    return {"mean": point, "low": low, "high": high, "n": len(pairs), "clear": clear}
