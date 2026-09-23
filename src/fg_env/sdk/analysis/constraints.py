"""Constraints an optimiser must meet: over a measure's runs, per key of a list or map, and with a stated confidence.

Written as text:

* ``"fill_rate >= 0.95"`` — the mean over runs; ``"p10 of fill_rate >= 0.9"`` — a statistic over runs;
* ``"sl >= 0.8 in 90% of runs"`` — the share of runs where it holds;
* ``"each service_level_by_interval >= 0.8"`` — every key of a list or map output on its own (a half-hour, a SKU);
  ``"at least 20 of …"`` and ``"at most 2 of sl_by_interval < 0.8"`` — how many keys must (or may) meet it; an *at most*
  is judged as its complement (at least all but 2 keys with ``sl_by_interval >= 0.8``);
* ``"… with 95% confidence"`` — how sure the verdict must be (otherwise the optimiser's ``confidence``).

Every constraint is judged three ways from its runs:

* *met* — the estimate is on the right side of the bound;
* *confident* — a one-sided bound at the confidence (Student-t for a mean, Wilson for a share of runs, the bootstrap's
  spread for a percentile) is on the right side too, so fresh seeds would rarely disagree;
* *clearly missed* — even the optimistic one-sided bound is on the wrong side.

A decision meets its constraints with confidence when every one is confident, is infeasible when one is clearly missed,
and borderline in between. Per key, the counts of confident and plausible keys decide the same way; every key reports
its *slack* (how many standard errors it sits on the right side of the bound), and the *binding* keys are those not
confident plus those within one standard error of the tightest — the keys that decide the plan. A search may ask for more than the confidence:
``margin`` multiplies the one-sided bound's distance (see :mod:`.optimise`).
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..measure import RunResult
from .goals import Measure, Stat, stat_prefix
from .stats import normal_quantile, t_quantile, wilson

__all__ = ["Constraint", "Standard", "parse_constraints", "check", "VERDICTS"]

_SHARE = re.compile(r"^(.*\S)\s+in\s+(\d+(?:\.\d+)?)\s*%\s+of\s+runs\s*$", re.IGNORECASE)
_CONFIDENCE = re.compile(r"^(.*\S)\s+with\s+(\d+(?:\.\d+)?)\s*%\s+confidence\s*$", re.IGNORECASE)
_KEYED = re.compile(r"^\s*(?:(each)|at\s+(least|most)\s+(\d+)\s+of)\s+(.+)$", re.IGNORECASE)
_OPERATORS = (">=", "<=", ">", "<")
_COMPARE = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b}
_COMPLEMENT = {">=": "<", "<=": ">", ">": "<=", "<": ">="}
#: A decision's verdict on its constraints, best first.
VERDICTS = ("feasible", "borderline", "infeasible")


@dataclass(frozen=True)
class Standard:
    """How sure a check must be: the confidence of its one-sided bounds, and how many times that bound's distance a
    decision must clear to *pass* (1: exactly the confidence; a search asks for more, see :mod:`.optimise`)."""

    confidence: float
    margin: float = 1.0


@dataclass(frozen=True)
class Constraint:
    text: str
    measure: Measure
    op: str
    bound: float
    stat: Stat
    share: Optional[float] = None  # the share of runs it must hold in; None = a statistic over runs
    confidence: Optional[float] = None  # stated in the text; None = the optimiser's
    #: Per key: None for one number per run, else "each", "least" or "most" (with ``count``).
    keys: Optional[str] = None
    count: int = 0

    def holds(self, value: float) -> bool:
        return bool(_COMPARE[self.op](value, self.bound))


def parse_constraints(contract: Any, constraints: Any) -> List[Constraint]:
    items = [constraints] if isinstance(constraints, str) else list(constraints or [])
    return [_constraint(contract, text) for text in items]


def _constraint(contract: Any, text: Any) -> Constraint:
    if not isinstance(text, str):
        raise ValueError(f"a constraint is text like 'fill_rate >= 0.95' or 'sl >= 0.8 in 90% of runs', got {text!r}")
    body, confidence = text, None
    match = _CONFIDENCE.match(body)
    if match:
        body, confidence = match.group(1), float(match.group(2)) / 100
        if not 0.5 < confidence < 1:
            raise ValueError(f"constraint '{text}': the confidence must be above 50% and below 100%")
    share = None
    match = _SHARE.match(body)
    if match:
        body, share = match.group(1), float(match.group(2)) / 100
        if not 0 < share <= 1:
            raise ValueError(f"constraint '{text}': the share of runs must be above 0% and at most 100%")
    keys, count = None, 0
    match = _KEYED.match(body)
    if match:
        keys = "each" if match.group(1) else match.group(2).lower()
        count, body = int(match.group(3) or 0), match.group(4)
    left, op, right = _split_comparison(body, text)
    try:
        bound = float(right)
    except ValueError:
        raise ValueError(f"constraint '{text}': the right side must be a number (move expressions to the left), "
                         f"got {right!r}") from None
    stat, rest = stat_prefix(left)
    if share is not None and stat.name != "mean":
        raise ValueError(f"constraint '{text}': give a statistic or a share of runs, not both")
    return Constraint(text.strip(), Measure.parse(contract, rest, f"constraint '{text}'"), op, bound, stat, share,
                      confidence, keys, count)


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
        raise ValueError(f"constraint '{text}': write it as '[each] <output or $expression> >= <number>' "
                         f"(operators {', '.join(_OPERATORS)}; add 'in 90% of runs' for a share and "
                         "'with 90% confidence' for how sure)")
    at, op = found
    return body[:at].strip(), op, body[at + len(op):].strip()


def check(c: Constraint, runs: Sequence[RunResult], standard: Standard, rng: random.Random) -> Dict[str, Any]:
    """One constraint over a decision's runs: the estimate with its 95% interval and the three verdicts."""
    level = c.confidence if c.confidence is not None else standard.confidence
    row: Dict[str, Any] = {"constraint": c.text, "bound": c.bound, "op": c.op, "confidence": level}
    if c.keys is None:
        values = [v for v in c.measure.per_run(runs) if v is not None]
        row["n"] = len(values)
        if not values:
            return {**row, **_EMPTY}
        return {**row, **_judge(c, c.op, values, level, standard.margin, rng)}
    return {**row, **_keyed(c, runs, level, standard.margin, rng)}


