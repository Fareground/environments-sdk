"""The small numerical core fitting uses — plain Python, no dependencies.

* :func:`least_squares` — (weighted) linear least squares with coefficient standard errors. Rows are kept sparse,
  columns are scaled to unit size before solving, and ``groups`` absorbs one intercept per group (a base level per
  SKU) by block elimination, so a design with hundreds of per-key levels solves as fast as one with a few shared terms.
* :func:`count_regression` — a log-link regression for counts (Poisson, or negative binomial with a known
  dispersion) by iteratively reweighted least squares from a least-squares start, halving any step that makes the fit
  worse; rows marked censored are observed only as a lower bound (sales capped by a stockout) and are fitted by
  expectation–maximisation: each round replaces a censored count by its expected value given that it was at least what
  was seen, then refits.
* :func:`dispersion` — the negative-binomial dispersion k by the method of moments (variance = μ + μ²/k).
* :func:`nelder_mead` — derivative-free minimisation for curves that are not linear in their parameters.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple, Union

__all__ = ["Design", "Linear", "CountFit", "least_squares", "count_regression", "dispersion", "truncated_mean", "truncated_moments",
           "nelder_mead", "inverse"]

Matrix = List[List[float]]
_COLLINEAR = "the columns are collinear (a factor never varies, or two always move together)"


def inverse(a: Matrix) -> Optional[Matrix]:
    """The inverse of a square matrix by Gauss–Jordan elimination with partial pivoting; None when it is singular."""
    n = len(a)
    rows = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    scale = max((abs(v) for row in a for v in row), default=0.0) or 1.0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) <= 1e-10 * scale:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        lead = rows[col][col]
        rows[col] = [v / lead for v in rows[col]]
        base = rows[col]
        for r in range(n):
            factor = rows[r][col]
            if r != col and factor:
                rows[r] = [v - factor * b for v, b in zip(rows[r], base)]
    return [row[n:] for row in rows]


@dataclass
class Design:
    """Rows as ``[(column, value), …]`` with zeros left out, and each row's group (None: one overall intercept)."""

    rows: List[List[Tuple[int, float]]]
    width: int
    groups: List[int]
    count: int

    @classmethod
    def of(cls, x: Union["Design", Sequence[Sequence[float]]], groups: Optional[Sequence[int]] = None) -> "Design":
        if isinstance(x, Design):
            return x
        width = len(x[0]) if x else 0
        rows = [[(j, float(v)) for j, v in enumerate(row) if v] for row in x]
        if groups is None:
            return cls(rows, width, [], 0)
        return cls(rows, width, list(groups), (max(groups) + 1) if groups else 0)

    def apply(self, coef: Sequence[float], intercepts: Sequence[float]) -> List[float]:
        """x·β (plus each row's group intercept) for every row."""
        values = [sum(coef[j] * v for j, v in row) for row in self.rows]
        return [v + intercepts[g] for v, g in zip(values, self.groups)] if self.count else values


@dataclass(frozen=True)
class Linear:
    """A fitted linear model: coefficients, their standard errors, group intercepts, and how well it fits."""

    coef: List[float]
    se: List[float]
    rmse: float
    r2: float
    n: int
    intercepts: List[float] = field(default_factory=list)
    intercept_se: List[float] = field(default_factory=list)


@dataclass
class _Solution:
    coef: List[float]
    intercepts: List[float]
    covariance: Matrix  # of the scaled coefficients, before multiplying by the residual variance
    intercept_var: List[float]
    scales: List[float]


