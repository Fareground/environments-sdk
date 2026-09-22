"""The ``economy`` family's ``inventory`` mode: typed goods held by entities, with capacity,
hand-overs, the ground, consumption, recurring needs and spoilage."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..registry import MechanismError, family_action, mode
from ._common import ToolsSetting, tools_field
from .econ_assets import assets, destroy_items, is_holder
from .econ_base import (INVENTORY, LEDGER, choice_param, props, config_of, declared_names, emit_to, guarded, register_config,
                        require_types, top_types, type_list, valid_name)

__all__ = ["InventoryConfig", "ItemSpec", "agent_types", "baseline"]


class ItemSpec(BaseModel):
    """One kind of good."""

    model_config = ConfigDict(extra="forbid")

    unique: bool = Field(False, description="Each unit is its own entity (type named after the item) with an owner and props.")
    value: float = Field(0, description="Worth of one unit, used by $net_worth when no price is given.")
    size: float = Field(1, ge=0, description="Capacity one unit takes.")
    unit: str = Field("", description="Unit word shown with quantities (loaf, kg).")
    description: str = ""
    shelf_life: Optional[int] = Field(None, ge=1, description="Rounds until a unit spoils (oldest units first).")
    decay: Optional[float] = Field(None, ge=0, le=1, description="Chance each unit is lost every round (seeded).")
    props: Dict[str, Any] = Field({}, description="Unique items: properties of each instance.")
    consumable: bool = Field(False, description="Holders may consume it (a `<name>_consume` tool).")
    on_consume: List[Any] = Field([], description="Effects when consumed ($actor, $qty); implies consumable.")


class InventoryConfig(BaseModel):
    """Goods held by entities."""

    model_config = ConfigDict(extra="forbid")

    who: Union[str, List[str]] = Field(..., description="Type(s) that hold goods (subtypes included).")
    items: Dict[str, ItemSpec] = Field(..., min_length=1, description="{item: {unique, value, size, unit, shelf_life, decay, props, consumable, on_consume}}.")
    prop: Optional[str] = Field(None, description="Holder property with the stackable goods {item: qty}; default the mechanism's name.")
    capacity: Union[float, str, None] = Field(None, description="Space each holder has (number or expression); unlimited when omitted.")
    start: Dict[str, Union[int, str]] = Field(
        {}, description="Goods every holder starts with {item: qty or expression} (entity props override).")
    actions: List[Literal["give", "consume", "drop", "pickup"]] = Field(
        ["give", "consume"], description="Tools generated for agent holders: give, consume (consumable items), drop and pickup (needs a space).")
    tools: ToolsSetting = tools_field()
    give_to: str = Field("$it.id != $actor.id", description="Which holders an agent may give goods to ($actor, $it).")
    needs: Dict[str, Dict[str, Union[int, str]]] = Field(
        {}, description="Goods used up every `every` rounds per type: {type: {item: qty or expression over $it}}.")
    on_short: List[Any] = Field([], description="Effects when a need is not met ($it, $item, $short).")
    every: int = Field(1, ge=1, description="Rounds between needs.")


register_config(INVENTORY, InventoryConfig)


def agent_types(contract: Mapping[str, Any], names: List[str]) -> List[str]:
    types = contract.get("types") or {}
    out = []
    for name in names:
        current, seen = name, set()
        while current in types and current not in seen:
            seen.add(current)
            if (types[current] or {}).get("agent"):
                out.append(name)
                break
            current = (types[current] or {}).get("extends")
    return out


def baseline(contract: Mapping[str, Any], holders: List[str], assets_: List[str], loose: Optional[str] = None) -> str:
    """A world default counting what exists when the world is built (it reads entities, so it runs after them)."""
    parts = []
    for asset in assets_:
        terms = [f"$total_held({t}, '{asset}')" for t in top_types(contract, holders)]
        if loose:
            terms.append(f"$loose_total('{loose}', '{asset}')")
        parts.append(f"'{asset}': {' + '.join(terms)}")
    return "{" + ", ".join(parts) + "}"


def _start_default(start: Dict[str, Union[int, str]], stackable: List[str]) -> Any:
    """Starting goods, every stackable item listed (0 unless started) so `$it.goods.bread` always reads a count:
    a literal map, or an expression building one when a quantity is an expression."""
    full = {item: start.get(item, 0) for item in stackable}
    if not any(isinstance(qty, str) for qty in full.values()):
        return full
    return "{" + ", ".join(f"'{item}': ({qty})" for item, qty in full.items()) + "}"


def _other_names(contract: Mapping[str, Any], name: str) -> Dict[str, str]:
    taken = {**declared_names(contract, INVENTORY, "items"), **declared_names(contract, LEDGER, "currencies")}
    return {asset: other for asset, other in taken.items() if other != name}


@mode("economy", "inventory", InventoryConfig,
      "Goods held by entities: stackable items in a map property listing each one (`$actor.goods.bread`, 0 when "
      "none are held) and unique items as "
      "entities with an owner. Generates `<name>_give`, `<name>_consume`, `<name>_drop` and `<name>_pickup` tools "
      "listing only goods you hold, capacity limits, recurring needs and spoilage, and the invariant "
      "`$conserved(<name>)`: goods change only by moves or by named sources and sinks (the `make` and `use` actions). "
      "Totals are in $world.<name>_supply and every named flow in $world.<name>_flows.",
      example={"who": "villager", "capacity": 20,
               "items": {"bread": {"value": 3, "shelf_life": 4, "on_consume": ["$actor.hunger -= 2 * $qty"]},
                         "axe": {"unique": True, "value": 20, "props": {"durability": 10}}}}, was="inventory")
def _expand_inventory(name: str, config: InventoryConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    holders = type_list(config.who)
    require_types(contract, holders, "who")
    prop = config.prop or name
    if not valid_name(prop):
        raise MechanismError(f"prop '{prop}' is not a property name", "use letters, digits and _ (not a word expressions use, like in or not)", "prop")
    taken = _other_names(contract, name)
    for item, spec in config.items.items():
        if not valid_name(item):
            raise MechanismError(f"item '{item}' is not a valid name", "use letters, digits and _ (not a word expressions use, like in or not)", f"items.{item}")
        if item in taken:
            raise MechanismError(f"'{item}' is already declared by '{taken[item]}'", "give every item and currency its own name",
                                 f"items.{item}")
        if spec.unique and (spec.consumable or spec.on_consume or spec.shelf_life is None and spec.decay is not None):
            raise MechanismError(f"unique item '{item}' cannot be consumable or decay", "use a stackable item, or `use` it in your own action",
                                 f"items.{item}")
        if spec.props and not spec.unique:
            raise MechanismError(f"item '{item}' has props but is not unique", "set \"unique\": true", f"items.{item}.props")
    for item in config.start:
        if item not in config.items or config.items[item].unique:
            raise MechanismError(f"start: '{item}' is not a stackable item of this inventory", None, f"start.{item}")
    if ("drop" in config.actions or "pickup" in config.actions) and not contract.get("space"):
        raise MechanismError("drop and pickup need a declared space", "declare `space`, or remove them from actions", "actions")
    for type_name, wants in config.needs.items():
        require_types(contract, [type_name], f"needs.{type_name}")
        for item in wants:
            if item not in config.items or config.items[item].unique:
                raise MechanismError(f"needs: '{item}' is not a stackable item of this inventory", None, f"needs.{type_name}.{item}")

    stackable = [i for i, s in config.items.items() if not s.unique]
    holder_props: Dict[str, Any] = {prop: {"type": "map", "default": _start_default(config.start, stackable),
                                           "description": f"Goods held ({name}): {{item: quantity}}, every stackable item listed."}}
    if config.capacity is not None:
        holder_props[f"{prop}_capacity"] = {"type": "number", "default": config.capacity, "min": 0,
                                            "description": f"Space for {name} goods."}
    if any(s.shelf_life is not None for s in config.items.values()):
        holder_props[f"{prop}_batches"] = {"type": "map", "default": {}, "private": True,
                                           "description": "Rounds when perishable goods were made (oldest first)."}
    types: Dict[str, Any] = {t: {"props": holder_props} for t in holders}
    for item, spec in config.items.items():
        if spec.unique:
            types[item] = {"description": spec.description or f"A {item}.",
                           "props": {"owner": {"type": "text", "default": "", "description": "Id of the holder."},
                                     "made": {"type": "int", "default": 0}, **spec.props}}
    world: Dict[str, Any] = {
        f"{name}_supply": {"type": "map", "default": baseline(contract, holders, list(config.items), name),
                           "description": f"Goods of {name} in existence: {{item: quantity}}."},
        f"{name}_flows": {"type": "map", "default": {}, "description": "Goods made (+) and used up (−) by each named source and sink."},
    }
    if "drop" in config.actions or "pickup" in config.actions:
        world[f"{name}_ground"] = {"type": "map", "default": {}, "description": "Goods lying at each place: {place: {item: qty}}."}
    fragment: Dict[str, Any] = {
        "types": types, "world": world,
        "invariants": [{"expr": f"$conserved('{name}')", "check": "round",
                        "why": f"Goods of {name} change only by moves or named sources and sinks."}],
        "actions": _actions(name, config, contract, holders, stackable),
    }
    events = _events(name, config)
    if events:
        fragment["events"] = events
    agents = agent_types(contract, holders)
    if agents:
        fragment["views"] = {f"{name}_holdings": {"for": agents, "title": f"Your {name}", "bullet": False,
                                                  "show": f"{{$items_text($actor, '{name}')}}"}}
        if f"{name}_ground" in world:
            fragment["views"][f"{name}_here"] = {"for": agents, "title": "Goods lying here", "bullet": False,
                                                 "when": f"$actor.at != null and $len($keys($ground_items('{name}', $actor.at))) > 0",
                                                 "show": f"{{$ground_items('{name}', $actor.at)}}"}
    return fragment


def _actions(name: str, config: InventoryConfig, contract: Mapping[str, Any], holders: List[str],
             stackable: List[str]) -> Dict[str, Any]:
    agents = agent_types(contract, holders)
    if not agents:
        return {}
    owned = f"$owned_items($actor, '{name}')"
    have = {"expr": f"$len({owned}) > 0", "why": f"You hold no {name}."}
    qty = {"type": "int", "min": 1, "max": guarded("$count_items($actor, $params.item)", "item"), "default": 1,
           "description": "How many."}
    actions: Dict[str, Any] = {}
    if "give" in config.actions:
        to, ref = choice_param(holders, config.give_to, "Who receives the goods.")
        target = ref.format(name="to")
        actions[f"{name}_give"] = {
            "by": agents, "description": "Give goods you hold to someone.", "when": [have],
            "params": {"to": to, "item": {"type": "enum", "values": owned, "description": "Item you hold (a name, or the id of a unique item)."},
                       "qty": qty},
            "do": [{"economy": name, "action": "give", "item": "$params.item", "from": "$actor", "to": target, "qty": "$params.qty"}],
            "outcome": f"You gave {{$params.qty}} × {{$params.item}} to {{{target}}}."}
    consumables = [i for i in stackable if config.items[i].consumable or config.items[i].on_consume]
    if "consume" in config.actions and consumables:
        listed = "[" + ", ".join(f"'{i}'" for i in consumables) + "]"
        choices = f"$filter({owned}, $it in {listed})"
        effects: List[Any] = [{"economy": name, "action": "use", "item": "$params.item", "from": "$actor", "qty": "$params.qty",
                               "sink": "consumed"},
                              "$qty = $params.qty"]
        for item in consumables:
            if config.items[item].on_consume:
                effects.append({"if": f"$params.item == '{item}'", "then": list(config.items[item].on_consume)})
        actions[f"{name}_consume"] = {
            "by": agents, "description": f"Use up goods you hold ({', '.join(consumables)}).",
            "when": [{"expr": f"$len({choices}) > 0", "why": "You hold nothing you can consume."}],
            "params": {"item": {"type": "enum", "values": choices, "description": "What to consume."}, "qty": qty},
            "do": effects, "outcome": "You consumed {$params.qty} × {$params.item}.", "private": True}
    listed_stack = "[" + ", ".join(f"'{i}'" for i in stackable) + "]"
    if "drop" in config.actions and stackable:
        choices = f"$filter({owned}, $it in {listed_stack})"
        actions[f"{name}_drop"] = {
            "by": agents, "description": "Leave goods on the ground where you are.",
            "when": [{"expr": f"$actor.at != null and $len({choices}) > 0", "why": "You hold nothing you can put down here."}],
            "params": {"item": {"type": "enum", "values": choices, "description": "What to put down."}, "qty": qty},
            "do": [{"economy": name, "action": "drop", "item": "$params.item", "from": "$actor", "qty": "$params.qty"}],
            "outcome": "You left {$params.qty} × {$params.item} here."}
    if "pickup" in config.actions and stackable:
        here = f"$ground_items('{name}', $actor.at)"
        actions[f"{name}_pickup"] = {
            "by": agents, "description": "Pick up goods lying where you are.",
            "when": [{"expr": f"$actor.at != null and $len($keys({here})) > 0", "why": "Nothing lies here."}],
            "params": {"item": {"type": "enum", "values": f"$keys({here})", "description": "What to pick up."},
                       "qty": {"type": "int", "min": 1, "max": guarded(f"$get({here}, $params.item, 0)", "item"), "default": 1,
                               "description": "How many."}},
            "do": [{"economy": name, "action": "pickup", "item": "$params.item", "to": "$actor", "qty": "$params.qty"}],
            "outcome": "You picked up {$params.qty} × {$params.item}."}
    return actions


def _events(name: str, config: InventoryConfig) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for type_name, wants in config.needs.items():
        effects: List[Any] = []
        for item, qty in wants.items():
            effects += [f"$need = {qty}", f"$got = $min($need, $count_items($it, '{item}'))",
                        {"economy": name, "action": "use", "item": item, "from": "$it", "qty": "$got", "sink": "needs"}]
            if config.on_short:
                effects.append({"if": "$got < $need", "then": [f"$item = '{item}'", "$short = $need - $got", *config.on_short]})
        events.append({"name": f"{name} needs of {type_name}", "phase": "end", "every": config.every,
                       "each": type_name, "do": effects})
    if any(s.shelf_life is not None or s.decay is not None for s in config.items.values()):
        events.append({"name": f"{name} spoilage", "phase": "end", "do": [{"economy": name, "action": "tick"}]})
    return events


def _losses(world: Any, count: int, chance: float) -> int:
    rng = world.rng
    if count <= 2_000:
        return sum(1 for _ in range(count) if rng.random() < chance)
    mean, sd = count * chance, (count * chance * (1 - chance)) ** 0.5
    return max(0, min(count, round(rng.gauss(mean, sd))))


@family_action("economy", ("inventory",), "tick", internal=True, was=("inventory_tick",),
               example='{"economy": "goods", "action": "tick"}  (spoil goods past their shelf life and apply decay, now)')
def _inventory_tick(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: InventoryConfig = config_of(world, name, INVENTORY, where)
    prop = assets(world).props[name]
    for entity in [e for e in world.entities.values() if e.alive and is_holder(world, e, prop)]:
        lost: Dict[str, int] = {}
        for item, spec in config.items.items():
            if spec.unique:
                continue
            count = int((props(entity).get(prop) or {}).get(item, 0))
            if not count:
                continue
            spoiled = _expired(world, entity, prop, item, count, spec.shelf_life) if spec.shelf_life is not None else 0
            decayed = _losses(world, count - spoiled, spec.decay) if spec.decay else 0
            if spoiled + decayed:
                destroy_items(world, item, entity, spoiled + decayed, "spoiled", where)
                lost[item] = spoiled + decayed
        for item, spec in config.items.items():
            if spec.unique and spec.shelf_life is not None:
                old = [e for e in world.entities_of(item) if props(e).get("owner") == entity.id
                       and world.round - int(props(e).get("made") or 0) >= spec.shelf_life]
                if old:
                    destroy_items(world, old[0].entity_type, entity, len(old), "spoiled", where)
                    lost[item] = len(old)
        if lost and world.contract.is_agent(entity.entity_type):
            text = ", ".join(f"{qty} {item}" for item, qty in lost.items())
            emit_to(world, f"{name}_spoiled", f"Spoiled: {text}.", [entity.id], {"lost": lost})


def _expired(world: Any, entity: Entity, prop: str, item: str, count: int, shelf_life: int) -> int:
    """Units of ``item`` at or past their shelf life. Units without a recorded batch count as made at round 0."""
    rows = [list(r) for r in (props(entity).get(f"{prop}_batches") or {}).get(item, [])]
    recorded = sum(r[1] for r in rows)
    if recorded < count:
        rows.insert(0, [0, count - recorded])
        ages = dict(props(entity).get(f"{prop}_batches") or {})
        ages[item] = rows
        world.set_prop(entity, f"{prop}_batches", ages)
    return min(count, sum(n for made, n in rows if world.round - made >= shelf_life))
