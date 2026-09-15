"""Shared plumbing for the economy mechanisms: config lookup, entity and number coercion,
journaled counters and parameter builders.

Nothing here keeps state of its own. Parsed configs are cached on the world object because
they are a pure function of the (immutable) contract; every value that changes lives in world
props and entities and changes only through the world's journaled API.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Type, Union, cast

from pydantic import BaseModel, ValidationError

from ...entity import Entity
from ..errors import RunError
from ..expr import ExprError, compile_expr
from ..registry import MechanismError

__all__ = [
    "EPS", "NAME", "CONFIG_MODELS", "register_config", "config_of", "uses_of", "cached", "type_list", "require_types",
    "require_currency", "lineage", "common_ancestor", "top_types", "declared_use", "guarded", "choice_param", "entity_of",
    "maybe_entity", "props",
    "to_ids", "whole", "amount", "bump", "money", "emit_to", "compiles",
]

#: Tolerance for money comparisons (float sums).
EPS = 1e-9
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")

#: kind → config model, registered by each economy module so runtime lookups can parse any use.
CONFIG_MODELS: Dict[str, Type[BaseModel]] = {}


def register_config(kind: str, model: Type[BaseModel]) -> None:
    CONFIG_MODELS[kind] = model


def _cache(world: Any) -> Dict[Any, Any]:
    cache = world.__dict__.get("_econ_configs")
    if cache is None or cache[0] is not world.contract:
        cache = (world.contract, {})
        world.__dict__["_econ_configs"] = cache
    return cache[1]


def config_of(world: Any, name: str, kind: str, where: str = "") -> Any:
    """The parsed config of the declared mechanism ``name`` of ``kind`` (cached per contract)."""
    cache = _cache(world)
    key = ("use", name)
    if key not in cache:
        raw = world.contract.mechanisms.get(name) if isinstance(name, str) else None
        if not isinstance(raw, Mapping) or raw.get("kind") != kind:
            declared = [n for n, u in world.contract.mechanisms.items() if isinstance(u, Mapping) and u.get("kind") == kind]
            raise RunError(f"'{name}' is not a declared {kind} (declared: {', '.join(declared) or 'none'})", where)
        try:
            cache[key] = CONFIG_MODELS[kind].model_validate({k: v for k, v in raw.items() if k != "kind"})
        except ValidationError as exc:  # expansion validated it already; only a patched contract lands here
            raise RunError(f"mechanism '{name}' has an invalid config: {exc.errors()[0]['msg']}", where) from None
    return cache[key]


def uses_of(world: Any, kind: str) -> Dict[str, Any]:
    """Every declared use of ``kind``: ``{name: parsed config}``."""
    cache = _cache(world)
    key = ("kind", kind)
    if key not in cache:
        cache[key] = {name: config_of(world, name, kind) for name, use in world.contract.mechanisms.items()
                      if isinstance(use, Mapping) and use.get("kind") == kind}
    return dict(cache[key])


def cached(world: Any, key: Any, build: Any) -> Any:
    cache = _cache(world)
    if key not in cache:
        cache[key] = build()
    return cache[key]


# ---------------------------------------------------------------------------
# Expansion-time helpers
# ---------------------------------------------------------------------------


def type_list(value: Union[str, Sequence[str]]) -> List[str]:
    return [value] if isinstance(value, str) else list(value)


def require_types(contract: Mapping[str, Any], names: Sequence[str], field: str) -> None:
    types = contract.get("types") or {}
    for name in names:
        if name not in types:
            raise MechanismError(f"'{name}' is not a declared type", f"types: {', '.join(types) or 'none'}", field)


def common_ancestor(contract: Mapping[str, Any], names: Sequence[str]) -> Optional[str]:
    """The most specific type every one of ``names`` is (or extends), or None when they share none."""
    chains = [lineage(contract, name) for name in names]
    if not chains:
        return None
    return next((t for t in chains[0] if all(t in chain for chain in chains[1:])), None)


def lineage(contract: Mapping[str, Any], name: str) -> List[str]:
    """``name`` then its ancestors, nearest first (raw contract, before parsing)."""
    types = contract.get("types") or {}
    chain: List[str] = []
    current: Optional[str] = name
    while current is not None and current in types and current not in chain:
        chain.append(current)
        current = (types[current] or {}).get("extends")
    return chain


def require_currency(contract: Mapping[str, Any], currency: str, field: str = "currency") -> None:
    """Fail expansion unless some ledger declares ``currency``."""
    for use in (contract.get("mechanisms") or {}).values():
        if isinstance(use, Mapping) and use.get("kind") == "ledger" and currency in (use.get("currencies") or {}):
            return
    raise MechanismError(f"'{currency}' is not a declared currency", "declare a ledger with it", field)


def top_types(contract: Mapping[str, Any], names: Sequence[str]) -> List[str]:
    """``names`` without any type whose ancestor is also listed (so no entity is counted twice)."""
    out = []
    for name in dict.fromkeys(names):
        if not any(parent in names for parent in lineage(contract, name)[1:]):
            out.append(name)
    return out


def declared_use(contract: Mapping[str, Any], name: Optional[str], kind: str, field: str) -> Dict[str, Any]:
    """The raw config of another mechanism this one refers to (declared earlier or later)."""
    uses = contract.get("mechanisms") or {}
    use = uses.get(name) if isinstance(name, str) else None
    if not isinstance(use, Mapping) or use.get("kind") != kind:
        declared = [n for n, u in uses.items() if isinstance(u, Mapping) and u.get("kind") == kind]
        raise MechanismError(f"'{name}' is not a declared {kind} mechanism",
                             f"declare one, e.g. \"mechanisms\": {{\"{name or kind}\": {{\"kind\": \"{kind}\", ...}}}}"
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


def choice_param(types: Sequence[str], where: str, description: str) -> Tuple[Dict[str, Any], str]:
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


def entity_of(world: Any, value: Any, where: str, what: str = "an entity") -> Entity:
    found = maybe_entity(world, value)
    if found is None or not found.alive:
        raise RunError(f"expected {what}, got {value!r}", where)
    return found


def props(entity: Entity) -> Dict[str, Any]:
    """An entity's properties for reading (the legacy Entity annotates their values narrowly)."""
    return cast(Dict[str, Any], entity.properties)