def _solve(design: Design, w: Sequence[float], y: Sequence[float]) -> _Solution:
    """Weighted normal equations, with group intercepts eliminated block-wise (Schur complement)."""
    p, g = design.width, design.count
    total = sum(w)
    sizes = [0.0] * p
    for row, weight in zip(design.rows, w):
        for j, v in row:
            sizes[j] += weight * v * v
    scales = [math.sqrt(s / total) if total > 0 else 0.0 for s in sizes]
    if any(s <= 0 or not math.isfinite(s) for s in scales):
        raise ValueError(_COLLINEAR)
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    cross = [[0.0] * p for _ in range(g)]
    group_weight, group_y = [0.0] * g, [0.0] * g
    for index, (row, weight, target) in enumerate(zip(design.rows, w, y)):
        scaled = [(j, v / scales[j]) for j, v in row]
        for a, (i, vi) in enumerate(scaled):
            xty[i] += weight * vi * target
            for j, vj in scaled[a:]:
                xtx[i][j] += weight * vi * vj
        if g:
            group = design.groups[index]
            group_weight[group] += weight
            group_y[group] += weight * target
            for i, vi in scaled:
                cross[group][i] += weight * vi
    for i in range(p):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
    if g and any(v <= 0 for v in group_weight):
        raise ValueError("a group has no weight (no rows)")
    schur = [row[:] for row in xtx]
    rhs = xty[:]
    for group in range(g):
        b, inv_w = cross[group], 1.0 / group_weight[group]
        nonzero = [i for i in range(p) if b[i]]
        for i in nonzero:
            rhs[i] -= b[i] * group_y[group] * inv_w
            for j in nonzero:
                schur[i][j] -= b[i] * b[j] * inv_w
    covariance = inverse(schur) if p else []
    if covariance is None:
        raise ValueError(_COLLINEAR)
    scaled_coef = [sum(covariance[i][j] * rhs[j] for j in range(p)) for i in range(p)]
    intercepts, intercept_var = [], []
    for group in range(g):
        b, inv_w = cross[group], 1.0 / group_weight[group]
        intercepts.append((group_y[group] - sum(bi * c for bi, c in zip(b, scaled_coef))) * inv_w)
        projected = [bi * inv_w for bi in b]
        spread = sum(projected[i] * sum(covariance[i][j] * projected[j] for j in range(p)) for i in range(p) if projected[i])
        intercept_var.append(inv_w + spread)
    return _Solution([c / s for c, s in zip(scaled_coef, scales)], intercepts, covariance, intercept_var, scales)


def least_squares(x: Union[Design, Sequence[Sequence[float]]], y: Sequence[float], weights: Optional[Sequence[float]] = None,
                  groups: Optional[Sequence[int]] = None) -> Linear:
    """Minimise Σ w·(y − x·β − intercept of the row's group)². Raises ValueError when there are too few rows or the
    columns are collinear."""
    design = Design.of(x, groups)
    n, p = len(y), design.width + design.count
    if n <= p:
        raise ValueError(f"{n} row(s) cannot estimate {p} parameter(s)")
    w = list(weights) if weights is not None else [1.0] * n
    solution = _solve(design, w, y)
    fitted = design.apply(solution.coef, solution.intercepts)
    residuals = [target - f for target, f in zip(y, fitted)]
    total = sum(w)
    sse = sum(weight * r * r for weight, r in zip(w, residuals))
    mean = sum(weight * t for weight, t in zip(w, y)) / total
    sst = sum(weight * (t - mean) ** 2 for weight, t in zip(w, y))
    sigma2 = sse / (n - p)
    se = [math.sqrt(max(0.0, solution.covariance[i][i] * sigma2)) / solution.scales[i] for i in range(design.width)]
    intercept_se = [math.sqrt(max(0.0, v * sigma2)) for v in solution.intercept_var]
    return Linear(solution.coef, se, math.sqrt(sse / total), 1 - sse / sst if sst > 0 else 0.0, n,
                  solution.intercepts, intercept_se)


def _count_pmf(mean: float, k: Optional[float], upto: int) -> List[float]:
    """P(Y = 0 … upto−1) for a Poisson (k None) or negative binomial count with this mean."""
    if mean <= 0:
        return [1.0] + [0.0] * (upto - 1)
    if k:
        p = math.exp(k * math.log(k / (k + mean)))
        ratio = mean / (k + mean)
    else:
        p, ratio = math.exp(-mean), 0.0
    out = []
    for y in range(upto):
        out.append(p)
        p *= (y + k) / (y + 1) * ratio if k else mean / (y + 1)
    return out


