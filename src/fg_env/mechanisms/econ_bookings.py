"""The ``agreements`` family's ``bookings`` mode: capacity-limited resources (tables, seats, rides)
booked for a future round or joined as a queue, with waitlists, FIFO or priority service and abandonment."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function
from ..registry import MechanismError, family_action, mode
from ..world.live import Abort
from ._common import ToolsSetting, entity_of, tools_field
from .econ_assets import move_money
from .econ_base import (
    BOOKINGS,
    bump,
    config_of,
    emit_to,
    guarded,
    maybe_entity,
    money,
    props,
    register_config,
    require_currency,
    require_types,
    type_list,
    valid_name,
    whole,
)
from .econ_inventory import agent_types

__all__ = ["BookingsConfig", "ResourceSpec"]

LIVE = ("booked", "waiting")
STATS = {"booked": 0, "waitlisted": 0, "promoted": 0, "served": 0, "cancelled": 0, "abandoned": 0, "turned_away": 0,
         "offered": 0, "revenue": 0}


class ResourceSpec(BaseModel):
    """Something with limited places each round."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(None, description="Entity id paid for bookings (needed when price > 0).")
    name: str = ""
    capacity: int | str = Field(1, description="Places per round (number or expression over $inputs).")
    price: float = Field(0, ge=0, description="Price per place.")
    horizon: int = Field(3, ge=1, description="Slots format: how many rounds ahead guests may book.")
    max_party: int = Field(1, ge=1, description="Most places one booking may take.")
    description: str = ""


class BookingsConfig(BaseModel):
    """Places booked by guests."""

    model_config = ConfigDict(extra="forbid")

    who: str | list[str] = Field(..., description="Type(s) that book.")
    resources: dict[str, ResourceSpec] = Field({},
                                               description="{resource id: {provider, capacity, price, horizon, "
                                                           "max_party}}.")
    format: Literal["slots", "queue"] = Field("slots",
                                              description="slots: book a future round; queue: wait in line, served as "
                                                          "places free up.")
    currency: str | None = Field(None, description="Ledger currency for prices.")
    waitlist: bool = Field(True,
                           description="Slots format: a full round puts the guest on its waitlist instead of refusing.")
    order: Literal["fifo", "priority"] = Field("fifo", description="Who is served or promoted first.")
    priority: str | None = Field(None, description="Priority order: expression over $it (the guest), higher first.")
    patience: int | str | None = Field(None,
                                       description="Rounds a waiting guest waits before giving up (number or "
                                                   "expression over $it).")
    refund: float = Field(1, ge=0, le=1, description="Share of the price returned when a booking is cancelled.")
    actions: list[Literal["book", "cancel"]] = Field(["book", "cancel"],
                                                     description="Tools generated for agent guests.")
    tools: ToolsSetting = tools_field()


register_config(BOOKINGS, BookingsConfig)


@mode("agreements", "bookings", BookingsConfig,
           "Capacity-limited places: with `format: slots` guests book a future round (paying on booking) and join a "
           "waitlist when it is full, promoted first-fit when a place frees; with `format: queue` they wait in line "
           "and are served (paying on service) as capacity allows each round. Service is FIFO or by `priority`, and "
           "waiting guests give up after `patience` rounds. Generates `<name>_book` and `<name>_cancel`; resources "
           "are entities of `<name>_resource` (served, offered, revenue), bookings of `<name>_booking`. Totals in "
           "$world.<name>_stats; utilization is stats.served / stats.offered.",
           example={"who": "household", "currency": "cash", "waitlist": True, "patience": 2,
                    "resources": {"tables": {"provider": "bistro", "capacity": 8, "price": 25, "horizon": 3,
                                             "max_party": 4}}})
