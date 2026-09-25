"""Posted-price markets (the ``market`` family's ``posted`` mode): listings with prices, stock, per-round capacity,
promotions, sponsored placement, ratings, and haggling with a floor and a counter-offer (as in Fareground Market).

Listings are entities of the generated type ``<name>_listing`` (declared in the mechanism config,
or by the author under ``entities``). Buyers see a ranked shelf (``$shelf``: sponsored first, then
rating, then price by default) and buy, make an offer, accept a counter-offer or rate; sellers set
prices, run promotions and sponsor listings. Money moves from buyer to seller with conserved
transfers; goods move from a listing's stock into the buyer's ``<name>_basket``.

Haggling: an offer below the asking price on a negotiable listing is accepted when it reaches the
listing's floor (never above the current price); otherwise the seller counters at the floor, and
the buyer can accept that counter within ``counter_rounds`` rounds.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import Call, ExprError, function
from ..expr.objects import Entity
from ..registry import MechanismError, family_action, mechanism_config, mode
from ..world.abort import Abort
from ._common import entity_of, fmt
from .econ_base import money_prop
from .ledger import Account, clean, move

__all__ = ["ListingSpec", "PostedMarketConfig"]

KEY = "market.posted"
#: Longest promotion or sponsorship a seller may buy at once.
_MAX_ROUNDS = 20

#: Keys a shelf can be ranked by.
RankKey = Literal["sponsored", "rating", "price", "sold"]
DEFAULT_RANK: tuple[RankKey, ...] = ("sponsored", "rating", "price")


class ListingSpec(BaseModel):
    """One listing declared with the market."""

    model_config = ConfigDict(extra="forbid")

    seller: str = Field(..., description="Entity id of the seller (paid on every sale).")
    item: str = Field(..., description="What is sold; baskets count goods by item.")
    price: float = Field(..., gt=0, description="Asking price per unit.")
    stock: int = Field(0, ge=0, description="Units available.")
    capacity: int = Field(0, ge=0, description="Units that can be sold per round (0 = no limit).")
    negotiable: bool = Field(False, description="Buyers may make offers below the price.")
    floor: float | None = Field(None, gt=0,
                                description="Lowest price the seller accepts in haggling (default: the price).")
    sponsored: bool = Field(False, description="Placed first on the shelf.")
    rating: float = Field(0, ge=0, le=5, description="Starting average rating (0 = unrated).")
    ratings: int = Field(0, ge=0, description="Ratings behind the starting average.")
    name: str = Field("", description="Display name (default: the item).")


class PostedMarketConfig(BaseModel):
    """A market of listings at posted prices."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Agent type that shops (subtypes included).")
    sellers: str | None = Field(None,
                                description="Agent type that manages its listings (sets prices, promotes, sponsors).")
    currency: str = Field("cash", description="Property holding money (buyers and sellers).")
    listings: dict[str, ListingSpec] = Field({}, description="Listings by id.")
    rank: list[RankKey] = Field(
        list(DEFAULT_RANK), description="Shelf order: sponsored first, higher rating, lower price, more sold.")
    shelf: int = Field(6, ge=1, description="Listings shown on the shelf view.")
    max_qty: int = Field(10, ge=1, description="Units per purchase.")
    counter_rounds: int = Field(1, ge=1, description="Rounds a counter-offer stays open.")
    ratings: bool = Field(True, description="Buyers may rate listings they bought from (once each).")
    sponsor_fee: float = Field(0, ge=0,
                               description="Fee per round of sponsored placement, paid to $world.<name>_ad_revenue.")
    max_promo: float = Field(0.5, gt=0, lt=1, description="Largest promotion discount a seller may run.")
    stage: str | None = Field(None,
                              description="Trade during this declared stage; default: a sequential stage named after "
                                          "the market.")
    max_actions: int = Field(3, ge=1, description="Actions per turn in the generated stage.")
    conserve: bool = Field(True, description="Declare the invariant that no listing's stock goes negative.")