def truncated_mean(mean: float, k: Optional[float], floor: float) -> float:
    """E[Y | Y ≥ floor] for a Poisson or negative-binomial count Y with this mean."""
    c = max(0, math.ceil(floor))
    if c == 0 or mean <= 0:
        return max(mean, floor)
    pmf = _count_pmf(mean, k, c)
    below = sum(pmf)
    if below >= 1 - 1e-12:
        return float(c)
    return max(float(c), (mean - sum(y * q for y, q in enumerate(pmf))) / (1 - below))


@dataclass(frozen=True)
class CountFit:
    coef: List[float]
    se: List[float]
    means: List[float]
    filled: List[float]
    iterations: int
    intercepts: List[float] = field(default_factory=list)
    intercept_se: List[float] = field(default_factory=list)


def count_regression(x: Union[Design, Sequence[Sequence[float]]], y: Sequence[float], *,
                     offset: Optional[Sequence[float]] = None, censored: Optional[Sequence[bool]] = None,
                     k: Optional[float] = None, groups: Optional[Sequence[int]] = None, iterations: int = 100,
                     tolerance: float = 1e-6) -> CountFit:
    """log E[y] = offset + x·β (+ the row's group intercept) for counts (see the module)."""
    design = Design.of(x, groups)
    n = len(y)
    offs = list(offset) if offset is not None else [0.0] * n
    cens = list(censored) if censored is not None else [False] * n
    start = least_squares(design, [math.log(max(0.0, v) + 0.5) - o for v, o in zip(y, offs)])
    coef, intercepts = start.coef, start.intercepts

    def means_of(c: Sequence[float], a: Sequence[float]) -> List[float]:
        return [math.exp(max(-30.0, min(30.0, o + e))) for o, e in zip(offs, design.apply(c, a))]

    def deviance(means: Sequence[float], values: Sequence[float]) -> float:
        return 2 * sum((v * math.log(v / m) if v > 0 else 0.0) - (v - m) for v, m in zip(values, means))

    means = means_of(coef, intercepts)
    filled = [float(v) for v in y]
    done = 0
    for done in range(1, iterations + 1):
        filled = [truncated_mean(m, k, v) if c else float(v) for m, v, c in zip(means, y, cens)]
        weights = [m / (1 + m / k) if k else m for m in means]
        working = [math.log(m) - o + (f - m) / m for o, f, m in zip(offs, filled, means)]
        proposal = _solve(design, weights, working)
        before, step = deviance(means, filled), 1.0
        while True:
            trial = [c + step * (q - c) for c, q in zip(coef, proposal.coef)]
            trial_intercepts = [a + step * (q - a) for a, q in zip(intercepts, proposal.intercepts)]
            trial_means = means_of(trial, trial_intercepts)
            if deviance(trial_means, filled) <= before + 1e-9 * abs(before) or step < 1e-4:
                break
            step /= 2
        change = max([abs(a - b) for a, b in zip(trial, coef)] + [abs(a - b) for a, b in zip(trial_intercepts, intercepts)])
        after = deviance(trial_means, filled)
        coef, intercepts, means = trial, trial_intercepts, trial_means
        if change < tolerance or abs(before - after) <= 1e-10 * max(1.0, abs(after)):
            break
    weights = [m / (1 + m / k) if k else m for m in means]
    solution = _solve(design, weights, [0.0] * n)
    pearson = sum((f - m) ** 2 / (m + (m * m / k if k else 0.0)) for f, m, c in zip(filled, means, cens) if not c)
    free = max(1, sum(1 for c in cens if not c) - design.width - design.count)
    spread = max(1.0, pearson / free)  # quasi-likelihood: widen errors when the counts are noisier than the model
    se = [math.sqrt(max(0.0, solution.covariance[i][i] * spread)) / solution.scales[i] for i in range(design.width)]
    intercept_se = [math.sqrt(max(0.0, v * spread)) for v in solution.intercept_var]
    return CountFit(coef, se, means, filled, done, intercepts, intercept_se)


