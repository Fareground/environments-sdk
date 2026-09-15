"""Poisson sampling shared by expression draws and population patterns.

Large means use Hörmann's transformed rejection (PTRS), Insurance: Mathematics
and Economics 12 (1993), 39–45. The proposal is inexpensive; the acceptance test
uses the Poisson mass, rather than returning a rounded normal approximation.
"""
from __future__ import annotations

import math
from typing import Any

# Beyond this range, floating-point proposals cannot reliably distinguish unit counts.
_MAX_MEAN = 2**52


def _log_mass(count: int, mean: float) -> float:
    if count == 0:
        return -mean
    if count < 16:
        return count * math.log(mean) - mean - math.lgamma(count + 1)
    # Stable Poisson deviance near the mode: avoid subtracting terms of size mean*log(mean).
    if abs(count - mean) < 0.1 * (count + mean):
        ratio = (count - mean) / (count + mean)
        deviance = (count - mean) * ratio
        term = 2 * count * ratio
        square = ratio * ratio
        for index in range(1, 100):
            term *= square
            updated = deviance + term / (2 * index + 1)
            if updated == deviance:
                break
            deviance = updated
    else:
        deviance = count * math.log(count / mean) + mean - count
    inv = 1 / count
    correction = inv * (1/12 - inv**2 / 360 + inv**4 / 1260 - inv**6 / 1680 + inv**8 / 1188)
    return -deviance - 0.5 * math.log(2 * math.pi * count) - correction


def sample_poisson(rng: Any, mean: float) -> int:
    if not 0 <= mean <= _MAX_MEAN or not math.isfinite(mean):
        raise ValueError(f"Poisson mean must be finite and between 0 and {_MAX_MEAN}, got {mean!r}")
    if mean <= 500:
        # Preserve the existing small-mean algorithm and its seeded draw sequence, including zero.
        threshold, count, product = math.exp(-mean), 0, rng.random()
        while product > threshold:
            count += 1
            product *= rng.random()
        return count

    integer_mean = math.floor(mean)
    fractional_mean = mean - integer_mean
    scale = 0.931 + 2.53 * math.sqrt(mean)
    curvature = -0.059 + 0.02483 * scale
    normalizer = 1.1239 + 1.1328 / (scale - 3.4)
    squeeze = 0.9277 - 3.6224 / (scale - 2)
    while True:
        centered, height = rng.random() - 0.5, rng.random()
        distance = 0.5 - abs(centered)
        if distance == 0:
            continue
        candidate = integer_mean + math.floor((2 * curvature / distance + scale) * centered + fractional_mean + 0.43)
        if candidate < 0:
            continue
        if distance >= 0.07 and height <= squeeze:
            return candidate
        if distance < 0.013 and height > distance:
            continue
        if height == 0:
            return candidate
        envelope = math.log(height) + math.log(normalizer) - math.log(curvature / distance**2 + scale)
        if envelope <= _log_mass(candidate, mean):
            return candidate
