"""Shared plumbing for the native mechanism families.

* Config lookup: a mechanism's validated config, parsed once per contract.
* Action hooks: families attach extra ``when`` conditions and effects to actions the contract
  already declares (a status that blocks ``attack``, a cooldown on ``fireball``) by returning an
  ``action_hooks`` section, which the spine's merge applies.
* Modifiers: ``$effective(entity, prop)`` — a property with every active modifier applied. Status
  and terrain families register modifier sources here.
* Small value helpers shared by the families (numbers, entity lists, frozen params, checks).
"""
from __future__ import annotations

import json
import math
import re
from difflib import get_close_matches
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple, Type, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..errors import RunError
from ..captures import CAPTURE_VERSION, freeze, thaw
from ..expr import Call, ExprError, compile_expr, function, is_expr, truthy
from ..registry import MechanismError, config_data, describe, use_key

__all__ = [
    "Config", "Number", "Effects", "ModifierSpec", "NAME", "MODIFIER_SOURCES", "parsed", "uses", "config",
    "actions_by", "types_in", "suggest", "evaluate", "condition", "number", "whole", "entities_of",
    "freeze", "thaw", "CAPTURE_VERSION", "canonical", "modifier_terms", "check_names", "carriers", "raw_is_a", "is_agent_type",
]

M = TypeVar("M", bound=BaseModel)

#: A number, or an expression giving one.
Number = Union[float, str]
Effects = List[Any]
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")


class Config(BaseModel):
    """Base for mechanism configs: unknown fields are errors."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


#: How a mode that generates several agent tools offers them (the spine applies it after expansion).
ToolsSetting = Literal["each", "one", "auto"]


def tools_field() -> Any:
    """The ``tools`` field every mode that generates several agent tools declares, with one meaning."""
    return Field("each", description="How the generated tools are offered: each (one tool per action) | one (one tool "
                                     "named after the mechanism, whose `action` argument lists the actions legal now) | "
                                     "auto (one tool only when every action takes the same arguments).")


class ModifierSpec(Config):
    """How a status or a place changes a property: ``(base + add) * mul`` (per stack for statuses)."""

    add: Number = Field(0.0, description="Added to the property (number or expression over $it).")
    mul: Number = Field(1.0, description="Multiplies the property (number or expression over $it).")


# ---------------------------------------------------------------------------
# Config lookup
# ---------------------------------------------------------------------------

_CACHE: Dict[Tuple[int, str], Tuple[Any, Any]] = {}
_CACHE_LIMIT = 1_024


def parsed(raw: Mapping[str, Any], model: Type[M]) -> M:
    """``raw`` (a contract's mechanism entry) validated as ``model``; parsed once per entry object."""
    key = (id(raw), model.__name__)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] is raw:
        return hit[1]  # type: ignore[no-any-return]
    value = model.model_validate(config_data(raw))
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[key] = (raw, value)
    return value


def uses(contract: Any, kind: str) -> List[Tuple[str, Mapping[str, Any]]]:
    """``(name, raw config)`` of every mechanism of ``kind`` in a parsed contract or contract data."""
    mechanisms = contract.get("mechanisms") if isinstance(contract, Mapping) else contract.mechanisms
    return [(name, raw) for name, raw in (mechanisms or {}).items()
            if use_key(raw) == kind]


def config(world: Any, name: str, kind: str, model: Type[M], where: str) -> M:
    """The config of the mechanism ``name`` of ``kind`` at run time."""
    raw = world.contract.mechanisms.get(name)
    if not isinstance(raw, Mapping) or use_key(raw) != kind:
        declared = [n for n, _ in uses(world.contract, kind)]
        hint = get_close_matches(str(name), declared, n=1)
        raise RunError(f"'{name}' is not a declared {describe(kind)} mechanism"
                       + (f" — did you mean '{hint[0]}'?" if hint else ""), where)
    return parsed(raw, model)


# ---------------------------------------------------------------------------
# Expansion helpers (contract data, before validation)
# ---------------------------------------------------------------------------


def suggest(name: str, options: Iterable[str]) -> str:
    listed = sorted(options)
    hint = get_close_matches(str(name), listed, n=1)
    return f"did you mean '{hint[0]}'?" if hint else f"choices: {', '.join(listed) or 'none'}"


def types_in(contract: Mapping[str, Any], names: Union[str, Sequence[str]], field: str) -> List[str]:
    """``names`` as a list, each a declared type."""
    listed = [names] if isinstance(names, str) else list(names)
    declared = contract.get("types") or {}
    for name in listed:
        if name not in declared:
            raise MechanismError(f"'{name}' is not a declared type", suggest(name, declared), field)
    return listed


def raw_is_a(contract: Mapping[str, Any], type_name: str, ancestor: str) -> bool:
    """``type_name`` is ``ancestor`` or extends it (on contract data)."""
    types = contract.get("types") or {}
    seen: List[str] = []
    current: Optional[str] = type_name
    while current is not None and current not in seen:
        if current == ancestor:
            return True
        seen.append(current)
        spec = types.get(current)
        current = spec.get("extends") if isinstance(spec, Mapping) else None
    return False


def is_agent_type(contract: Mapping[str, Any], type_name: str) -> bool:
    """``type_name`` is an agent type, or extends one (on contract data)."""
    types = contract.get("types") or {}
    seen: List[str] = []
    current: Optional[str] = type_name
    while current is not None and current not in seen and isinstance(types.get(current), Mapping):
        if types[current].get("agent"):
            return True
        seen.append(current)
        current = types[current].get("extends")
    return False


