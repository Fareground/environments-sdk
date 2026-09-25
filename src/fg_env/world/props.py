"""Property values: what kind of value a declared property holds, and which numbers can be stored."""
from __future__ import annotations

import math
from typing import Any

from ..contract import PropSpec
from ..errors import RunError
from ..expr import is_expr
from .abort import within_bounds

__all__ = ["prop_type", "finite_number", "shown_value", "stored", "EXACT_INT"]

#: The largest whole number an int property holds: every whole number up to it is exact however it was worked out
#: (2^53, where fractions' whole numbers end); past it, `1e17 + 1` would be stored as 1e17.
EXACT_INT = 2 ** 53


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


def stored(spec: PropSpec | None, value: Any, where: str, owner: str = "") -> Any:
    """``value`` as ``spec`` stores it: the one rule a property's value follows wherever it is written — its default
    as the world is built and as the check reads it, and every write while a run plays. A number past a declared min
    or max is refused (:class:`~fg_env.world.abort.OutOfBounds`), never clamped: an action is rolled back and its
    actor told why. ``owner`` names who holds the property in that refusal. Any other value that does not fit is a
    :class:`RunError` at ``where``."""
    if spec is None:
        return value
    kind = prop_type(spec)
    if value is None:
        if spec.default is None or kind == "any":  # declared without a value: it may be empty
            return None
        raise RunError(
            f"cannot be null: it starts with a value, so it always holds one ({kind}; an empty list's $max, $avg "
            f"or $first is null — guard it, e.g. `$max(xs) if $len(xs) > 0 else 0`); to let it be empty, "
            f'declare it with "default": null', where)
    if kind in ("number", "int"):
        if not finite_number(value):
            raise RunError(f"must be a finite number that fits in a float, got {shown_value(value)}", where)
        prop = where.rsplit(".", 1)[-1]
        within_bounds(spec, value, f"{owner}'s {prop}" if owner else prop)
        if kind == "int":
            if isinstance(value, float) and not value.is_integer():
                raise RunError(f"must be a whole number, got {value}", where)
            if abs(value) > EXACT_INT:  # a whole number there no longer holds every value exactly
                raise RunError(f"{shown_value(value)} is beyond the exact whole-number range (±2^53, "
                               f"{EXACT_INT:,})", where)
            value = int(value)
    elif kind == "bool" and not isinstance(value, bool):
        raise RunError(f"must be true or false, got {value!r}", where)
    elif kind == "text" and not isinstance(value, str):
        hint = ('; property shorthand "bool" is a text default, not a type declaration; '
                'use {"type": "bool", "default": false}'
                if spec.type is None and spec.default == "bool" and isinstance(value, bool) else '')
        raise RunError(f"must be text, got {value!r}{hint}", where)
    elif kind == "enum" and value not in (spec.values or []):
        raise RunError(f"must be one of {spec.values}, got {value!r}", where)
    elif kind == "list" and not isinstance(value, list):
        raise RunError(f"must be a list, got {value!r}", where)
    elif kind == "map" and not isinstance(value, dict):
        raise RunError(f"must be an object, got {value!r}", where)
    return value
