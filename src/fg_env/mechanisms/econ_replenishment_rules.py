"""The ``economy.replenishment`` round: orders arriving at its start; costs, reviews and the policy's orders at its end;
the ``order`` action agents and effects use; and ``$replenishment_totals``.

Lead times drawn from a pattern use the pattern's own stream (one draw per item and round), so arms of an experiment
see the same delivery luck and a snapshot, clone or fork needs nothing beyond the world's properties.
"""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function
from ..expr.objects import Entity
from ..patterns.runtime import key_text
from ..registry import family_action
from ..world.live import Abort
from ._common import entity_of
from .econ_assets import balance, burn_money, credit_of
from .econ_base import DEMAND, REPLENISHMENT, config_of, props
from .econ_demand import DemandConfig
from .econ_demand_trade import number_of, stock_in
from .econ_replenishment import NEEDS, POLICIES, LeadTimeRef, ReplenishmentConfig

__all__: list[str] = []

_NORMAL = NormalDist()


def _configs(world: Any, name: str, where: str) -> tuple[ReplenishmentConfig, DemandConfig]:
    config: ReplenishmentConfig = config_of(world, name, REPLENISHMENT, where)
    return config, config_of(world, config.demand, DEMAND, where)


def _step(world: Any) -> float:
    return float(world.contract.clock.step) or 1.0


# ---------------------------------------------------------------------------
# Lead times
# ---------------------------------------------------------------------------


def _lead_key(runner: Any, lead: LeadTimeRef, item: Entity) -> Any:
    return runner.eval(lead.key, {"it": item}) if lead.key else item


def _lead_moments(runner: Any, config: ReplenishmentConfig, item: Entity, where: str) -> tuple[float, float]:
    """Mean and standard deviation of an item's lead time, in clock units."""
    lead = config.lead_time
    if not isinstance(lead, LeadTimeRef):
        return number_of(runner, lead, {"it": item}, where, low=0), 0.0
    runtime = runner.world.patterns
    cfg = runtime.configs[lead.pattern]
    key = key_text(_lead_key(runner, lead, item)) if cfg.keyed else None
    scale = number_of(runner, lead.scale, {"it": item}, f"{where}.scale", low=0)

    def param(field: str) -> float:
        return float(runtime.param(lead.pattern, key, field, where))

    if cfg.dist == "lognormal":
        mu, sigma = param("mean"), param("sd")
        mean = math.exp(mu + sigma * sigma / 2)
        return scale * mean, scale * mean * math.sqrt(math.expm1(sigma * sigma))
    if cfg.dist == "uniform":
        low, high = param("low"), param("high")
        return scale * (low + high) / 2, scale * (high - low) / math.sqrt(12)
    return scale * param("mean"), scale * param("sd")


def _lead_draw(runner: Any, config: ReplenishmentConfig, item: Entity, where: str) -> tuple[float, float]:
    """This order's lead time in clock units, and the pattern's draw (1 for a known lead time)."""
    lead = config.lead_time
    if not isinstance(lead, LeadTimeRef):
        return number_of(runner, lead, {"it": item}, where, low=0), 1.0
    runtime = runner.world.patterns
    key = _lead_key(runner, lead, item) if runtime.configs[lead.pattern].keyed else item.id
    try:
        factor = runtime.call(lead.pattern, [key], where)
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    factor = max(0.0, float(factor))
    return number_of(runner, lead.scale, {"it": item}, f"{where}.scale", low=0) * factor, factor


# ---------------------------------------------------------------------------
# What a review reads
# ---------------------------------------------------------------------------


