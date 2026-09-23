"""Deterministic derivative-free search for noisy simulators evaluated on common seeds.

All searches work in the unit cube (each parameter scaled to [0, 1]) and spend at most
``budget`` distinct evaluations; repeated points are served from a cache, so rounding an
integer parameter never costs a second evaluation.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__all__ = ["Evaluator", "bisection", "golden_section", "nelder_mead", "cross_entropy", "cross_entropy_sizes",
           "BudgetExhausted"]

Point = Tuple[float, ...]

#: Golden-ratio step for golden-section search.
_INVERSE_PHI = (math.sqrt(5.0) - 1.0) / 2.0
#: Nelder–Mead coefficients (reflection, expansion, contraction, shrink): the standard values.
_REFLECT, _EXPAND, _CONTRACT, _SHRINK = 1.0, 2.0, 0.5, 0.5
#: Initial simplex edge, as a share of the unit range.
_SIMPLEX_STEP = 0.25
#: Cross-entropy population per generation, per parameter, and the share kept as elite.
_CE_POPULATION_PER_DIM, _CE_ELITE_SHARE = 6, 0.25


def cross_entropy_sizes(dims: int, budget: int) -> Tuple[int, int, int]:
    """``(population, elite, generations)`` for a cross-entropy search of ``dims`` parameters within ``budget``."""
    population = _CE_POPULATION_PER_DIM * dims
    return population, max(2, int(population * _CE_ELITE_SHARE)), max(1, budget // population)


class BudgetExhausted(Exception):
    """Raised inside a search when the evaluation budget is spent; the best point so far stands."""


@dataclass
class Evaluator:
    """Caches ``objective(point) → (loss, detail)`` and enforces the evaluation budget.

    ``key`` maps a unit point to the parameter values actually run (after rounding), so points
    that run identically share one evaluation.
    """

    objective: Callable[[Point], Tuple[float, Any]]
    key: Callable[[Point], Tuple]
    budget: int
    history: List[Tuple[Point, float, Any]] = field(default_factory=list)
    _cache: Dict[Tuple, Tuple[float, Any]] = field(default_factory=dict)

    def __call__(self, point: Sequence[float]) -> float:
        clipped = tuple(min(1.0, max(0.0, float(x))) for x in point)
        k = self.key(clipped)
        if k in self._cache:
            return self._cache[k][0]
        if len(self._cache) >= self.budget:
            raise BudgetExhausted()
        loss, detail = self.objective(clipped)
        loss = loss if math.isfinite(loss) else math.inf
        self._cache[k] = (loss, detail)
        self.history.append((clipped, loss, detail))
        return loss

    def detail(self, point: Sequence[float]) -> Any:
        """The detail recorded for ``point`` (evaluating it first if needed)."""
        self(point)
        clipped = tuple(min(1.0, max(0.0, float(x))) for x in point)
        return self._cache[self.key(clipped)][1]

    @property
    def best(self) -> Optional[Tuple[Point, float, Any]]:
        return min(self.history, key=lambda h: h[1]) if self.history else None


def bisection(signed: Callable[[float], Optional[float]], evaluate: Evaluator, iterations: int) -> bool:
    """Find where a monotone ``signed(u)`` (simulated − target) crosses zero on [0, 1].

    Returns ``False`` without searching when both ends have the same sign (no crossing to find).
    ``signed`` must call ``evaluate`` so every point is recorded and budgeted.
    """
    try:
        low, high = signed(0.0), signed(1.0)
    except BudgetExhausted:
        return True
    if low is None or high is None or low == 0 or high == 0:
        return low is not None and high is not None
    if (low > 0) == (high > 0):
        return False
    a, b, fa = 0.0, 1.0, low
    try:
        for _ in range(iterations):
            mid = (a + b) / 2.0
            fm = signed(mid)
            if fm is None or fm == 0:
                return True
            if (fm > 0) == (fa > 0):
                a, fa = mid, fm
            else:
                b = mid
    except BudgetExhausted:
        pass
    return True


def golden_section(evaluate: Evaluator, iterations: int) -> None:
    """Minimise a unimodal function of one unit parameter."""
    a, b = 0.0, 1.0
    try:
        c = b - _INVERSE_PHI * (b - a)
        d = a + _INVERSE_PHI * (b - a)
        fc, fd = evaluate((c,)), evaluate((d,))
        for _ in range(iterations):
            if fc <= fd:
                b, d, fd = d, c, fc
                c = b - _INVERSE_PHI * (b - a)
                fc = evaluate((c,))
            else:
                a, c, fc = c, d, fd
                d = a + _INVERSE_PHI * (b - a)
                fd = evaluate((d,))
    except BudgetExhausted:
        pass


def nelder_mead(evaluate: Evaluator, dims: int, start: Optional[Sequence[float]] = None, tolerance: float = 1e-4) -> None:
    """Nelder–Mead simplex search in the unit cube (points are clipped to the cube)."""
    origin = list(start) if start is not None else [0.5] * dims
    simplex = [origin] + [[x + (_SIMPLEX_STEP if i == j else 0.0) if x + _SIMPLEX_STEP <= 1.0
                           else x - (_SIMPLEX_STEP if i == j else 0.0) for j, x in enumerate(origin)]
                          for i in range(dims)]
    try:
        scored = [(evaluate(p), p) for p in simplex]
        while True:
            scored.sort(key=lambda s: s[0])
            best, worst = scored[0], scored[-1]
            spread = max(max(abs(a - b) for a, b in zip(p, best[1])) for _, p in scored)
            if spread < tolerance:
                return
            centroid = [sum(p[j] for _, p in scored[:-1]) / dims for j in range(dims)]
            reflected = [c + _REFLECT * (c - w) for c, w in zip(centroid, worst[1])]
            fr = evaluate(reflected)
            if fr < best[0]:
                expanded = [c + _EXPAND * (r - c) for c, r in zip(centroid, reflected)]
                fe = evaluate(expanded)
                scored[-1] = (fe, expanded) if fe < fr else (fr, reflected)
            elif fr < scored[-2][0]:
                scored[-1] = (fr, reflected)
            else:
                contracted = [c + _CONTRACT * (w - c) for c, w in zip(centroid, worst[1])]
                fc = evaluate(contracted)
                if fc < worst[0]:
                    scored[-1] = (fc, contracted)
                else:
                    scored = [best] + [(evaluate(q), q) for q in
                                       ([b + _SHRINK * (x - b) for b, x in zip(best[1], p)] for _, p in scored[1:])]
    except BudgetExhausted:
        return


def cross_entropy(evaluate: Evaluator, dims: int, rng: random.Random, population: int, elite: int,
                  generations: int) -> None:
    """Cross-entropy method: sample a Gaussian, refit it to the best ``elite`` samples, repeat.

    Robust on rugged or multi-modal objectives where a simplex gets stuck, at a higher cost.
    """
    centre, spread = [0.5] * dims, [0.3] * dims
    try:
        for _ in range(generations):
            samples = [[min(1.0, max(0.0, rng.gauss(m, s))) for m, s in zip(centre, spread)] for _ in range(population)]
            ranked = sorted(samples, key=lambda p: evaluate(p))[:elite]
            centre = [sum(p[j] for p in ranked) / len(ranked) for j in range(dims)]
            spread = [max(1e-3, math.sqrt(sum((p[j] - centre[j]) ** 2 for p in ranked) / len(ranked)))
                      for j in range(dims)]
    except BudgetExhausted:
        return
