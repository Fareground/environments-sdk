"""The game master's allow-list: a host proposal changes the world only when every effect fits.

The contract declares what a game master may do (``allow``); at the moment of an attempt each
rule is resolved to concrete targets, destinations and bounds. The host's proposal is then
checked field by field — effect kind, exact keys, target, property, value type, bounds, change
per attempt, cumulative amounts, destinations — and applied atomically through the world's
journaled API, or refused as a whole with the reason. Host values never pass through the
expression engine: they are data, bound as values, so no text can become a rule.
"""
from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..entity import Entity
from ..errors import RunError
from ..expr import ExprError, Untrusted
from ..template import format_value
from ..world import prop_type

__all__ = ["Rule", "Change", "Plan", "resolve_rules", "describe", "validate", "apply", "MAX_NARRATION"]

MAX_NARRATION = 2000
#: Tolerance when comparing a proposed number with a bound (float arithmetic in the host).
_EPSILON = 1e-9
_SHOWN = 60

_KEYS: Dict[str, frozenset] = {
    "set": frozenset({"effect", "target", "prop", "value"}),
    "set_world": frozenset({"effect", "prop", "value"}),
    "transfer": frozenset({"effect", "prop", "from", "to", "amount"}),
    "move": frozenset({"effect", "target", "to"}),
    "news": frozenset({"effect", "text"}),
}


@dataclass
class Rule:
    """One allow rule resolved for this attempt."""

    index: int
    effect: str
    prop: Optional[str] = None
    targets: Tuple[str, ...] = ()
    receivers: Tuple[str, ...] = ()
    places: Dict[str, List[Any]] = field(default_factory=dict)
    low: Optional[float] = None
    high: Optional[float] = None
    delta: Optional[float] = None
    values: Optional[List[Any]] = None
    max_chars: int = 200
    amount: Optional[float] = None
    description: str = ""


@dataclass
class Change:
    effect: str
    rule: int
    summary: str
    target: Optional[str] = None
    prop: Optional[str] = None
    value: Any = None
    receiver: Optional[str] = None
    amount: float = 0.0
    place: Any = None
    text: Optional[str] = None


@dataclass
class Plan:
    narration: str
    changes: List[Change]


# ---------------------------------------------------------------------------
# Resolving the rules
# ---------------------------------------------------------------------------


def resolve_rules(runner: Any, allow: Sequence[Any], vars: Dict[str, Any], where: str) -> List[Rule]:
    world = runner.world
    rules: List[Rule] = []
    for index, spec in enumerate(allow):
        path = f"{where}.allow[{index}]"
        rule = Rule(index, spec.effect, spec.prop, low=spec.min, high=spec.max, delta=spec.delta,
                    values=list(spec.values) if spec.values is not None else None, max_chars=spec.max_chars,
                    amount=spec.amount, description=spec.description)
        if spec.effect in ("set", "move"):
            rule.targets = _entity_ids(runner, spec.target, vars, path)
        if spec.effect == "transfer":
            rule.targets = _entity_ids(runner, spec.giver, vars, path)
            rule.receivers = _entity_ids(runner, spec.to, vars, path)
        if spec.effect == "move":
            rule.places = {target: _places(runner, world.entities[target], spec.to, vars, path) for target in rule.targets}
        rules.append(rule)
    return rules


def _entity_ids(runner: Any, raw: str, vars: Dict[str, Any], path: str) -> Tuple[str, ...]:
    world = runner.world
    if raw == "actor":
        actor = vars.get("actor")
        return (actor.id,) if isinstance(actor, Entity) and actor.alive else ()
    try:
        value = runner.eval(raw, vars)
    except ExprError as exc:
        raise RunError(str(exc), path) from None
    if isinstance(value, str) and world.is_type(value):
        value = world.entities_of(value)
    items = value if isinstance(value, (list, tuple)) else [value]
    out: List[str] = []
    for item in items:
        if item is None:
            continue
        entity = world.entity(item)
        if entity is None:
            raise RunError(f"expected entities, got {format_value(item)}", path)
        if entity.alive and entity.id not in out:
            out.append(entity.id)
    return tuple(out)


def _places(runner: Any, entity: Entity, raw: Optional[str], vars: Dict[str, Any], path: str) -> List[Any]:
    world = runner.world
    space = world.space
    if raw == "adjacent":
        here: Any = entity.location_id
        if space is None or here is None:
            return []
        if space.geometry.kind == "plane":
            raise RunError("`adjacent` needs a graph or grid space", path)
        return space.geometry.adjacent(here)
    try:
        value = runner.eval(raw, {**vars, "it": entity})
    except ExprError as exc:
        raise RunError(str(exc), path) from None
    if value is None:
        return []
    if not isinstance(value, list):
        raise RunError(f"move destinations must be a list of places, got {format_value(value)}", path)
    return [copy.deepcopy(v) for v in value]


