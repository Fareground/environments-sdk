"""Names the contract checker's sections share: the roots every expression may read, built-in entity and
entry fields, and record field types."""
from __future__ import annotations

from typing import Dict, Set

__all__ = ["BASE", "ENTITY_FIELDS", "ENTRY_FIELDS", "RECORD_FIELD_TYPES", "Types"]

BASE = frozenset({"inputs", "world", "physics", "clock", "round", "stage", "metrics", "series", "arm", "pending", "pattern"})
ENTITY_FIELDS = frozenset({"id", "name", "type", "alive", "at"})
ENTRY_FIELDS = frozenset({"seq", "round", "stage", "author", "to"})
RECORD_FIELD_TYPES = ("text", "number", "int", "bool", "list", "map", "any", "asset")

Types = Dict[str, Set[str]]