def maybe_entity(world: Any, value: Any) -> Optional[Entity]:
    if isinstance(value, Entity):
        return value
    if isinstance(value, str):
        return world.entities.get(value)  # type: ignore[no-any-return]
    return None


def to_ids(value: Any) -> List[str]:
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


def bump(world: Any, prop: str, key: str, delta: float, group: Optional[str] = None) -> None:
    """Add ``delta`` to ``$world.<prop>[key]`` (or ``[group][key]``), journaled."""
    current = dict(world.props.get(prop) or {})
    if group is None:
        value = current.get(key, 0) + delta
        current[key] = int(value) if isinstance(value, float) and value.is_integer() and isinstance(delta, int) else value
    else:
        inner = dict(current.get(group) or {})
        inner[key] = inner.get(key, 0) + delta
        current[group] = inner
    world.set_world(prop, current)


def money(value: Any) -> str:
    """An amount as compact text (at most 2 decimals). Callers name the currency: money is not always dollars."""
    from ..template import format_value

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format_value(round(float(value), 2))
    return format_value(value)


def emit_to(world: Any, kind: str, text: str, to: Sequence[str], data: Optional[Dict[str, Any]] = None,
            why: Optional[str] = None) -> None:
    """Private news for some agents; ``why`` also asks the engine to tell them at their next turn."""
    recipients = [t for t in dict.fromkeys(to) if t]
    if not recipients:
        return
    world.emit(kind, text, to=recipients, data=data or {})
    if why:
        for entity_id in recipients:
            world.request_wake(entity_id, why)