def describe(rules: Sequence[Rule]) -> List[Dict[str, Any]]:
    """The resolved rules as the host reads them."""
    out = []
    for rule in rules:
        item: Dict[str, Any] = {"rule": rule.index, "effect": rule.effect}
        if rule.description:
            item["description"] = rule.description
        if rule.prop is not None:
            item["prop"] = rule.prop
        if rule.effect in ("set", "move"):
            item["targets"] = list(rule.targets)
        if rule.effect == "transfer":
            item.update({"from": list(rule.targets), "to": list(rule.receivers), "max_amount": rule.amount})
        if rule.effect == "move":
            item["destinations"] = {target: places for target, places in rule.places.items()}
        for key, value in (("min", rule.low), ("max", rule.high), ("max_change", rule.delta), ("values", rule.values)):
            if value is not None:
                item[key] = value
        if rule.effect == "news" or rule.values is None:
            item["max_chars"] = rule.max_chars
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Validating a proposal
# ---------------------------------------------------------------------------


def validate(world: Any, rules: Sequence[Rule], proposal: Any, max_effects: int) -> Tuple[Plan, Optional[str]]:
    """``(plan, None)`` when every effect fits the rules, else ``(empty plan, reason)``."""
    empty = Plan("", [])
    if not isinstance(proposal, Mapping):
        return empty, "the game master's answer was not an object"
    if "refuse" in proposal:
        reason = proposal.get("refuse")
        if set(proposal) - {"refuse", "narration"} or not isinstance(reason, str) or not reason.strip():
            return empty, "the game master's refusal was malformed"
        return empty, Untrusted(reason.strip()[:MAX_NARRATION])
    unknown = sorted(set(proposal) - {"narration", "effects"})
    if unknown:
        return empty, f"the game master's answer has unexpected fields ({_shown(unknown)})"
    narration = proposal.get("narration", "")
    if not isinstance(narration, str) or len(narration) > MAX_NARRATION:
        return empty, f"the narration must be text of at most {MAX_NARRATION} characters"
    effects = proposal.get("effects", [])
    if not isinstance(effects, list):
        return empty, "effects must be a list"
    if len(effects) > max_effects:
        return empty, f"at most {max_effects} effect(s) are allowed per attempt, got {len(effects)}"
    changes: List[Change] = []
    used: Dict[Tuple[Any, ...], float] = {}
    for position, raw in enumerate(effects):
        change, problem = _one(world, rules, raw, used)
        if change is None:
            return empty, f"effect {position + 1} is not allowed: {problem}"
        changes.append(change)
    return Plan(narration.strip(), changes), None


#: What a rule check gives: the change, or the problem; the usage key; and whether the rule was the right
#: one for this effect (its target, giver and property matched), so the refusal names the closest rule.
_Checked = Tuple[Optional[Change], str, Tuple[Any, ...], bool]


def _one(world: Any, rules: Sequence[Rule], raw: Any, used: Dict[Tuple[Any, ...], float]) -> Tuple[Optional[Change], str]:
    if not isinstance(raw, Mapping):
        return None, "each effect is an object"
    kind = raw.get("effect")
    if not isinstance(kind, str) or kind not in _KEYS:
        return None, f"unknown effect {_shown(kind)} (effects: {', '.join(_KEYS)})"
    keys = set(raw)
    if keys != _KEYS[kind]:
        extra, missing = sorted(keys - _KEYS[kind]), sorted(_KEYS[kind] - keys)
        parts = ([f"unexpected {_shown(extra)}"] if extra else []) + ([f"missing {', '.join(missing)}"] if missing else [])
        return None, f"`{kind}` takes exactly {', '.join(sorted(_KEYS[kind]))} ({'; '.join(parts)})"
    candidates = [rule for rule in rules if rule.effect == kind]
    if not candidates:
        return None, f"no rule allows `{kind}`"
    closest, closest_fit = "", False
    for rule in candidates:
        change, problem, key, fit = _CHECKS[kind](world, rule, raw, used)
        if change is not None:
            used[key] = used.get(key, 0.0) + (change.amount if kind == "transfer" else 1.0)
            return change, ""
        if not closest or (fit and not closest_fit):
            closest, closest_fit = problem, fit
    return None, closest