_EMPTY: Dict[str, Any] = {"value": None, "low": None, "high": None, "met": False, "passes": False, "confident": False,
                          "clearly_missed": False, "verdict": "infeasible", "shortfall": None, "violation": None}


def _verdict(confident: bool, missed: bool) -> str:
    return "feasible" if confident else ("infeasible" if missed else "borderline")


def _judge(c: Constraint, op: str, values: List[float], level: float, margin: float,
           rng: random.Random) -> Dict[str, Any]:
    compare = _COMPARE[op]
    if c.share is not None:
        return _judge_share(c, compare, values, level, margin)
    n = len(values)
    value = c.stat.of(values)
    low, high, se = c.stat.spread(values, rng)
    z = t_quantile(level, n - 1) if c.stat.q is None and n > 1 else normal_quantile(level)
    toward = 1 if op in (">=", ">") else -1  # the side the bound must be on

    def bound_at(distance: float) -> float:
        return value - toward * distance * (se or 0.0)

    met, passes = compare(value, c.bound), se is not None and compare(bound_at(margin * z), c.bound)
    confident = se is not None and compare(bound_at(z), c.bound)
    missed = se is not None and not compare(bound_at(-z), c.bound)  # one run cannot show a miss is not luck
    reached = bound_at(margin * z) if se is not None else value
    scale = abs(c.bound) or 1.0
    return {"stat": c.stat.name, "n": n, "value": value, "low": low, "high": high, "se": se,
            "slack": _slack(toward * (value - c.bound), se), "met": met,
            "passes": passes or (se is None and met), "confident": confident, "clearly_missed": missed,
            "verdict": _verdict(confident, missed), "shortfall": 0.0 if met else abs(c.bound - value),
            "violation": 0.0 if passes else max(abs(c.bound - reached) / scale, 1e-12)}


