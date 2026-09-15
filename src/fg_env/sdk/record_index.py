"""Derived author index for records with an exact author-only visibility rule."""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
from typing import Dict, Iterable, List, Mapping, Optional

from .world_parts import Entry


@lru_cache(maxsize=512)
def author_only(source: str) -> bool:
    # Recognize only these complete predicates. General policies, including an
    # additional condition or function call, retain ordinary evaluation.
    return "".join(source.split()) in ("$viewer.id==$it.author", "$it.author==$viewer.id")


class RecordAuthors:
    def __init__(self, names: Iterable[str], rows: Mapping[str, List[Entry]]):
        self.by_record: Dict[str, Dict[Optional[str], OrderedDict[int, Entry]]] = {name: {} for name in names}
        for name in self.by_record:
            for row in rows.get(name, ()):
                self.add(name, row)

    def add(self, name: str, row: Entry, *, first: bool = False) -> None:
        owners = self.by_record.get(name)
        if owners is None:
            return
        entries = owners.setdefault(row.get("author"), OrderedDict())
        entries[row["seq"]] = row
        if first:
            entries.move_to_end(row["seq"], last=False)

    def remove(self, name: str, row: Entry) -> None:
        owners = self.by_record.get(name)
        if owners is None:
            return
        author = row.get("author")
        entries = owners.get(author)
        if entries is not None:
            entries.pop(row["seq"], None)
            if not entries:
                del owners[author]

    def for_author(self, name: str, author: str) -> Optional[List[Entry]]:
        owners = self.by_record.get(name)
        if owners is None:
            return None
        return list(owners.get(author, {}).values())
