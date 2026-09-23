"""Derived candidates for exact reader/record equality; permissions stay live."""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .expr import ExprError
from .expr.values import attr, _entity_id
from .world_parts import Entry

# Unsupported values and unresolved references must still receive ordinary checks.
FALLBACK = object()


@lru_cache(maxsize=512)
def author_only(source: str) -> bool:
    return "".join(source.split()) in ("$viewer.id==$it.author", "$it.author==$viewer.id")


@lru_cache(maxsize=512)
def equality_fields(source: str) -> Optional[Tuple[str, str]]:
    if author_only(source):
        return "id", "author"
    # Match the entire predicate, without modifying literals or other syntax.
    field = r"([A-Za-z][A-Za-z0-9_]*)"
    for left, right in (("viewer", "it"), ("it", "viewer")):
        match = re.fullmatch(rf"\s*\${left}\.{field}\s*==\s*\${right}\.{field}\s*", source)
        if match:
            a, b = match.groups()
            viewer, record = (a, b) if left == "viewer" else (b, a)
            # Entry.author resolves a live entity, unlike stored record fields.
            if record != "author":
                return viewer, record
    return None


def scalar_key(value: Any) -> Any:
    value = _entity_id(value)
    return value if type(value) in (str, int, float, bool, type(None)) else FALLBACK


def viewer_key(source: str, viewer: Any) -> Any:
    fields = equality_fields(source)
    if fields is None:
        return FALLBACK
    try:
        return scalar_key(attr(viewer, fields[0], source))
    except ExprError:
        # Preserve ordinary error timing, including recipient checks and empty sets.
        return FALLBACK


def entry_key(source: str, row: Entry) -> Any:
    fields = equality_fields(source)
    if fields is None or fields[1] not in row:
        return FALLBACK
    return scalar_key(row[fields[1]])


class RecordAuthors:
    """Indexes author-only and simple field-equality records by immutable row data."""

    def __init__(self, rules: Mapping[str, str], rows: Mapping[str, List[Entry]]):
        self.rules = {name: rule for name, rule in rules.items() if equality_fields(rule) is not None}
        self.by_record: Dict[str, Dict[Any, OrderedDict[int, Entry]]] = {name: {} for name in self.rules}
        for name in self.by_record:
            for row in rows.get(name, ()):
                self.add(name, row)

    def add(self, name: str, row: Entry, *, first: bool = False) -> None:
        owners = self.by_record.get(name)
        if owners is None:
            return
        entries = owners.setdefault(entry_key(self.rules[name], row), OrderedDict())
        entries[row["seq"]] = row
        if first:
            entries.move_to_end(row["seq"], last=False)

    def remove(self, name: str, row: Entry) -> None:
        owners = self.by_record.get(name)
        if owners is None:
            return
        key = entry_key(self.rules[name], row)
        entries = owners.get(key)
        if entries is not None:
            entries.pop(row["seq"], None)
            if not entries:
                del owners[key]

    def candidates(self, name: str, viewer: Any) -> Optional[List[Entry]]:
        owners = self.by_record.get(name)
        if owners is None:
            return None
        key = viewer_key(self.rules[name], viewer)
        if key is FALLBACK:
            return None
        rows = list(owners.get(key, {}).values())
        fallback = owners.get(FALLBACK)
        if fallback:
            rows.extend(fallback.values())
            rows.sort(key=lambda row: row["seq"])
        return rows
