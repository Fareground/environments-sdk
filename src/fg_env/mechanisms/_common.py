"""Shared plumbing for the native mechanism families.

* Action hooks: families attach extra ``when`` conditions and effects to actions the contract
  already declares (a status that blocks ``attack``) by returning an ``action_hooks`` section, which the
  spine's merge applies.
* Modifiers: ``$effective(entity, prop)`` — a property with every active modifier applied. Statuses register
  their modifier source here.
* Small value helpers shared by the families (numbers, entity lists, frozen params, checks).
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from difflib import get_close_matches
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..effects.captures import CAPTURE_VERSION, freeze, thaw
from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function, is_expr, truthy
from ..expr.objects import Entity
from ..expr.template import format_value
from ..registry import MechanismError, use_key

__all__ = [
    "Config", "Number", "Effects", "ModifierSpec", "NAME", "MODIFIER_SOURCES", "uses",
    "actions_by", "types_in", "suggest", "evaluate", "condition", "number", "number_of", "whole", "entity_of",
    "entities_of", "lot_floor", "fmt", "pct",
    "freeze", "thaw", "CAPTURE_VERSION", "canonical", "modifier_terms", "check_names", "carriers", "raw_is_a",
    "is_agent_type", "stage_event", "declared_entity", "setting_kept", "Conserve", "conserve_field",
    "conserve_invariant",
]


#: A number, or an expression giving one.
Number = float | str
#: When a market checks the invariant that it holds exactly what its traders put in.
Conserve = bool | Literal["action", "round", "end"]


def conserve_field(what: str) -> Any:
    """The one ``conserve`` setting every market takes: when it checks that ``what`` (default: after every round)."""
    return Field("round", description=f"Declare the invariant that {what}: round (the default: after every round), "
                                      "true or action (after every action: each check goes over every trader, so a "
                                      "round costs the square of the crowd), end (once, when the run finishes), or "
                                      "false.")


def conserve_invariant(conserve: Conserve, expr: str, why: str) -> list[dict[str, Any]]:
    """The invariant a market's ``conserve`` setting declares: none when false."""
    if conserve is False:
        return []
    return [{"expr": expr, "check": "action" if conserve is True else conserve, "why": why}]
Effects = list[Any]
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")


