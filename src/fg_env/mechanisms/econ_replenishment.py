"""The ``economy`` family's ``replenishment`` mode: inventory policies keeping a demand mechanism's items in stock.

Each item's orders travel in a pipeline for a lead time drawn per order and arrive at the start of a round; at the end
of every round, after the demand mechanism has sold, the policy reviews each item's inventory position (on hand plus on
order minus backorders) and orders — (s, S), (s, Q), order-up-to (base stock or periodic review), a service-level target
whose safety stock comes from the forecast's error and the lead time's spread, a custom expression, or agents' own
orders — within case packs, minimum and maximum orders, storage capacity and a budget, paying from a ledger account.
Holding, ordering, stockout and backorder costs are totalled per item. The round's work is in
:mod:`.econ_replenishment_rules`.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..patterns.base import declared
from ..registry import MechanismError, mode
from .econ_base import (
    DEMAND,
    REPLENISHMENT,
    compiles,
    declared_use,
    register_config,
    require_currency,
    require_types,
    type_list,
)
from .econ_demand import DemandConfig
from .econ_inventory import agent_types
from .expressions import Expr

__all__ = ["ReplenishmentConfig", "LeadTimeRef", "POLICIES", "NEEDS"]

#: Every policy, and the fields it needs.
POLICIES = ("s_S", "s_Q", "order_up_to", "service", "custom", "manual")
NEEDS: dict[str, tuple] = {"s_S": ("reorder_point", "order_up_to"), "s_Q": ("reorder_point", "order_qty"),
                           "order_up_to": ("order_up_to",), "service": (), "custom": ("decide",), "manual": ()}
_NOISE_DISTS = ("normal", "lognormal", "uniform", "laplace")


class LeadTimeRef(BaseModel):
    """A lead time drawn for every order from a noise pattern (fitted from purchase orders, say)."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(...,
                         description="A noise pattern (normal, lognormal, uniform or laplace), drawn once per order.")
    key: Expr | None = Field(None,
                            description="Its key as an expression over $it, for a keyed pattern (default: the item).")
    scale: float | str = Field(1.0, description="Multiplies the draw (expression over $it): \"$it.lead_weeks\" with a "
                                                "lognormal factor around 1.")


_LOCALS = ("$position (on hand + on order − backorders), $on_hand, $on_order, $backlog, $forecast and $forecast_sd "
           "(demand per round), $recent (average sales of the kept rounds), $lead_time and $lead_time_sd (rounds), "
           "$cover (lead time + review period), $safety (the service level's safety stock) and $target (the service "
           "level's order-up-to point)")


class ReplenishmentConfig(BaseModel):
    """Inventory policies for a demand mechanism's items."""

    model_config = ConfigDict(extra="forbid")

    demand: str = Field(...,
                        description="The economy.demand mechanism whose items and stock this keeps (declared before "
                                    "it).")
    policy: str = Field("s_S", description="s_S: when the position is at or below reorder_point, order up to "
                                           "order_up_to | s_Q: order order_qty (repeated until above the reorder "
                                           "point) | order_up_to: order up to order_up_to every review (base stock; "
                                           "periodic with review_every) | service: order up to $target, from "
                                           "service_level | custom: order what `decide` gives | manual: only orders "
                                           "placed by agents or effects. Or an expression over $inputs and $it giving "
                                           "one (for arms).")
    reorder_point: float | str | None = Field(None, description=f"s, as an expression over $it and {_LOCALS}.")
    order_up_to: float | str | None = Field(None, description="S, as an expression over the same locals.")
    order_qty: float | str | None = Field(None, description="Q, as an expression over the same locals.")
    decide: Expr | None = Field(None, description="custom: units to order now, as an expression over the same locals.")
    review_every: int | str = Field(1, description="Rounds between reviews (number or expression over $it).")
    service_level: float | str = Field(0.95, description="Chance of not running out before the next order can arrive "
                                                         "(the service policy, and $safety and $target).")
    forecast: str = Field("model", description="Demand per round the policies read: model (the demand mechanism's "
                                               "expected demand and its variance, averaged over its kept rounds, so "
                                               "one promotion week does not swing it) | recent (the average and "
                                               "spread of kept sales) | an expression over $it.")
    forecast_sd: float | str | None = Field(None, description="Standard deviation of demand per round (default: from "
                                                              "the forecast: the model's variance, the recent spread, "
                                                              "or √forecast).")
    lead_time: float | str | LeadTimeRef = Field(1.0, description="Clock units from order to arrival: a number or an "
                                                                  "expression over $it (known), or "
                                                                  "{pattern, key, scale} drawn per order. An order "
                                                                  "arrives at the start of the round that many units "
                                                                  "later (at least the next).")
    case_pack: int | str = Field(1, description="Orders come in multiples of this (expression over $it).")
    min_order: int | str = Field(0, description="Smallest order (expression over $it).")
    max_order: int | str | None = Field(None, description="Largest order (expression over $it).")
    capacity: float | str | None = Field(None, description="Most units on hand plus on order (expression over $it).")
    budget: float | str | None = Field(None, description="Money for orders each round across all items (expression); "
                                                         "the most urgent items are ordered first.")
    unit_cost: float | str | None = Field(None, description="Purchase price per unit (default: the demand's cost).")
    holding_cost: float | str = Field(0.0, description="Cost per unit on hand per round (expression over $it).")
    order_cost: float | str = Field(0.0, description="Cost per order placed (expression over $it).")
    stockout_cost: float | str = Field(0.0, description="Cost per unit of lost demand (expression over $it).")
    backorder_cost: float | str = Field(0.0, description="Cost per unit on backorder per round (expression over $it).")
    account: str | None = Field(None, description="Entity id paying for orders from its ledger balance (default: the "
                                                  "demand's account); orders it cannot afford are cut.")
    currency: str | None = Field(None, description="The ledger currency of `account` (default: the demand's).")
    who: str | list[str] | None = Field(None, description="Agent type(s) that may order with the tool <name>_order.")
    record: bool | str = Field(False, description="Post every order to the record <name>_orders when it arrives: time, "
                                                  "item, placed, arrived, qty, lead_time (clock units) and factor (the "
                                                  "draw ÷ scale) — rows to fit a lead-time pattern from.")