def _context(runner: Any, name: str, config: ReplenishmentConfig, demand: DemandConfig, item: Entity,
             base: str) -> dict[str, Any]:
    """The locals policy expressions read for one item now."""
    world = runner.world
    p = props(item)
    assert demand.stock is not None
    on_hand, on_order, backlog = int(p[demand.stock]), int(p[f"{name}_on_order"]), int(p[f"{config.demand}_backlog"])
    recent = [float(v) for v in p[f"{config.demand}_recent"]]
    average = sum(recent) / len(recent) if recent else 0.0
    if config.forecast == "model":
        expected = [float(v) for v in p[f"{config.demand}_expected_recent"]] or [0.0]
        variances = [float(v) for v in p[f"{config.demand}_variance_recent"]] or [0.0]
        forecast, sd = sum(expected) / len(expected), math.sqrt(max(0.0, sum(variances) / len(variances)))
    elif config.forecast == "recent":
        forecast = average
        sd = (math.sqrt(sum((v - average) ** 2 for v in recent) / (len(recent) - 1)) if len(recent) > 1
              else math.sqrt(average))
    else:
        forecast = number_of(runner, config.forecast, {"it": item}, f"{base}.forecast", low=0)
        sd = math.sqrt(forecast)
    if config.forecast_sd is not None:
        sd = number_of(runner, config.forecast_sd, {"it": item, "forecast": forecast}, f"{base}.forecast_sd", low=0)
    lead, lead_sd = _lead_moments(runner, config, item, f"{base}.lead_time")
    step = _step(world)
    lead, lead_sd = lead / step, lead_sd / step
    review = max(1.0, number_of(runner, config.review_every, {"it": item}, f"{base}.review_every", low=1))
    level = number_of(runner, config.service_level, {"it": item}, f"{base}.service_level", 0, 1)
    z = _NORMAL.inv_cdf(min(max(level, 1e-9), 1 - 1e-9))
    cover = lead + review
    safety = z * math.sqrt(cover * sd * sd + forecast * forecast * lead_sd * lead_sd)
    return {"it": item, "position": on_hand + on_order - backlog, "on_hand": on_hand, "on_order": on_order,
            "backlog": backlog, "forecast": forecast, "forecast_sd": sd, "recent": average, "lead_time": lead,
            "lead_time_sd": lead_sd, "cover": cover, "safety": safety, "target": forecast * cover + safety}


def _policy(runner: Any, config: ReplenishmentConfig, item: Entity, base: str) -> str:
    policy = config.policy
    if "$" in policy:
        try:
            policy = runner.eval(policy, {"it": item})
        except ExprError as exc:
            raise RunError(str(exc), f"{base}.policy") from None
    if policy not in POLICIES:
        raise RunError(f"gave {policy!r}, not a policy ({', '.join(POLICIES)})", f"{base}.policy")
    missing = [field for field in NEEDS[policy] if getattr(config, field) is None]
    if missing:
        raise RunError(f"policy {policy} needs `{missing[0]}`", f"{base}.{missing[0]}")
    return str(policy)


def _wanted(runner: Any, config: ReplenishmentConfig, policy: str, ctx: dict[str, Any], base: str) -> tuple[int, float]:
    """Units the policy orders now, and the order-up-to point it aimed at."""
    position = ctx["position"]

    def value(field: str) -> float:
        return number_of(runner, getattr(config, field), ctx, f"{base}.{field}")

    if policy == "s_S":
        point, level = value("reorder_point"), value("order_up_to")
        return (math.ceil(level - position) if position <= point else 0), level
    if policy == "s_Q":
        point, qty = value("reorder_point"), value("order_qty")
        if qty <= 0:
            raise RunError(f"must be more than 0, got {qty:g}", f"{base}.order_qty")
        return (math.ceil((math.floor((point - position) / qty) + 1) * qty) if position <= point else 0), point + qty
    if policy == "order_up_to":
        level = value("order_up_to")
        return math.ceil(level - position), level
    if policy == "service":
        level = math.ceil(ctx["target"])
        return level - position, level
    if policy == "custom":
        return math.ceil(value("decide")), position
    return 0, position