def actions_by(contract: Mapping[str, Any], type_names: Sequence[str]) -> List[str]:
    """Actions (declared so far) that agents of any of ``type_names`` may take."""
    out = []
    for name, action in (contract.get("actions") or {}).items():
        if not isinstance(action, Mapping):
            continue
        by = action.get("by")
        allowed = [by] if isinstance(by, str) else list(by or [])
        if any(raw_is_a(contract, t, a) or raw_is_a(contract, a, t) for t in type_names for a in allowed):
            out.append(name)
    return out


def check_names(contract: Mapping[str, Any], names: Union[str, Sequence[str]], field: str,
                carrier_types: Sequence[str]) -> List[str]:
    """Action names (or "all": every action the carriers may take) that must be declared."""
    if names == "all":
        return actions_by(contract, carrier_types)
    declared = contract.get("actions") or {}
    listed = [names] if isinstance(names, str) else list(names)
    for name in listed:
        if name not in declared:
            raise MechanismError(f"there is no action '{name}'", suggest(name, declared), field)
    return listed


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def base_roots() -> frozenset:
    """Roots every contract expression may read (``$inputs``, ``$world``, ``$round`` …), for op checks."""
    from ..check import BASE  # imported late: the checker imports the effect ops, which import this module

    return BASE


# ---------------------------------------------------------------------------
# Run-time value helpers
# ---------------------------------------------------------------------------


def evaluate(world: Any, raw: Any, where: str, **roots: Any) -> Any:
    """A literal as is, or an expression evaluated with ``roots``."""
    if not is_expr(raw):
        return raw
    try:
        return compile_expr(raw)(world.scope(**roots))
    except ExprError as exc:
        raise RunError(str(exc), where) from None


def condition(world: Any, raw: str, where: str, **roots: Any) -> bool:
    """A predicate is an expression, including constant expressions without $ references."""
    try:
        return truthy(compile_expr(raw)(world.scope(**roots)))
    except ExprError as exc:
        raise RunError(str(exc), where) from None


def number(value: Any, where: str, what: str = "a number") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RunError(f"must be {what}, got {value!r}", where)
    return value


def whole(value: Any, where: str, low: int = 1) -> int:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise RunError(f"must be a whole number ≥ {low}, got {value!r}", where)
    return value


def entities_of(world: Any, value: Any, where: str) -> List[Entity]:
    """Entities from an entity, an id, or a list of them (nulls skipped)."""
    items = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for item in items:
        if item is None:
            continue
        found = world.entity(item)
        if found is None:
            raise RunError(f"expected an entity or an entity id, got {item!r}", where)
        out.append(found)
    return out


def carriers(world: Any, type_names: Sequence[str]) -> List[Entity]:
    """Alive entities of any of the types (subtypes included), once each, in creation order."""
    kinds = {kind for type_name in type_names for kind in world.contract.subtypes(type_name)}
    return [e for e in world.entities.values() if e.alive and e.entity_type in kinds]


def plain(value: Any) -> Any:
    """Entities as ids, deeply: how a winner or an emitted value is stored."""
    if isinstance(value, Entity):
        return value.id
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, Mapping):
        return {k: plain(v) for k, v in value.items()}
    return value


def by_types(action: Any) -> List[str]:
    """The agent types that may take an action (an ActionSpec or action data)."""
    by = action.get("by") if isinstance(action, Mapping) else action.by
    return [by] if isinstance(by, str) else list(by or [])


# ---------------------------------------------------------------------------
# Modifiers and $effective
# ---------------------------------------------------------------------------

#: ``source(world, entity, prop)`` → ``[(add, mul), ...]`` for every modifier on that property.
ModifierSource = Callable[[Any, Entity, str], Iterable[Tuple[float, float]]]
MODIFIER_SOURCES: List[ModifierSource] = []


def modifier_terms(world: Any, raw: Any, entity: Entity, where: str) -> Tuple[float, float]:
    """``(add, mul)`` of one modifier: a number (added), an expression (added) or ``{add, mul}``."""
    if isinstance(raw, ModifierSpec):
        add, mul = raw.add, raw.mul
    elif isinstance(raw, Mapping):
        add, mul = raw.get("add", 0), raw.get("mul", 1)
    else:
        add, mul = raw, 1
    return (number(evaluate(world, add, where, it=entity), where),
            number(evaluate(world, mul, where, it=entity), where))


@function("effective(entity, prop)",
          "The property with every active modifier applied: (base + adds) × multipliers from statuses and "
          "terrain, kept within the property's min/max, e.g. $effective($actor, 'armor').",
          min_args=2, max_args=2)
def _effective(call: Call) -> Any:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    prop = call.arg(1)
    if entity is None:
        raise ExprError(f"$effective: expected an entity, got {call.arg(0)!r}", call.source)
    if not isinstance(prop, str) or prop not in entity.properties:
        raise ExprError(f"$effective: {entity.entity_type} has no property {prop!r}", call.source)
    base = entity.properties[prop]
    if isinstance(base, bool) or not isinstance(base, (int, float)):
        raise ExprError(f"$effective: {entity.id}.{prop} is {base!r}, not a number", call.source)
    add, mul = 0.0, 1.0
    try:
        for source in MODIFIER_SOURCES:
            for a, m in source(world, entity, prop):
                add += a
                mul *= m
    except RunError as exc:
        raise ExprError(str(exc), call.source) from None
    value: Union[int, float] = (base + add) * mul
    spec = world.prop_spec(entity, prop)
    if spec.min is not None:
        value = max(spec.min, value)
    if spec.max is not None:
        value = min(spec.max, value)
    if isinstance(value, float) and value.is_integer() and isinstance(base, int):
        value = int(value)
    return value
