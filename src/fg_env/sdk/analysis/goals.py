"""What an optimiser aims for: measures and objectives over a candidate's runs, with intervals.

Objectives are written ``"maximise margin"``, ``"minimise staffing_cost"`` or ``"maximise p10 of margin"`` (risk
averse: the 10th percentile over runs); the statistic is ``mean`` (the default), ``median`` or ``p1``…``p99``. A
measure is an output or a metric (its final value) by name, or an expression over ``$outputs``, ``$metrics`` and
``$inputs``: ``"maximise $outputs.revenue - $outputs.holding_cost"``, ``"maximise $min($values($outputs.fill_by_sku))"``.
Constraints, per key and with a confidence, are :mod:`.constraints`; a candidate's verdict and rank, :mod:`.assessment`.

Every value comes with a 95% interval: Student-t for a mean, Wilson for a share of runs, a seeded bootstrap for a
percentile. Candidates run on the same seeds, so :func:`paired` compares two of them run by run.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..expr import ExprError, Scope, compile_expr, evaluate
from ..measure import RunResult
from . import runner
from .stats import estimate, mean, numeric, quantile, sd

__all__ = ["Stat", "Measure", "Objective", "parse_objectives", "stat_prefix", "paired", "dominates"]

#: Bootstrap resamples behind a percentile's interval.
_BOOTSTRAP = 200
#: Most objectives a Pareto frontier is traced for.
MAX_OBJECTIVES = 3
_OBJECTIVE = re.compile(r"^\s*(maximi[sz]e|max|minimi[sz]e|min)\s+(.+?)\s*$", re.IGNORECASE)
_STAT = re.compile(r"^\s*(mean|median|p(\d{1,2}))\s+of\s+(.+?)\s*$", re.IGNORECASE)


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
        low, high, _ = self.spread(values, rng)
        return low, high

    def spread(self, values: Sequence[float], rng: random.Random
               ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """The 95% interval and the standard error (``None`` with fewer than two values): Student-t for a mean, a
        seeded bootstrap for a percentile."""
        if len(values) < 2:
            return None, None, None
        if self.q is None:
            e = estimate(values)
            return e.low, e.high, e.se
        draws = sorted(self.of([values[rng.randrange(len(values))] for _ in values]) for _ in range(_BOOTSTRAP))
        return quantile(draws, 0.025), quantile(draws, 0.975), sd(draws)


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

    def per_run_keyed(self, runs: Sequence[RunResult]) -> List[Optional[Dict[str, Optional[float]]]]:
        """One ``{key: number}`` per run for a list (keys are positions) or map output (``None`` for a failed run);
        raises when a completed run gives neither."""
        out: List[Optional[Dict[str, Optional[float]]]] = []
        for r in runs:
            if r.status == "failed":
                out.append(None)
                continue
            raw = runner.raw_value(r, self.named) if self.named else self._evaluate(r)
            if isinstance(raw, Mapping):
                out.append({str(k): numeric(v) for k, v in raw.items()})
            elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                out.append({str(i): numeric(v) for i, v in enumerate(raw)})
            else:
                raise ValueError(f"'{self.text}' gives {type(raw).__name__} {str(raw)[:60]!r}; a per-key constraint "
                                 "(each, at least, at most) needs a list or map of numbers per run")
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
        stat, rest = stat_prefix(match.group(2))
        out.append(Objective(sense, stat, Measure.parse(contract, rest, f"objective '{text}'")))
    return out


def stat_prefix(text: str) -> Tuple[Stat, str]:
    """``p90 of cost`` → the statistic and ``cost`` (the mean when no statistic is named)."""
    match = _STAT.match(text)
    return (Stat.parse(match.group(1)), match.group(3)) if match else (Stat("mean"), text)


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