class Config(BaseModel):
    """Base for mechanism configs: unknown fields are errors."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ModifierSpec(Config):
    """How a status changes a property: ``(base + add) * mul`` per stack."""

    add: Number = Field(0.0, description="Added to the property (number or expression over $it).")
    mul: Number = Field(1.0, description="Multiplies the property (number or expression over $it).")


def stage_event(stage: str, point: str, do: Effects, when: str | None = None) -> dict[str, Any]:
    """An event that runs ``do`` at ``point`` of stage ``stage``: its start, its end, or after each agent's turn
    (``turn``), under ``when``."""
    return {"on": f"stage.{stage}.{point}", **({"when": when} if when else {}), "do": list(do)}


def uses(contract: Any, kind: str) -> list[tuple[str, Mapping[str, Any]]]:
    """``(name, raw config)`` of every mechanism of ``kind`` in a parsed contract or contract data."""
    mechanisms = contract.get("mechanisms") if isinstance(contract, Mapping) else contract.mechanisms
    return [(name, raw) for name, raw in (mechanisms or {}).items()
            if use_key(raw) == kind]


# ---------------------------------------------------------------------------
# Expansion helpers (contract data, before validation)
# ---------------------------------------------------------------------------


def suggest(name: str, options: Iterable[str]) -> str:
    listed = sorted(options)
    hint = get_close_matches(str(name), listed, n=1)
    return f"did you mean '{hint[0]}'?" if hint else f"choices: {', '.join(listed) or 'none'}"


def types_in(contract: Mapping[str, Any], names: str | Sequence[str], field: str) -> list[str]:
    """``names`` as a list, each a declared type."""
    listed = [names] if isinstance(names, str) else list(names)
    declared = contract.get("types") or {}
    for name in listed:
        if name not in declared:
            raise MechanismError(f"'{name}' is not a declared type", suggest(name, declared), field)
    return listed


def setting_kept(contract: Mapping[str, Any], holders: Sequence[str], prop: str, value: Any, setting: str) -> None:
    """The one rule for a property a mechanism starts from one of its settings (a ledger's `start`, a pot's `stack`):
    a holder type that declares that property itself would silently replace the setting, so it is an error unless it
    agrees. An entity may still set its own value."""
    for kind, spec in (contract.get("types") or {}).items():
        declared = ((spec.get("props") or {}) if isinstance(spec, Mapping) else {}).get(prop)
        if declared is None or not any(raw_is_a(contract, kind, holder) for holder in holders):
            continue
        default = declared.get("default") if isinstance(declared, Mapping) else declared
        if default != value:
            raise MechanismError(f"types.{kind}.props.{prop} (default {format_value(default)}) would replace "
                                 f"`{setting}` here ({format_value(value)})",
                                 f"remove types.{kind}.props.{prop} (the mechanism declares it) and give the starting "
                                 f"value as `{setting}`; an entity may still set its own {prop}", setting)


def raw_is_a(contract: Mapping[str, Any], type_name: str, ancestor: str) -> bool:
    """``type_name`` is ``ancestor`` or extends it (on contract data)."""
    types = contract.get("types") or {}
    seen: list[str] = []
    current: str | None = type_name
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
    seen: list[str] = []
    current: str | None = type_name
    while current is not None and current not in seen and isinstance(types.get(current), Mapping):
        if types[current].get("agent"):
            return True
        seen.append(current)
        current = types[current].get("extends")
    return False


def actions_by(contract: Mapping[str, Any], type_names: Sequence[str]) -> list[str]:
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


def declared_entity(contract: Mapping[str, Any], entity_id: Any, field: str, what: str) -> Mapping[str, Any]:
    """The `entities` entry that makes the entity ``entity_id`` as the run will name it: a named entry, or a generator
    whose ids are ``<key>_<n>`` (``buyer_1`` of ``"buyer": {"count": 3}``). Else a :class:`MechanismError` at ``field``
    saying what is wrong (``what``: "house", "seller" …) — naming the generated ids when a generator's key was given."""
    entities = contract.get("entities") or {}
    entry = entities.get(entity_id) if isinstance(entity_id, str) else None
    if isinstance(entry, Mapping) and "count" not in entry and "from" not in entry:
        return entry
    for key, group in entities.items():
        count = group.get("count") if isinstance(group, Mapping) else None
        if count is None or "from" in group or "id" in group or not isinstance(entity_id, str):
            continue  # ids from data rows or an id template are known only once built
        number = entity_id.removeprefix(f"{key}_")
        if number != entity_id and number.isdigit() and (not isinstance(count, int) or 1 <= int(number) <= count):
            return group
    if isinstance(entry, Mapping):
        count = entry.get("count")
        ids = (f"{entity_id}_1" if count == 1 else f"{entity_id}_1 … {entity_id}_{count}") if isinstance(count, int) \
            else f"{entity_id}_1, {entity_id}_2 …"
        raise MechanismError(f"{what} '{entity_id}' names a group of entities, not one", f"name one of them: {ids}",
                             field)
    if any(isinstance(group, Mapping) and ("from" in group or "id" in group) for group in entities.values()):
        return {}  # ids from data rows or an id template are known only once built: the run checks it
    close = get_close_matches(str(entity_id), _ids(entities), n=1)
    raise MechanismError(f"{what} '{entity_id}' is not a declared entity",
                         f"did you mean '{close[0]}'?" if close else "declare it under entities", field)


def _ids(entities: Mapping[str, Any]) -> list[str]:
    """Every id the ``entities`` entries make that is known before the build."""
    ids: list[str] = []
    for key, entry in entities.items():
        count = entry.get("count") if isinstance(entry, Mapping) else None
        if count is None:
            ids.append(key)
        elif isinstance(count, int):
            ids += [f"{key}_{n}" for n in range(1, min(count, 50) + 1)]
    return ids


