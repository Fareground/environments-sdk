"""Statistics shared by every analysis: estimates with intervals, correlations, designs.

Standard library only. Every interval states its confidence level; small samples use the
Student-t distribution (computed exactly, not from a table) because analyses are often a
handful of runs, where the normal value would overstate certainty.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from statistics import NormalDist
from typing import Any, TypeGuard

__all__ = [
    "Estimate", "is_number", "numeric", "mean", "sd", "t_quantile", "normal_quantile", "estimate",
    "proportion", "wilson", "quantile", "ranks", "pearson", "spearman", "fisher_interval",
    "correlation_ratio", "wasserstein", "latin_hypercube", "levels", "two_sided_z",
]

#: Continued-fraction iterations and tolerance for the incomplete beta function.
_BETA_ITERATIONS = 300
_BETA_EPSILON = 1e-15
_TINY = 1e-300
#: Degrees of freedom beyond which the Student-t equals the normal to many digits.
_NORMAL_DF = 1e7
#: Spearman's standard error in Fisher-z units exceeds Pearson's by this factor (Fieller et al., 1957).
_SPEARMAN_SE_FACTOR = 1.06


@dataclass(frozen=True)
class Estimate:
    """A mean with its uncertainty. ``low``/``high`` are ``None`` when fewer than two values exist."""

    n: int
    mean: float | None
    sd: float | None = None
    se: float | None = None
    low: float | None = None
    high: float | None = None
    level: float = 0.95

    @property
    def excludes_zero(self) -> bool:
        return self.low is not None and self.high is not None and (self.low > 0 or self.high < 0)

    def to_dict(self) -> dict[str, Any]:
        return {"n": self.n, "mean": self.mean, "sd": self.sd, "se": self.se, "low": self.low,
                "high": self.high, "level": self.level}

    def text(self, digits: int = 4) -> str:
        if self.mean is None:
            return "—"
        if self.low is None or self.high is None:
            return f"{self.mean:.{digits}g} (n={self.n})"
        return f"{self.mean:.{digits}g} [{self.low:.{digits}g}, {self.high:.{digits}g}] (n={self.n})"


def is_number(value: Any) -> TypeGuard[float]:
    """A finite int or float (booleans are not numbers here)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def numeric(value: Any) -> float | None:
    """``value`` as a float: numbers as is, yes/no as 1/0, anything else ``None``."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return float(value) if is_number(value) else None


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean of no values")
    return math.fsum(values) / len(values)


def sd(values: Sequence[float]) -> float:
    """Sample standard deviation (n − 1); 0 for fewer than two values."""
    n = len(values)
    if n < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(math.fsum((v - m) ** 2 for v in values) / (n - 1))


def _beta_fraction(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (modified Lentz)."""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > _TINY else _TINY)
    h = d
    for m in range(1, _BETA_ITERATIONS + 1):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)),
                   -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > _TINY else _TINY)
            c = 1.0 + aa / c
            c = c if abs(c) > _TINY else _TINY
            step = d * c
            h *= step
        if abs(step - 1.0) < _BETA_EPSILON:
            break
    return h


def _incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_fraction(a, b, x) / a
    return 1.0 - front * _beta_fraction(b, a, 1.0 - x) / b


