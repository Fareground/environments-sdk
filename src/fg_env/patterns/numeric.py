"""The small numerical core fitting uses — plain Python, no dependencies.

* :func:`least_squares` — (weighted) linear least squares with coefficient standard errors. Rows are kept sparse,
  columns are scaled to unit size before solving, and ``groups`` absorbs one intercept per group (a base level per
  SKU) by block elimination, so a design with hundreds of per-key levels solves as fast as one with a few shared terms.
* :func:`count_regression` — a log-link regression for counts (Poisson, or negative binomial with a known
  dispersion) by iteratively reweighted least squares from a least-squares start, halving any step that makes the fit
  worse; rows marked censored are observed only as a lower bound (demand went unmet: sales capped by a stockout) and
  are fitted by expectation–maximisation: each round replaces a censored count by its expected value given that it was
  more than what was seen, then refits.
* :func:`dispersion` — the negative-binomial dispersion k by the method of moments (variance = μ + μ²/k).
* :func:`nelder_mead` — derivative-free minimisation for curves that are not linear in their parameters.
"""
from __future__ import annotations

import math
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import islice

from .count_math import probabilities

__all__ = ["Design", "Linear", "CountFit", "least_squares", "count_regression", "dispersion", "truncated_mean",
           "truncated_moments", "nelder_mead", "inverse"]

Matrix = list[list[float]]
_COLLINEAR = "the columns are collinear (a factor never varies, or two always move together)"


def inverse(a: Matrix) -> Matrix | None:
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

    rows: list[list[tuple[int, float]]]
    width: int
    groups: list[int]
    count: int
    _pairs: list[list[tuple[int, float]]] | None = field(default=None, init=False, repr=False, compare=False)

    def pairs(self) -> list[list[tuple[int, float]]]:
        """Each row's products xᵢ·xⱼ (i ≤ j) at their place in the flattened XᵀX — fixed by the design, so a fit that
        re-solves with new weights every iteration builds them once."""
        if self._pairs is None:
            p = self.width
            self._pairs = [[(min(i, j) * p + max(i, j), vi * vj) for a, (i, vi) in enumerate(row) for j, vj in row[a:]]
                           for row in self.rows]
        return self._pairs

    @classmethod
    def of(cls, x: Design | Sequence[Sequence[float]], groups: Sequence[int] | None = None) -> Design:
        if isinstance(x, Design):
            return x
        width = len(x[0]) if x else 0
        rows = [[(j, float(v)) for j, v in enumerate(row) if v] for row in x]
        if groups is None:
            return cls(rows, width, [], 0)
        return cls(rows, width, list(groups), (max(groups) + 1) if groups else 0)

    def apply(self, coef: Sequence[float], intercepts: Sequence[float]) -> list[float]:
        """x·β (plus each row's group intercept) for every row."""
        values = [sum(coef[j] * v for j, v in row) for row in self.rows]
        return [v + intercepts[g] for v, g in zip(values, self.groups)] if self.count else values


@dataclass(frozen=True)
class Linear:
    """A fitted linear model: coefficients, their standard errors, group intercepts, and how well it fits."""

    coef: list[float]
    se: list[float]
    rmse: float
    r2: float
    n: int
    intercepts: list[float] = field(default_factory=list)
    intercept_se: list[float] = field(default_factory=list)


@dataclass
class _Solution:
    coef: list[float]
    intercepts: list[float]
    covariance: Matrix  # of the scaled coefficients, before multiplying by the residual variance
    intercept_var: list[float]
    scales: list[float]
    #: Each group intercept's covariance with each scaled coefficient (same units as ``covariance``).
    intercept_cross: Matrix = field(default_factory=list)
    #: Each coefficient's variance inflation: its variance over what it would be were its column unrelated to the rest.
    inflation: list[float] = field(default_factory=list)


def _cholesky(a: Matrix) -> Matrix | None:
    """The lower factor L of a symmetric positive definite matrix (a = L·Lᵀ); None when it is singular."""
    n = len(a)
    scale = max((abs(v) for row in a for v in row), default=0.0) or 1.0
    lower: Matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        row, target = lower[i], a[i]
        for j in range(i + 1):
            other = lower[j]
            value = target[j] - sum(map(operator.mul, row[:j], other[:j]))
            if i == j:
                if value <= 1e-10 * scale:
                    return None
                row[i] = math.sqrt(value)
            else:
                row[j] = value / other[j]
    return lower