def _expand_bookings(name: str, config: BookingsConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    guests = type_list(config.who)
    require_types(contract, guests, "who")
    if config.currency is not None:
        require_currency(contract, config.currency)
    if config.order == "priority" and not config.priority:
        raise MechanismError("priority order needs a `priority` expression", "e.g. \"priority\": \"$it.loyalty\"",
                             "priority")
    resource, booking = f"{name}_resource", f"{name}_booking"
    entities: dict[str, Any] = {}
    for rid, spec in config.resources.items():
        if not valid_name(rid):
            raise MechanismError(f"resource id '{rid}' is not a valid id",
                                 "use letters, digits and _ (not a word expressions use, like in or not)",
                                 f"resources.{rid}")
        if spec.price > 0 and (config.currency is None or spec.provider is None):
            raise MechanismError(f"resource '{rid}' has a price but no currency or provider",
                                 "set the mechanism's `currency` and the resource's `provider`", f"resources.{rid}")
        entities[rid] = {"type": resource, "name": spec.name or rid.replace("_", " ").title(), "props": {
            "provider": spec.provider or "", "capacity": spec.capacity, "price": spec.price, "horizon": spec.horizon,
            "max_party": spec.max_party, "description": spec.description}}
    count = {"type": "int", "default": 0, "min": 0}
    fragment: dict[str, Any] = {
        "types": {
            resource: {"description": "A resource with limited places each round.", "props": {
                "provider": {"type": "text", "default": ""}, "capacity": {"type": "int", "default": 1, "min": 0},
                "price": {"type": "number", "default": 0, "min": 0}, "horizon": {"type": "int", "default": 3, "min": 1},
                "max_party": {"type": "int", "default": 1, "min": 1}, "description": {"type": "text", "default": ""},
                "served": count, "offered": count, "revenue": {"type": "number", "default": 0, "min": 0}}},
            booking: {"description": "A booking or a place in line.", "props": {
                "guest": {"type": "text", "default": ""}, "resource": {"type": "text", "default": ""},
                "slot": {"type": "int", "default": 0}, "party": {"type": "int", "default": 1, "min": 1},
                "status": {"type": "enum",
                           "values": ["booked", "waiting", "served", "cancelled", "abandoned", "turned_away"],
                           "default": "waiting"},
                "joined": {"type": "int", "default": 0}, "paid": {"type": "number", "default": 0, "min": 0},
                "priority": {"type": "number", "default": 0}, "gives_up": {"type": "int", "default": 0}}}},
        "entities": entities,
        "world": {f"{name}_stats": {"type": "map", "default": dict(STATS), "description": "Booking totals (places)."}},
        "events": [{"name": f"{name}: service", "phase": "start", "do": [{"agreements": name, "action": "tick"}]}],
    }
    agents = agent_types(contract, guests)
    if not agents:
        return fragment
    mine = "$it.guest == $actor.id and ($it.status == booked or $it.status == waiting)"
    params: dict[str, Any] = {"resource": {"type": "entity", "of": resource, "where": "$it.capacity > 0",
                                           "description": "What to book."}}
    if config.format == "slots":
        params["ahead"] = {"type": "int", "min": 1, "max": guarded("$params.resource.horizon", "resource"),
                           "default": 1, "description": "Rounds from now (1 = next round)."}
    params["party"] = {"type": "int", "min": 1, "max": guarded("$params.resource.max_party", "resource"), "default": 1,
                       "description": "Places."}
    do: dict[str, Any] = {"agreements": name, "action": "book", "who": "$actor", "resource": "$params.resource",
                          "party": "$params.party"}
    if config.format == "slots":
        do["ahead"] = "$params.ahead"
    fragment["actions"] = {}
    if "book" in config.actions:
        fragment["actions"][f"{name}_book"] = {
            "by": agents, "private": True, "params": params, "do": [do],
            "description": ("Book places for a future round: 1 = next round, up to the place's booking horizon (look "
                            "at the places view for free places). Paid now; a full round puts you on its waitlist."
                            if config.format == "slots"
                            else "Join the line; you are served, and pay, when a place is free."),
            "outcome": f"{{$booking_text($actor, '{name}')}}"}
    if "cancel" in config.actions:
        fragment["actions"][f"{name}_cancel"] = {
            "by": agents, "private": True, "description": "Cancel a booking or leave the line.",
            "params": {"booking": {"type": "entity", "of": booking, "where": mine}},
            "do": [{"agreements": name, "action": "cancel", "booking": "$params.booking"}], "outcome": "Cancelled."}
    fragment["views"] = {
        f"{name}_places": {"for": agents, "title": "Places", "of": resource, "look": True,
                           "show": "[{id}] {name}: {price|money} a place, {capacity} per round · "
                                   f"{{$places_text($it, '{name}')}}"},
        f"{name}_mine": {"for": agents, "title": "Your bookings", "of": booking, "where": mine,
                         "show": "[{id}] {$entity($it.resource).name}"
                         + (": round {slot}" if config.format == "slots" else "")
                                 + ", {party} place(s), {status}"}}
    return fragment


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _bookings(world: Any, name: str, resource: str | None = None, slot: int | None = None,
              status: str | None = None) -> list[Any]:
    return [b for b in world.entities_of(f"{name}_booking")
            if (resource is None or props(b)["resource"] == resource) and (slot is None or props(b)["slot"] == slot)
            and (status is None or props(b)["status"] == status)]


def taken(world: Any, name: str, resource: str, slot: int) -> int:
    return sum(int(props(b)["party"]) for b in _bookings(world, name, resource, slot, "booked"))


def _in_order(config: BookingsConfig, bookings: list[Any]) -> list[Any]:
    if config.order == "priority":
        return sorted(bookings, key=lambda b: (-float(props(b)["priority"]), int(props(b)["joined"])))
    return sorted(bookings, key=lambda b: int(props(b)["joined"]))  # stable: creation order breaks ties


def _stat(world: Any, name: str, key: str, delta: float) -> None:
    bump(world, f"{name}_stats", key, delta)


def _pay(world: Any, config: BookingsConfig, guest: Any, resource: Any, party: int, where: str) -> float:
    price = float(props(resource)["price"]) * party
    if price <= 0:
        return 0.0
    if config.currency is None:
        raise RunError(f"{resource.id} has a price but the bookings mechanism names no currency", where)
    provider = entity_of(world, props(resource)["provider"], where, "the resource's provider")
    move_money(world, config.currency, guest, provider, price, where)
    world.set_prop(resource, "revenue", float(props(resource)["revenue"]) + price)
    return price


def _serve(world: Any, name: str, booking: Any, resource: Any) -> None:
    party = int(props(booking)["party"])
    world.set_prop(booking, "status", "served")
    world.set_prop(resource, "served", int(props(resource)["served"]) + party)
    _stat(world, name, "served", party)


def _promote(world: Any, name: str, config: BookingsConfig, resource: Any, slot: int, where: str) -> None:
    """Fill freed places from the waitlist, first fit in service order."""
    free = int(props(resource)["capacity"]) - taken(world, name, resource.id, slot)
    for booking in _in_order(config, _bookings(world, name, resource.id, slot, "waiting")):
        party = int(props(booking)["party"])
        guest = world.entities.get(props(booking)["guest"])
        if party > free or guest is None or not guest.alive:
            continue
        mark = world.journal.mark()
        try:
            paid = _pay(world, config, guest, resource, party, where)
        except Abort:
            world.journal.rollback(mark)
            continue
        world.set_prop(booking, "status", "booked")
        world.set_prop(booking, "paid", paid)
        _stat(world, name, "promoted", 1)
        _stat(world, name, "revenue", paid)
        free -= party
        emit_to(world, f"{name}_promoted", f"A place opened: you are booked at {resource.name} for round {slot}"
                + (f" (paid {money(paid)} {config.currency})." if paid else "."), [guest.id],
                why=f"You got a place at {resource.name}.")


@function("booking_text(agent, bookings)", "The agent's latest booking of a bookings mechanism, in words.", min_args=2,
          max_args=2, family="agreements")
def _booking_text(call: Call) -> str:
    world: Any = call.scope.world
    agent = maybe_entity(world, call.arg(0))
    name = str(call.arg(1))
    if agent is None or not world.is_type(f"{name}_booking"):
        raise ExprError(f"$booking_text: expected an agent and a bookings mechanism, got {call.arg(0)!r}, {name!r}",
                        call.source)
    config: BookingsConfig = config_of(world, name, BOOKINGS, call.source)
    mine = [b for b in world.entities_of(f"{name}_booking") if props(b)["guest"] == agent.id]
    if not mine:
        return "You have no bookings."
    booking = mine[-1]
    p = props(booking)
    resource = world.entities.get(p["resource"])
    where = resource.name if resource is not None else p["resource"]
    when = f" for round {p['slot']}" if p["slot"] else ""
    if p["status"] == "booked":
        return (f"Booked {p['party']} place(s) at {where}{when}"
                + (f", paid {money(p['paid'])} {config.currency}." if p["paid"] else "."))
    if p["status"] == "waiting":
        line = _in_order(config, _bookings(world, name, p["resource"], p["slot"], "waiting"))
        ahead = [b.id for b in line].index(booking.id)
        return f"{where}{when} is full; you are number {ahead + 1} in line."
    return f"Your booking at {where}{when} is {p['status'].replace('_', ' ')}."


@function("places_text(resource, bookings)",
          "Free places of a resource in the coming rounds (slots) or the line length (queue).", min_args=2, max_args=2,
          family="agreements")
def _places_text(call: Call) -> str:
    world: Any = call.scope.world
    resource = maybe_entity(world, call.arg(0))
    name = str(call.arg(1))
    if resource is None or not world.is_type(f"{name}_resource"):
        raise ExprError(f"$places_text: expected a resource of {name}, got {call.arg(0)!r}", call.source)
    config: BookingsConfig = config_of(world, name, BOOKINGS)
    capacity = int(props(resource)["capacity"])
    if config.format == "queue":
        return f"{len(_bookings(world, name, resource.id, 0, 'waiting'))} waiting"
    rounds = range(world.round + 1, world.round + 1 + int(props(resource)["horizon"]))
    return "free: " + ", ".join(f"round {r} {max(0, capacity - taken(world, name, resource.id, r))}" for r in rounds)


@family_action("agreements", ("bookings",), "book", keys=("who", "resource", "ahead", "party"),
               required=("who", "resource"),
               example='{"agreements": "dining", "action": "book", "who": "$actor", "resource": "$params.resource", '
                       '"ahead": 2, "party": 4}  (book places, or join the waitlist or the line; `ahead` only with '
                       'format slots)')
def _book(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    config: BookingsConfig = config_of(world, name, BOOKINGS, where)
    guest = entity_of(world, runner.eval(effect["who"], vars), where, "a guest")
    resource = entity_of(world, runner.eval(effect["resource"], vars), where, "a resource")
    if resource.entity_type != f"{name}_resource":
        raise RunError(f"{resource.id} is not a resource of {name}", where)
    party = whole(runner.eval(effect.get("party", 1), vars), where, "party")
    if not 1 <= party <= int(props(resource)["max_party"]):
        raise Abort(f"{resource.name} takes 1 to {props(resource)['max_party']} places per booking.")
    slot = 0
    if config.format == "slots":
        ahead = whole(runner.eval(effect.get("ahead", 1), vars), where, "ahead")
        if not 1 <= ahead <= int(props(resource)["horizon"]):
            raise Abort(f"{resource.name} can be booked 1 to {props(resource)['horizon']} rounds ahead.")
        slot = world.round + ahead
    if any(props(b)["guest"] == guest.id and props(b)["status"] in LIVE
           for b in _bookings(world, name, resource.id, slot)):
        raise Abort(f"You already have a booking at {resource.name}" + (f" for round {slot}." if slot else "."))
    values: dict[str, Any] = {"guest": guest.id, "resource": resource.id, "slot": slot, "party": party,
                              "joined": world.round}
    if config.priority:
        values["priority"] = _number(world, config.priority, guest, f"mechanisms.{name}.priority")
    if config.patience is not None:
        values["gives_up"] = world.round + int(_number(world, config.patience, guest, f"mechanisms.{name}.patience"))
    free = int(props(resource)["capacity"]) - taken(world, name, resource.id, slot)
    if config.format == "slots" and party <= free:
        values.update(status="booked", paid=_pay(world, config, guest, resource, party, where))
        _stat(world, name, "booked", 1)
        _stat(world, name, "revenue", values["paid"])
    elif config.format == "slots" and not config.waitlist:
        raise Abort(f"{resource.name} is full for round {slot}: {max(0, free)} place(s) left.")
    else:
        values["status"] = "waiting"
        _stat(world, name, "waitlisted", 1)
    world.create(f"{name}_booking", None, f"{guest.name} at {resource.name}", values, None, world.scope(), where)


def _number(world: Any, value: int | str, guest: Any, where: str) -> float:
    if isinstance(value, str):
        try:
            value = compile_expr(value)(world.scope(it=guest))
        except ExprError as exc:
            raise RunError(str(exc), where) from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunError(f"must give a number, got {value!r}", where)
    return float(value)


@family_action("agreements", ("bookings",), "cancel", keys=("booking",), required=("booking",),
               example='{"agreements": "dining", "action": "cancel", "booking": "$params.booking"}  '
                       '(cancel with a refund; the waitlist moves up)')
def _cancel_booking(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    config: BookingsConfig = config_of(world, name, BOOKINGS, where)
    booking = entity_of(world, runner.eval(effect["booking"], vars), where, "a booking")
    if booking.entity_type != f"{name}_booking" or props(booking)["status"] not in LIVE:
        raise Abort("That booking is not open.")
    p = props(booking)
    resource = entity_of(world, p["resource"], where, "a resource")
    was_booked = p["status"] == "booked"
    refund = round(float(p["paid"]) * config.refund, 9)
    if refund > 0 and config.currency is not None:
        provider = entity_of(world, props(resource)["provider"], where, "the resource's provider")
        move_money(world, config.currency, provider, entity_of(world, p["guest"], where), refund, where,
                   use_credit=False)
        world.set_prop(resource, "revenue", max(0.0, float(props(resource)["revenue"]) - refund))
        _stat(world, name, "revenue", -refund)
    world.set_prop(booking, "status", "cancelled")
    _stat(world, name, "cancelled", 1)
    if was_booked and config.format == "slots":
        _promote(world, name, config, resource, int(p["slot"]), where)


@family_action("agreements", ("bookings",), "tick", internal=True,
               example='{"agreements": "dining", "action": "tick"}  (serve this round\'s bookings or the line, let '
                       'impatient guests give up)')
def _bookings_tick(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["agreements"]
    config: BookingsConfig = config_of(world, name, BOOKINGS, where)
    freed: dict[tuple[str, int], None] = {}
    for booking in _bookings(world, name):
        p = props(booking)
        if p["status"] not in LIVE:
            continue
        guest = world.entities.get(p["guest"])
        if guest is None or not guest.alive:
            if p["status"] == "booked" and int(p["slot"]) >= world.round:
                freed[(p["resource"], int(p["slot"]))] = None
            # Departure is not cancellation: retain any payment, with refunds
            # left to an explicitly authored cancellation/exit procedure.
            world.set_prop(booking, "status", "abandoned")
            _stat(world, name, "abandoned", 1)
            emit_to(world, f"{name}_abandoned", "Booking abandoned: the guest is no longer active.", [],
                    {"booking": booking.id, "guest": p["guest"], "reason": "guest gone"})
            continue
        gives_up = int(p["gives_up"])
        if p["status"] == "waiting" and gives_up and gives_up < world.round:
            world.set_prop(booking, "status", "abandoned")
            _stat(world, name, "abandoned", 1)
            emit_to(world, f"{name}_gave_up", "You gave up waiting.", [p["guest"]])
    # Clear all departed/expired entries before promoting, so a freed place
    # cannot be sold to someone whose wait has already ended.
    for resource_id, slot in freed:
        resource = world.entities.get(resource_id)
        if resource is not None and resource.alive:
            _promote(world, name, config, resource, slot, where)
    for resource in world.entities_of(f"{name}_resource"):
        capacity = int(props(resource)["capacity"])
        world.set_prop(resource, "offered", int(props(resource)["offered"]) + capacity)
        _stat(world, name, "offered", capacity)
        if config.format == "slots":
            for booking in _bookings(world, name, resource.id, world.round, "booked"):
                _serve(world, name, booking, resource)
                emit_to(world, f"{name}_served", f"Your booking at {resource.name} is today.",
                        [props(booking)["guest"]])
            for booking in [b for b in _bookings(world, name, resource.id, status="waiting") if int(props(b)["slot"])
                            <= world.round]:
                world.set_prop(booking, "status", "turned_away")
                _stat(world, name, "turned_away", 1)
                emit_to(world, f"{name}_turned_away",
                        f"No place opened at {resource.name}; your waitlist spot has lapsed.",
                        [props(booking)["guest"]])
            continue
        left = capacity
        for booking in _in_order(config, _bookings(world, name, resource.id, 0, "waiting")):
            party = int(props(booking)["party"])
            guest = world.entities.get(props(booking)["guest"])
            if left <= 0:
                break
            if party > left or guest is None or not guest.alive:
                continue
            mark = world.journal.mark()
            try:
                paid = _pay(world, config, guest, resource, party, where)
            except Abort as exc:
                world.journal.rollback(mark)
                world.set_prop(booking, "status", "turned_away")
                _stat(world, name, "turned_away", 1)
                emit_to(world, f"{name}_turned_away",
                        f"You reached the front at {resource.name} but could not pay: {exc.reason}", [guest.id])
                continue
            world.set_prop(booking, "paid", paid)
            _stat(world, name, "revenue", paid)
            _serve(world, name, booking, resource)
            left -= party
            emit_to(world, f"{name}_served",
                    f"You were served at {resource.name}"
                    + (f" and paid {money(paid)} {config.currency}." if paid else "."), [guest.id])
