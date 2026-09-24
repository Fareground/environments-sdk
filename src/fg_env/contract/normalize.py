"""Earlier contract forms rewritten into the current ones, so a contract written for an earlier release still loads.

Each rule rewrites one construct wherever it finds its earlier form and leaves the current form alone, so a rule is
idempotent and a document mixing both forms works. :func:`normalize` applies every rule in order and returns the
current form with one note per rewrite (``fg-env migrate`` shows them and can write the result back)."""
from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

__all__ = ["normalize", "rule", "RULES"]

#: A rule rewrites ``data`` in place and returns a note for each rewrite it made ("path: what became what").
Rule = Callable[[dict[str, Any]], list[str]]
RULES: list[Rule] = []


def rule(fn: Rule) -> Rule:
    """Register ``fn`` as a normalization rule (rules run in registration order)."""
    RULES.append(fn)
    return fn


def normalize(data: Any) -> tuple[Any, list[str]]:
    """``data`` in the current contract form, and the notes of every rewrite; anything but an object is returned as
    is (the parser reports it)."""
    from . import normalize_mechanisms  # noqa: F401  (registers its rules; imported here: they import `rule` from here)

    if not isinstance(data, Mapping):
        return data, []
    out: dict[str, Any] = copy.deepcopy(dict(data))
    notes: list[str] = []
    for fn in RULES:
        notes.extend(fn(out))
    return out, notes