def _cholesky_solve(lower: Matrix, b: Sequence[float]) -> list[float]:
    """x with L·Lᵀ·x = b, by forward then back substitution."""
    n = len(b)
    z = [0.0] * n
    for i in range(n):
        row = lower[i]
        z[i] = (b[i] - sum(map(operator.mul, row[:i], z[:i]))) / row[i]
    x = [0.0] * n
    for i in reversed(range(n)):
        x[i] = (z[i] - sum(lower[k][i] * x[k] for k in range(i + 1, n))) / lower[i][i]
    return x


def _solve(design: Design, w: Sequence[float], y: Sequence[float], covariance: bool = True) -> _Solution:
    """Weighted normal equations, with group intercepts eliminated block-wise (Schur complement) and the rest solved by
    Cholesky. XᵀWX is accumulated from the design's cached products and scaled to unit columns after; the covariance
    (a full inverse) is computed only when asked for, since iterations need only the coefficients."""
    p, g = design.width, design.count
    total = sum(w)
    flat = [0.0] * (p * p)
    xty = [0.0] * p
    cross = [[0.0] * p for _ in range(g)]
    group_weight, group_y = [0.0] * g, [0.0] * g
    groups = design.groups if g else [0] * len(design.rows)
    for row, pairs, weight, target, group in zip(design.rows, design.pairs(), w, y, groups):
        for place, product in pairs:
            flat[place] += weight * product
        weighted = weight * target
        if g:
            group_weight[group] += weight
            group_y[group] += weighted
            line = cross[group]
            for j, v in row:
                xty[j] += weighted * v
                line[j] += weight * v
        else:
            for j, v in row:
                xty[j] += weighted * v
    scales = [math.sqrt(flat[j * p + j] / total) if total > 0 else 0.0 for j in range(p)]
    if any(s <= 0 or not math.isfinite(s) for s in scales):
        raise ValueError(_COLLINEAR)
    if g and any(v <= 0 for v in group_weight):
        raise ValueError("a group has no weight (no rows)")
    schur = [[flat[min(i, j) * p + max(i, j)] / (scales[i] * scales[j]) for j in range(p)] for i in range(p)]
    rhs = [value / scale for value, scale in zip(xty, scales)]
    cross = [[value / scale for value, scale in zip(line, scales)] for line in cross]
    supports = [[i for i in range(p) if line[i]] for line in cross]
    for group in range(g):
        b, inv_w, nonzero = cross[group], 1.0 / group_weight[group], supports[group]
        for i in nonzero:
            rhs[i] -= b[i] * group_y[group] * inv_w
            for j in nonzero:
                schur[i][j] -= b[i] * b[j] * inv_w
    schur_diagonal = [schur[i][i] for i in range(p)]
    lower = _cholesky(schur) if p else []
    if lower is None:
        raise ValueError(_COLLINEAR)
    scaled_coef = _cholesky_solve(lower, rhs) if p else []
    inverted: Matrix = []
    if covariance and p:
        found = inverse(schur)
        if found is None:
            raise ValueError(_COLLINEAR)
        inverted = found
    intercepts, intercept_var, intercept_cross = [], [], []
    for group in range(g):
        b, inv_w, nonzero = cross[group], 1.0 / group_weight[group], supports[group]
        intercepts.append((group_y[group] - sum(b[i] * scaled_coef[i] for i in nonzero)) * inv_w)
        if covariance:
            line = [-inv_w * sum(b[j] * inverted[j][i] for j in nonzero) for i in range(p)]
            intercept_cross.append(line)
            intercept_var.append(inv_w - inv_w * sum(b[i] * line[i] for i in nonzero))
    inflation = [inverted[i][i] * schur_diagonal[i] for i in range(p)] if inverted else []
    return _Solution([c / s for c, s in zip(scaled_coef, scales)], intercepts, inverted, intercept_var, scales,
                     intercept_cross, inflation)