register_config(REPLENISHMENT, ReplenishmentConfig)

ORDER_FIELDS = {"time": "text", "item": "text", "placed": "int", "arrived": "int", "qty": "int", "lead_time": "number",
                "factor": "number"}


_DOC = ("Inventory policies for a demand mechanism's items: orders travel in a per-item pipeline for a lead time "
        "(fixed, or drawn per order from a noise pattern fitted from purchase orders) and arrive at the start of a "
        "round; at the end of every round, after the sales, each item's position (on hand + on order − backorders) is "
        "reviewed and the `policy` orders: s_S, s_Q, order_up_to (base stock, periodic with review_every), service "
        "(order up to the forecast over lead time + review plus z·σ safety stock for the service_level, σ from the "
        "forecast's error and the lead time's spread), custom (`decide`) or manual (agents' <name>_order tool and the "
        "`order` action). Orders respect case_pack, min_order, max_order, capacity and a round's budget, and are paid "
        "from a ledger account. Holding, ordering, stockout and backorder costs accrue per item; outputs "
        "<name>_orders, _purchases, _holding_cost, _order_cost, _stockout_cost, _backorder_cost, _total_cost, _profit "
        "(the demand's margin less these costs) and _average_stock_value.")


@mode("economy", "replenishment", ReplenishmentConfig, _DOC,
      example={"demand": "shop", "policy": "service", "service_level": 0.95,
               "lead_time": {"pattern": "lead_noise", "scale": "$it.lead_weeks"}, "case_pack": "$it.case_pack",
               "holding_cost": "$it.unit_cost * 0.004", "order_cost": 6})
