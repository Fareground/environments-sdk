"""Add authored decimal durations without accumulating binary clock drift."""
from __future__ import annotations

from decimal import Decimal, localcontext
from math import isfinite

from ..errors import RunError


def advance_time(moment: float, duration: float, path: str) -> float:
    """Keep distinct representable moments; never merge events with a tolerance.

    JSON numbers and expressions arrive as floats. Their shortest decimal
    representations preserve that value's significant digits, while decimal
    addition keeps repeated authored durations (such as 0.1) on their boundary.
    State and snapshots remain ordinary floats.
    """
    if not isfinite(moment) or not isfinite(duration) or duration < 0:
        raise RunError("clock time and duration must be finite, with duration ≥ 0", path)
    with localcontext() as context:
        context.prec = 40
        target = float(Decimal(str(moment)) + Decimal(str(duration)))
    if not isfinite(target) or (duration > 0 and target <= moment):
        raise RunError("duration cannot advance this clock at its current numeric precision", path)
    return target
