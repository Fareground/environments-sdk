"""Set operations on lists (order of first appearance kept) and copies of maps with keys added or removed."""
from __future__ import annotations

from typing import Any, Dict, Hashable, Iterable, List

from ..expr import Untrusted, Call, _describe, charge, check_size, function
from ..functions import _keyed
from ._args import fail, key_of, map_arg, sequence_arg


def _distinct(items: Iterable[Any]) -> Dict[Hashable, Any]:
    """Items by identity in first-seen order; equal text that is participant text keeps that marker."""
    out: Dict[Hashable, Any] = {}
    for item in items:
        key = key_of(item)
        if key not in out:
            out[key] = item
        elif isinstance(item, Untrusted) and not isinstance(out[key], Untrusted):
            out[key] = item
    return out


@function("union(a, b)", "Items in either list, each once, in order of first appearance.", min_args=2, max_args=2)
def _union(call: Call) -> List[Any]:
    return check_size(list(_distinct(sequence_arg(call, 0) + sequence_arg(call, 1)).values()), call.source)


@function("intersect(a, b)", "Items of `a` that are also in `b`, each once, in `a`'s order.", min_args=2, max_args=2)
def _intersect(call: Call) -> List[Any]:
    other = _distinct(sequence_arg(call, 1))
    return [item for key, item in _distinct(sequence_arg(call, 0)).items() if key in other]


@function("difference(a, b)", "Items of `a` that are not in `b`, each once, in `a`'s order.", min_args=2, max_args=2)
def _difference(call: Call) -> List[Any]:
    other = _distinct(sequence_arg(call, 1))
    return [item for key, item in _distinct(sequence_arg(call, 0)).items() if key not in other]


@function("is_subset(a, b)", "True when every item of `a` is in `b`.", min_args=2, max_args=2)
def _is_subset(call: Call) -> bool:
    other = _distinct(sequence_arg(call, 1))
    return all(key_of(item) in other for item in sequence_arg(call, 0))


@function("merge(map, map, ...)", "One map from several; a key in a later map wins.", min_args=2)
def _merge(call: Call) -> Dict[Any, Any]:
    entries = [entry for index in range(len(call)) for entry in map_arg(call, index).items()]
    return check_size(_keyed(entries), call.source)


def _key_list(call: Call, index: int) -> List[Any]:
    value = call.arg(index)
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        charge(len(value), call.source)
        return list(value)
    raise fail(call, f"argument {index + 1} must be a key or a list of keys, got {_describe(value)}")


@function("without(map, keys)", "A copy of the map without `keys` (one key or a list); missing keys are ignored.",
          min_args=2, max_args=2)
def _without(call: Call) -> Dict[Any, Any]:
    source = map_arg(call, 0)
    drop = set(_key_list(call, 1))
    return {key: source[key] for key in source if key not in drop}


@function("pick_keys(map, keys)", "A map with only `keys` (one key or a list), in the order given; missing keys are skipped.",
          min_args=2, max_args=2)
def _pick_keys(call: Call) -> Dict[Any, Any]:
    source = map_arg(call, 0)
    stored = {key: key for key in source}
    return {stored[key]: source[key] for key in _key_list(call, 1) if key in source}


@function("items(map)", "The entries of a map as [key, value] pairs.", min_args=1, max_args=1)
def _items(call: Call) -> List[List[Any]]:
    return check_size([[key, value] for key, value in map_arg(call, 0).items()], call.source)
