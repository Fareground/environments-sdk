"""Stable count probabilities shared by demand draws and censored-data fitting."""
from __future__ import annotations

import math
from collections.abc import Iterator


def probabilities(mean: float, dispersion: float | None) -> Iterator[float]:
    """PMF from zero upward, recovering from underflow before the distribution's mass.

    The usual recurrence stays fast for small business counts. Log probabilities are
    used until the first probability is comfortably representable; multiplying zero
    (or rounded subnormal values) would otherwise destroy the entire distribution.
    """
    log_p = -dispersion * math.log1p(mean / dispersion) if dispersion else -mean
    ratio = mean / (dispersion + mean) if dispersion else 0.0
    log_ratio = math.log(ratio) if dispersion else 0.0
    p = math.exp(log_p)
    index = 0
    while True:
        yield p
        factor = (index + dispersion) / (index + 1) * ratio if dispersion else mean / (index + 1)
        if log_p < -500:
            log_p += math.log(index + dispersion) - math.log(index + 1) + log_ratio if dispersion else math.log(factor)
            p = math.exp(log_p)
        else:
            p *= factor
        index += 1


def _fraction(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta function."""
    tiny = 1e-300
    c = 1.0
    d = 1 - (a + b) * x / (a + 1)
    if abs(d) < tiny:
        d = tiny
    d = 1 / d
    result = d
    for m in range(1, 10001):
        for numerator in (m * (b - m) * x / ((a + 2*m - 1) * (a + 2*m)),
                          -(a + m) * (a + b + m) * x / ((a + 2*m) * (a + 2*m + 1))):
            d = 1 + numerator * d
            c = 1 + numerator / c
            if abs(d) < tiny:
                d = tiny
            if abs(c) < tiny:
                c = tiny
            d = 1 / d
            delta = d * c
            result *= delta
        if abs(delta - 1) < 2e-14:
            return result
    raise ArithmeticError("negative-binomial count CDF did not converge")


def _log_beta(a: float, b: float) -> float:
    # Avoid subtracting nearly equal lgamma values for a small shape and a huge count.
    adjustment = 0.0
    while a < 8:
        adjustment += math.log1p(b / a)
        a += 1
    while b < 8:
        adjustment += math.log1p(a / b)
        b += 1

    def remainder(x: float) -> float:
        inverse = 1 / x
        square = inverse * inverse
        return inverse * (1/12 + square * (-1/360 + square * (1/1260 + square * (-1/1680 +
                          square * (1/1188 + square * (-691/360360 + square * (1/156)))))))

    return (adjustment + (a - 0.5) * -math.log1p(b / a) + (b - 0.5) * -math.log1p(a / b)
            - 0.5 * math.log(a + b) + 0.5 * math.log(2 * math.pi)
            + remainder(a) + remainder(b) - remainder(a + b))


def negative_binomial_cdf(count: int, mean: float, dispersion: float) -> float:
    """P(Y ≤ count); avoids traversing every unit of a large, skewed demand count."""
    if count < 0:
        return 0.0
    a, b = dispersion, float(count + 1)
    x = dispersion / (dispersion + mean)
    complement = mean / (dispersion + mean)
    front = math.exp(-_log_beta(a, b)
                     - a * math.log1p(mean / dispersion) - b * math.log1p(dispersion / mean))
    value = front * _fraction(a, b, x) / a if x < (a + 1) / (a + b + 2) else \
        1 - front * _fraction(b, a, complement) / b
    return min(1.0, max(0.0, value))


def negative_binomial_quantile(u: float, mean: float, dispersion: float) -> int:
    """Inverse CDF, retaining skew at large means (unlike a clipped normal draw)."""
    if dispersion == 1:
        return max(0, math.ceil(math.log1p(-u) / -math.log1p(1 / mean)) - 1)
    lower, upper = -1, max(1, math.ceil(mean))
    for _ in range(100):
        if negative_binomial_cdf(upper, mean, dispersion) >= u:
            break
        lower, upper = upper, upper * 2
    else:
        raise ArithmeticError("negative-binomial count quantile could not be bounded")
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if negative_binomial_cdf(middle, mean, dispersion) >= u:
            upper = middle
        else:
            lower = middle
    return upper


def poisson_cdf(count: int, mean: float) -> float:
    """P(Y ≤ count), the upper regularized gamma function at (count + 1, mean)."""
    if count < 0:
        return 0.0
    a = float(count + 1)
    if a >= 8:
        inv = 1 / a
        correction = inv * (1/12 - inv**2 / 360 + inv**4 / 1260 - inv**6 / 1680 + inv**8 / 1188)
        log_front = a * math.log1p((mean - a) / a) + a - mean + 0.5 * math.log(a / (2*math.pi)) - correction
    else:
        log_front = a * math.log(mean) - mean - math.lgamma(a)
    front = math.exp(log_front)
    if mean < a + 1:
        term = total = 1 / a
        for i in range(1, 10001):
            term *= mean / (a + i)
            total += term
            if term <= total * 2e-14:
                return min(1.0, max(0.0, 1 - total * front))
    else:
        tiny = 1e-300
        b = mean + 1 - a
        c, d = 1 / tiny, 1 / b
        total = d
        for i in range(1, 10001):
            numerator = -i * (i - a)
            b += 2
            d = numerator * d + b
            c = b + numerator / c
            if abs(d) < tiny:
                d = tiny
            if abs(c) < tiny:
                c = tiny
            d = 1 / d
            delta = d * c
            total *= delta
            if abs(delta - 1) < 2e-14:
                return min(1.0, max(0.0, total * front))
    raise ArithmeticError("Poisson count CDF did not converge")


def poisson_quantile(u: float, mean: float) -> int:
    lower, upper = -1, max(1, math.ceil(mean))
    for _ in range(100):
        if poisson_cdf(upper, mean) >= u:
            break
        lower, upper = upper, upper * 2
    else:
        raise ArithmeticError("Poisson count quantile could not be bounded")
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if poisson_cdf(middle, mean) >= u:
            upper = middle
        else:
            lower = middle
    return upper
