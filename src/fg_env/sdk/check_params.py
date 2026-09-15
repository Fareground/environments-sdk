"""Static checks for values written as literals: parameter bounds no value can meet, and entity ids that do not exist.

Both used to surface only when a run reached them; a literal can be judged from the contract alone.
"""
from __future__ import annotations

import math
import re
from difflib import get_close_matches
from typing import Any, Dict, Optional

from . import contract as C
from .expr import is_expr
from .template import format_value

__all__ = ["check_param_bounds", "check_entity_literals"]

#: Keys of core effect ops whose value names an entity.
ENTITY_KEYS = {"transfer": ("from", "to"), "link": ("from", "to"), "unlink": ("from", "to"), "move": ("move",),
               "remove": ("remove",), "wake": ("wake",)}
#: Entity ids listed in a fix.
_LISTED = 12


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_param_bounds(checker: Any, path: str, spec: C.ActionSpec) -> None:
    """A number bound that is neither a number nor an expression, and literal bounds that leave no valid value."""
    for pname, param in spec.params.items():
        ppath = f"{path}.params.{pname}"
        if param.type == "enum" and isinstance(param.values, list) and not param.values:
            checker.error(f"{ppath}.values", "is empty, so no value can be chosen", "list the allowed values")
        if param.type not in ("number", "int"):
            continue
        literal: Dict[str, float] = {}
        for key in ("min", "max"):
            raw = getattr(param, key)
            if isinstance(raw, str) and not is_expr(raw):
                checker.error(f"{ppath}.{key}", f"must be a number or an expression with $, got the text '{raw}'",
                              f'e.g. "{key}": 1 or "{key}": "$actor.coins"')
            elif _number(raw):
                literal[key] = float(raw)  # type: ignore[arg-type]
        if len(literal) < 2:
            continue
        low, high = literal["min"], literal["max"]
        if param.type == "int":
            low, high = math.ceil(low), math.floor(high)
        if low > high:
            kind = "whole number" if param.type == "int" else "number"
            checker.error(ppath, f"no {kind} is at least {format_value(literal['min'])} and at most "
                                 f"{format_value(literal['max'])}", "lower `min` or raise `max`")


def check_entity_literals(checker: Any, op: str, effect: Dict[str, Any], path: str) -> None:
    """A plain id (no `$`) where an effect names an entity must be one that can exist."""
    contract: C.Contract = checker.c
    if any(group.id is not None for group in contract.population):
        return  # ids come from templates: any text may be one
    generated = re.compile(r"(?:" + "|".join(re.escape(t) for t in contract.types) + r")_\d+") if contract.types else None
    for key in ENTITY_KEYS.get(op, ()):
        raw = effect.get(key)
        if not isinstance(raw, str) or is_expr(raw) or "{" in raw or not raw.strip():
            continue
        if raw in contract.entities or (generated is not None and generated.fullmatch(raw)):
            continue
        checker.error(f"{path}.{key}", f"'{raw}' is not an entity id", _entity_fix(contract, raw))


def _entity_fix(contract: C.Contract, raw: str) -> Optional[str]:
    hint = get_close_matches(raw, list(contract.entities), n=1)
    if hint:
        return f"did you mean '{hint[0]}'?"
    listed = ", ".join(list(contract.entities)[:_LISTED])
    return (f"entity ids: {listed}; " if listed else "") + "or name one with an expression like $params.target"