def check_names(contract: Mapping[str, Any], names: str | Sequence[str], field: str,
                carrier_types: Sequence[str]) -> list[str]:
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
    from ..checks import BASE  # imported late: the checker imports the effect ops, which import this module

    return BASE


# ---------------------------------------------------------------------------
# Run-time value helpers
# ---------------------------------------------------------------------------


def evaluate(world: Any, raw: Any, where: str, **roots: Any) -> Any:
    """A literal as is, or an expression evaluated with ``roots``."""
    if not is_expr(raw):
        return raw
    try:
        return compile_expr(raw)(world.evaluation.scope(**roots))
    except ExprError as exc:
        raise RunError(str(exc), where) from None


def condition(world: Any, raw: str, where: str, **roots: Any) -> bool:
    """A predicate is an expression, including constant expressions without $ references."""
    try:
        return truthy(compile_expr(raw)(world.evaluation.scope(**roots)))
    except ExprError as exc:
        raise RunError(str(exc), where) from None


def number(value: Any, where: str, what: str = "a number") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RunError(f"must be {what}, got {value!r}", where)
    return value


def number_of(world: Any, raw: Any, where: str, **roots: Any) -> float:
    """A config value that is a number or an expression giving one (evaluated with ``roots``)."""
    return float(number(evaluate(world, raw, where, **roots), where))


def whole(value: Any, where: str, low: int = 1) -> int:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise RunError(f"must be a whole number ≥ {low}, got {value!r}", where)
    return value


def entity_of(world: Any, value: Any, where: str, what: str = "an entity") -> Entity:
    """The alive entity ``value`` (an entity or an id) names."""
    found = world.entity(value)
    if found is None or not found.alive:
        raise RunError(f"expected {what}, got {value!r}", where)
    return found  # type: ignore[no-any-return]


def entities_of(world: Any, value: Any, where: str) -> list[Entity]:
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


def lot_floor(qty: float, lot: float) -> float:
    """``qty`` rounded down to a whole number of lots."""
    if lot <= 0:
        return max(0.0, qty)
    lots = int(qty / lot + 1e-9)
    return round(lots * lot, 10)


def pct(share: float) -> str:
    """A share as a percentage with the digits it has: 0.005 is "0.5%", 0.1 "10%", 0.125 "12.5%"."""
    return f"{share * 100:.4g}%"


def fmt(value: float, digits: int = 2) -> str:
    """A compact number: no trailing zeros."""
    text = f"{value:,.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def carriers(world: Any, type_names: Sequence[str]) -> list[Entity]:
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


def by_types(action: Any) -> list[str]:
    """The agent types that may take an action (an ActionSpec or action data)."""
    by = action.get("by") if isinstance(action, Mapping) else action.by
    return [by] if isinstance(by, str) else list(by or [])


# ---------------------------------------------------------------------------
# Modifiers and $effective
# ---------------------------------------------------------------------------

#: ``source(world, entity, prop)`` → ``[(add, mul), ...]`` for every modifier on that property.
ModifierSource = Callable[[Any, Entity, str], Iterable[tuple[float, float]]]
MODIFIER_SOURCES: list[ModifierSource] = []


def modifier_terms(world: Any, raw: Any, entity: Entity, where: str) -> tuple[float, float]:
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
          "The property with every active modifier applied: (base + adds) × multipliers from statuses, kept "
          "within the property's min/max, e.g. $effective($actor, 'armor').",
          min_args=2, max_args=2, family="game")
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
    value: int | float = (base + add) * mul
    spec = world.prop_spec(entity, prop)
    if spec.min is not None:
        value = max(spec.min, value)
    if spec.max is not None:
        value = min(spec.max, value)
    if isinstance(value, float) and value.is_integer() and isinstance(base, int):
        value = int(value)
    return value
