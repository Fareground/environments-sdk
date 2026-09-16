"""Large binomial draws using Hörmann's transformed rejection with squeeze (BTRS).

The proposal constants follow BTRS, also used by CPython's random.binomialvariate.
Acceptance uses stable Poisson log masses: independent Poisson counts conditioned
on their total give the binomial mass. Ratios cancel the conditioning constant,
avoiding subtraction of factorial logs of order n*log(n) at large n.
"""
from __future__ import annotations

import math
from typing import Any

from .poisson import _log_mass


def _log_ratio(k: int, mode: int, n: int, p: float) -> float:
    mean, complement = n * p, n * (1 - p)
    return math.fsum((_log_mass(k, mean), _log_mass(n - k, complement),
                      -_log_mass(mode, mean), -_log_mass(n - mode, complement)))


def sample_large_binomial(rng: Any, n: int, p: float) -> int:
    """Binomial with n*min(p, 1-p) > 1000; validation and small draws live in stdlib.dists."""
    reflected = p > 0.5
    probability = 1 - p if reflected else p
    mean = n * probability
    deviation = math.sqrt(mean * (1 - probability))
    width = 1.15 + 2.53 * deviation
    curvature = -0.0873 + 0.0248 * width + 0.01 * probability
    squeeze = 0.92 - 4.2 / width
    normalizer = (2.83 + 5.1 / width) * deviation
    center = math.floor(mean)
    offset = mean - center + 0.5
    mode = math.floor((n + 1) * probability)
    while True:
        centered = rng.random() - 0.5
        distance = 0.5 - abs(centered)
        if distance == 0:
            continue
        candidate = center + math.floor((2 * curvature / distance + width) * centered + offset)
        if not 0 <= candidate <= n:
            continue
        height = rng.random()
        if height == 0 or (distance >= 0.07 and height <= squeeze):
            return n - candidate if reflected else candidate
        envelope = math.log(height) + math.log(normalizer) - math.log(curvature / distance**2 + width)
        if envelope <= _log_ratio(candidate, mode, n, probability):
            return n - candidate if reflected else candidate
