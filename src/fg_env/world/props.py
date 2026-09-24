"""Property values: what kind of value a declared property holds, and which numbers can be stored."""
from __future__ import annotations

import math
from typing import Any

from ..contract import PropSpec
from ..expr import is_expr

__all__ = ["prop_type", "finite_number", "shown_value"]


def prop_type(spec: PropSpec) -> str:
    if spec.type:
        return spec.type
    value = spec.default
    if isinstance(value, bool):
        return "bool"
    # A numeric default means "a number": `"cash": 0` must accept 12.5 later.
    # Whole-number enforcement is opt-in with `"type": "int"`.
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str) and not is_expr(value):
        return "enum" if spec.values else "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "map"
    return "any"


def finite_number(value: Any) -> bool:
    """A real number that is finite and fits in a float. Never raises: a huge whole number that
    cannot be converted to a float is simply not a storable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def shown_value(value: Any) -> str:
    """A value for an error message; huge whole numbers are described, not printed digit by digit."""
    if isinstance(value, int) and not isinstance(value, bool) and value.bit_length() > 64:
        return f"a whole number of {value.bit_length():,} bits"
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."
