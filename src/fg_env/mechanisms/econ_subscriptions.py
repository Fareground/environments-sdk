"""The ``agreements`` family's ``subscriptions`` mode: plans with a price and a period, free trials,
automatic renewals that wake the subscriber when the price changed or a trial ended, and cancellations."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import Call, ExprError, function
from ..registry import MechanismError, family_action, mode
from ..world.live import Abort
from ._common import entity_of
from .econ_assets import move_money
from .econ_base import (
    SUBSCRIPTIONS,
    amount,
    bump,
    config_of,
    emit_to,
    maybe_entity,
    money,
    props,
    register_config,
    require_currency,
    require_types,
    type_list,
    uses_of,
    valid_name,
)
from .econ_inventory import agent_types

__all__ = ["SubscriptionsConfig", "PlanSpec"]

STATS = {"started": 0, "trials": 0, "converted": 0, "renewed": 0, "cancelled": 0, "lapsed": 0, "revenue": 0}


class PlanSpec(BaseModel):
    """A plan offered from the start (more can be added as entities of type `<name>_plan`)."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., description="Entity id that offers the plan and receives the payments.")
    name: str = ""
    price: float = Field(..., ge=0, description="Charged at the start of every period.")
    period: int = Field(30, ge=1, description="Rounds between charges.")
    trial: int = Field(0, ge=0, description="Free rounds before the first charge (once per subscriber and plan).")
    description: str = ""


class SubscriptionsConfig(BaseModel):
    """Recurring plans paid from a ledger currency."""

    model_config = ConfigDict(extra="forbid")

    who: str | list[str] = Field(..., description="Type(s) that may subscribe.")
    currency: str = Field(..., description="Ledger currency plans are paid in.")
    plans: dict[str, PlanSpec] = Field({}, description="{plan id: {provider, name, price, period, trial}}.")
    providers: str | list[str] | None = Field(None, description="Agent type(s) that may reprice their own plans.")
    price_min: float = Field(0, ge=0, description="Lowest price a provider may set.")
    price_max: float | None = Field(None, ge=0, description="Highest price a provider may set.")
    actions: list[Literal["subscribe", "cancel", "resume", "set_price"]] = Field(
        ["subscribe", "cancel", "resume", "set_price"], description="Tools generated for agents.")


register_config(SUBSCRIPTIONS, SubscriptionsConfig)


@mode("agreements", "subscriptions", SubscriptionsConfig,
           "Recurring plans: `<name>_subscribe` (plans you do not have and can afford or try free), `<name>_cancel` "
           "(ends at the next renewal), `<name>_resume`, and `<name>_set_price` for providers. Renewals are charged "
           "automatically at the start of the round they are due; a subscriber is woken when a trial ends or the "
           "price changed, and a renewal that cannot be paid lapses. Plans are entities of `<name>_plan`, "
           "subscriptions of `<name>_sub`; totals (started, trials, converted, renewed, cancelled, lapsed, revenue) "
           "are in $world.<name>_stats. $subscribed(agent, plan_or_provider) reads membership.",
           example={"who": "household", "currency": "cash", "providers": "cafe",
                    "plans": {"coffee_club": {"provider": "bean_bar", "price": 30, "period": 30, "trial": 7}}})