def posted_config(world: Any, name: Any) -> PostedMarketConfig:
    return mechanism_config(world, name, KEY, PostedMarketConfig)


def _prop(entity: Entity, key: str, default: Any = None) -> Any:
    value: Any = entity.properties.get(key, default)
    return default if value is None else value


def price_now(world: Any, listing: Entity) -> float:
    """The asking price after any running promotion."""
    promo = float(_prop(listing, "promo", 0))
    active = promo > 0 and world.round <= int(_prop(listing, "promo_until", 0))
    return clean(float(_prop(listing, "price", 0)) * (1 - promo if active else 1))


def _sponsored(world: Any, listing: Entity) -> bool:
    return bool(_prop(listing, "sponsored", False)) or world.round <= int(_prop(listing, "sponsor_until", 0))


def _floor(world: Any, listing: Entity) -> float:
    return min(float(_prop(listing, "floor", 0) or _prop(listing, "price", 0)), price_now(world, listing))


def shelf(world: Any, name: str, item: str | None = None) -> list[Entity]:
    cfg = posted_config(world, name)
    listings = [e for e in world.entities_of(f"{name}_listing")
                if int(_prop(e, "stock", 0)) > 0 and (item is None or _prop(e, "item") == item)]
    keys = {"sponsored": lambda e: 0 if _sponsored(world, e) else 1, "rating": lambda e: -float(_prop(e, "rating", 0)),
            "price": lambda e: price_now(world, e), "sold": lambda e: -int(_prop(e, "sold", 0))}
    return sorted(listings, key=lambda e: tuple(keys[k](e) for k in cfg.rank) + (e.id,))


def _listing(world: Any, name: str, value: Any) -> Entity:
    found = world.entity(value)
    if found is None or not found.alive or found.entity_type != f"{name}_listing":
        raise Abort(f"There is no listing {value!r} here.")
    return found


def _sell(world: Any, name: str, cfg: PostedMarketConfig, buyer: Entity, listing: Entity, qty: int, unit: float,
          negotiated: bool) -> str:
    stock = int(_prop(listing, "stock", 0))
    if stock < qty:
        raise Abort(f"{listing.name} has only {stock} left." if stock else f"{listing.name} is sold out.")
    capacity = int(_prop(listing, "capacity", 0))
    sold_now = int(_prop(listing, "sold_round", 0))
    if capacity and sold_now + qty > capacity:
        left = capacity - sold_now
        raise Abort(f"{listing.name} can sell only {left} more this round." if left
                    else f"{listing.name} is sold out for this round.")
    seller = entity_of(world, _prop(listing, "seller"), f"{listing.id}.seller", "the seller")
    total = clean(unit * qty)
    move(world, Account(buyer, cfg.currency), Account(seller, cfg.currency), total, what="cash")
    item = str(_prop(listing, "item"))
    world.set_prop(listing, "stock", stock - qty)
    world.set_prop(listing, "sold_round", sold_now + qty)
    world.set_prop(listing, "sold", int(_prop(listing, "sold", 0)) + qty)
    world.set_prop(listing, "revenue", clean(float(_prop(listing, "revenue", 0)) + total))
    basket = dict(_prop(buyer, f"{name}_basket", {}))
    basket[item] = int(basket.get(item, 0)) + qty
    world.set_prop(buyer, f"{name}_basket", basket)
    bought = dict(_prop(buyer, f"{name}_bought", {}))
    bought[listing.id] = int(bought.get(listing.id, 0)) + qty
    world.set_prop(buyer, f"{name}_bought", bought)
    world.set_world(f"{name}_sales", int(world.props.get(f"{name}_sales") or 0) + qty)
    world.set_world(f"{name}_turnover", clean(float(world.props.get(f"{name}_turnover") or 0) + total))
    world.post(f"{name}_ledger",
               {"listing": listing.id, "item": item, "qty": qty, "unit_price": unit, "negotiated": negotiated},
               buyer.id, (buyer.id, seller.id), f"mechanisms.{name}")
    return f"Bought {qty} × {listing.name} at {fmt(unit)} each ({fmt(total)})."


