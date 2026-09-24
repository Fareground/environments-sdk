"""List functions: positions, windows, running totals, ranks and copies with one change. Nothing is modified in place.
"""
from __future__ import annotations

import math
from typing import Any

from ..expr import Call, _describe, check_size, function
from ._args import check_len, fail, int_arg, key_of, list_arg, present_numbers, sequence_arg, series_arg


@function("zip(a, b, ...)", "Lists combined position by position into [a_i, b_i, ...] (as long as the shortest).",
          min_args=2)
def _zip(call: Call) -> list[list[Any]]:
    lists = [sequence_arg(call, i) for i in range(len(call))]
    size = min(len(items) for items in lists)
    check_len(call, size * len(lists))
    return [list(row) for row in zip(*lists)]


@function("chunk(list, size)", "The list cut into consecutive pieces of `size` items (the last may be shorter).",
          min_args=2, max_args=2)
def _chunk(call: Call) -> list[list[Any]]:
    items = sequence_arg(call, 0)
    size = int_arg(call, 1, low=1, what="the piece size")
    check_len(call, len(items))
    return [items[i:i + size] for i in range(0, len(items), size)]


@function("window(list, size, step?)",
          "Sliding windows of `size` consecutive items, starting every `step` items (default 1).",
          min_args=2, max_args=3)
def _window(call: Call) -> list[list[Any]]:
    items = sequence_arg(call, 0)
    size = int_arg(call, 1, low=1, what="the window size")
    step = int_arg(call, 2, 1, low=1, what="the step")
    starts = range(0, len(items) - size + 1, step)
    check_len(call, len(starts) * size, "windows")
    return [items[i:i + size] for i in starts]


@function("index(list, value)", "Position of the first item equal to `value` (entities match their id), or -1.",
          min_args=2, max_args=2)
def _index(call: Call) -> int:
    target = key_of(call.arg(1))
    for position, item in enumerate(sequence_arg(call, 0)):
        if key_of(item) == target:
            return position
    return -1


def _scores(call: Call) -> list[Any | None]:
    """One number (or null) per item: the items themselves, or the per-item `value` argument."""
    items = call.collection(0)
    raw = items if len(call) < 2 else [call.each(1, it, i) for i, it in enumerate(items)]
    present_numbers(call, raw, "the values compared")
    return raw


@function("rank(items, value?)",
          "Rank of each item, 1 = largest; ties share the best rank (1, 2, 2, 4); a null value ranks null.",
          min_args=1, max_args=2, lazy=[1])
def _rank(call: Call) -> list[int | None]:
    scores = _scores(call)
    ordered = sorted((s for s in scores if s is not None), reverse=True)
    first_at: dict[Any, int] = {}
    for position, score in enumerate(ordered, 1):
        first_at.setdefault(score, position)
    return [None if s is None else first_at[s] for s in scores]


@function("cumsum(series)", "Running totals: item i is the sum of items 0..i.", min_args=1, max_args=1)
def _cumsum(call: Call) -> list[Any]:
    total: Any = 0
    out = []
    for value in series_arg(call, 0):
        total += value
        if isinstance(total, float) and not math.isfinite(total):
            raise fail(call, "the running total overflowed")
        out.append(total)
    return out


@function("diff(series)", "Differences between consecutive items (one shorter than the series).", min_args=1,
          max_args=1)
def _diff(call: Call) -> list[Any]:
    values = series_arg(call, 0)
    return [b - a for a, b in zip(values, values[1:])]


@function("rotate(list, k)", "The list rotated left by `k` places (negative rotates right).", min_args=2, max_args=2)
def _rotate(call: Call) -> list[Any]:
    items = sequence_arg(call, 0)
    k = int_arg(call, 1, what="the number of places")
    if not items:
        return []
    k %= len(items)
    return items[k:] + items[:k]


def _position(call: Call, size: int, allow_end: bool) -> int:
    index = int_arg(call, 1, what="a position (negative counts from the end)")
    high = size if allow_end else size - 1
    if not -size <= index <= high:
        raise fail(call, f"position {index} is out of range for a list of {size} items")
    return index + size if index < 0 else index


@function("insert(list, index, value)",
          "A copy of the list with `value` inserted before position `index` (the length appends).",
          min_args=3, max_args=3)
def _insert(call: Call) -> list[Any]:
    items = list_arg(call, 0)
    at = _position(call, len(items), allow_end=True)
    return check_size(items[:at] + [call.arg(2)] + items[at:], call.source)


@function("remove_at(list, index)", "A copy of the list without the item at `index`.", min_args=2, max_args=2)
def _remove_at(call: Call) -> list[Any]:
    items = list_arg(call, 0)
    at = _position(call, len(items), allow_end=False)
    return items[:at] + items[at + 1:]


@function("set_at(list, index, value)", "A copy of the list with the item at `index` replaced by `value`.",
          min_args=3, max_args=3)
def _set_at(call: Call) -> list[Any]:
    items = list_arg(call, 0)
    if not items:
        raise fail(call, f"cannot set a position in an empty list, got {_describe(items)}")
    at = _position(call, len(items), allow_end=False)
    return items[:at] + [call.arg(2)] + items[at + 1:]