def _expand_subscriptions(name: str, config: SubscriptionsConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    subscribers = type_list(config.who)
    require_types(contract, subscribers, "who")
    providers = type_list(config.providers) if config.providers is not None else []
    require_types(contract, providers, "providers")
    require_currency(contract, config.currency)
    if config.price_max is not None and config.price_max < config.price_min:
        raise MechanismError("price_max is below price_min", None, "price_max")
    plan, sub = f"{name}_plan", f"{name}_sub"
    entities: dict[str, Any] = {}
    for plan_id, spec in config.plans.items():
        if not valid_name(plan_id):
            raise MechanismError(f"plan id '{plan_id}' is not a valid id",
                                 "use letters, digits and _ (not a word expressions use, like in or not)",
                                 f"plans.{plan_id}")
        entities[plan_id] = {"type": plan, "name": spec.name or plan_id.replace("_", " ").title(), "props": {
            "provider": spec.provider, "price": spec.price, "period": spec.period, "trial": spec.trial,
            "description": spec.description}}
    fragment: dict[str, Any] = {
        "types": {
            plan: {"description": "A subscription plan.", "props": {
                "provider": {"type": "text", "default": ""}, "price": {"type": "number", "default": 0, "min": 0},
                "period": {"type": "int", "default": 30, "min": 1}, "trial": {"type": "int", "default": 0, "min": 0},
                "description": {"type": "text", "default": ""}, "subscribers": {"type": "int", "default": 0, "min": 0},
                "revenue": {"type": "number", "default": 0, "min": 0}}},
            sub: {"description": "One subscriber's subscription to a plan.", "props": {
                "subscriber": {"type": "text", "default": ""}, "plan": {"type": "text", "default": ""},
                "status": {"type": "enum", "values": ["trial", "active", "ended"], "default": "active"},
                "cancelling": {"type": "bool", "default": False}, "started": {"type": "int", "default": 0},
                "renews": {"type": "int", "default": 0}, "price": {"type": "number", "default": 0, "min": 0},
                "renewals": {"type": "int", "default": 0, "min": 0}, "reason": {"type": "text", "default": ""}}}},
        "entities": entities,
        "world": {f"{name}_stats": {"type": "map", "default": dict(STATS), "description": "Subscription totals."}},
        "events": [{"name": f"{name}: renewals", "phase": "start", "do": [{"agreements": name, "action": "tick"}]}],
        "actions": {}, "views": {},
    }
    agents = agent_types(contract, subscribers)
    mine = "$it.subscriber == $actor.id and $it.status != ended"
    offered = "$get($entity($it.provider), 'alive', false)"  # the plan's provider is still in business
    trial_available = f"$it.trial > 0 and not $any({sub}, $it.subscriber == $actor.id and $it.plan == $outer.id)"
    if agents:
        actions, views = fragment["actions"], fragment["views"]
        if "subscribe" in config.actions:
            actions[f"{name}_subscribe"] = {
                "by": agents,
                "description": "Subscribe to a plan: its price is charged now and every period (a free trial on your "
                               "first subscription to a plan that offers one).",
                "params": {"plan": {"type": "entity", "of": plan,
                      "where": f"{offered} and not $subscribed($actor, $it.id) and "
                               f"(({trial_available}) or $has($actor, '{config.currency}', $it.price))",
                      "description": "Plan."}},
                "do": [{"agreements": name, "action": "subscribe", "who": "$actor", "plan": "$params.plan"}],
                "outcome": "You subscribed to {$params.plan.name}.", "private": True}
        if "cancel" in config.actions:
            actions[f"{name}_cancel"] = {
                "by": agents,
                "description": "Cancel a subscription: it stays yours until its next renewal, then ends without a "
                               "charge.",
                "params": {"subscription": {"type": "entity", "of": sub, "where": f"{mine} and not $it.cancelling"}},
                "do": ["$params.subscription.cancelling = true"],
                "outcome": "Cancelled: it ends in round {$params.subscription.renews}.", "private": True}
        if "resume" in config.actions:
            actions[f"{name}_resume"] = {
                "by": agents, "description": "Keep a subscription you cancelled.",
                "params": {"subscription": {"type": "entity", "of": sub, "where": f"{mine} and $it.cancelling"}},
                "do": ["$params.subscription.cancelling = false"],
                "outcome": "It renews again in round {$params.subscription.renews}.", "private": True}
        views[f"{name}_plans"] = {"for": agents, "title": "Plans", "of": plan, "where": offered,
                                  "look": True, "show": "[{id}] {name}: {price|money} every {period} "
                                          "rounds{$' · ' + $text($it.trial) + ' rounds free' if " + trial_available
                                  + " else ''}"}
        views[f"{name}_mine"] = {"for": agents, "title": "Your subscriptions", "of": sub, "where": mine,
                                 "show": "[{id}] {$entity($it.plan).name}: {status}, next charge round "
                                         "{renews} at {$entity($it.plan).price|money}{$' (cancelled, ends then)' "
                                         "if $it.cancelling else ''}"}
    sellers = agent_types(contract, providers)
    if sellers and "set_price" in config.actions:
        price: dict[str, Any] = {"type": "number", "min": config.price_min, "description": "New price per period."}
        if config.price_max is not None:
            price["max"] = config.price_max
        fragment["actions"][f"{name}_set_price"] = {
            "by": sellers,
            "description": "Change a plan's price; subscribers pay it from their next renewal and are told now.",
            "params": {"plan": {"type": "entity", "of": plan, "where": "$it.provider == $actor.id"}, "price": price},
            "do": [{"agreements": name, "action": "set_price", "plan": "$params.plan", "price": "$params.price"}],
            "outcome": "{$params.plan.name} now costs {$params.price|money}."}
        fragment["views"][f"{name}_offered"] = {
            "for": sellers, "title": "Your plans", "of": plan, "where": "$it.provider == $actor.id",
            "show": "[{id}] {name}: {price|money}, {subscribers} subscribers, revenue {revenue|money}"}
    return fragment


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _subscriptions(world: Any, name: str, who: str) -> list[Any]:
    return [s for s in world.entities_of(f"{name}_sub") if props(s)["subscriber"] == who]


def _stat(world: Any, name: str, key: str, delta: float) -> None:
    bump(world, f"{name}_stats", key, delta)


def _end(world: Any, name: str, sub: Any, plan: Any | None, reason: str, stat: str | None) -> None:
    world.set_prop(sub, "status", "ended")
    world.set_prop(sub, "reason", reason)
    if plan is not None:
        world.set_prop(plan, "subscribers", max(0, int(props(plan)["subscribers"]) - 1))
    if stat:
        _stat(world, name, stat, 1)


def _charge(world: Any, config: SubscriptionsConfig, subscriber: Any, plan: Any, name: str, where: str) -> float:
    price = float(props(plan)["price"])
    provider = entity_of(world, props(plan)["provider"], where, "the plan's provider")
    move_money(world, config.currency, subscriber, provider, price, where)
    world.set_prop(plan, "revenue", float(props(plan)["revenue"]) + price)
    _stat(world, name, "revenue", price)
    return price


@function("subscribed(agent, plan_or_provider)",
          "True when the agent has a live subscription (trial or paid) to the plan, or to any plan of the provider.",
          min_args=2, max_args=2, family="agreements")
def _subscribed(call: Call) -> bool:
    world: Any = call.scope.world
    agent = maybe_entity(world, call.arg(0))
    if agent is None:
        raise ExprError(f"$subscribed: expected an entity or id, got {call.arg(0)!r}", call.source)
    target = call.arg(1)
    target_id = target.id if hasattr(target, "entity_type") else str(target)
    for name in uses_of(world, SUBSCRIPTIONS):
        for sub in _subscriptions(world, name, agent.id):
            if props(sub)["status"] == "ended":
                continue
            plan = world.entities.get(props(sub)["plan"])
            if props(sub)["plan"] == target_id or (plan is not None and props(plan)["provider"] == target_id):
                return True
    return False


@family_action("agreements", ("subscriptions",), "subscribe", keys=("who", "plan"), required=("who", "plan"),
               example='{"agreements": "coffee", "action": "subscribe", "who": "$actor", "plan": "$params.plan"}  '
                       '(start a subscription: a trial or a first charge)')
def _subscribe(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    config: SubscriptionsConfig = config_of(world, name, SUBSCRIPTIONS, where)
    subscriber = entity_of(world, runner.eval(effect["who"], vars), where, "a subscriber")
    plan = entity_of(world, runner.eval(effect["plan"], vars), where, "a plan")
    if plan.entity_type != f"{name}_plan":
        raise RunError(f"{plan.id} is not a plan of {name}", where)
    provider = maybe_entity(world, props(plan)["provider"])
    if provider is None or not provider.alive:
        raise Abort(f"{plan.name} is no longer available.")
    history = [s for s in _subscriptions(world, name, subscriber.id) if props(s)["plan"] == plan.id]
    if any(props(s)["status"] != "ended" for s in history):
        raise Abort(f"You already subscribe to {plan.name}.")
    trial = int(props(plan)["trial"]) if not history else 0
    values: dict[str, Any] = {"subscriber": subscriber.id, "plan": plan.id, "started": world.round}
    if trial:
        values.update(status="trial", renews=world.round + trial, price=0)
        _stat(world, name, "trials", 1)
    else:
        values.update(status="active", renews=world.round + int(props(plan)["period"]),
                      price=_charge(world, config, subscriber, plan, name, where))
    world.create(f"{name}_sub", None, f"{subscriber.name}: {plan.name}", values, None, world.scope(), where)
    world.set_prop(plan, "subscribers", int(props(plan)["subscribers"]) + 1)
    _stat(world, name, "started", 1)
    emit_to(world, f"{name}_joined",
            f"{subscriber.name} subscribed to {plan.name}" + (" (free trial)." if trial else "."),
            [props(plan)["provider"]])


@family_action("agreements", ("subscriptions",), "set_price", keys=("plan", "price"), required=("plan", "price"),
               example='{"agreements": "coffee", "action": "set_price", "plan": "coffee_club", "price": 36}  '
                       '(reprice a plan and tell its subscribers)')
def _set_plan_price(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    config: SubscriptionsConfig = config_of(world, name, SUBSCRIPTIONS, where)
    plan = entity_of(world, runner.eval(effect["plan"], vars), where, "a plan")
    if plan.entity_type != f"{name}_plan":
        raise RunError(f"{plan.id} is not a plan of {name}", where)
    price = amount(runner.eval(effect["price"], vars), where, "a price")
    if price < config.price_min or (config.price_max is not None and price > config.price_max):
        raise Abort(f"The price must be between {money(config.price_min)} and {money(config.price_max)}.")
    old = float(props(plan)["price"])
    if abs(price - old) < 1e-9:
        return
    world.set_prop(plan, "price", price)
    members = [s for s in world.entities_of(f"{name}_sub") if props(s)["plan"] == plan.id and props(s)["status"]
               != "ended"]
    for sub in members:
        text = (f"{plan.name} changes from {money(old)} to {money(price)} {config.currency}; your next charge in round "
                f"{props(sub)['renews']} is the new price unless you cancel.")
        emit_to(world, f"{name}_price", text, [props(sub)["subscriber"]], {"plan": plan.id, "old": old, "price": price},
                why=f"{plan.name} changed its price.")


@family_action("agreements", ("subscriptions",), "tick", internal=True,
               example='{"agreements": "coffee", "action": "tick"}  '
                       '(renew, convert trials, end cancelled or unpaid subscriptions that are due)')
def _subscriptions_tick(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    config: SubscriptionsConfig = config_of(world, name, SUBSCRIPTIONS, where)
    due = [s for s in world.entities_of(f"{name}_sub") if props(s)["status"] != "ended" and int(props(s)["renews"])
           <= world.round]
    for sub in due:
        p = props(sub)
        plan, subscriber = world.entities.get(p["plan"]), world.entities.get(p["subscriber"])
        if subscriber is None or not subscriber.alive:
            _end(world, name, sub, plan, "subscriber gone", None)
            continue
        provider = maybe_entity(world, props(plan)["provider"]) if plan is not None else None
        if plan is None or not plan.alive or provider is None or not provider.alive:
            _end(world, name, sub, plan, "plan withdrawn", None)
            emit_to(world, f"{name}_ended", "A plan you subscribed to was withdrawn.", [subscriber.id])
            continue
        if p["cancelling"]:
            _end(world, name, sub, plan, "cancelled", "cancelled")
            emit_to(world, f"{name}_ended", f"Your {plan.name} subscription has ended.", [subscriber.id])
            continue
        mark = world.journal.mark()
        try:
            price = _charge(world, config, subscriber, plan, name, where)
        except Abort:
            world.journal.rollback(mark)
            _end(world, name, sub, plan, "payment failed", "lapsed")
            emit_to(world, f"{name}_lapsed",
                    f"Your {plan.name} renewal of {money(props(plan)['price'])} {config.currency} could not be paid; "
                    "it has ended.",
                    [subscriber.id], why=f"Your {plan.name} subscription lapsed.")
            continue
        was_trial, last = p["status"] == "trial", float(p["price"])
        world.set_prop(sub, "status", "active")
        world.set_prop(sub, "renews", int(p["renews"]) + int(props(plan)["period"]))
        world.set_prop(sub, "price", price)
        if was_trial:
            _stat(world, name, "converted", 1)
            emit_to(world, f"{name}_renewed", f"Your free trial of {plan.name} ended; you were charged {money(price)} "
                                              f"{config.currency}. Next charge in round {props(sub)['renews']}; cancel "
                                              "any time.",
                    [subscriber.id], why=f"Your {plan.name} trial ended and you were charged.")
            continue
        world.set_prop(sub, "renewals", int(p["renewals"]) + 1)
        _stat(world, name, "renewed", 1)
        if abs(price - last) > 1e-9:
            emit_to(world, f"{name}_renewed",
                    f"{plan.name} renewed at {money(price)} {config.currency} instead of {money(last)}.",
                    [subscriber.id], why=f"{plan.name} renewed at a new price.")
        else:
            emit_to(world, f"{name}_renewed", f"{plan.name} renewed: {money(price)} {config.currency}.",
                    [subscriber.id])