def least_squares(x: Design | Sequence[Sequence[float]], y: Sequence[float], weights: Sequence[float] | None = None,
                  groups: Sequence[int] | None = None) -> Linear:
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


def _count_pmf(mean: float, k: float | None, upto: int) -> list[float]:
    """P(Y = 0 … upto−1) for a Poisson (k None) or negative binomial count with this mean."""
    if mean <= 0:
        return [1.0] + [0.0] * (upto - 1)
    return list(islice(probabilities(mean, k), upto))


def truncated_mean(mean: float, k: float | None, floor: float) -> float:
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
    coef: list[float]
    se: list[float]
    means: list[float]
    filled: list[float]
    iterations: int
    intercepts: list[float] = field(default_factory=list)
    intercept_se: list[float] = field(default_factory=list)
    #: The coefficients' covariance, and each group intercept's covariance with each coefficient — what the standard
    #: error of a quantity combining several of them (a profile rescaled to average 1) needs.
    covariance: list[list[float]] = field(default_factory=list)
    intercept_covariance: list[list[float]] = field(default_factory=list)
    #: Each coefficient's variance inflation: how many times its variance is what it would be were its driver unrelated
    #: to the other coefficients' drivers (1: separate; large: the data can hardly tell it from the others).
    inflation: list[float] = field(default_factory=list)


def count_regression(x: Design | Sequence[Sequence[float]], y: Sequence[float], *,
                     offset: Sequence[float] | None = None, censored: Sequence[bool] | None = None,
                     k: float | None = None, groups: Sequence[int] | None = None, iterations: int = 100,
                     tolerance: float = 1e-6, start: CountFit | None = None, errors: bool = True) -> CountFit:
    """log E[y] = offset + x·β (+ the row's group intercept) for counts (see the module).

    ``start`` continues from an earlier fit of the same design (a refit with a new dispersion converges in a few
    iterations instead of starting over); ``errors`` False skips the standard errors (an intermediate fit)."""
    design = Design.of(x, groups)
    n, p = len(y), design.width + design.count
    if n <= p:
        raise ValueError(f"{n} row(s) cannot estimate {p} parameter(s)")
    offs = list(offset) if offset is not None else [0.0] * n
    cens = list(censored) if censored is not None else [False] * n
    if start is not None:
        coef, intercepts = list(start.coef), list(start.intercepts)
    else:
        first = _solve(design, [1.0] * n, [math.log(max(0.0, v) + 0.5) - o for v, o in zip(y, offs)], covariance=False)
        coef, intercepts = first.coef, first.intercepts

    def means_of(c: Sequence[float], a: Sequence[float]) -> list[float]:
        return [math.exp(max(-30.0, min(30.0, o + e))) for o, e in zip(offs, design.apply(c, a))]

    def deviance(means: Sequence[float], values: Sequence[float]) -> float:
        """The deviance the reweighted step minimises: Poisson, or negative binomial with the dispersion k (a step
        judged by the Poisson deviance would be refused near the negative-binomial solution)."""
        if k:
            return 2 * sum((v * math.log(v / m) if v > 0 else 0.0) - (v + k) * math.log((v + k) / (m + k))
                           for v, m in zip(values, means))
        return 2 * sum((v * math.log(v / m) if v > 0 else 0.0) - (v - m) for v, m in zip(values, means))

    means = means_of(coef, intercepts)
    filled = [float(v) for v in y]
    done = 0
    for done in range(1, iterations + 1):  # noqa: B007 — the iterations run, read after the loop
        filled = [truncated_mean(m, k, v + 1) if c else float(v) for m, v, c in zip(means, y, cens)]
        weights = [m / (1 + m / k) if k else m for m in means]
        working = [math.log(m) - o + (f - m) / m for o, f, m in zip(offs, filled, means)]
        proposal = _solve(design, weights, working, covariance=False)
        before, step = deviance(means, filled), 1.0
        while True:
            trial = [c + step * (q - c) for c, q in zip(coef, proposal.coef)]
            trial_intercepts = [a + step * (q - a) for a, q in zip(intercepts, proposal.intercepts)]
            trial_means = means_of(trial, trial_intercepts)
            if deviance(trial_means, filled) <= before + 1e-9 * abs(before) or step < 1e-4:
                break
            step /= 2
        change = max([abs(a - b) for a, b in zip(trial, coef)]
                     + [abs(a - b) for a, b in zip(trial_intercepts, intercepts)])
        after = deviance(trial_means, filled)
        coef, intercepts, means = trial, trial_intercepts, trial_means
        if change < tolerance or abs(before - after) <= 1e-10 * max(1.0, abs(after)):
            break
    if not errors:
        return CountFit(coef, [], means, filled, done, intercepts, [])
    solution = _solve(design, [_information(m, v, k, c) for m, v, c in zip(means, y, cens)], [0.0] * n)
    pearson = sum((f - m) ** 2 / (m + (m * m / k if k else 0.0)) for f, m, c in zip(filled, means, cens) if not c)
    free = max(1, sum(1 for c in cens if not c) - design.width - design.count)
    spread = max(1.0, pearson / free)  # quasi-likelihood: widen errors when the counts are noisier than the model
    scales = solution.scales
    covariance = [[value * spread / (scales[i] * scales[j]) for j, value in enumerate(line)]
                  for i, line in enumerate(solution.covariance)]
    se = [math.sqrt(max(0.0, covariance[i][i])) for i in range(design.width)]
    intercept_se = [math.sqrt(max(0.0, v * spread)) for v in solution.intercept_var]
    crossed = [[value * spread / scales[j] for j, value in enumerate(line)] for line in solution.intercept_cross]
    return CountFit(coef, se, means, filled, done, intercepts, intercept_se, covariance, crossed, solution.inflation)


