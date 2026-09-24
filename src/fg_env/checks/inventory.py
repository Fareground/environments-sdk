"""Validate authored inventory literals before generated supply totals evaluate."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..expr import is_expr
from ..registry import config_data, use_key

if TYPE_CHECKING:
    from .core import Checker


def check_inventory(checker: Checker) -> None:
    for name, raw in checker.c.mechanisms.items():
        if use_key(raw) == "economy.inventory":
            _check_one(checker, name, config_data(raw))


def _check_one(checker: Checker, name: str, config: Mapping[str, Any]) -> None:
    """The authored literals of one inventory mechanism."""
    contract = checker.c
    prop = config.get("prop") or name
    items = config.get("items", {})
    stackable = {item for item, spec in items.items() if not spec.get("unique", False)}

    def applies(kind: str) -> bool:
        # Runtime ownership follows declared inventory properties, including
        # types that supplied the property without being listed in who.
        return prop in checker.type_props.get(kind, set())

    def value(stock: Any, path: str) -> None:
        if not isinstance(stock, Mapping):
            return  # whole-map expressions and ordinary property shape checks
        for item, quantity in stock.items():
            field = f"{path}.{item}"
            if item not in stackable:
                checker.error(field, f"'{item}' is not a stackable item of inventory '{name}'",
                              "use a declared stackable item; unique items are entities with an owner")
            elif isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
                fix = "use a non-negative integer count"
                if isinstance(quantity, str) and is_expr(quantity):
                    fix = f"use one expression for the entire map, e.g. \"{{{item!r}: {quantity}}}\""
                checker.error(field, f"inventory quantity must be a non-negative integer, got {quantity!r}", fix)

    for kind in contract.types:
        if not applies(kind):
            continue
        # Locate the authored default even when this holder inherits it.
        for ancestor in reversed(contract.lineage(kind)):
            spec = contract.types[ancestor].props.get(prop)
            if spec is not None and "default" in spec.model_fields_set:
                value(spec.default, f"types.{ancestor}.props.{prop}.default")
                break
    for entity_id, entity in contract.entities.items():
        if applies(entity.type) and prop in entity.props:
            value(entity.props[prop], f"entities.{entity_id}.props.{prop}")
