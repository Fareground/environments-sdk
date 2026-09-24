"""What tool schemas and argument validation share: argument limits, short previews of arguments, and the
element spec and length bounds of list parameters."""
from __future__ import annotations

import re
import reprlib
from collections.abc import Callable
from typing import Any

from ..contract import MAX_LIST_ITEMS, ParamSpec
from ..errors import RunError

__all__ = ["TEXT_MAX_LEN", "MAX_SAFE_INT", "MAX_ARG_DEPTH", "REFUSED_ARGS", "unbounded", "choice_list"]

#: Longest text a participant may pass to a text parameter that declares no `max_len`.
TEXT_MAX_LEN = 4_000
#: Largest magnitude a number argument may have (the whole numbers JSON carries exactly).
MAX_SAFE_INT = 2**53 - 1
#: How deeply the lists and objects of one call's arguments may nest; deeper is refused as an invalid call.
MAX_ARG_DEPTH = 32
#: The only key of the arguments a call is recorded with when its own were refused before validation (see
#: :func:`unbounded`): not a name any parameter can have, so the call is invalid however it is replayed.
REFUSED_ARGS = "<refused arguments>"
#: Unknown argument names listed in one correction.
_LISTED_UNKNOWN = 8
#: Significant digits kept for schema bounds and defaults (0.1 + 0.2 shows as 0.3).
_SCHEMA_DIGITS = 12
#: How far (in steps) a number may sit from a step boundary and still count as on it (float noise).
_STEP_TOLERANCE = 1e-9

_SURROGATE = re.compile("[\ud800-\udfff]")

_PREVIEW = reprlib.Repr()
_PREVIEW.maxstring = 60
_PREVIEW.maxother = 60
_PREVIEW.maxlevel = 3
_PREVIEW.maxlist = 6
_PREVIEW.maxdict = 6


def _preview(value: Any) -> str:
    """A short, safe rendering of an argument for a correction message, whatever it is."""
    return _PREVIEW.repr(value)


def unbounded(args: Any) -> str | None:
    """Why a call's arguments cannot even be looked at — lists and objects nested more than :data:`MAX_ARG_DEPTH` deep
    (copying or checking them would exhaust Python's stack), or text holding a lone surrogate (it cannot be encoded, so
    it would poison the saved result) — or None. Walks them without recursion."""
    pending: list[tuple[Any, int]] = [(args, 0)]
    while pending:
        value, depth = pending.pop()
        if isinstance(value, str):
            if _SURROGATE.search(value):
                return "text holds an invalid character (a lone surrogate, which no text encoding can carry)"
        elif isinstance(value, (list, tuple, dict)):
            if depth >= MAX_ARG_DEPTH:
                return f"the arguments nest lists and objects more than {MAX_ARG_DEPTH} levels deep"
            items = [*value.keys(), *value.values()] if isinstance(value, dict) else value
            pending.extend((item, depth + 1) for item in items)
    return None


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


def _list_bounds(param: ParamSpec, count: Callable[[Any, str], int | None]) -> tuple[int, int | None]:
    """A list parameter's fewest and most elements (None: no more than its candidates). ``count(raw, key)`` resolves a
    bound (a number or an expression; None when it cannot be known yet). A list of distinct choices (entities or listed
    values, `unique`) can never hold more than there are candidates, so only its own `max_items` caps it — a ranking of
    every applicant must fit however many apply; any other list is capped at :data:`MAX_LIST_ITEMS`."""
    low = count(param.min_items, "min_items") if param.min_items is not None else None
    high = count(param.max_items, "max_items") if param.max_items is not None else None
    if choice_list(param):
        return low or 0, high
    return low or 0, min(high if high is not None else MAX_LIST_ITEMS, MAX_LIST_ITEMS)


def choice_list(param: ParamSpec) -> bool:
    """Whether a list parameter holds distinct choices: entities or listed values, each at most once."""
    return param.unique and _item_spec(param).type in ("entity", "enum")


def _item_count(value: Any, path: str) -> int | None:
    """A resolved `min_items` / `max_items`: a whole number ≥ 0 (None stays unknown)."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RunError(f"must be a whole number ≥ 0, got {_preview(value)}", path)
    return value
