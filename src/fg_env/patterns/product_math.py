"""Products of finite floats without losing range in intermediate multiplication."""
from __future__ import annotations

import math
import sys
from collections.abc import Iterable, Sequence

_MIN_NORMAL = sys.float_info.min
_MAX = sys.float_info.max


def scaled_product(values: Iterable[float], start: float = 1.0) -> tuple[float, int]:
    """Keep the binary exponent separate until the final conversion to a float."""
    mantissa, exponent = math.frexp(start)
    for value in values:
        part, power = math.frexp(value)
        mantissa, adjustment = math.frexp(mantissa * part)
        exponent += power + adjustment
    return mantissa, exponent


def unscale(mantissa: float, exponent: int) -> float:
    """Restore the float range; callers diagnose a genuinely overflowing result."""
    try:
        return math.ldexp(mantissa, exponent)
    except OverflowError:
        return math.copysign(math.inf, mantissa)


def product(values: Sequence[float], start: float = 1.0) -> float:
    """Ordinary multiplication, with a scaled fallback before intermediate range loss.

    Inputs are finite (validated by the pattern runtime). Subnormal intermediate
    results need the fallback too: they may lose significant bits before a later
    large factor restores the final magnitude. A genuine zero preserves its sign.
    """
    total = start
    for value in values:
        previous = total
        total *= value
        magnitude = abs(total)
        if magnitude > _MAX or (magnitude < _MIN_NORMAL and previous != 0 and value != 0):
            return unscale(*scaled_product(values, start))
    return total
