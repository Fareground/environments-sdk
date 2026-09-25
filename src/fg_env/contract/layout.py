"""A contract written back as JSON text a person reads: sections in the contract's own order, a section or entry
on one line when it fits (or when it is one spec, holding nothing nested to break up), one line per item otherwise.
``fg-env migrate --write``, ``fg-env new`` and ``fg_env.engines.clone`` write contracts this way."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

__all__ = ["ordered", "dumps"]

#: Longest line an entry is kept on when it holds nested lists or objects (an entry of plain values, such as an
#: input with a long description, stays on one line whatever its length: breaking it would not make it easier to read).
WIDTH = 120
_INDENT = "  "


def ordered(data: Mapping[str, Any]) -> dict[str, Any]:
    """``data`` with its sections in the order the contract declares them; keys it does not know keep their place
    after them."""
    from . import Contract

    known = [key for key in Contract.model_fields if key in data]
    return {**{key: data[key] for key in known}, **{k: v for k, v in data.items() if k not in known}}


def dumps(data: Mapping[str, Any]) -> str:
    """``data`` (a contract, sections ordered) as JSON text ending in a newline."""
    return _value(ordered(data), 0) + "\n"


def _inline(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _nested(value: Any) -> bool:
    items = value.values() if isinstance(value, dict) else value
    return any(isinstance(item, (dict, list)) and item for item in items)


def _value(value: Any, depth: int, lead: int = 0) -> str:
    """``value`` written at ``depth``, on a line whose first ``lead`` characters (indent and key) are already taken."""
    if not isinstance(value, (dict, list)) or not value:
        return _inline(value)
    flat = _inline(value)
    # The contract always opens up; a section or entry stays whole when it fits, and below the sections an object of
    # plain values (one spec) stays whole however long.
    fits = lead + len(flat) + 1 <= WIDTH
    if depth >= 1 and fits or depth >= 2 and isinstance(value, dict) and not _nested(value):
        return flat
    inner = _INDENT * (depth + 1)
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            head = f"{inner}{_inline(key)}: "
            lines.append(head + _value(item, depth + 1, len(head)))
        return "{\n" + ",\n".join(lines) + "\n" + _INDENT * depth + "}"
    lines = [inner + _value(item, depth + 1, len(inner)) for item in value]
    return "[\n" + ",\n".join(lines) + "\n" + _INDENT * depth + "]"
