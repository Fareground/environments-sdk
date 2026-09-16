"""Validate authored inventory literals before generated supply totals evaluate."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from .expr import is_expr
from .registry import config_data, use_key

if TYPE_CHECKING:
    from .check import _Checker


def check_inventory(checker: "_Checker") -> None:
    contract = checker.c
    for name, raw in contract.mechanisms.items():
        if use_key(raw) != "economy.inventory":
            continue
        config = config_data(raw)
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
        for index, group in enumerate(contract.population):
            path = f"population[{index}]"
            if applies(group.type):
                if prop in group.props:
                    value(group.props[prop], f"{path}.props.{prop}")
                for mix_index, archetype in enumerate(group.mix):
                    if prop in archetype.props:
                        value(archetype.props[prop], f"{path}.mix[{mix_index}].props.{prop}")
            for member_index, member in enumerate(group.members):
                if applies(member.type) and prop in member.props:
                    value(member.props[prop], f"{path}.members[{member_index}].props.{prop}")
