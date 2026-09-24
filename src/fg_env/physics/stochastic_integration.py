"""Refine general diagonal Itô dynamics on one Brownian path.

Brownian bridges split existing increments; rejected resolutions never replace
noise with a new draw. Compare whole trajectories at their shared time points,
requiring two successive refinements to meet the requested convergence target.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from typing import Any

Vector = list[float]
Coefficients = Callable[[Vector, float], tuple[Vector, Vector]]
Bounds = Sequence[tuple[float | None, float | None]]


def integrate_noise(coefficients: Coefficients, initial: Vector, start: float, dt: float,
                    streams: dict[int, Any], bounds: Bounds, rtol: float = 0.01,
                    atol: float = 1e-10,
                    derivatives: Callable[[Vector, float], Vector] | None = None) -> Vector:
    if dt <= 0:
        return list(initial)
    drift, diffusion = coefficients(initial, start)
    if not all(math.isfinite(v) for v in drift + diffusion):
        raise ArithmeticError("non-finite stochastic coefficient at the initial state")
    increments = {index: [rng.gauss(0, math.sqrt(dt))] for index, rng in streams.items()}
    bridges = {index: random.Random(rng.getrandbits(128)) for index, rng in streams.items()}
    baseline = [max(abs(x), abs(g)*math.sqrt(dt)) for x, g in zip(initial, diffusion)]
    previous = None
    successes = 0
    for level in range(13):
        count = 2**level
        if count * len(initial) > 2_000_000:
            raise ArithmeticError("stochastic accuracy exceeds the interval work limit; reduce the clock tick")
        h = dt/count
        values = list(initial)
        trajectory = []
        try:
            for step in range(count):
                time = start + step*h
                f, g = coefficients(values, time)
                dg = derivatives(values, time) if derivatives is not None else [0.0]*len(values)
                if derivatives is None and len(values) == 1 and g[0] != 0:
                    epsilon = 1e-5*max(1.0, abs(values[0]))
                    _, shifted = coefficients([values[0]+epsilon], time)
                    dg[0] = (shifted[0]-g[0])/epsilon
                nxt = [x + h*a + b*(increments[i][step] if i in increments else 0.0)
                       for i, (x, a, b) in enumerate(zip(values, f, g))]
                for i, path in increments.items():
                    nxt[i] += 0.5*g[i]*dg[i]*(path[step]**2-h)
                for i, (low, high) in enumerate(bounds):
                    if not math.isfinite(nxt[i]):
                        raise ArithmeticError("non-finite stochastic trial state")
                    if low is not None:
                        nxt[i] = max(low, nxt[i])
                    if high is not None:
                        nxt[i] = min(high, nxt[i])
                # Trapezoidal drift includes the noise's effect on coupled
                # deterministic variables within this step (for example an
                # integral driven by a noisy velocity).
                end_drift, _ = coefficients(nxt, time+h)
                nxt = [x + 0.5*h*(b-a) for x, a, b in zip(nxt, f, end_drift)]
                for i, (low, high) in enumerate(bounds):
                    if not math.isfinite(nxt[i]):
                        raise ArithmeticError("non-finite stochastic trial state")
                    if low is not None:
                        nxt[i] = max(low, nxt[i])
                    if high is not None:
                        nxt[i] = min(high, nxt[i])
                values = nxt
                trajectory.append(values)
        except (ArithmeticError, ValueError):
            trajectory = []
        if previous and trajectory:
            magnitudes = list(baseline)
            for point in previous + trajectory:
                magnitudes = [max(old, abs(new)) for old, new in zip(magnitudes, point)]
            scales = [atol + rtol*value for value in magnitudes]
            error = max(abs(f-c)/scales[i]
                        for old, new in zip(previous, trajectory[1::2])
                        for i, (c, f) in enumerate(zip(old, new)))
            successes = successes + 1 if error <= 1 else 0
            if successes >= 2:
                return values
        else:
            successes = 0
        previous = trajectory
        # Conditional on the parent increment, the two children sum to it
        # exactly and each has the correct Gaussian variance for half a step.
        for index, parents in increments.items():
            children: list[float] = []
            for parent in parents:
                left = parent/2 + bridges[index].gauss(0, math.sqrt(h/4))
                children.extend((left, parent-left))
            increments[index] = children
    raise ArithmeticError("stochastic timestep convergence was not established; reduce the clock tick or review the "
                          "equations")