def act(world: Any, name: str, action: str, trader: Entity, listing_id: Any = None, qty: Any = None, price: Any = None,
        pct: Any = None, rounds: Any = None, stars: Any = None) -> str:
    cfg = posted_config(world, name)
    listing = _listing(world, name, listing_id)
    if action == "buy":
        text = _sell(world, name, cfg, trader, listing, _whole(qty, 1, cfg.max_qty, "qty"), price_now(world, listing),
                     False)
    elif action == "offer":
        text = _offer(world, name, cfg, trader, listing, _money(price, "price"))
    elif action == "accept":
        counters = dict(_prop(trader, f"{name}_counters", {}))
        counter = counters.get(listing.id)
        if not counter or world.round - int(counter["round"]) > cfg.counter_rounds:
            raise Abort(f"There is no open counter-offer on {listing.name}.")
        text = _sell(world, name, cfg, trader, listing, 1, float(counter["price"]), True)
        counters.pop(listing.id)
        world.set_prop(trader, f"{name}_counters", counters)
    elif action == "rate":
        text = _rate(world, name, trader, listing, _whole(stars, 1, 5, "stars"))
    else:
        text = _manage(world, name, cfg, action, trader, listing, price, pct, rounds)
    world.set_world(f"{name}_receipt", text)
    return text


def _whole(value: Any, low: int, high: int, what: str) -> int:
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != int(value) or not low <= value
        <= high):
        raise Abort(f"{what} must be a whole number from {low} to {high}, not {value!r}.")
    return int(value)


