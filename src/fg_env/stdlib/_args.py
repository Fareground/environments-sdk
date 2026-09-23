"""Argument checks shared by the standard library: every error names the function and what to pass."""
from __future__ import annotations

import math
from typing import Any, Dict, Hashable, List, Optional

from ..expr import MAX_LIST_LEN, Call, ExprError, _describe, _entity_id, charge

__all__ = [
    "fail", "text_arg", "optional_text", "int_arg", "number_arg", "list_arg", "map_arg", "key_of",
    "present_numbers", "series_arg", "probability", "check_len", "sequence_arg",
]


def fail(call: Call, message: str) -> ExprError:
    """An error for this call: ``$name: message — in `source```."""
    return ExprError(f"${call.name}: {message}", call.source)


def text_arg(call: Call, index: int, what: str = "text") -> str:
    """Argument ``index`` as text; anything else (including null) is an error."""
    value = call.arg(index)
    if not isinstance(value, str):
        raise fail(call, f"argument {index + 1} must be {what}, got {_describe(value)}")
    charge(len(value), call.source)
    return value


def optional_text(call: Call, index: int, default: str, what: str = "text") -> str:
    """Argument ``index`` as text, or ``default`` when it was not passed or is null."""
    if index >= len(call) or call.arg(index) is None:
        return default
    return text_arg(call, index, what)


def int_arg(call: Call, index: int, default: Optional[int] = None, *, low: Optional[int] = None,
            high: Optional[int] = None, what: str = "a whole number") -> int:
    """Argument ``index`` as a whole number (``3.0`` is accepted) within [low, high]."""
    if index >= len(call) or (default is not None and call.arg(index) is None):
        if default is None:
            raise fail(call, f"argument {index + 1} ({what}) is required")
        return default
    value = call.arg(index)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            (isinstance(value, float) and not (math.isfinite(value) and value.is_integer())):
        raise fail(call, f"argument {index + 1} must be {what}, got {_describe(value)}")
    number = int(value)
    if low is not None and number < low:
        raise fail(call, f"argument {index + 1} ({what}) must be at least {low}, got {number}")
    if high is not None and number > high:
        raise fail(call, f"argument {index + 1} ({what}) must be at most {high:,}, got {number:,}")
    return number


def number_arg(call: Call, index: int, default: Optional[float] = None, *, low: Optional[float] = None,
               high: Optional[float] = None, what: str = "a number") -> Any:
    """Argument ``index`` as a finite number within [low, high]."""
    if index >= len(call) or (default is not None and call.arg(index) is None):
        if default is None:
            raise fail(call, f"argument {index + 1} ({what}) is required")
        return default
    value = call.arg(index)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            (isinstance(value, float) and not math.isfinite(value)):
        raise fail(call, f"argument {index + 1} must be {what}, got {_describe(value)}")
    if low is not None and value < low:
        raise fail(call, f"argument {index + 1} ({what}) must be at least {low}, got {value}")
    if high is not None and value > high:
        raise fail(call, f"argument {index + 1} ({what}) must be at most {high}, got {value}")
    return value


def probability(call: Call, value: Any, what: str) -> float:
    """``value`` as a probability in [0, 1]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not 0 <= value <= 1:
        raise fail(call, f"{what} must be a probability between 0 and 1, got {_describe(value)}")
    return float(value)


def list_arg(call: Call, index: int, what: str = "a list") -> List[Any]:
    """Argument ``index`` as a list (an entity type name gives its entities). Null is an error."""
    value = call.arg(index)
    if isinstance(value, (list, tuple)):
        charge(len(value), call.source)
        return list(value)
    if isinstance(value, str) and call.scope.world.is_type(value):
        return call.collection(index)
    raise fail(call, f"argument {index + 1} must be {what}, got {_describe(value)}")


def sequence_arg(call: Call, index: int) -> List[Any]:
    """Argument ``index`` as a list; null reads as an empty list (like the collection functions)."""
    if call.arg(index) is None:
        return []
    return list_arg(call, index)


def map_arg(call: Call, index: int) -> Dict[Any, Any]:
    value = call.arg(index)
    if not isinstance(value, dict):
        raise fail(call, f"argument {index + 1} must be a map like {{a: 1}}, got {_describe(value)}")
    charge(len(value), call.source)
    return value


def key_of(value: Any) -> Hashable:
    """A hashable identity for set membership: entities by id, lists and maps by content."""
    value = _entity_id(value)
    if isinstance(value, (list, tuple, dict)):
        return ("__container__", repr(value))
    if isinstance(value, float) and value.is_integer():
        return int(value)  # 2.0 and 2 are the same member, as `==` says
    return value


def present_numbers(call: Call, values: List[Any], what: str = "values") -> List[Any]:
    """The numbers in ``values`` with nulls skipped (like the built-in aggregates)."""
    out = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                (isinstance(value, float) and not math.isfinite(value)):
            raise fail(call, f"{what} must be numbers, got {_describe(value)}")
        out.append(value)
    return out


def series_arg(call: Call, index: int, what: str = "series") -> List[Any]:
    """Argument ``index`` as a complete list of numbers: positions matter, so a null is an error."""
    values = list_arg(call, index, f"a {what} (a list of numbers)")
    for position, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                (isinstance(value, float) and not math.isfinite(value)):
            raise fail(call, f"the {what} must be numbers only; item {position} is {_describe(value)} "
                             "(filter nulls out first if positions do not matter)")
    return values


def check_len(call: Call, size: int, what: str = "result") -> None:
    """Refuse building a list longer than the list limit, before building it."""
    if size > MAX_LIST_LEN:
        raise fail(call, f"the {what} would have {size:,} items; the limit is {MAX_LIST_LEN:,}")
    charge(size, call.source)