def _judge_share(c: Constraint, compare: Any, values: List[float], level: float, margin: float) -> Dict[str, Any]:
    assert c.share is not None
    n = len(values)
    held = sum(1 for v in values if compare(v, c.bound))
    share = held / n
    low, high = wilson(held, n)
    z = normal_quantile(level)
    cautious = _wilson_side(held, n, margin * z, lower=True)
    confident = _wilson_side(held, n, z, lower=True) >= c.share
    missed = _wilson_side(held, n, z, lower=False) < c.share
    se = math.sqrt(c.share * (1 - c.share) / n)
    return {"share_needed": c.share, "n": n, "value": share, "low": low, "high": high,
            "slack": _slack(share - c.share, se), "met": share >= c.share,
            "passes": cautious >= c.share, "confident": confident, "clearly_missed": missed,
            "verdict": _verdict(confident, missed), "shortfall": max(0.0, c.share - share),
            "violation": max(0.0, c.share - cautious)}


def _slack(room: float, se: Optional[float]) -> float:
    """How far a value is on the right side of its bound (negative: the wrong side), in standard errors; a value without
    noise is infinitely far unless it sits exactly on the bound."""
    if se:
        return room / se
    return 0.0 if room == 0 else math.copysign(math.inf, room)


def _wilson_side(successes: int, n: int, z: float, lower: bool) -> float:
    """A one-sided Wilson bound on a share, ``z`` standard errors out."""
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half) if lower else min(1.0, centre + half)


def _keyed(c: Constraint, runs: Sequence[RunResult], level: float, margin: float,
           rng: random.Random) -> Dict[str, Any]:
    """Each key judged on its own (an *at most* through its complement), then counted against how many must hold."""
    per_run = c.measure.per_run_keyed(runs)
    order: Dict[str, None] = {}
    for keyed in per_run:
        order.update(dict.fromkeys(keyed or {}))
    op = _COMPLEMENT[c.op] if c.keys == "most" else c.op
    rows = []
    for key in order:
        values = [x for x in (v.get(key) for v in per_run if v is not None) if x is not None]
        if values:
            rows.append({"key": key, "op": op, **_judge(c, op, values, level, margin, rng)})
    if not rows:
        return {"n": 0, **_EMPTY, "keys": []}
    need = {"each": len(rows), "least": c.count, "most": len(rows) - c.count}[c.keys or "each"]
    if need > len(rows) or need < 0:
        raise ValueError(f"constraint '{c.text}': {c.measure.text} has {len(rows)} key(s), so {c.count} cannot be met")
    passing = sum(r["passes"] for r in rows)
    confident = sum(r["confident"] for r in rows) >= need
    missed = sum(not r["clearly_missed"] for r in rows) < need
    gaps = sorted(r["violation"] for r in rows if not r["passes"])
    shortfalls = sorted(r["shortfall"] for r in rows if not r["met"])
    met = sum(r["met"] for r in rows)
    tightest = min(r["slack"] for r in rows)
    binding = [r["key"] for r in rows if not r["confident"] or r["slack"] <= tightest + 1]
    return {"n": min(r["n"] for r in rows), "value": met if c.keys != "most" else len(rows) - met,
            "keys_holding": met, "keys_needed": need, "keys_total": len(rows), "low": None, "high": None, "met": met >= need,
            "passes": passing >= need, "confident": confident, "clearly_missed": missed,
            "verdict": _verdict(confident, missed), "shortfall": math.fsum(shortfalls[:max(0, need - met)]),
            "violation": math.fsum(gaps[:max(0, need - passing)]), "binding": binding,
            "keys": [{k: r[k] for k in ("key", "value", "low", "high", "slack", "met", "confident", "clearly_missed",
                                        "verdict")} for r in rows]}
