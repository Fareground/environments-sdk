"""Shared plumbing for the economy mechanisms: config lookup, entity and number coercion,
journaled counters and parameter builders.

Nothing here keeps state of its own. Parsed configs are cached on the world object because
they are a pure function of the (immutable) contract; every value that changes lives in world
props and entities and changes only through the world's journaled API.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from ..errors import RunError
from ..expr import EXPRESSION_WORDS, ExprError, compile_expr
from ..expr.objects import Entity
from ..registry import MechanismError, config_data, describe, mechanism_config, use_key
from ..world.live import Abort

__all__ = [
    "EPS", "NAME", "valid_name", "CONFIG_MODELS", "register_config", "config_of", "uses_of", "cached", "type_list",
    "require_types",
    "require_currency", "lineage", "common_ancestor", "top_types", "declared_use", "guarded", "choice_param",
    "maybe_entity", "props", "checked_config", "declared_names", "money_prop",
    "LEDGER", "INVENTORY", "PRODUCTION", "SUPPLY_CHAIN", "DEMAND", "REPLENISHMENT", "NEGOTIATION", "LABOR",
    "SUBSCRIPTIONS", "BOOKINGS",
    "to_ids", "whole", "amount", "bump", "money", "emit_to", "compiles", "run_hook",
]

#: Tolerance for money comparisons (float sums).
EPS = 1e-9
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")

#: ``family.mode`` of every economy and agreements mode.
LEDGER, INVENTORY, PRODUCTION, SUPPLY_CHAIN = ("economy.ledger", "economy.inventory", "economy.production",
                                               "economy.supply_chain")
DEMAND, REPLENISHMENT = "economy.demand", "economy.replenishment"
NEGOTIATION, LABOR, SUBSCRIPTIONS, BOOKINGS = ("agreements.negotiation", "agreements.labor", "agreements.subscriptions",
                                               "agreements.bookings")

#: ``family.mode`` → config model, registered by each module so runtime lookups can parse any use.
CONFIG_MODELS: dict[str, type[BaseModel]] = {}


def valid_name(name: str) -> bool:
    """Letters, digits and _, starting with a letter, and not a word expressions use themselves (`in`, `not`)."""
    return bool(NAME.match(name)) and name not in EXPRESSION_WORDS


def register_config(kind: str, model: type[BaseModel]) -> None:
    CONFIG_MODELS[kind] = model


def _cache(world: Any) -> dict[Any, Any]:
    cache = world.__dict__.get("_econ_configs")
    if cache is None or cache[0] is not world.contract:
        cache = (world.contract, {})
        world.__dict__["_econ_configs"] = cache
    return cache[1]


def config_of(world: Any, name: str, kind: str, where: str = "") -> Any:
    """The parsed config of the declared mechanism ``name`` of ``kind`` (its model registered by its module)."""
    return mechanism_config(world, name, kind, CONFIG_MODELS[kind], where or None)


def uses_of(world: Any, kind: str) -> dict[str, Any]:
    """Every declared use of ``kind``: ``{name: parsed config}``."""
    cache = _cache(world)
    key = ("kind", kind)
    if key not in cache:
        cache[key] = {name: config_of(world, name, kind) for name, use in world.contract.mechanisms.items()
                      if use_key(use) == kind}
    return dict(cache[key])


def checked_config(checker: Any, effect: Mapping[str, Any], family: str) -> Any:
    """At check time, the parsed config of the mechanism a family op names (None when its config is invalid,
    which the mechanism's expansion reports already)."""
    raw = (checker.c.mechanisms or {}).get(effect.get(family))
    model = CONFIG_MODELS.get(use_key(raw) or "")
    if model is None:
        return None
    try:
        return model.model_validate(config_data(raw))  # type: ignore[arg-type]
    except ValidationError:
        return None


def cached(world: Any, key: Any, build: Any) -> Any:
    cache = _cache(world)
    if key not in cache:
        cache[key] = build()
    return cache[key]


# ---------------------------------------------------------------------------
# Expansion-time helpers
# ---------------------------------------------------------------------------


def type_list(value: str | Sequence[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def require_types(contract: Mapping[str, Any], names: Sequence[str], field: str) -> None:
    types = contract.get("types") or {}
    for name in names:
        if name not in types:
            raise MechanismError(f"'{name}' is not a declared type", f"types: {', '.join(types) or 'none'}", field)


def common_ancestor(contract: Mapping[str, Any], names: Sequence[str]) -> str | None:
    """The most specific type every one of ``names`` is (or extends), or None when they share none."""
    chains = [lineage(contract, name) for name in names]
    if not chains:
        return None
    return next((t for t in chains[0] if all(t in chain for chain in chains[1:])), None)


def lineage(contract: Mapping[str, Any], name: str) -> list[str]:
    """``name`` then its ancestors, nearest first (raw contract, before parsing)."""
    types = contract.get("types") or {}
    chain: list[str] = []
    current: str | None = name
    while current is not None and current in types and current not in chain:
        chain.append(current)
        current = (types[current] or {}).get("extends")
    return chain


def declared_names(contract: Mapping[str, Any], key: str, field: str) -> dict[str, str]:
    """Every name under ``field`` (``currencies``, ``items``) of the declared ``key`` mechanisms: {name: mechanism}."""
    return {name: other for other, use in (contract.get("mechanisms") or {}).items() if use_key(use) == key
            for name in (use.get(field) or {})}


def require_currency(contract: Mapping[str, Any], currency: str, field: str = "currency") -> None:
    """Fail expansion unless some ledger declares ``currency``."""
    if currency not in declared_names(contract, LEDGER, "currencies"):
        raise MechanismError(f"'{currency}' is not a declared currency", "declare a ledger with it", field)


def money_prop(contract: Mapping[str, Any], holder: str, currency: str, description: str = "") -> dict[str, Any]:
    """``{currency: a number prop starting at 0}`` for a market's traders of type ``holder``, or ``{}`` when a ledger
    gives that type the currency: the ledger's starting balance then holds, whichever mechanism is declared first."""
    for use in (contract.get("mechanisms") or {}).values():
        who = use.get("who") if isinstance(use, Mapping) and use_key(use) == LEDGER else None
        if isinstance(who, (str, list)) and currency in (use.get("currencies") or {}) \
                and set(type_list(who)) & set(lineage(contract, holder)):
            return {}
    return {currency: {"type": "number", "default": 0, **({"description": description} if description else {})}}


def top_types(contract: Mapping[str, Any], names: Sequence[str]) -> list[str]:
    """``names`` without any type whose ancestor is also listed (so no entity is counted twice)."""
    out = []
    for name in dict.fromkeys(names):
        if not any(parent in names for parent in lineage(contract, name)[1:]):
            out.append(name)
    return out


def declared_use(contract: Mapping[str, Any], name: str | None, kind: str, field: str) -> dict[str, Any]:
    """The raw config of another mechanism this one refers to (declared earlier or later)."""
    uses = contract.get("mechanisms") or {}
    use = uses.get(name) if isinstance(name, str) else None
    if not isinstance(use, Mapping) or use_key(use) != kind:
        declared = [n for n, u in uses.items() if use_key(u) == kind]
        family, _, mode = kind.partition(".")
        raise MechanismError(f"'{name}' is not a declared {describe(kind)} mechanism",
                             "declare one, e.g. \"mechanisms\": "
                             f"{{\"{name or mode}\": {{\"kind\": \"{family}\", \"mode\": \"{mode}\", ...}}}}"
                             + (f" (declared: {', '.join(declared)})" if declared else ""), field)
    return dict(use)


def compiles(value: Any, field: str) -> None:
    """Fail expansion when a config value that is an expression does not compile."""
    if isinstance(value, str) and "$" in value:
        try:
            compile_expr(value)
        except ExprError as exc:
            raise MechanismError(f"is not a valid expression: {exc.detail}", f"in `{value}`", field) from None


def guarded(expr: str, *params: str) -> str:
    """A parameter bound reading earlier parameters, skipped (null) while any of them is invalid.

    The engine validates parameters in order and leaves an invalid one out of ``$params``; a bound
    that reads it would otherwise stop the run instead of reporting the invalid argument."""
    present = " and ".join(f"'{p}' in $params" for p in params)
    return f"({expr}) if {present} else null"


def choice_param(types: Sequence[str], where: str, description: str) -> tuple[dict[str, Any], str]:
    """A parameter choosing one entity of any of ``types``, and the expression reading the choice.

    One type becomes an entity parameter (names listed); several become an enum of ids whose
    values are computed for the actor, since an entity parameter names exactly one type."""
    if len(types) == 1:
        return {"type": "entity", "of": types[0], "where": where, "description": description}, "$params.{name}"
    values = " + ".join(f"$ids($filter({t}, {where}))" for t in types)
    return {"type": "enum", "values": values, "description": description + " (an id)"}, "$entity($params.{name})"


# ---------------------------------------------------------------------------
# Run-time helpers
# ---------------------------------------------------------------------------


def props(entity: Entity) -> dict[str, Any]:
    """An entity's properties for reading (the legacy Entity annotates their values narrowly)."""
    return cast(dict[str, Any], entity.properties)


def maybe_entity(world: Any, value: Any) -> Entity | None:
    if isinstance(value, Entity):
        return value
    if isinstance(value, str):
        return world.entities.get(value)  # type: ignore[no-any-return]
    return None


def to_ids(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    return [item.id if isinstance(item, Entity) else str(item) for item in items]


def whole(value: Any, where: str, what: str = "a quantity") -> int:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RunError(f"{what} must be a whole number ≥ 0, got {value!r}", where)
    return value


def amount(value: Any, where: str, what: str = "an amount") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise RunError(f"{what} must be a number ≥ 0, got {value!r}", where)
    return value


def bump(world: Any, prop: str, key: str, delta: float, group: str | None = None) -> None:
    """Add ``delta`` to ``$world.<prop>[key]`` (or ``[group][key]``), journaled."""
    current = dict(world.props.get(prop) or {})
    if group is None:
        value = current.get(key, 0) + delta
        current[key] = (int(value) if isinstance(value, float) and value.is_integer() and isinstance(delta, int)
                        else value)
    else:
        inner = dict(current.get(group) or {})
        inner[key] = inner.get(key, 0) + delta
        current[group] = inner
    world.set_world(prop, current)


def money(value: Any) -> str:
    """An amount as compact text (at most 2 decimals). Callers name the currency: money is not always dollars."""
    from ..expr.template import format_value

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format_value(round(float(value), 2))
    return format_value(value)


def emit_to(world: Any, kind: str, text: str, to: Sequence[str], data: dict[str, Any] | None = None,
            why: str | None = None) -> None:
    """Private news for some agents; ``why`` also asks the engine to tell them at their next turn."""
    recipients = [t for t in dict.fromkeys(to) if t]
    if not recipients:
        return
    world.emit(kind, text, to=recipients, data=data or {})
    if why:
        for entity_id in recipients:
            world.request_wake(entity_id, why)


def run_hook(runner: Any, block: str, args: dict[str, Any], path: str) -> None:
    """Run an author's effect block (``on_default``, ``on_breach``) for one item on its own.

    The mechanism has already recorded what happened. A refusal inside the block (``fail``, a short
    transfer) undoes only the block's own changes and is logged as a ``mechanism_refused`` event that no
    agent is shown, instead of escaping into the tick and silently rolling back everything else it did."""
    world = runner.world
    mark = world.journal.mark()
    try:
        runner.run([{"call": block, "with": {key: f"${key}" for key in args}}], dict(args), path)
    except Abort as exc:
        world.journal.rollback(mark)
        world.emit("mechanism_refused", f"{block} was refused: {exc.reason}", to=[],
                   data={"block": block, "reason": exc.reason})
