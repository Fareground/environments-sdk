"""The ``economy`` family's ``supply_chain`` mode: a serial chain of stock holders (retailer …
producer) with order and shipping pipelines, backlog, holding and backlog costs — the beer game's
mechanics as data. Goods in a pipeline are real goods of the chain's inventory and count toward its supply."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import Call, ExprError, function
from ..registry import MechanismError, family_action, mode
from ..world.abort import Abort
from ._common import declared_entity, entity_of
from .econ_assets import destroy_items, held, put_items, take_items
from .econ_base import (
    INVENTORY,
    SUPPLY_CHAIN,
    bump,
    common_ancestor,
    config_of,
    declared_use,
    maybe_entity,
    props,
    register_config,
    whole,
)
from .econ_inventory import agent_types

__all__ = ["SupplyChainConfig"]


class SupplyChainConfig(BaseModel):
    """A serial supply chain for one item."""

    model_config = ConfigDict(extra="forbid")

    inventory: str = Field(..., description="Inventory holding the chain's stock.")
    item: str = Field(..., description="The item that flows down the chain.")
    nodes: list[str] = Field(..., min_length=1, description="Entity ids from the customer-facing node to the producer.")
    demand: float | str = Field(...,
                                description="Customers' order at the first node each round (number or expression).")
    order_delay: int | str = Field(1, description="Rounds for an order to reach the node upstream.")
    lead_time: int | str = Field(2, description="Rounds for a shipment to reach the node downstream.")
    production_delay: int | str = Field(2, description="Rounds for the producer's batch to be ready.")
    initial_flow: float | str = Field(0.0, description="Steady flow already in every pipeline when the run starts.")
    holding_cost: float | str = Field(0.0, description="Cost per unit in stock per round.")
    backlog_cost: float | str = Field(0.0, description="Cost per unit of backlog per round.")
    max_order: int | str = Field(1000, description="Largest order in one round.")
    default_order: int | str = Field(0,
                                     description="Order placed for a node that placed none this round (expression over "
                                                 "$it).")
    actions: list[Literal["order"]] = Field(["order"],
                                            description="order: nodes that are agents place their orders with a tool.")


register_config(SUPPLY_CHAIN, SupplyChainConfig)


def _filled(length: int | str, flow: float | str) -> str:
    return f"$map($range({length}), {flow})"


@mode("economy", "supply_chain", SupplyChainConfig,
      "A serial supply chain (the beer game as data): each round every node receives what reached it, gets its order "
      "(customers' demand at the first node), ships what it can toward that order plus backlog and pays holding and "
      "backlog costs; then nodes order from the node upstream (the producer starts a batch) with `<name>_order`, one "
      "order a round. Orders and shipments travel in pipelines ($world.<name>_pipes) with their own delays; goods in "
      "a pipeline count toward the inventory's supply, production enters from the named source `production` and "
      "customer sales leave through the sink `sold`. Node props: <name>_backlog, _incoming, _received, _shipped, "
      "_last_order, _round_cost, _cost, _peak_backlog; $pipeline(agent, chain) lists goods on the way.",
      example={"inventory": "stock", "item": "beer",
               "nodes": ["retailer", "wholesaler", "distributor", "factory"], "demand": "4 if $round < 5 else 8",
               "initial_flow": 4, "holding_cost": 0.5, "backlog_cost": 1})
def _expand_supply_chain(name: str, config: SupplyChainConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    inventory = declared_use(contract, config.inventory, INVENTORY, "inventory")
    items = inventory.get("items") or {}
    if config.item not in items or (items[config.item] or {}).get("unique"):
        raise MechanismError(f"'{config.item}' is not a stackable item of inventory '{config.inventory}'", None, "item")
    if len(set(config.nodes)) != len(config.nodes):
        raise MechanismError("a node is listed twice", None, "nodes")
    node_types: list[str] = []
    for node in config.nodes:
        kind = str(declared_entity(contract, node, "nodes", "node").get("type"))
        if kind not in node_types:
            node_types.append(kind)
    # Node props go on the most specific type all nodes share, so expressions over that type can read them.
    shared = common_ancestor(contract, node_types)
    types = [shared] if shared is not None else node_types
    whole_int = {"type": "int", "default": 0, "min": 0}
    node_props: dict[str, Any] = {
        f"{name}_backlog": {**whole_int, "description": "Units owed downstream."},
        f"{name}_incoming": {**whole_int, "description": "Order received this round."},
        f"{name}_received": {**whole_int, "description": "Units that arrived this round."},
        f"{name}_shipped": {**whole_int, "description": "Units shipped this round."},
        f"{name}_ordered": {"type": "bool", "default": False},
        f"{name}_last_order": {"type": "int", "default": f"$round({config.initial_flow})", "min": 0},
        f"{name}_recent": {"type": "list", "default": [], "description": "Your last four orders, oldest first."},
        f"{name}_round_cost": {"type": "number", "default": 0, "min": 0},
        f"{name}_cost": {"type": "number", "default": 0, "min": 0},
        f"{name}_peak_backlog": whole_int}
    pipes = []
    for index, node in enumerate(config.nodes):
        last = index == len(config.nodes) - 1
        inbound = _filled(config.production_delay if last else config.lead_time, f"$round({config.initial_flow})")
        orders = _filled(config.order_delay, f"$round({config.initial_flow})") if index > 0 else "[]"
        pipes.append(f"'{node}': {{'inbound': {inbound}, 'orders': {orders}}}")
    fragment: dict[str, Any] = {
        "types": {t: {"props": node_props} for t in types},
        "world": {f"{name}_pipes": {"type": "map", "default": "{" + ", ".join(pipes) + "}",
                                    "description": "Per node: inbound goods and orders on the way; slot 0 arrives next "
                                                   "round."},
                  f"{name}_demand": {"type": "int", "default": 0, "min": 0,
                                     "description": "Customers' order this round."},
                  f"{name}_sold": {"type": "int", "default": 0, "min": 0,
                                   "description": "Units delivered to customers so far."}},
        "events": [{"name": f"{name}: arrivals and shipping", "phase": "start",
                    "do": [{"economy": name, "action": "tick"}]},
                   {"name": f"{name}: default orders", "phase": "end", "do": [{"economy": name, "action": "close"}]}],
    }
    agents = agent_types(contract, types)
    if agents and "order" in config.actions:
        producer = config.nodes[-1]
        fragment["actions"] = {f"{name}_order": {
            "by": agents, "private": True, "terminal": True, "per_round": 1,
            "description": "Place this round's order with the node upstream (the producer: start a batch). One order a "
                           "round.",
            "when": [{"expr": f"not $actor.{name}_ordered", "why": "You already ordered this round."}],
            "params": {"qty": {"type": "int", "min": 0, "max": config.max_order, "description": "Units."}},
            "do": [{"economy": name, "action": "order", "who": "$actor", "qty": "$params.qty"}],
            "outcome": f"{{$'You started a batch of ' if $actor.id == '{producer}' else 'You ordered "
                       "'}{$params.qty} units."}}
        fragment["views"] = {
            f"{name}_position": {"for": agents, "title": "Your position", "bullet": False,
                                 "show": f"Stock {{$count_items($actor, '{config.item}')}} · backlog "
                                         f"{{{name}_backlog}} · cost this round {{{name}_round_cost}} · total cost "
                                         f"{{{name}_cost}}"},
            f"{name}_this_round": {"for": agents, "title": "This round", "bullet": False,
                                   "show": f"Arrived {{{name}_received}} · order received {{{name}_incoming}} · "
                                           f"shipped {{{name}_shipped}}"},
            f"{name}_on_the_way": {"for": agents, "title": "On the way to you", "bullet": False,
                                   "show": f"{{$pipeline_text($actor, '{name}')}}"}}
    return fragment


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _number(runner: Any, value: Any, vars: dict[str, Any], where: str, integer: bool = False) -> float:
    try:
        result = runner.eval(value, vars)
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    if isinstance(result, bool) or not isinstance(result, (int, float)) or result < 0:
        raise RunError(f"must give a number ≥ 0, got {result!r}", where)
    return float(round(result)) if integer else float(result)


def _pipeline(world: Any, name: str, node_id: str) -> list[int]:
    return list(((world.props.get(f"{name}_pipes") or {}).get(node_id) or {}).get("inbound") or [])


@function("pipeline(agent, chain)", "Units on their way to a node of a supply chain, slot 0 arriving next round.",
          min_args=2, max_args=2, family="economy")
def _pipeline_fn(call: Call) -> list[int]:
    world: Any = call.scope.world
    agent = maybe_entity(world, call.arg(0))
    if agent is None:
        raise ExprError(f"$pipeline: expected an entity or id, got {call.arg(0)!r}", call.source)
    return _pipeline(world, str(call.arg(1)), agent.id)


@function("pipeline_text(agent, chain)", "Units on their way to a node, in words: '4 next round, 8 in 2 rounds'.",
          min_args=2, max_args=2, family="economy")
def _pipeline_text(call: Call) -> str:
    world: Any = call.scope.world
    agent = maybe_entity(world, call.arg(0))
    if agent is None:
        raise ExprError(f"$pipeline_text: expected an entity or id, got {call.arg(0)!r}", call.source)
    pipe = _pipeline(world, str(call.arg(1)), agent.id)
    parts = [f"{qty} {'next round' if slot == 0 else f'in {slot + 1} rounds'}" for slot, qty in enumerate(pipe)]
    return ", ".join(parts) or "nothing"


def _advance(pipe: list[int]) -> list[int]:
    return pipe[1:] + [0] if pipe else []


def _push(pipe: list[int], qty: int) -> list[int]:
    return pipe[:-1] + [pipe[-1] + qty] if pipe else [qty]


@family_action("economy", ("supply_chain",), "tick", internal=True,
               example='{"economy": "beer", "action": "tick"}  (demand, arrivals, orders received, shipping and costs '
                       'for every node)')
def _supply_chain_tick(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: SupplyChainConfig = config_of(world, name, SUPPLY_CHAIN, where)
    base = f"mechanisms.{name}"
    demand = int(_number(runner, config.demand, {}, f"{base}.demand", integer=True))
    world.set_world(f"{name}_demand", demand)
    pipes = {k: {"inbound": list(v.get("inbound") or []), "orders": list(v.get("orders") or [])}
             for k, v in (world.props.get(f"{name}_pipes") or {}).items()}
    nodes = [entity_of(world, node, where, "a chain node") for node in config.nodes]
    for index, node in enumerate(nodes):
        pipe = pipes[node.id]
        arrived = int(pipe["inbound"][0]) if pipe["inbound"] else 0
        pipe["inbound"] = _advance(pipe["inbound"])
        try:
            put_items(world, node, config.item, arrived, where)
        except Abort as exc:
            raise RunError(f"{node.name} has no room for arriving {config.item}: {exc.reason}", base) from None
        world.set_prop(node, f"{name}_received", arrived)
        if index == 0:
            incoming = demand
        else:
            incoming = int(pipe["orders"][0]) if pipe["orders"] else 0
            pipe["orders"] = _advance(pipe["orders"])
        world.set_prop(node, f"{name}_incoming", incoming)
        world.set_prop(node, f"{name}_ordered", False)
    holding = _number(runner, config.holding_cost, {}, f"{base}.holding_cost")
    backlog_cost = _number(runner, config.backlog_cost, {}, f"{base}.backlog_cost")
    for index, node in enumerate(nodes):
        due = int(props(node)[f"{name}_incoming"]) + int(props(node)[f"{name}_backlog"])
        shipped = min(held(world, node, config.item, where), due)
        if index == 0:
            destroy_items(world, config.item, node, shipped, "sold", where)
            world.set_world(f"{name}_sold", int(world.props.get(f"{name}_sold") or 0) + shipped)
        else:
            take_items(world, node, config.item, shipped, where)
            downstream = nodes[index - 1].id
            pipes[downstream]["inbound"] = _push(pipes[downstream]["inbound"], shipped)
        backlog = due - shipped
        world.set_prop(node, f"{name}_shipped", shipped)
        world.set_prop(node, f"{name}_backlog", backlog)
        world.set_prop(node, f"{name}_peak_backlog", max(int(props(node)[f"{name}_peak_backlog"]), backlog))
        cost = held(world, node, config.item, where) * holding + backlog * backlog_cost
        world.set_prop(node, f"{name}_round_cost", cost)
        world.set_prop(node, f"{name}_cost", float(props(node)[f"{name}_cost"]) + cost)
    world.set_world(f"{name}_pipes", pipes)


def _place(runner: Any, name: str, config: SupplyChainConfig, node: Any, qty: int, where: str) -> None:
    world = runner.world
    if node.id not in config.nodes:
        raise RunError(f"{node.id} is not a node of supply chain {name}", where)
    if props(node)[f"{name}_ordered"]:
        raise Abort("You already ordered this round.")
    limit = int(_number(runner, config.max_order, {}, f"mechanisms.{name}.max_order", integer=True))
    if qty > limit:
        raise Abort(f"An order is at most {limit} units.")
    pipes = {k: {"inbound": list(v.get("inbound") or []), "orders": list(v.get("orders") or [])}
             for k, v in (world.props.get(f"{name}_pipes") or {}).items()}
    index = config.nodes.index(node.id)
    if index == len(config.nodes) - 1:
        pipes[node.id]["inbound"] = _push(pipes[node.id]["inbound"], qty)
        if qty:
            bump(world, f"{config.inventory}_supply", config.item, qty)
            bump(world, f"{config.inventory}_flows", config.item, qty, group="production")
    else:
        upstream = config.nodes[index + 1]
        pipes[upstream]["orders"] = _push(pipes[upstream]["orders"], qty)
    world.set_world(f"{name}_pipes", pipes)
    world.set_prop(node, f"{name}_last_order", qty)
    world.set_prop(node, f"{name}_recent", (list(props(node)[f"{name}_recent"]) + [qty])[-4:])
    world.set_prop(node, f"{name}_ordered", True)


@family_action("economy", ("supply_chain",), "order", keys=("who", "qty"), required=("who", "qty"),
               example='{"economy": "beer", "action": "order", "who": "$actor", "qty": 8}  '
                       '(a node orders from upstream; the producer starts a batch)')
def _place_order(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: SupplyChainConfig = config_of(world, name, SUPPLY_CHAIN, where)
    node = entity_of(world, runner.eval(effect["who"], vars), where, "a chain node")
    _place(runner, name, config, node, whole(runner.eval(effect["qty"], vars), where, "qty"), where)


@family_action("economy", ("supply_chain",), "close", internal=True,
               example='{"economy": "beer", "action": "close"}  (place the default order for every node that placed '
                       'none)')
def _supply_chain_close(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: SupplyChainConfig = config_of(world, name, SUPPLY_CHAIN, where)
    for node_id in config.nodes:
        node = entity_of(world, node_id, where, "a chain node")
        if not props(node)[f"{name}_ordered"]:
            qty = int(_number(runner, config.default_order, {"it": node}, f"mechanisms.{name}.default_order",
                              integer=True))
            _place(runner, name, config, node, qty, where)
