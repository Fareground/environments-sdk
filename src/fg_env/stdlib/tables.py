"""Keyed lookups into tables: the rows whose field holds a value, found through an index instead of a scan.

``$filter($inputs.sales, $it.sku == $row.sku)`` visits every sales row for every SKU; ``$lookup($inputs.sales, sku,
$row.sku)`` finds the same rows at once. An input table is indexed the first time a field is looked up and the index
is kept for the run (inputs never change during a run); any other list is indexed for that one call.
"""
from __future__ import annotations

import weakref
from typing import Any, Dict, Hashable, List, Tuple

from ..expr import Call, _describe, charge, function
from ._args import fail

__all__ = ["rows_by"]

Index = Dict[Hashable, List[Any]]
#: Indexes of input tables per run: ``{world: {(id(table), fields): index}}``; dropped with the run.
_INDEXES: "weakref.WeakKeyDictionary[Any, Dict[Tuple[int, Tuple[str, ...]], Index]]" = weakref.WeakKeyDictionary()


def _fields(call: Call, index: int) -> Tuple[str, ...]:
    raw = call.arg(index)
    names = raw if isinstance(raw, (list, tuple)) else [raw]
    if not names or not all(isinstance(name, str) and name for name in names):
        raise fail(call, f"argument {index + 1} must be a field name or a list of field names, got {_describe(raw)}")
    return tuple(names)


def _table(call: Call) -> List[Any]:
    table = call.arg(0)
    if not isinstance(table, list):
        hint = "; for entities use $filter" if isinstance(table, str) and call.scope.world.is_type(table) else ""
        raise fail(call, f"argument 1 must be a table (a list of rows), got {_describe(table)}{hint}")
    return table


def _key(value: Any, call: Call) -> Hashable:
    if isinstance(value, (list, tuple)):
        return tuple(_key(item, call) for item in value)
    if isinstance(value, (dict, set)):
        raise fail(call, f"a key must be text, a number, true/false or null, got {_describe(value)}")
    return value  # type: ignore[no-any-return]


def rows_by(call: Call, table: List[Any], fields: Tuple[str, ...]) -> Index:
    """``{key: rows}`` for ``table`` keyed by ``fields`` (one field's value, or a tuple of several)."""
    world = call.scope.world
    inputs = getattr(world, "inputs", None) or {}
    shared = any(table is value for value in inputs.values())
    cache: Dict[Tuple[int, Tuple[str, ...]], Index] = {}
    if shared:
        try:
            cache = _INDEXES.setdefault(world, {})
        except TypeError:  # a world that cannot be referenced weakly keeps no index
            shared = False
    found = cache.get((id(table), fields)) if shared else None
    if found is not None:
        return found
    charge(len(table), call.source)
    built: Index = {}
    for position, row in enumerate(table):
        if not isinstance(row, dict):
            raise fail(call, f"row {position} is {_describe(row)}, not a row with fields")
        try:
            key = _key(row[fields[0]] if len(fields) == 1 else tuple(row[f] for f in fields), call)
        except KeyError as missing:
            raise fail(call, f"row {position} has no field {missing} (fields: {', '.join(map(str, row)) or 'none'})") from None
        built.setdefault(key, []).append(row)
    if shared:
        cache[(id(table), fields)] = built
    return built


@function("lookup(table, field, key)",
          "The rows of `table` whose `field` equals `key`, in table order (an empty list when none): "
          "$lookup($inputs.sales, sku, $row.sku) finds them through an index built once per run instead of scanning "
          "the table for every SKU. `field` may be a list of fields with `key` a list of values.",
          min_args=3, max_args=3)
def _lookup(call: Call) -> List[Any]:
    fields = _fields(call, 1)
    index = rows_by(call, _table(call), fields)
    raw = call.arg(2)
    if len(fields) > 1 and (not isinstance(raw, (list, tuple)) or len(raw) != len(fields)):
        raise fail(call, f"{len(fields)} fields need a list of {len(fields)} key values, got {_describe(raw)}")
    rows = index.get(_key(raw, call), [])
    charge(len(rows), call.source)
    return list(rows)


@function("lookup_one(table, field, key, default?)",
          "The first row of `table` whose `field` equals `key`, or `default` (null) when none: "
          "$lookup_one($inputs.models, model, $row.model).msrp. Indexed like $lookup.", min_args=3, max_args=4)
def _lookup_one(call: Call) -> Any:
    rows = _lookup(call)
    return rows[0] if rows else call.arg(3)