def _check_set(world: Any, rule: Rule, raw: Mapping[str, Any], used: Dict[Tuple[Any, ...], float]) -> _Checked:
    target, prop = raw["target"], raw["prop"]
    key = ("set", target, prop)
    if not isinstance(target, str) or target not in rule.targets:
        return None, f"target {_shown(target)} may not be changed (allowed: {', '.join(rule.targets) or 'none'})", key, False
    if prop != rule.prop:
        return None, f"property {_shown(prop)} may not be changed (allowed: {rule.prop})", key, False
    if key in used:
        return None, f"{target}.{prop} is changed twice", key, True
    entity = world.entities.get(target)
    if entity is None or not entity.alive:
        return None, f"{target} is not active", key, True
    spec = world.contract.props_of(entity.entity_type).get(prop)
    if spec is None:
        return None, f"{entity.name} has no property {prop}", key, True
    old = entity.properties.get(prop)
    value, problem = _value(spec, rule, old, raw["value"], f"{entity.name}'s {prop}")
    if problem:
        return None, problem, key, True
    return Change("set", rule.index, f"{entity.name}'s {prop}: {format_value(old)} → {format_value(value)}",
                  target=target, prop=prop, value=value), "", key, True


def _check_set_world(world: Any, rule: Rule, raw: Mapping[str, Any], used: Dict[Tuple[Any, ...], float]) -> _Checked:
    prop = raw["prop"]
    key = ("set_world", prop)
    if prop != rule.prop:
        return None, f"world property {_shown(prop)} may not be changed (allowed: {rule.prop})", key, False
    if key in used:
        return None, f"world property {prop} is changed twice", key, True
    spec = world.contract.world.get(prop)
    if spec is None:
        return None, f"the world has no property {prop}", key, True
    old = world.props.get(prop)
    value, problem = _value(spec, rule, old, raw["value"], f"the world's {prop}")
    if problem:
        return None, problem, key, True
    return Change("set_world", rule.index, f"{prop}: {format_value(old)} → {format_value(value)}", prop=prop,
                  value=value), "", key, True


def _check_transfer(world: Any, rule: Rule, raw: Mapping[str, Any], used: Dict[Tuple[Any, ...], float]) -> _Checked:
    prop, giver, receiver, amount = raw["prop"], raw["from"], raw["to"], raw["amount"]
    key = ("transfer", rule.index, giver)
    if prop != rule.prop:
        return None, f"only {rule.prop} may be transferred, not {_shown(prop)}", key, False
    if not isinstance(giver, str) or giver not in rule.targets:
        return None, f"{_shown(giver)} may not give (allowed: {', '.join(rule.targets) or 'none'})", key, False
    if not isinstance(receiver, str) or receiver not in rule.receivers:
        return None, f"{_shown(receiver)} may not receive (allowed: {', '.join(rule.receivers) or 'none'})", key, False
    if giver == receiver:
        return None, "a transfer needs two different parties", key, True
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
        return None, f"the amount must be a number greater than 0, got {_shown(amount)}", key, True
    limit = rule.amount
    assert limit is not None  # every transfer rule declares its amount
    if used.get(key, 0.0) + amount > limit + _EPSILON:
        return None, f"at most {limit:g} {prop} may move from {giver} per attempt", key, True
    source, target = world.entities.get(giver), world.entities.get(receiver)
    for party in (source, target):
        if party is None or not party.alive:
            return None, "both parties must be active", key, True
        spec = world.contract.props_of(party.entity_type).get(prop)
        if spec is None or prop_type(spec) not in ("number", "int"):
            return None, f"{party.name} holds no number {prop}", key, True
        if prop_type(spec) == "int" and float(amount) != int(amount):
            return None, f"{prop} moves in whole numbers, got {_shown(amount)}", key, True
    return Change("transfer", rule.index, f"{source.name} gave {target.name} {format_value(amount)} {prop}", target=giver,
                  prop=prop, receiver=receiver, amount=float(amount)), "", key, True


def _check_move(world: Any, rule: Rule, raw: Mapping[str, Any], used: Dict[Tuple[Any, ...], float]) -> _Checked:
    target, destination = raw["target"], raw["to"]
    key = ("move", target)
    if not isinstance(target, str) or target not in rule.targets:
        return None, f"target {_shown(target)} may not move (allowed: {', '.join(rule.targets) or 'none'})", key, False
    if key in used:
        return None, f"{target} moves twice", key, True
    wanted = _canonical(destination)
    for place in rule.places.get(target, []):
        if _canonical(place) == wanted:
            entity = world.entities[target]
            return Change("move", rule.index, f"{entity.name} moved to {format_value(place)}", target=target,
                          place=copy.deepcopy(place)), "", key, True
    allowed = ", ".join(format_value(p) for p in rule.places.get(target, [])) or "none"
    return None, f"{_shown(destination)} is not a destination {target} may reach (allowed: {allowed})", key, True


