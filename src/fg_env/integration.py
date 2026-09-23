"""Bounded, error-controlled RK4 integration shared by SDK dynamics."""
from __future__ import annotations

import math
from typing import Callable, List, Optional

Slope = Callable[[List[float], float], List[float]]


def integrate(slope: Slope, values: List[float], start: float, dt: float,
              substeps: int = 4, rtol: float = 1e-7, atol: float = 1e-10) -> List[float]:
    """Refine RK4 steps until step doubling establishes the requested local error.

    Initial substeps remain an upper step-size bound. Accuracy failures raise;
    a finite but unverified trajectory is never returned as a successful step.
    The caller controls trial-state visibility and commit/rollback.
    """
    if dt <= 0 or not values:
        return list(values)
    end = start + dt
    t = start
    y = list(values)
    ceiling = dt / substeps
    h = ceiling

    def step(v: List[float], at: float, width: float, first: Optional[List[float]] = None) -> List[float]:
        a = slope(v, at) if first is None else first
        b = slope([x + width*k/2 for x, k in zip(v, a)], at + width/2)
        c = slope([x + width*k/2 for x, k in zip(v, b)], at + width/2)
        d = slope([x + width*k for x, k in zip(v, c)], at + width)
        result = [x + width*(aa + 2*bb + 2*cc + dd)/6 for x, aa, bb, cc, dd in zip(v, a, b, c, d)]
        if not all(math.isfinite(x) for x in result):
            raise ArithmeticError("non-finite trial state")
        return result

    for _ in range(10000):
        h = min(h, end - t)
        if t + h == t:
            raise ArithmeticError("requested physics accuracy exceeds time resolution")
        # An undefined derivative at the actual state cannot be repaired by a
        # smaller trial step. Fail immediately instead of repeatedly shrinking.
        first = slope(y, t)
        if not all(math.isfinite(x) for x in first):
            raise ArithmeticError("non-finite derivative at the current physical state")
        try:
            coarse = step(y, t, h, first)
            half = step(y, t, h/2, first)
            fine = step(half, t + h/2, h/2)
            error = max(abs(f-c) / (15 * (atol + rtol * max(abs(v), abs(f))))
                        for v, f, c in zip(y, fine, coarse))
        except (ArithmeticError, ValueError):
            h *= 0.25
            continue
        if error <= 1:
            y = [f + (f-c)/15 for f, c in zip(fine, coarse)]
            t += h
            if t >= end:
                return y
        factor = 4.0 if error == 0 else max(0.1, min(4.0, 0.9 * error ** -0.2))
        h = min(ceiling, h * factor)
    raise ArithmeticError("physics accuracy could not be established within 10000 trial steps; rescale rates or time")