def _t_cdf(t: float, df: float) -> float:
    tail = 0.5 * _incomplete_beta(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t >= 0 else tail


def normal_quantile(p: float) -> float:
    return NormalDist().inv_cdf(p)


@lru_cache(maxsize=512)
def t_quantile(p: float, df: float) -> float:
    """Inverse CDF of Student's t with ``df`` degrees of freedom (``p`` in (0, 1))."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be strictly between 0 and 1, got {p}")
    if df <= 0:
        raise ValueError(f"degrees of freedom must be positive, got {df}")
    if df >= _NORMAL_DF:
        return normal_quantile(p)
    if p < 0.5:
        return -t_quantile(1.0 - p, df)
    low, high = 0.0, 1.0
    while _t_cdf(high, df) < p:
        high *= 2.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if _t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
        if high - low <= 1e-12 * max(1.0, high):
            break
    return (low + high) / 2.0


def _check_level(level: float) -> None:
    if not 0.0 < level < 1.0:
        raise ValueError(f"level must be strictly between 0 and 1, got {level}")


def estimate(values: Sequence[float], level: float = 0.95) -> Estimate:
    """Mean with a Student-t confidence interval."""
    _check_level(level)
    n = len(values)
    if n == 0:
        return Estimate(0, None, level=level)
    m = mean(values)
    if n == 1:
        return Estimate(1, m, level=level)
    s = sd(values)
    se = s / math.sqrt(n)
    half = t_quantile(1.0 - (1.0 - level) / 2.0, n - 1) * se
    return Estimate(n, m, s, se, m - half, m + half, level)


def wilson(successes: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a proportion: honest at 0%, 100% and small n."""
    _check_level(level)
    if n <= 0:
        raise ValueError("a proportion needs at least one trial")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be between 0 and {n}, got {successes}")
    z = normal_quantile(1.0 - (1.0 - level) / 2.0)
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def proportion(flags: Sequence[bool], level: float = 0.95) -> Estimate:
    """Share of true values with a Wilson interval; ``se`` is the binomial standard error."""
    n = len(flags)
    if n == 0:
        return Estimate(0, None, level=level)
    k = sum(1 for f in flags if f)
    p = k / n
    low, high = wilson(k, n, level)
    return Estimate(n, p, math.sqrt(p * (1 - p)), math.sqrt(p * (1 - p) / n), low, high, level)


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (the common "type 7" definition)."""
    if not values:
        raise ValueError("quantile of no values")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be between 0 and 1, got {q}")
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    below = math.floor(position)
    above = min(below + 1, len(ordered) - 1)
    return ordered[below] + (ordered[above] - ordered[below]) * (position - below)


def ranks(values: Sequence[float]) -> list[float]:
    """1-based ranks; tied values share their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        shared = (start + end) / 2.0 + 1.0
        for k in range(start, end + 1):
            out[order[k]] = shared
        start = end + 1
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation; ``None`` when either side has no variation or fewer than 3 pairs."""
    if len(xs) != len(ys):
        raise ValueError("correlation needs two sequences of the same length")
    if len(xs) < 3:
        return None
    mx, my = mean(xs), mean(ys)
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Rank correlation: monotone association, robust to outliers and scale."""
    return pearson(ranks(xs), ranks(ys))


def fisher_interval(r: float, n: int, level: float = 0.95, rank: bool = True) -> tuple[float, float] | None:
    """Confidence interval for a correlation via Fisher's z (with the Spearman correction when ``rank``)."""
    _check_level(level)
    if n <= 3:
        return None
    clipped = max(-0.999999, min(0.999999, r))
    se = (_SPEARMAN_SE_FACTOR if rank else 1.0) / math.sqrt(n - 3)
    z = normal_quantile(1.0 - (1.0 - level) / 2.0)
    centre = math.atanh(clipped)
    return math.tanh(centre - z * se), math.tanh(centre + z * se)


def correlation_ratio(xs: Sequence[float], ys: Sequence[float], bins: int, adjusted: bool = False) -> float | None:
    """η²: the share of the variance of ``ys`` explained by the bin of ``xs`` (a first-order index).

    With at most ``bins`` distinct ``xs`` each value is its own group; otherwise ``xs`` is cut
    into equal-count bins. ``None`` when ``ys`` does not vary. ``adjusted`` returns ε², which
    removes the share that ``k`` groups explain by chance alone ((SS_between − (k−1)·MS_within)
    / (SS_total + MS_within), floored at 0), so an irrelevant input scores about 0, not (k−1)/(n−1).
    """
    if len(xs) != len(ys) or not xs:
        raise ValueError("correlation ratio needs two non-empty sequences of the same length")
    if bins < 1:
        raise ValueError("bins must be at least 1")
    n = len(ys)
    overall = mean(ys)
    total = math.fsum((y - overall) ** 2 for y in ys)
    if total <= 0:
        return None
    distinct = sorted(set(xs))
    if len(distinct) <= bins:
        groups: dict[Any, list[float]] = {}
        for x, y in zip(xs, ys):
            groups.setdefault(x, []).append(y)
    else:
        order = sorted(range(n), key=lambda i: xs[i])
        groups = {}
        for position, i in enumerate(order):
            groups.setdefault(position * bins // n, []).append(ys[i])
    between = math.fsum(len(g) * (mean(g) - overall) ** 2 for g in groups.values())
    if not adjusted:
        return between / total
    k = len(groups)
    if n <= k:
        return None
    within_mean_square = (total - between) / (n - k)
    return max(0.0, (between - (k - 1) * within_mean_square) / (total + within_mean_square))


def wasserstein(a: Sequence[float], b: Sequence[float]) -> float:
    """First Wasserstein (earth mover's) distance between two empirical samples."""
    if not a or not b:
        raise ValueError("distance needs two non-empty samples")
    xa, xb = sorted(a), sorted(b)
    na, nb = len(xa), len(xb)
    i = j = 0
    position, total = 0.0, 0.0
    while i < na and j < nb:
        step_to = min((i + 1) / na, (j + 1) / nb)
        total += (step_to - position) * abs(xa[i] - xb[j])
        position = step_to
        if math.isclose(step_to, (i + 1) / na):
            i += 1
        if math.isclose(step_to, (j + 1) / nb):
            j += 1
    return total


def latin_hypercube(samples: int, dims: int, rng: random.Random) -> list[list[float]]:
    """``samples`` points in the unit cube, exactly one in each of ``samples`` strata per dimension."""
    if samples < 1 or dims < 1:
        raise ValueError("a Latin hypercube needs at least one sample and one dimension")
    columns = []
    for _ in range(dims):
        strata = list(range(samples))
        rng.shuffle(strata)
        columns.append([(s + rng.random()) / samples for s in strata])
    return [[columns[d][i] for d in range(dims)] for i in range(samples)]


def levels(low: float, high: float, steps: int, log: bool = False) -> list[float]:
    """``steps`` evenly spaced values from ``low`` to ``high`` (geometric spacing when ``log``)."""
    if steps < 1:
        raise ValueError("steps must be at least 1")
    if log and (low <= 0 or high <= 0):
        raise ValueError("log spacing needs positive low and high")
    if steps == 1:
        return [float(low)]
    if log:
        a, b = math.log(low), math.log(high)
        return [math.exp(a + (b - a) * i / (steps - 1)) for i in range(steps)]
    return [low + (high - low) * i / (steps - 1) for i in range(steps)]


def two_sided_z(p: float) -> float:
    """The normal deviate whose two-sided tail probability is ``p``: a common "surprise" scale."""
    clipped = min(1.0, max(p, 1e-12))
    return normal_quantile(1.0 - clipped / 2.0) if clipped < 1.0 else 0.0
