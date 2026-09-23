"""What tool schemas and argument validation share: argument limits, short previews of arguments, and the
element spec and length bounds of list parameters."""
from __future__ import annotations

import reprlib
from typing import Any, Tuple

from .contract import MAX_LIST_ITEMS, ParamSpec

__all__ = ["TEXT_MAX_LEN", "MAX_SAFE_INT"]

#: Longest text a participant may pass to a text parameter that declares no `max_len`.
TEXT_MAX_LEN = 4_000
#: Largest magnitude a number argument may have (the whole numbers JSON carries exactly).
MAX_SAFE_INT = 2**53 - 1
#: Unknown argument names listed in one correction.
_LISTED_UNKNOWN = 8
#: Significant digits kept for schema bounds and defaults (0.1 + 0.2 shows as 0.3).
_SCHEMA_DIGITS = 12
#: How far (in steps) a number may sit from a step boundary and still count as on it (float noise).
_STEP_TOLERANCE = 1e-9

_PREVIEW = reprlib.Repr()
_PREVIEW.maxstring = 60
_PREVIEW.maxother = 60
_PREVIEW.maxlevel = 3
_PREVIEW.maxlist = 6
_PREVIEW.maxdict = 6


def _preview(value: Any) -> str:
    """A short, safe rendering of an argument for a correction message, whatever it is."""
    return _PREVIEW.repr(value)


def _tidy(value: Any) -> Any:
    """Drop float noise: 0.30000000000000004 → 0.3."""
    return float(f"{value:.{_SCHEMA_DIGITS}g}") if isinstance(value, float) else value


def _item_spec(param: ParamSpec) -> ParamSpec:
    """The element spec of a list parameter: explicit `items`, or `of` / `values` shorthand."""
    if param.items is not None:
        return param.items
    if param.of is not None:
        return ParamSpec(type="entity", of=param.of, where=param.where)
    if param.values is not None:
        return ParamSpec(type="enum", values=param.values)
    return ParamSpec(type="text", max_len=param.max_len)


def _list_bounds(param: ParamSpec) -> Tuple[int, int]:
    low = param.min_items or 0
    high = min(param.max_items if param.max_items is not None else MAX_LIST_ITEMS, MAX_LIST_ITEMS)
    return low, high