def _information(mean: float, seen: float, k: float | None, censored: bool) -> float:
    """A row's observed information about its log mean — the curvature of its log-likelihood there.

    A negative-binomial count's curvature grows with the count, so it is read at the count seen rather than averaged
    over counts: when stockouts decide which rows are seen in full, those are the low draws, and their average would
    overstate what they tell. A censored row is read at its expected count given it was more than what sold, less the
    spread of that count left unknown (Louis' missing information), so errors from a censored fit are as wide as the
    censoring makes them."""
    damp = 1 + mean / k if k else 1.0
    count, unknown = float(seen), 0.0
    if censored:
        count, second = truncated_moments(mean, k, seen + 1)
        unknown = second - count * count
    return max(0.0, (mean * (1 + count / k) if k else mean) - unknown) / (damp * damp)


def truncated_moments(mean: float, k: float | None, floor: float) -> tuple[float, float]:
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


def dispersion(values: Sequence[float], means: Sequence[float], censored: Sequence[bool] | None = None,
               iterations: int = 30, fitted: int = 0) -> float | None:
    """The negative-binomial k matching Σ(y − μ)² = Σ(μ + μ²/k); None when the counts are not over-dispersed.

    A censored row (demand went unmet, so it was more than what was sold) counts with its expected squared distance
    from the mean given that, so stockouts — which cut off exactly the high draws — do not make demand look calmer
    than it is; k and those expectations are iterated to agree. Means ``fitted`` from these values with that many
    parameters sit closer to the counts seen than the true means do, so those squared distances are scaled up by
    n / (n − fitted), as a residual variance is, or k would come out too large and demand too calm (a censored row's
    expected distance comes from the model, not from a count the fit could chase, and is left as it is)."""
    cens = list(censored) if censored is not None else [False] * len(values)
    squares = sum(m * m for m in means)
    widen = len(values) / (len(values) - fitted) if len(values) > fitted else 1.0

    def solve(k: float | None) -> float | None:
        excess = 0.0
        for v, m, c in zip(values, means, cens):
            if c:
                first, second = truncated_moments(m, k, v + 1)
                excess += second - 2 * m * first + m * m - m
            else:
                excess += widen * (v - m) ** 2 - m
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


def nelder_mead(objective: Callable[[list[float]], float], start: Sequence[float], *, step: float = 0.1,
                iterations: int = 2000, tolerance: float = 1e-10) -> tuple[list[float], float]:
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