def _expand_replenishment(name: str, config: ReplenishmentConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    raw = declared_use(contract, config.demand, DEMAND, "demand")
    demand = DemandConfig.model_validate({k: v for k, v in raw.items() if k not in ("kind", "mode")})
    order = list(contract.get("mechanisms") or {})
    if name in order and order.index(config.demand) > order.index(name):
        raise MechanismError(f"'{config.demand}' is declared after '{name}'",
                             f"declare '{config.demand}' first, so a round's sales are counted before reordering",
                             "demand")
    if demand.stock is None:
        raise MechanismError(f"'{config.demand}' keeps no stock", f"set \"stock\" on '{config.demand}'", "demand")
    _check_policy(config)
    for field in ("policy", "reorder_point", "order_up_to", "order_qty", "decide", "review_every", "service_level",
                  "forecast_sd", "case_pack", "min_order", "max_order", "capacity", "budget", "unit_cost",
                  "holding_cost", "order_cost", "stockout_cost", "backorder_cost", "record"):
        compiles(getattr(config, field), field)
    if config.forecast not in ("model", "recent"):
        compiles(config.forecast, "forecast")
    _check_lead_time(config, contract)
    account, currency = config.account or demand.account, config.currency or demand.currency
    if (account is None) != (currency is None):
        raise MechanismError("`account` and `currency` go together",
                             "give both to pay for orders from a ledger, or neither", "account")
    if currency is not None:
        require_currency(contract, currency)
    items = demand.items
    fragment: dict[str, Any] = {
        "types": {items: {"props": _item_props(name)}},
        "world": {f"{name}_spent": {"type": "map", "default": {},
                                    "description": "Money spent on orders this round: {round, amount}."}},
        "events": [{"name": f"{name}: arrivals", "phase": "start", "do": [{"economy": name, "action": "arrivals"}]},
                   {"name": f"{name}: review", "phase": "end", "do": [{"economy": name, "action": "review"}]}],
        "metrics": {f"{name}_stock_value": {"expr": f"$replenishment_totals('{name}', 'stock_value')", "unit": "money"},
                    f"{name}_on_order": {"expr": f"$sum({items}, $it.{name}_on_order)", "unit": "units"}},
        "outputs": _outputs(name, config.demand, demand),
    }
    if config.record is not False:
        fragment["records"] = {f"{name}_orders": {"fields": dict(ORDER_FIELDS), "notify": False,
                                                  "description": f"Orders of {name}, when they arrive."}}
    if config.who is not None:
        who = type_list(config.who)
        require_types(contract, who, "who")
        agents = agent_types(contract, who)
        if not agents:
            raise MechanismError("`who` names no agent type", "list types with \"agent\": true", "who")
        fragment["actions"] = {f"{name}_order": {
            "by": agents,
            "description": f"Order stock of one {items} from its supplier; it arrives after the lead time.",
            "params": {"item": {"type": "entity", "of": items, "description": "What to order."},
                       "qty": {"type": "int", "min": 1, "description": "Units (rounded up to the case pack)."}},
            "do": [{"economy": name, "action": "order", "item": "$params.item", "qty": "$params.qty"}],
            "outcome": "You ordered {$params.qty} × {$params.item.name}."}}
        fragment["views"] = {f"{name}_stock": {
            "for": agents, "title": "Stock", "of": items,
            "show": f"{{$it}}: {{{demand.stock}}} on hand, {{{name}_on_order}} on order, sold {{{config.demand}_sold}} "
                    "last round"}}
    return fragment


def _check_policy(config: ReplenishmentConfig) -> None:
    policy = config.policy
    if "$" in policy:
        return
    if policy not in POLICIES:
        raise MechanismError(f"'{policy}' is not a policy",
                             f"policies: {', '.join(POLICIES)} (or an expression giving one)", "policy")
    missing = [field for field in NEEDS[policy] if getattr(config, field) is None]
    if missing:
        raise MechanismError(f"policy {policy} needs `{missing[0]}`",
                             f"policy {policy} reads {', '.join(NEEDS[policy])}", missing[0])


def _check_lead_time(config: ReplenishmentConfig, contract: Mapping[str, Any]) -> None:
    lead = config.lead_time
    if not isinstance(lead, LeadTimeRef):
        compiles(lead, "lead_time")
        return
    spec = declared(contract).get(lead.pattern)
    if not isinstance(spec, Mapping):
        raise MechanismError(f"'{lead.pattern}' is not a declared pattern", "declare a noise pattern for the lead time",
                             "lead_time.pattern")
    if spec.get("kind") != "noise" or spec.get("dist", "normal") not in _NOISE_DISTS:
        raise MechanismError(f"'{lead.pattern}' is not a noise pattern",
                             "a lead time is drawn per order from {\"kind\": \"noise\", \"dist\": \"lognormal\", ...}",
                             "lead_time.pattern")
    compiles(lead.key, "lead_time.key")
    compiles(lead.scale, "lead_time.scale")


def _item_props(name: str) -> dict[str, Any]:
    whole = {"type": "int", "default": 0, "min": 0}
    amount = {"type": "number", "default": 0.0, "min": 0}
    return {
        f"{name}_pipeline": {"type": "list", "default": [], "description": "Orders on the way: [round due, units, "
                                                                           "round placed, lead time, lead-time draw]."},
        f"{name}_on_order": {**whole, "description": "Units on order."},
        f"{name}_orders": {**whole, "description": "Orders placed."},
        f"{name}_units_ordered": {**whole, "description": "Units ordered."},
        f"{name}_last_order": {**whole, "description": "Units of the latest order."},
        f"{name}_target": {"type": "number", "default": 0.0, "description": "Order-up-to point at the latest review."},
        f"{name}_purchases": {**amount, "description": "Paid for units ordered."},
        f"{name}_holding": {**amount, "description": "Holding cost so far."},
        f"{name}_ordering": {**amount, "description": "Ordering cost so far."},
        f"{name}_stockout_cost": {**amount, "description": "Stockout cost so far."},
        f"{name}_backorder_cost": {**amount, "description": "Backorder cost so far."},
    }


def _outputs(name: str, demand_name: str, demand: DemandConfig) -> dict[str, Any]:
    out: dict[str, Any] = {}
    # Only the outcomes an owner acts on carry a display format (reports pick their headline measures by it): profit and
    # the stock it ties up here, fill rate and revenue on the demand; the cost components stay plain numbers.
    for measure in ("orders", "units_ordered", "purchases", "holding_cost", "order_cost", "stockout_cost",
                    "backorder_cost", "total_cost"):
        out[f"{name}_{measure}"] = {"expr": f"$replenishment_totals('{name}', '{measure}')", "type": "number"}
    out[f"{name}_profit"] = {"expr": f"$round($demand_totals('{demand_name}', 'margin') - "
                                     f"$replenishment_totals('{name}', 'total_cost'), 2)", "type": "number",
                             "format": "money",
                             "description": "The demand's margin less holding, ordering, stockout and backorder costs."}
    out[f"{name}_average_stock_value"] = {"expr": f"$round($avg($series.{name}_stock_value, $it), 2) if "
                                                  f"$len($series.{name}_stock_value) > 0 else 0", "type": "number",
                                          "format": "money"}
    if demand.group is not None:
        out[f"{name}_total_cost_by_group"] = {"expr": f"$replenishment_totals('{name}', 'total_cost', 'group')",
                                              "type": "map"}
    return out
