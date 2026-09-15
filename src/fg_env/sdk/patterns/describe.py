"""Patterns in plain words, for ``fg_env.describe``: what each is, what it is kept per, how it is read, and whether
it was fitted or carries estimation uncertainty."""
from __future__ import annotations

from typing import Any, List

from ..expr import is_expr
from .base import KINDS
from .expand import validated

__all__ = ["pattern_rows"]


def _per(cfg: Any) -> str:
    if isinstance(cfg.keys, str) and not is_expr(cfg.keys):
        return f"each {cfg.keys}"
    if cfg.table is not None:
        return f"each row of {cfg.table} (by {cfg.column})"
    if cfg.keys is not None:
        return "each key"
    return "the whole world"


def pattern_rows(contract: Any) -> List[List[Any]]:
    """``[pattern, what it is, kind, per, read as, notes]`` for every declared pattern."""
    rows = []
    for name, spec in contract.patterns.items():
        cfg, _ = validated(name, spec)
        if cfg is None:
            continue
        kind = KINDS[cfg.kind]
        words = kind.words(cfg) or kind.doc
        about = f"{cfg.description} — {words}" if cfg.description else words
        args = [*kind.arg_names(cfg), *(["key"] if cfg.keyed else [])]
        read = f"$pattern.{name}({', '.join(args)})" if args else f"$pattern.{name}"
        notes = [text for flag, text in ((cfg.fit is not None, f"fitted from {cfg.fit.data if cfg.fit else ''}"),
                                         (bool(cfg.uncertainty), "parameters drawn around their estimates each run"),
                                         (cfg.record, "recorded every round")) if flag]
        rows.append([name, about, f"{cfg.kind} ({kind.group})", _per(cfg), f"`{read}`", "; ".join(notes)])
    return rows