def _fitted(runner: Any, config: ReplenishmentConfig, item: Entity, qty: int, position: int,
            base: str) -> tuple[int, str]:
    """``qty`` within the case pack, minimum and maximum order and capacity; 0 and why when nothing fits."""
    scope = {"it": item}
    pack = max(1, round(number_of(runner, config.case_pack, scope, f"{base}.case_pack", low=0)))
    minimum = number_of(runner, config.min_order, scope, f"{base}.min_order", low=0)
    if qty <= 0:
        return 0, "Nothing to order."
    qty = math.ceil(max(qty, minimum) / pack) * pack
    if config.max_order is not None:
        most = number_of(runner, config.max_order, scope, f"{base}.max_order", low=0)
        qty = min(qty, math.floor(most / pack) * pack)
    if config.capacity is not None:
        room = number_of(runner, config.capacity, scope, f"{base}.capacity") - position
        qty = min(qty, math.floor(room / pack) * pack)
        if qty <= 0:
            return 0, f"No room: capacity {room + position:g} units, {position} already on hand or on order."
    if qty <= 0 or qty < minimum:
        return 0, f"An order of {item.name} is at least {math.ceil(minimum / pack) * pack} units in packs of {pack}."
    return qty, ""


def _money_left(runner: Any, config: ReplenishmentConfig, demand: DemandConfig, name: str, base: str) -> float:
    """What orders may still spend this round: the budget's rest, and the account's balance with its credit."""
    world = runner.world
    left = math.inf
    if config.budget is not None:
        spent = world.props.get(f"{name}_spent") or {}
        used = float(spent.get("amount", 0.0)) if spent.get("round") == world.round else 0.0
        left = number_of(runner, config.budget, {}, f"{base}.budget", low=0) - used
    account, currency = config.account or demand.account, config.currency or demand.currency
    if account is not None and currency is not None:
        holder = entity_of(world, account, f"{base}.account", "the account")
        left = min(left, balance(world, holder, currency, base) + credit_of(world, holder, currency))
    return left


def _place(runner: Any, name: str, config: ReplenishmentConfig, demand: DemandConfig, item: Entity, qty: int,
           base: str) -> None:
    world = runner.world
    scope = {"it": item}
    lead, factor = _lead_draw(runner, config, item, f"{base}.lead_time")
    due = world.round + max(1, round(lead / _step(world)))
    p = props(item)
    unit = number_of(runner, config.unit_cost if config.unit_cost is not None else demand.cost, scope,
                     f"{base}.unit_cost", low=0)
    fee = number_of(runner, config.order_cost, scope, f"{base}.order_cost", low=0)
    account, currency = config.account or demand.account, config.currency or demand.currency
    if account is not None and currency is not None:
        holder = entity_of(world, account, f"{base}.account", "the account")
        burn_money(world, currency, holder, qty * unit, f"{name}_suppliers", base)
        burn_money(world, currency, holder, fee, f"{name}_ordering", base)
    pipeline = ([list(entry) for entry in p[f"{name}_pipeline"]]
                + [[due, qty, world.round, round(lead, 6), round(factor, 6)]])
    for prop, value in ((f"{name}_pipeline", pipeline), (f"{name}_on_order", int(p[f"{name}_on_order"]) + qty),
                        (f"{name}_orders", int(p[f"{name}_orders"]) + 1),
                        (f"{name}_units_ordered", int(p[f"{name}_units_ordered"]) + qty),
                        (f"{name}_last_order", qty), (f"{name}_purchases", float(p[f"{name}_purchases"]) + qty * unit),
                        (f"{name}_ordering", float(p[f"{name}_ordering"]) + fee)):
        world.set_prop(item, prop, value)
    spent = world.props.get(f"{name}_spent") or {}
    used = float(spent.get("amount", 0.0)) if spent.get("round") == world.round else 0.0
    world.set_world(f"{name}_spent", {"round": world.round, "amount": used + qty * unit + fee})


def _cost_of(runner: Any, config: ReplenishmentConfig, demand: DemandConfig, item: Entity, qty: int,
             base: str) -> tuple[float, float]:
    scope = {"it": item}
    unit = number_of(runner, config.unit_cost if config.unit_cost is not None else demand.cost, scope,
                     f"{base}.unit_cost", low=0)
    return unit, number_of(runner, config.order_cost, scope, f"{base}.order_cost", low=0)