def _check_news(world: Any, rule: Rule, raw: Mapping[str, Any], used: Dict[Tuple[Any, ...], float]) -> _Checked:
    text = raw["text"]
    key: Tuple[Any, ...] = ("news",)
    if key in used:
        return None, "only one news item is allowed per attempt", key, True
    if not isinstance(text, str) or not text.strip() or len(text) > rule.max_chars:
        return None, f"news must be text of 1 to {rule.max_chars} characters", key, True
    return Change("news", rule.index, "news was spread", text=Untrusted(text.strip())), "", key, True


_CHECKS = {"set": _check_set, "set_world": _check_set_world, "transfer": _check_transfer, "move": _check_move,
           "news": _check_news}


def _value(spec: Any, rule: Rule, current: Any, value: Any, label: str) -> Tuple[Any, str]:
    kind = prop_type(spec)
    if rule.values is not None and not any(_canonical(value) == _canonical(v) for v in rule.values):
        return None, f"{label} may only become one of {', '.join(format_value(v) for v in rule.values)}"
    if kind in ("number", "int"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None, f"{label} must be a finite number, got {_shown(value)}"
        if kind == "int" and float(value) != int(value):
            return None, f"{label} must be a whole number, got {_shown(value)}"
        lows = [b for b in (rule.low, spec.min) if b is not None]
        highs = [b for b in (rule.high, spec.max) if b is not None]
        if lows and value < max(lows) - _EPSILON:
            return None, f"{label} must be at least {max(lows):g}, got {_shown(value)}"
        if highs and value > min(highs) + _EPSILON:
            return None, f"{label} must be at most {min(highs):g}, got {_shown(value)}"
        if rule.delta is not None:
            if isinstance(current, bool) or not isinstance(current, (int, float)):
                return None, f"{label} has no number to change from"
            if abs(value - current) > rule.delta + _EPSILON:
                return None, f"{label} may change by at most {rule.delta:g} at once (from {format_value(current)})"
        return (int(value) if kind == "int" else value), ""
    if kind == "bool":
        return (value, "") if isinstance(value, bool) else (None, f"{label} must be true or false, got {_shown(value)}")
    if kind == "enum":
        if isinstance(value, bool) or value not in (spec.values or []):
            return None, f"{label} must be one of {', '.join(format_value(v) for v in spec.values or [])}"
        return value, ""
    if kind == "text":
        if not isinstance(value, str) or len(value) > rule.max_chars:
            return None, f"{label} must be text of at most {rule.max_chars} characters"
        return (value if rule.values is not None else Untrusted(value)), ""
    if rule.values is None:
        return None, f"{label} can only be set to one of the rule's declared `values`"
    return copy.deepcopy(value), ""


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def apply(runner: Any, plan: Plan, actor: Entity, mechanism: str, where: str) -> None:
    """Apply every change through the journaled world API (the caller owns the rollback)."""
    world = runner.world
    for change in plan.changes:
        if change.effect == "set":
            world.set_prop(world.entities[change.target], change.prop, change.value)
        elif change.effect == "set_world":
            world.set_world(change.prop, change.value)
        elif change.effect == "transfer":
            runner.run([{"transfer": change.prop, "from": "$gm_giver", "to": "$gm_receiver", "amount": "$gm_amount"}],
                       {"gm_giver": world.entities[change.target], "gm_receiver": world.entities[change.receiver],
                        "gm_amount": change.amount}, where)
        elif change.effect == "move":
            world.move(world.entities[change.target], change.place, where)
        elif change.effect == "news":
            world.emit("news", format_value(change.text), actor=actor.id, data={"mechanism": mechanism})


def _canonical(value: Any) -> str:
    """A comparison key that keeps true apart from 1 and false apart from 0."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=repr)
    return f"bool:{text}" if isinstance(value, bool) else text


def _shown(value: Any) -> str:
    """A host value quoted for a refusal: short, and marked as not the engine's words."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        text = repr(value)
    return format_value(Untrusted(text if len(text) <= _SHOWN else text[: _SHOWN - 1] + "…"))