def truncated_moments(mean: float, k: Optional[float], floor: float) -> Tuple[float, float]:
    """E[Y | Y ≥ floor] and E[Y² | Y ≥ floor] for a Poisson or negative-binomial count Y with this mean."""
    c = max(0, math.ceil(floor))
    second = mean + (mean * mean / k if k else 0.0) + mean * mean
    if c == 0 or mean <= 0:
        return max(mean, floor), max(second, floor * floor)
    pmf = _count_pmf(mean, k, c)
    below = sum(pmf)
    if below >= 1 - 1e-12:
        return float(c), float(c * c)
    first = (mean - sum(y * q for y, q in enumerate(pmf))) / (1 - below)
    squared = (second - sum(y * y * q for y, q in enumerate(pmf))) / (1 - below)
    return max(float(c), first), max(float(c * c), squared)


def dispersion(values: Sequence[float], means: Sequence[float], censored: Optional[Sequence[bool]] = None,
               iterations: int = 30) -> Optional[float]:
    """The negative-binomial k matching Σ(y − μ)² = Σ(μ + μ²/k); None when the counts are not over-dispersed.

    A censored row (only a lower bound was seen) counts with its expected squared distance from the mean given that
    it was at least what was seen, so stockouts — which cut off exactly the high draws — do not make demand look
    calmer than it is; k and those expectations are iterated to agree."""
    cens = list(censored) if censored is not None else [False] * len(values)
    squares = sum(m * m for m in means)

    def solve(k: Optional[float]) -> Optional[float]:
        excess = 0.0
        for v, m, c in zip(values, means, cens):
            if c:
                first, second = truncated_moments(m, k, v)
                excess += second - 2 * m * first + m * m - m
            else:
                excess += (v - m) ** 2 - m
        return squares / excess if excess > 0 and squares > 0 else None

    k = solve(None)
    if not any(cens):
        return k
    for _ in range(iterations):
        if k is None:
            return None
        updated = solve(k)
        if updated is None or abs(updated - k) <= 1e-6 * k:
            return updated
        k = updated
    return k


def nelder_mead(objective: Callable[[List[float]], float], start: Sequence[float], *, step: float = 0.1,
                iterations: int = 2000, tolerance: float = 1e-10) -> Tuple[List[float], float]:
    """The point minimising ``objective`` near ``start`` (the downhill simplex), and its value."""
    dims = len(start)
    simplex = [list(map(float, start))]
    for i in range(dims):
        point = list(map(float, start))
        point[i] = point[i] + (step * abs(point[i]) if point[i] else step)
        simplex.append(point)
    scores = [objective(point) for point in simplex]
    for _ in range(iterations):
        order = sorted(range(dims + 1), key=lambda i: scores[i])
        simplex, scores = [simplex[i] for i in order], [scores[i] for i in order]
        if abs(scores[-1] - scores[0]) <= tolerance * (abs(scores[0]) + tolerance):
            break
        centre = [sum(p[i] for p in simplex[:-1]) / dims for i in range(dims)]
        worst = simplex[-1]
        reflected = [c + (c - w) for c, w in zip(centre, worst)]
        score = objective(reflected)
        if score < scores[0]:
            expanded = [c + 2 * (c - w) for c, w in zip(centre, worst)]
            expanded_score = objective(expanded)
            simplex[-1], scores[-1] = (expanded, expanded_score) if expanded_score < score else (reflected, score)
        elif score < scores[-2]:
            simplex[-1], scores[-1] = reflected, score
        else:
            contracted = [c + 0.5 * (w - c) for c, w in zip(centre, worst)]
            contracted_score = objective(contracted)
            if contracted_score < scores[-1]:
                simplex[-1], scores[-1] = contracted, contracted_score
            else:
                anchor = simplex[0]
                simplex = [anchor] + [[b + 0.5 * (v - b) for b, v in zip(anchor, p)] for p in simplex[1:]]
                scores = [scores[0]] + [objective(p) for p in simplex[1:]]
    best = min(range(dims + 1), key=lambda i: scores[i])
    return simplex[best], scores[best]