def _money(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise Abort(f"{what} must be a positive number, not {value!r}.")
    return round(float(value), 2)


def _offer(world: Any, name: str, cfg: PostedMarketConfig, buyer: Entity, listing: Entity, price: float) -> str:
    if not _prop(listing, "negotiable", False):
        raise Abort(f"{listing.name} is sold at its price; use buy.")
    asking = price_now(world, listing)
    if price >= asking:
        raise Abort(f"No need to offer that much; buy it at {fmt(asking)}.")
    floor = _floor(world, listing)
    if price >= floor - 1e-9:
        return "The seller accepts. " + _sell(world, name, cfg, buyer, listing, 1, price, True)
    if int(_prop(listing, "stock", 0)) <= 0:
        raise Abort(f"{listing.name} is sold out.")
    counters = dict(_prop(buyer, f"{name}_counters", {}))
    counters[listing.id] = {"price": floor, "round": world.round}
    world.set_prop(buyer, f"{name}_counters", counters)
    return f"The seller declines {fmt(price)} and counters at {fmt(floor)}; accept it with {name}_accept."


def _rate(world: Any, name: str, buyer: Entity, listing: Entity, stars: int) -> str:
    if listing.id not in dict(_prop(buyer, f"{name}_bought", {})):
        raise Abort(f"You can rate only what you bought; you have not bought {listing.name}.")
    rated = list(_prop(buyer, f"{name}_rated", []))
    if listing.id in rated:
        raise Abort(f"You already rated {listing.name}.")
    count = int(_prop(listing, "ratings", 0))
    average = (float(_prop(listing, "rating", 0)) * count + stars) / (count + 1)
    world.set_prop(listing, "rating", round(average, 4))
    world.set_prop(listing, "ratings", count + 1)
    world.set_prop(buyer, f"{name}_rated", rated + [listing.id])
    return f"You rated {listing.name} {stars}★; it now averages {average:.2f}★ from {count + 1} rating(s)."


def _manage(world: Any, name: str, cfg: PostedMarketConfig, action: str, seller: Entity, listing: Entity, price: Any,
            pct: Any, rounds: Any) -> str:
    if _prop(listing, "seller") != seller.id:
        raise Abort(f"{listing.name} is not your listing.")
    if action == "set_price":
        value = _money(price, "price")
        world.set_prop(listing, "price", value)
        if float(_prop(listing, "floor", 0) or 0) > value:
            world.set_prop(listing, "floor", value)
        return f"{listing.name} now costs {fmt(value)}."
    if action == "promote":
        if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 < pct <= cfg.max_promo:
            raise Abort(f"The discount must be above 0 and at most {cfg.max_promo:.0%}.")
        length = _whole(rounds, 1, 1000, "rounds")
        world.set_prop(listing, "promo", float(pct))
        world.set_prop(listing, "promo_until", world.round + length - 1)
        return f"{listing.name} is {pct:.0%} off for {length} round(s): {fmt(price_now(world, listing))}."
    if action == "sponsor":
        length = _whole(rounds, 1, 1000, "rounds")
        move(world, Account(seller, cfg.currency), Account(None, f"{name}_ad_revenue"), cfg.sponsor_fee * length,
             what="cash")
        world.set_prop(listing, "sponsor_until", max(int(_prop(listing, "sponsor_until", 0)), world.round) + length)
        return f"{listing.name} is sponsored for {length} round(s) for {fmt(cfg.sponsor_fee * length)}."
    raise RunError(f"'{action}' is not an action of a posted market", f"mechanisms.{name}")


def _open(world: Any, name: str) -> None:
    for listing in world.entities_of(f"{name}_listing"):
        if int(_prop(listing, "sold_round", 0)):
            world.set_prop(listing, "sold_round", 0)


def audit(world: Any, name: str) -> list[str]:
    problems: list[str] = []
    for listing in world.entities_of(f"{name}_listing"):
        if int(_prop(listing, "stock", 0)) < 0:
            problems.append(f"{listing.id} has negative stock")
    return problems


def line(world: Any, name: str, listing: Entity, viewer: Entity | None) -> str:
    """One listing as a shelf line."""
    seller = world.entity(_prop(listing, "seller"))
    asking, base = price_now(world, listing), float(_prop(listing, "price", 0))
    parts = [f"[{listing.id}] {listing.name} from {seller.name if seller else _prop(listing, 'seller')}: {fmt(asking)}"]
    if asking < base:
        parts[0] += f" (was {fmt(base)}, promotion until round {_prop(listing, 'promo_until')})"
    rating, count = float(_prop(listing, "rating", 0)), int(_prop(listing, "ratings", 0))
    parts.append(f"{rating:.1f}★ ({count})" if count else "no ratings")
    parts.append(f"{_prop(listing, 'stock')} in stock")
    capacity = int(_prop(listing, "capacity", 0))
    if capacity:
        parts.append(f"{max(0, capacity - int(_prop(listing, 'sold_round', 0)))} left this round")
    if _prop(listing, "negotiable", False):
        parts.append("open to offers")
    if _sponsored(world, listing):
        parts.append("sponsored")
    if viewer is not None:
        counter = (dict(_prop(viewer, f"{name}_counters", {})).get(listing.id)
                   if f"{name}_counters" in viewer.properties else None)
        if counter and world.round - int(counter["round"]) <= posted_config(world, name).counter_rounds:
            parts.append(f"seller countered you at {fmt(counter['price'])}")
    return " · ".join(parts)


def _market(call: Call) -> str:
    try:
        posted_config(call.scope.world, call.arg(0))
    except RunError as exc:
        raise ExprError(f"${call.name}: {exc}", call.source) from None
    return str(call.arg(0))


def _listing_arg(call: Call, index: int, name: str) -> Entity:
    found = call.scope.world.entity(call.arg(index))
    if found is None or found.entity_type != f"{name}_listing":
        raise ExprError(f"${call.name}: expected a {name}_listing, got {call.arg(index)!r}", call.source)
    return found


@function("shelf(name, item?)",
          "Listings in stock on a posted-price market, ranked (sponsored, rating, price by default).",
          min_args=1, max_args=2, family="market")
def _shelf_function(call: Call) -> list[Entity]:
    return shelf(call.scope.world, _market(call), call.arg(1))


@function("posted_price(name, listing)", "A listing's asking price now, promotions applied.", min_args=2, max_args=2,
          family="market")
def _price_function(call: Call) -> float:
    name = _market(call)
    return price_now(call.scope.world, _listing_arg(call, 1, name))


@function("posted_line(name, listing, viewer?)",
          "A listing as one shelf line: price, rating, stock, capacity, offers, counters.", min_args=2, max_args=3,
          family="market")
def _line_function(call: Call) -> str:
    name = _market(call)
    viewer = call.scope.world.entity(call.arg(2)) if len(call) > 2 else None
    return line(call.scope.world, name, _listing_arg(call, 1, name), viewer)


@function("posted_counters(name, buyer)", "Ids of listings where the buyer holds an open counter-offer.", min_args=2,
          max_args=2, family="market")
def _counters_function(call: Call) -> list[str]:
    name = _market(call)
    world: Any = call.scope.world
    buyer = world.entity(call.arg(1))
    if buyer is None:
        raise ExprError(f"$posted_counters: expected a buyer, got {call.arg(1)!r}", call.source)
    rounds = posted_config(world, name).counter_rounds
    counters = dict(_prop(buyer, f"{name}_counters", {}))
    return [k for k, c in counters.items() if world.round - int(c["round"]) <= rounds
            and world.entity(k) is not None and int(_prop(world.entity(k), "stock", 0)) > 0]


@function("posted_ok(name)", "True while no listing of a posted-price market has negative stock.", min_args=1,
          max_args=1, family="market")
def _ok_function(call: Call) -> bool:
    return not audit(call.scope.world, _market(call))


#: action → (its keys after `who` and `listing`, generated by the mechanism itself, example keys, what it does).
#: Every key an action takes is required, except `who` (default $actor); receipts land in $world.<name>_receipt.
_ACTIONS: dict[str, tuple[tuple[str, ...], bool, str, str]] = {
    "buy": (("qty",), False, '"qty": 2', "buy from a listing at its current price"),
    "offer": (("price",), False, '"price": 2.5',
              "offer less than the asking price for one unit of a negotiable listing"),
    "accept": ((), False, "", "buy one unit at the seller's open counter-offer"),
    "rate": (("stars",), False, '"stars": 5', "rate a listing bought from, once"),
    "set_price": (("price",), False, '"price": 4', "a seller changes its listing's price"),
    "promote": (("pct", "rounds"), False, '"pct": 0.2, "rounds": 3', "a seller runs a discount"),
    "sponsor": (("rounds",), False, '"rounds": 2', "a seller puts its listing first on the shelf"),
    "open": ((), True, "", "start a round: reset per-round capacity"),
}


def _runner(action: str) -> Callable[[Any, dict[str, Any], dict[str, Any], str], None]:
    def run(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        world = runner.world
        name = effect["market"]
        try:
            if action == "open":
                _open(world, name)
                return
            value = {key: runner.eval(effect.get(key), vars)
                     for key in ("listing", "qty", "price", "pct", "rounds", "stars")}
            trader = entity_of(world, runner.eval(effect.get("who", "$actor"), vars), f"{where}.who", "a trader")
            listing = value["listing"]
            act(world, name, action, trader, listing.id if isinstance(listing, Entity) else listing, value["qty"],
                value["price"], value["pct"], value["rounds"], value["stars"])
        except RunError as exc:
            raise RunError(str(exc), where) from None

    return run


def _register_actions() -> None:
    for action, (keys, internal, fields, doc) in _ACTIONS.items():
        required = () if internal else ("listing", *keys)
        taken = () if internal else ("who", *required)
        listing = "" if internal else ', "listing": "$params.listing"'
        example = ('{"market": "market", "action": "' + action + '"' + listing + (f", {fields}" if fields else "")
                   + f"}}  ({doc})")
        family_action("market", ("posted",), action, keys=taken, required=required, internal=internal,
                      example=example)(_runner(action))


_register_actions()


@mode("market", "posted", PostedMarketConfig,
           "A market of listings at posted prices with stock, per-round capacity, promotions, sponsored placement, "
           "ratings and haggling (an offer at or above the floor is accepted, below it the seller counters at the "
           "floor). Buyer tools `<name>_buy`, `<name>_offer`, `<name>_accept`, `<name>_rate`; seller tools "
           "`<name>_set_price`, `<name>_promote`, `<name>_sponsor`. Listings are `<name>_listing` entities; goods "
           "land in the buyer's `<name>_basket` ({item: count}). Read the ranked shelf with $shelf(name, item?), "
           "prices with $posted_price(name, listing).",
           example={"who": "shopper", "sellers": "farmer",
                    "listings": {"apples": {"seller": "ana", "item": "apples", "price": 3, "stock": 40,
                                            "negotiable": True, "floor": 2.5}}})
def _expand_posted(name: str, cfg: PostedMarketConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    types = contract.get("types") or {}
    entities = contract.get("entities") or {}
    for field, kind in (("who", cfg.who), ("sellers", cfg.sellers)):
        if kind is not None and kind not in types:
            raise MechanismError(f"{field} '{kind}' is not a declared type", f"types: {', '.join(types) or 'none'}",
                                 field)
    fragment_types: dict[str, Any] = {
        cfg.who: {"props": {**money_prop(contract, cfg.who, cfg.currency),
                            f"{name}_basket": {"type": "map", "default": {}, "description": "Goods bought, by item."},
                               f"{name}_bought": {"type": "map", "default": {}, "private": True},
                               f"{name}_counters": {"type": "map", "default": {}, "private": True},
                               f"{name}_rated": {"type": "list", "default": [], "private": True}}},
        f"{name}_listing": {"description": "A listing at a posted price.", "owner": "seller", "props": {
            "seller": {"type": "text", "default": ""}, "item": {"type": "text", "default": ""},
            "price": {"type": "number", "default": 0}, "stock": {"type": "int", "default": 0},
            "capacity": {"type": "int", "default": 0}, "sold_round": {"type": "int", "default": 0},
            "promo": {"type": "number", "default": 0}, "promo_until": {"type": "int", "default": 0},
            "negotiable": {"type": "bool", "default": False},
            "floor": {"type": "number", "default": 0, "private": True},
            "sponsored": {"type": "bool", "default": False}, "sponsor_until": {"type": "int", "default": 0},
            "rating": {"type": "number", "default": 0}, "ratings": {"type": "int", "default": 0},
            "sold": {"type": "int", "default": 0}, "revenue": {"type": "number", "default": 0, "private": True}}},
    }
    if cfg.sellers:
        seller_props = fragment_types.setdefault(cfg.sellers, {"props": {}})["props"]
        seller_props.update(money_prop(contract, cfg.sellers, cfg.currency))
    generated: dict[str, Any] = {}
    for listing_id, spec in cfg.listings.items():
        seller = entities.get(spec.seller)
        if not isinstance(seller, Mapping) or seller.get("type") not in types:
            raise MechanismError(f"listing '{listing_id}': seller '{spec.seller}' is not a declared entity",
                                 "declare the seller under entities", f"listings.{listing_id}.seller")
        seller_props = fragment_types.setdefault(seller["type"], {"props": {}})["props"]
        seller_props.update(money_prop(contract, seller["type"], cfg.currency))
        generated[listing_id] = {"type": f"{name}_listing", "name": spec.name or spec.item, "props": {
            "seller": spec.seller, "item": spec.item, "price": spec.price, "stock": spec.stock,
            "capacity": spec.capacity,
            "negotiable": spec.negotiable, "floor": spec.floor or spec.price, "sponsored": spec.sponsored,
            "rating": spec.rating, "ratings": spec.ratings}}
    receipt = f"{{$world.{name}_receipt}}"
    listing = f"{name}_listing"
    buy_actions: dict[str, Any] = {
        f"{name}_buy": {"by": cfg.who,
                        "description": f"Buy from a listing at its current price (up to {cfg.max_qty} units).",
                        "params": {"listing": {"type": "entity", "of": listing, "where": "$it.stock > 0",
                                "description": "Listing id."},
                                   "qty": {"type": "int", "min": 1, "max": cfg.max_qty, "default": 1,
                            "description": "Units."}},
                        "do": [{"market": name, "action": "buy", "listing": "$params.listing", "qty": "$params.qty"}],
                        "outcome": receipt, "private": True},
        f"{name}_offer": {"by": cfg.who,
                          "description": "Offer less than the asking price for one unit of a listing open to offers. "
                           "The seller accepts or counters.",
                          "params": {"listing": {"type": "entity", "of": listing,
                                  "where": "$it.negotiable and $it.stock > 0",
                                  "description": "Listing id."},
                                     "price": {"type": "number", "min": 0.01, "max": f"$actor.{cfg.currency}",
                                "description": "Your offer."}},
                          "do": [{"market": name, "action": "offer", "listing": "$params.listing",
                                  "price": "$params.price"}],
                          "outcome": receipt, "private": True},
        f"{name}_accept": {"by": cfg.who,
                           "description": "Accept a seller's counter-offer and buy one unit at that price.",
                           "params": {"listing": {"type": "enum", "values": f"$posted_counters({name}, $actor)",
                                   "description": "Listing with a counter-offer."}},
                           "when": [{"expr": f"$len($posted_counters({name}, $actor)) > 0",
                                     "why": "You have no open counter-offers."}],
                           "do": [{"market": name, "action": "accept", "listing": "$params.listing"}],
                           "outcome": receipt, "private": True},
    }
    if cfg.ratings:
        buy_actions[f"{name}_rate"] = {
            "by": cfg.who, "description": "Rate a listing you bought from (1-5 stars, once).",
            "params": {"listing": {"type": "enum",
                                   "values": f"$filter($keys($actor.{name}_bought), not ($it in $actor.{name}_rated))",
                                   "description": "Listing you bought from."},
                       "stars": {"type": "int", "min": 1, "max": 5, "description": "1 (bad) to 5 (great)."}},
            "when": [{"expr": f"$len($filter($keys($actor.{name}_bought), not ($it in $actor.{name}_rated))) > 0",
                      "why": "There is nothing you bought and have not rated."}],
            "do": [{"market": name, "action": "rate", "listing": "$params.listing", "stars": "$params.stars"}],
            "outcome": receipt}
    actions = dict(buy_actions)
    if cfg.sellers:
        mine = {"type": "entity", "of": listing, "where": "$it.seller == $actor.id", "description": "Your listing."}
        actions.update({
            f"{name}_set_price": {"by": cfg.sellers, "description": "Change a listing's asking price.",
                                  "params": {"listing": mine,
                                             "price": {"type": "number", "min": 0.01, "description": "New price."}},
                                  "do": [{"market": name, "action": "set_price", "listing": "$params.listing",
                                          "price": "$params.price"}], "outcome": receipt},
            f"{name}_promote": {"by": cfg.sellers,
                                "description": "Run a discount on a listing for some rounds (at most "
                                               f"{cfg.max_promo:.0%}).",
                                "params": {"listing": mine, "pct": {"type": "number", "min": 0.01, "max": cfg.max_promo,
                                                 "description": "Discount as a fraction (0.2 = 20% "
                                               "off)."},
                                           "rounds": {"type": "int", "min": 1, "max": _MAX_ROUNDS,
                                   "description": "Rounds the discount runs."}},
                                "do": [{"market": name, "action": "promote", "listing": "$params.listing",
                                        "pct": "$params.pct", "rounds": "$params.rounds"}], "outcome": receipt},
            f"{name}_sponsor": {"by": cfg.sellers,
                                "description": "Put a listing first on the shelf for some rounds "
                                               f"({fmt(cfg.sponsor_fee)} per round, paid now from your cash).",
                                "params": {"listing": mine, "rounds": {
                                    "type": "int", "min": 1, "description": "Rounds the listing stays first.",
                                    # at most what the seller can pay for: no rounds affordable hides the tool
                                    "max": f"$min({_MAX_ROUNDS}, $actor.{cfg.currency} / {cfg.sponsor_fee})"
                                    if cfg.sponsor_fee > 0 else _MAX_ROUNDS}},
                                "do": [{"market": name, "action": "sponsor", "listing": "$params.listing",
                                        "rounds": "$params.rounds"}], "outcome": receipt, "private": True},
        })
    viewers = sorted({cfg.who, cfg.sellers} - {None})  # type: ignore[type-var]
    fragment: dict[str, Any] = {
        "types": fragment_types,
        "entities": generated,
        "world": {f"{name}_receipt": {"type": "text", "default": ""},
                  f"{name}_ad_revenue": {"type": "number", "default": 0},
                  f"{name}_sales": {"type": "int", "default": 0}, f"{name}_turnover": {"type": "number", "default": 0}},
        "records": {f"{name}_ledger": {"fields": {"listing": "text", "item": "text", "qty": "int",
                                                  "unit_price": "number", "negotiated": "bool"},
                                       "show": "{author} bought {qty} × {item} at {unit_price|money}", "notify": True,
                                       "description": "Sales, visible to the buyer and the seller."}},
        "actions": actions,
        "events": [{"name": f"{name}_open", "phase": "start", "do": [{"market": name, "action": "open"}]}],
        "views": {f"{name}_shelf": {"for": viewers, "title": "On the shelf", "of": f"$shelf({name})",
                                    "limit": cfg.shelf,
                                    "show": f"{{$posted_line({name}, $it, $actor)}}", "empty": "Nothing is for sale."},
                  f"{name}_basket": {"for": cfg.who, "title": "Your basket",
                                     "show": (f"Cash {{$actor.{cfg.currency}|money}} · basket: "
                                              f"{{$join($map($keys($actor.{name}_basket), $it + ' × ' + "
                                              f"$text($get($actor.{name}_basket, $it))), ', ') or 'empty'}}")}},
        "metrics": {f"{name}_sales": f"$world.{name}_sales", f"{name}_turnover": f"$world.{name}_turnover",
                    f"{name}_avg_price": f"$avg({listing}, $posted_price({name}, $it))"},
        "outputs": {f"{name}_sales": {"expr": f"$world.{name}_sales", "type": "int", "description": "Units sold."},
                    f"{name}_turnover": {"expr": f"$round($world.{name}_turnover, 2)", "type": "number",
                                         "description": "Money spent."}},
    }
    if cfg.sellers:
        fragment["views"][f"{name}_mine"] = {"for": cfg.sellers,
                                             "title": f"Your listings (your cash: {{$actor.{cfg.currency}|money}})",
                                             "of": listing, "where": "$it.seller == $actor.id",
                                             "show": f"{{$posted_line({name}, $it)}} · sold {{sold}} for "
                                                     "{revenue|money}"}
    names = list(actions)
    if cfg.stage is None:
        fragment["stages"] = [{"name": name, "turns": "sequential", "order": "random", "actions": names,
                               "max_actions": cfg.max_actions,
                               "brief": "Shop, haggle or manage your listings, or end your turn."}]
    else:
        fragment["stage_hooks"] = {cfg.stage: {"actions": names, "max_actions": cfg.max_actions}}
    if cfg.conserve:
        fragment["invariants"] = [{"expr": f"$posted_ok({name})",
                                   "why": f"No {name} listing sells more stock than it has."}]
    return fragment