# ---------------------------------------------------------------------------
# The round
# ---------------------------------------------------------------------------


@family_action("economy", ("replenishment",), "arrivals", internal=True,
               example='{"economy": "reorder", "action": "arrivals"}  (orders due this round enter stock)')
def _arrivals(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config, demand = _configs(world, name, where)
    recording = config.record is True or (isinstance(config.record, str) and runner.eval(config.record, {}))
    date = runner.eval("$clock.date", {}) if recording else None
    for item in world.alive_of(demand.items):
        pipeline = [list(entry) for entry in props(item)[f"{name}_pipeline"]]
        due = [entry for entry in pipeline if entry[0] <= world.round]
        if not due:
            continue
        units = sum(int(entry[1]) for entry in due)
        stock_in(world, config.demand, demand, item, units, name)
        world.set_prop(item, f"{name}_pipeline", [entry for entry in pipeline if entry[0] > world.round])
        world.set_prop(item, f"{name}_on_order", int(props(item)[f"{name}_on_order"]) - units)
        for entry in due if recording else []:
            world.post(f"{name}_orders",
                       {"time": str(date) if date else str(world.round), "item": item.id, "placed": entry[2],
                        "arrived": world.round, "qty": entry[1], "lead_time": entry[3],
                        "factor": entry[4]},
                       None, None, f"mechanisms.{name}.record")


@family_action("economy", ("replenishment",), "review", internal=True,
               example='{"economy": "reorder", "action": "review"}  (accrue this round\'s costs, then order by the '
                       'policy)')
def _review(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config, demand = _configs(world, name, where)
    base = f"mechanisms.{name}"
    wanted: list[tuple[float, int, Entity, int, int]] = []
    for index, item in enumerate(world.alive_of(demand.items)):
        _accrue(runner, name, config, demand, item, base)
        review = number_of(runner, config.review_every, {"it": item}, f"{base}.review_every", low=1)
        if (world.round - 1) % max(1, round(review)):
            continue
        policy = _policy(runner, config, item, base)
        if policy == "manual":
            continue
        ctx = _context(runner, name, config, demand, item, base)
        qty, level = _wanted(runner, config, policy, ctx, base)
        world.set_prop(item, f"{name}_target", float(level))
        qty, _ = _fitted(runner, config, item, qty, ctx["position"], base)
        if qty:
            wanted.append((ctx["position"] / max(ctx["forecast"], 1e-9), index, item, qty, ctx["position"]))
    left = _money_left(runner, config, demand, name, base)
    for _, _, item, qty, position in sorted(wanted, key=lambda entry: entry[:2]):
        unit, fee = _cost_of(runner, config, demand, item, qty, base)
        if qty * unit + fee > left + 1e-9:
            affordable = math.floor(max(0.0, left - fee) / unit) if unit > 0 else 0
            qty, _ = _fitted(runner, config, item, min(qty, affordable), position, base)
            if not qty or qty * unit + fee > left + 1e-9:
                continue
        _place(runner, name, config, demand, item, qty, base)
        left -= qty * unit + fee


def _accrue(runner: Any, name: str, config: ReplenishmentConfig, demand: DemandConfig, item: Entity, base: str) -> None:
    world = runner.world
    p = props(item)
    scope = {"it": item}
    assert demand.stock is not None
    for prop, field, units in ((f"{name}_holding", "holding_cost", int(p[demand.stock])),
                               (f"{name}_stockout_cost", "stockout_cost", int(p[f"{config.demand}_lost"])),
                               (f"{name}_backorder_cost", "backorder_cost", int(p[f"{config.demand}_backlog"]))):
        rate = getattr(config, field)
        if units and rate != 0:
            world.set_prop(item, prop,
                           float(p[prop]) + units * number_of(runner, rate, scope, f"{base}.{field}", low=0))


@family_action("economy", ("replenishment",), "order", keys=("item", "qty"), required=("item", "qty"),
               example='{"economy": "reorder", "action": "order", "item": "$params.item", "qty": 24}  '
                       '(order stock now: rounded to the case pack, within capacity, the budget and the account)')
def _order(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config, demand = _configs(world, name, where)
    item = entity_of(world, runner.eval(effect["item"], vars), where, f"a {demand.items}")
    if not world.is_a(item.entity_type, demand.items):
        raise RunError(f"{item.id} is not a {demand.items}", where)
    wanted = runner.eval(effect["qty"], vars)
    if isinstance(wanted, bool) or not isinstance(wanted, (int, float)) or wanted < 0:
        raise RunError(f"qty must be a number ≥ 0, got {wanted!r}", where)
    base = f"mechanisms.{name}"
    assert demand.stock is not None
    p = props(item)
    position = int(p[demand.stock]) + int(p[f"{name}_on_order"]) - int(p[f"{config.demand}_backlog"])
    qty, why = _fitted(runner, config, item, math.ceil(wanted), position, base)
    if not qty:
        raise Abort(why)
    unit, fee = _cost_of(runner, config, demand, item, qty, base)
    left = _money_left(runner, config, demand, name, base)
    if qty * unit + fee > left + 1e-9:
        raise Abort(f"{qty} × {item.name} costs {qty * unit + fee:.2f}; only {max(0.0, left):.2f} is available this "
                    "round.")
    _place(runner, name, config, demand, item, qty, base)


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------

_PROPS = {"orders": "orders", "units_ordered": "units_ordered", "purchases": "purchases", "holding_cost": "holding",
          "order_cost": "ordering", "stockout_cost": "stockout_cost", "backorder_cost": "backorder_cost",
          "on_order": "on_order"}
_COSTS = ("holding_cost", "order_cost", "stockout_cost", "backorder_cost")


def _item_value(world: Any, name: str, config: ReplenishmentConfig, demand: DemandConfig, item: Entity, measure: str,
                where: str) -> float:
    p = props(item)
    if measure == "total_cost":
        return sum(float(p[f"{name}_{_PROPS[m]}"]) for m in _COSTS)
    if measure == "stock_value":
        assert demand.stock is not None
        cost = config.unit_cost if config.unit_cost is not None else demand.cost
        unit = compile_expr(cost)(world.scope(it=item)) if isinstance(cost, str) else cost
        return float(p[demand.stock]) * float(unit)
    return float(p[f"{name}_{_PROPS[measure]}"])


@function("replenishment_totals(mechanism, measure, by?)",
          "A replenishment mechanism's total: orders, units_ordered, purchases, holding_cost, order_cost, "
          "stockout_cost, backorder_cost, total_cost, on_order or stock_value (units on hand at unit cost, now) — "
          "overall, or {key: total} by 'item' or 'group'.", min_args=2, max_args=3, family="economy")
def _replenishment_totals(call: Call) -> Any:
    world: Any = call.scope.world
    name, measure, by = str(call.arg(0)), str(call.arg(1)), call.arg(2)
    config, demand = _configs(world, name, call.source)
    known = [*_PROPS, "total_cost", "stock_value"]
    if measure not in known:
        raise ExprError(f"$replenishment_totals: unknown measure '{measure}' (measures: {', '.join(known)})",
                        call.source)
    if by not in (None, "item", "group") or (by == "group" and demand.group is None):
        raise ExprError(f"$replenishment_totals: `by` is item or group (with the demand's `group`), got {by!r}",
                        call.source)
    group = compile_expr(demand.group) if by == "group" and demand.group else None
    out: dict[str | None, float] = {}
    for item in world.entities_of(demand.items):
        if measure == "stock_value" and not item.alive:
            continue
        key = None if by is None else item.id if by == "item" else str(group(world.scope(it=item)))  # type: ignore[misc]
        out[key] = out.get(key, 0.0) + _item_value(world, name, config, demand, item, measure, call.source)
    rounded = {key: (int(value) if measure in ("orders", "units_ordered", "on_order") else round(value, 2))
               for key, value in out.items()}
    return rounded.get(None, 0) if by is None else rounded
