"""Auctions: sealed first-price, sealed second-price (Vickrey), English, Dutch, double (call market)
and multi-unit uniform-price, with reserve prices, tie rules and escrowed, conserved payments.

A lot opens at the start of a round (when ``when`` holds and there is something to sell) and runs
until it closes:

* ``first_price`` / ``second_price`` / ``uniform`` / ``double`` — sealed: bids are placed in a
  simultaneous stage and cleared at its end. First price pays its bid; second price pays the
  highest losing bid (or the reserve); uniform sells ``units`` (or what stock is left, if less) to
  the highest bids at one price (the lowest accepted bid, or with ``price_rule: highest_rejected``
  the highest rejected one, or the reserve when no bid was rejected); double matches buyers' bids with sellers' asks at one
  market-clearing price: the middle of the range no matched order would refuse and no unmatched one would take.
* ``english`` — open ascending: each bid beats the high bid by at least ``increment``; the lot
  closes when ``timeout`` rounds pass without a new bid. The winner pays its bid.
* ``dutch`` — a descending clock starts at ``start_price`` and falls by ``decrement`` each round;
  the first bid at or above the clock takes the lot at the clock price. It closes unsold below the
  reserve.
* ``reverse`` (first or second price) — a procurement tender: the ``house`` buys, bidders offer prices, the
  lowest offer at or below ``reserve`` (the most the house pays) wins a unit and is paid its offer, or with second
  price the second-lowest offer (or the reserve). Offers escrow nothing; the house pays from its ``currency``.
* ``score`` (first price) — the award goes to the acceptable bid with the highest score, an expression over the
  bid's ``$price`` and ``$it`` (the bidder), e.g. quality points minus price; the winner pays (or is paid) its bid.
* ``combinatorial`` — sealed package bids on bundles of distinct ``items``; each bidder wins at most
  one of its packages and each item goes to one winner, chosen exactly to raise the most over the
  per-item reserves (:mod:`.package_auction`). Winners pay VCG prices (or their bids); a bidder's
  escrow is its highest package bid.

Every bid escrows its money (and every ask its units) so a winner can always pay; losers and
change are refunded from escrow. Proceeds go to the ``house`` entity, or to
``$world.<name>_revenue`` for a house auction selling from ``$world.<name>_stock``.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple, Union, cast

from pydantic import BaseModel, ConfigDict, Field

from ..entity import Entity
from ..errors import RunError
from ..expr import compile_expr, truthy
from ..registry import MechanismError, family_action, mode
from ..world import Abort
from ._common import ToolsSetting, tools_field
from ._social import check_expr
from .common import config_of, entity_of, fmt, number
from .econ_base import money_prop
from .ledger import Account, balance, clean, move
from .package_auction import MAX_PACKAGE_BIDS, MAX_PACKAGE_ITEMS, PackageBid, SearchLimit, settle

__all__ = ["AuctionConfig", "FORMATS", "SEALED"]

KEY = "market.auction"
FORMATS = ("first_price", "second_price", "english", "dutch", "double", "uniform", "combinatorial")
SEALED = ("first_price", "second_price", "double", "uniform", "combinatorial")


class AuctionConfig(BaseModel):
    """An auction house selling lots to agents."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["first_price", "second_price", "english", "dutch", "double", "uniform", "combinatorial"] = Field(
        ..., description="first_price | second_price (Vickrey) | english | dutch | double | uniform (multi-unit) | "
                         "combinatorial (package bids on `items`).")
    who: str = Field(..., description="Agent type that bids (subtypes included).")
    sellers: Optional[str] = Field(None, description="double: agent type that asks (default: `who`).")
    currency: str = Field("cash", description="Property holding money.")
    item: str = Field("lot", description="What is sold, in plain words.")
    house: Optional[str] = Field(None, description="Entity id of the auction house: sells its units and is paid (with `reverse`: "
                                                   "buys and pays); default: the mechanism itself (stock and revenue in world props).")
    stock: Union[int, str] = Field(1, description="Units the house has to sell, or with `reverse` to buy (number or expression).")
    units: int = Field(1, ge=1, description="Units in each lot (uniform; the last lot sells what is left), or the most units "
                                            "one bid or ask may carry (double).")
    reserve: Union[float, str] = Field(0.0, description="Lowest acceptable price per unit (number or expression); with "
                                                         "`reverse`, the highest the house pays.")
    reverse: bool = Field(False, description="first_price / second_price: a procurement tender: the `house` buys, the lowest "
                                             "offer wins and is paid (its offer, or the second-lowest).")
    score: Optional[str] = Field(None, description="first_price: award to the acceptable bid with the highest score, an "
                                                   "expression over $price and $it (the bidder), e.g. \"$it.quality * 10 - $price\".")
    start_price: Optional[Union[float, str]] = Field(None, description="dutch: where the clock starts.")
    decrement: float = Field(1, gt=0, description="dutch: how much the clock falls each round.")
    increment: float = Field(1, gt=0, description="english: minimum raise over the high bid.")
    timeout: int = Field(1, ge=1, description="english: rounds without a new bid before the lot closes.")
    ties: Literal["first", "random"] = Field("first", description="Equal bids: the earliest wins (sealed bids arrive in their stage's commit order: random unless it sets `order`), or a seeded random one.")
    price_rule: Literal["lowest_accepted", "highest_rejected"] = Field(
        "lowest_accepted", description="uniform: the clearing price — the lowest accepted bid, or the highest rejected one "
                                       "(the reserve when none was rejected).")
    items: List[str] = Field(default_factory=list, description="combinatorial: the distinct items for sale, bid on in packages.")
    reserves: Dict[str, Union[float, str]] = Field(default_factory=dict, description="combinatorial: reserve per item (number or expression); others use `reserve`.")
    packages: int = Field(3, ge=1, le=8, description="combinatorial: most package bids one bidder may hold (it wins at most one).")
    payment: Literal["vcg", "pay_bid"] = Field("vcg", description="combinatorial: vcg (winners pay the value they displace; truthful bids are safe) | pay_bid.")
    when: Optional[str] = Field(None, description="Open lots only when true (e.g. \"$round <= 3\").")
    stage: Optional[str] = Field(None, description="Bid during this declared stage; default: a stage named after the auction.")
    conserve: bool = Field(True, description="Declare the invariant that escrow matches open bids and every item is held once.")
    tools: ToolsSetting = tools_field()


def auction_config(world: Any, name: Any) -> AuctionConfig:
    return config_of(world, name, KEY, AuctionConfig)


def _lot(world: Any, name: str) -> Dict[str, Any]:
    lot = world.props.get(f"{name}_lot") or {}
    return {**lot, "bids": [dict(b) for b in lot.get("bids", [])]}


def _parties(world: Any, cfg: AuctionConfig) -> List[Entity]:
    seen: Dict[str, Entity] = {}
    for kind in {cfg.who, cfg.sellers or cfg.who}:
        for entity in world.entities_of(kind):
            seen[entity.id] = entity
    if cfg.house:
        house = world.entity(cfg.house)
        if house is not None:
            seen[house.id] = house
    return list(seen.values())


def _payee(world: Any, name: str, cfg: AuctionConfig) -> Tuple[Account, Account]:
    """Where money goes and units come from (a combinatorial lot's items always come from the house list)."""
    if cfg.house:
        house = entity_of(world, cfg.house, f"mechanisms.{name}.house", "the auction house")
        source = Account(None, f"{name}_stock") if _house_stock(cfg) else Account(house, f"{name}_units")
        return Account(house, cfg.currency), source
    return Account(None, f"{name}_revenue"), Account(None, f"{name}_stock")


def _house_stock(cfg: AuctionConfig) -> bool:
    """Units come from ``$world.<name>_stock`` even with a house: a combinatorial lot's items, a tender's contracts."""
    return cfg.format == "combinatorial" or cfg.reverse


def _reserve(world: Any, name: str, cfg: AuctionConfig) -> float:
    return number(world, cfg.reserve, f"mechanisms.{name}.reserve")


def min_bid(world: Any, name: str) -> float:
    cfg = auction_config(world, name)
    lot = world.props.get(f"{name}_lot") or {}
    reserve = _reserve(world, name, cfg)
    if cfg.format == "english" and lot.get("leader"):
        return float(lot["price"]) + cfg.increment
    if cfg.format == "dutch" and lot.get("open"):
        return float(lot["price"])
    return 0.0 if cfg.format in ("double", "combinatorial") or cfg.reverse else reserve


def _receipt(world: Any, name: str, text: str) -> str:
    world.set_world(f"{name}_receipt", text)
    return text


def open_lot(world: Any, name: str) -> None:
    cfg = auction_config(world, name)
    lot = world.props.get(f"{name}_lot") or {}
    if lot.get("open"):
        return
    if cfg.when is not None and not truthy(compile_expr(cfg.when)(world.scope())):
        return
    units = 1
    if cfg.format != "double":
        _, source = _payee(world, name, cfg)
        left = int(balance(world, source))
        if left < 1:
            return
        if cfg.format == "uniform":
            units = min(cfg.units, left)  # the last lot sells what is left
    price = 0.0
    if cfg.format == "dutch":
        price = number(world, cfg.start_price, f"mechanisms.{name}.start_price")
    elif cfg.format == "english":
        price = _reserve(world, name, cfg)
    world.set_world(f"{name}_lot", {"open": True, "number": int(lot.get("number", 0)) + 1, "opened": world.round,
                                    "price": price, "leader": None, "last_bid": world.round, "units": units, "bids": []})


def bid(world: Any, name: str, trader: Entity, side: str, price: Any, qty: Any = 1, items: Any = None) -> str:
    cfg = auction_config(world, name)
    lot = _lot(world, name)
    if not lot.get("open"):
        raise Abort("No lot is open for bidding right now.")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise Abort(f"price must be a positive number, not {price!r}.")
    qty = 1 if qty is None else qty
    if isinstance(qty, bool) or not isinstance(qty, (int, float)) or qty < 1 or float(qty) != int(qty):
        raise Abort(f"qty must be a whole number ≥ 1, not {qty!r}.")
    qty = int(qty)
    if side == "ask" and cfg.format != "double":
        raise Abort("Only a double auction takes asks.")
    if cfg.format in ("first_price", "second_price", "english", "dutch") and qty != 1:
        raise Abort("This auction sells one unit at a time; bid for qty 1.")
    if cfg.format in ("uniform", "double") and qty > cfg.units:
        raise Abort(f"Bid or ask for at most {cfg.units} units.")
    if cfg.format == "combinatorial":
        return _bid_package(world, name, cfg, trader, price, items, lot)
    floor = min_bid(world, name)
    if side == "bid" and price < floor - 1e-9:
        raise Abort(f"Your bid must be at least {fmt(floor, 4)}.")
    if cfg.reverse and price > _reserve(world, name, cfg) + 1e-9:
        raise Abort(f"Your offer must be at most {fmt(_reserve(world, name, cfg), 4)}, the most the house pays.")
    cash, escrow = Account(trader, cfg.currency), Account(trader, f"{name}_escrow")
    item = f"{cfg.item}"
    if cfg.format == "dutch":
        clock = float(lot["price"])
        _, source = _payee(world, name, cfg)
        payee, _ = _payee(world, name, cfg)
        move(world, cash, payee, clock, what="cash")
        move(world, source, Account(trader, f"{name}_units"), 1, what="units")
        _won(world, name, trader, 1)
        _close(world, name, cfg, lot, [(trader.id, 1, clock)], note="took the Dutch clock")
        return _receipt(world, name, f"You took the {item} at the clock price {fmt(clock, 4)}.")
    if cfg.format == "english":
        if lot.get("leader") == trader.id:
            raise Abort("You already hold the high bid.")
        if lot.get("leader"):
            leader = entity_of(world, lot["leader"], f"mechanisms.{name}", "the leader")
            move(world, Account(leader, f"{name}_escrow"), Account(leader, cfg.currency), float(lot["price"]), what="escrow")
        move(world, cash, escrow, price, what="cash")
        lot.update(price=clean(price), leader=trader.id, last_bid=world.round)
        seq = int(world.props.get(f"{name}_seq") or 0) + 1
        world.set_world(f"{name}_seq", seq)
        lot["bids"] = [{"bidder": trader.id, "price": clean(price), "qty": 1, "seq": seq, "side": "bid"}]
        world.set_world(f"{name}_lot", lot)
        return _receipt(world, name, f"You lead with {fmt(price, 4)} for the {item}.")
    # sealed: replace any earlier bid of this side
    kept = []
    for earlier in lot["bids"]:
        if earlier["bidder"] == trader.id and earlier["side"] == side:
            _refund(world, name, cfg, earlier)
        else:
            kept.append(earlier)
    if side == "bid":
        if not cfg.reverse:
            move(world, cash, escrow, price * qty, what="cash")
    else:
        move(world, Account(trader, f"{name}_units"), Account(trader, f"{name}_escrow_units"), qty, what="units")
    seq = int(world.props.get(f"{name}_seq") or 0) + 1
    world.set_world(f"{name}_seq", seq)
    kept.append({"bidder": trader.id, "price": clean(price), "qty": qty, "seq": seq, "side": side})
    lot["bids"] = kept
    world.set_world(f"{name}_lot", lot)
    verb = "ask" if side == "ask" else "offer" if cfg.reverse else "bid"
    amount = item if qty == 1 else f"{qty} × {item}"
    return _receipt(world, name, f"Your sealed {verb} of {fmt(price, 4)} for {amount} is in.")


def _refund(world: Any, name: str, cfg: AuctionConfig, entry: Mapping[str, Any], keep: float = 0.0) -> None:
    if cfg.reverse:  # a tender's offers hold nothing
        return
    owner = entity_of(world, entry["bidder"], f"mechanisms.{name}", "a bidder")
    if entry["side"] == "bid":
        move(world, Account(owner, f"{name}_escrow"), Account(owner, cfg.currency), entry["price"] * entry["qty"] - keep, what="escrow")
    else:
        move(world, Account(owner, f"{name}_escrow_units"), Account(owner, f"{name}_units"), entry["qty"] - keep, what="units")


def _won(world: Any, name: str, trader: Entity, qty: int) -> None:
    world.set_prop(trader, f"{name}_won", int(trader.properties.get(f"{name}_won") or 0) + qty)  # type: ignore[arg-type]


def _close(world: Any, name: str, cfg: AuctionConfig, lot: Dict[str, Any], winners: List[Tuple[str, int, float]],
           note: str, packages: Optional[Mapping[str, List[str]]] = None) -> None:
    lot = {**lot, "open": False, "bids": []}
    world.set_world(f"{name}_lot", lot)
    if winners:
        world.set_world(f"{name}_sold", int(world.props.get(f"{name}_sold") or 0) + sum(q for _, q, _ in winners))
    for winner, qty, price in winners or [("", 0, 0.0)]:
        fields: Dict[str, Any] = {"lot": lot["number"], "winner": winner, "price": clean(price), "qty": qty, "note": note}
        if cfg.format == "combinatorial":
            fields["items"] = list((packages or {}).get(winner, []))
        world.post(f"{name}_results", fields, None, None, f"mechanisms.{name}")


def _order(world: Any, cfg: AuctionConfig, bids: List[Dict[str, Any]], merit: Callable[[Dict[str, Any]], float]
           ) -> List[Dict[str, Any]]:
    """Highest merit first; equal merit by time, or shuffled with the run's seed."""
    rated = {id(b): merit(b) for b in bids}
    keyed = sorted(bids, key=lambda b: (-rated[id(b)], b["seq"]))
    if cfg.ties == "random":
        out: List[Dict[str, Any]] = []
        i = 0
        while i < len(keyed):
            j = i
            while j < len(keyed) and rated[id(keyed[j])] == rated[id(keyed[i])]:
                j += 1
            group = keyed[i:j]
            world.rng.shuffle(group)
            out.extend(group)
            i = j
        return out
    return keyed


def close_sealed(world: Any, name: str) -> None:
    cfg = auction_config(world, name)
    lot = _lot(world, name)
    if not lot.get("open") or cfg.format not in SEALED:
        return
    payee, source = _payee(world, name, cfg)
    bids = _order(world, cfg, [b for b in lot["bids"] if b["side"] == "bid"], _merit(world, name, cfg))
    if cfg.reverse:
        _award_tender(world, name, cfg, lot, bids, payee, source)
        return
    if cfg.format == "double":
        _clear_double(world, name, cfg, lot, bids)
        return
    if cfg.format == "combinatorial":
        _clear_packages(world, name, cfg, lot)
        return
    reserve = _reserve(world, name, cfg)
    supply = int(lot["units"])
    allocation: List[Tuple[Dict[str, Any], int]] = []
    left = supply
    rejected: List[float] = []
    for entry in bids:
        take = min(left, entry["qty"]) if entry["price"] >= reserve else 0
        if take:
            allocation.append((entry, take))
            left -= take
        if take < entry["qty"]:
            rejected.append(entry["price"])
    if not allocation:
        for entry in bids:
            _refund(world, name, cfg, entry)
        _close(world, name, cfg, lot, [], note="no bid met the reserve" if bids else "no bids")
        return
    if cfg.format == "first_price":
        prices = [allocation[0][0]["price"]]
    elif cfg.format == "second_price":
        prices = [max([reserve] + [b["price"] for b in bids[1:]][:1])]
    else:
        lowest = min(e["price"] for e, _ in allocation)
        prices = [max([reserve, *rejected]) if cfg.price_rule == "highest_rejected" else lowest]
    winners: List[Tuple[str, int, float]] = []
    for entry, take in allocation:
        price = prices[0]
        owner = entity_of(world, entry["bidder"], f"mechanisms.{name}", "a bidder")
        move(world, Account(owner, f"{name}_escrow"), payee, price * take, what="escrow")
        _refund(world, name, cfg, entry, keep=price * take)
        move(world, source, Account(owner, f"{name}_units"), take, what="units")
        _won(world, name, owner, take)
        winners.append((owner.id, take, price))
    for entry in bids:
        if all(entry is not e for e, _ in allocation):
            _refund(world, name, cfg, entry)
    by_reserve = len(bids) < 2 or reserve > bids[1]["price"]  # the reserve, not a losing bid, set a second price
    labels = {"first_price": "best score, pays its bid" if cfg.score else "pays its bid",
              "second_price": "pays the reserve" if by_reserve else "pays the second-highest bid",
              "uniform": f"uniform price ({cfg.price_rule.replace('_', ' ')})"}
    _close(world, name, cfg, lot, winners, note=labels[cfg.format])


def _merit(world: Any, name: str, cfg: AuctionConfig) -> Callable[[Dict[str, Any]], float]:
    """How sealed bids rank, best first: by `score`, else the highest price (the lowest in a tender)."""
    if cfg.score is not None:
        score = cfg.score
        return lambda b: number(world, score, f"mechanisms.{name}.score", price=b["price"],
                                it=entity_of(world, b["bidder"], f"mechanisms.{name}", "a bidder"))
    return (lambda b: -b["price"]) if cfg.reverse else (lambda b: b["price"])


def _award_tender(world: Any, name: str, cfg: AuctionConfig, lot: Dict[str, Any], bids: List[Dict[str, Any]],
                  payer: Account, source: Account) -> None:
    """A procurement lot: the best offer the house can pay, at most the reserve, wins a unit and is paid."""
    cap = min(_reserve(world, name, cfg), balance(world, payer))
    accepted = [b for b in bids if b["price"] <= cap + 1e-9]
    if not accepted:
        _close(world, name, cfg, lot, [], note="no offer met the reserve" if bids else "no offers")
        return
    best = accepted[0]
    others = [b["price"] for b in bids if b is not best]
    price = best["price"] if cfg.format == "first_price" else min([cap, *others])
    winner = entity_of(world, best["bidder"], f"mechanisms.{name}", "a bidder")
    move(world, payer, Account(winner, cfg.currency), price, what="cash")
    move(world, source, Account(winner, f"{name}_units"), 1, what="units")
    _won(world, name, winner, 1)
    capped = price == cap and not any(p <= cap for p in others)
    notes = {"first_price": "best score, paid its offer" if cfg.score else "lowest offer, paid its offer",
             "second_price": ("lowest offer, paid the reserve" if cap == _reserve(world, name, cfg) else
                              "lowest offer, paid what the house had left") if capped else "lowest offer, paid the second-lowest"}
    _close(world, name, cfg, lot, [(winner.id, 1, price)], note=notes[cfg.format])


def _clear_double(world: Any, name: str, cfg: AuctionConfig, lot: Dict[str, Any], bids: List[Dict[str, Any]]) -> None:
    asks = _order(world, cfg, [b for b in lot["bids"] if b["side"] == "ask"], lambda b: -b["price"])
    trades: List[Tuple[Dict[str, Any], Dict[str, Any], int]] = []
    bid_left = {id(b): b["qty"] for b in bids}
    ask_left = {id(a): a["qty"] for a in asks}
    bi = ai = 0
    marginal: Optional[Tuple[float, float]] = None
    while bi < len(bids) and ai < len(asks) and bids[bi]["price"] >= asks[ai]["price"]:
        b, a = bids[bi], asks[ai]
        q = min(bid_left[id(b)], ask_left[id(a)])
        trades.append((b, a, q))
        marginal = (b["price"], a["price"])
        bid_left[id(b)] -= q
        ask_left[id(a)] -= q
        bi += bid_left[id(b)] == 0
        ai += ask_left[id(a)] == 0
    price = 0.0
    if marginal:  # the middle of the prices that clear the market: every matched order trades, no unmatched one would
        low = max([marginal[1]] + ([bids[bi]["price"]] if bi < len(bids) else []))
        high = min([marginal[0]] + ([asks[ai]["price"]] if ai < len(asks) else []))
        price = clean((low + high) / 2)
    bought: Dict[str, int] = {}
    for b, a, q in trades:
        buyer = entity_of(world, b["bidder"], f"mechanisms.{name}", "a bidder")
        seller = entity_of(world, a["bidder"], f"mechanisms.{name}", "a seller")
        move(world, Account(buyer, f"{name}_escrow"), Account(seller, cfg.currency), price * q, what="escrow")
        move(world, Account(seller, f"{name}_escrow_units"), Account(buyer, f"{name}_units"), q, what="units")
        bought[buyer.id] = bought.get(buyer.id, 0) + q
    for entry in bids:
        filled = entry["qty"] - bid_left[id(entry)]
        _refund(world, name, cfg, {**entry}, keep=price * filled)
        if filled:
            _won(world, name, entity_of(world, entry["bidder"], f"mechanisms.{name}", "a bidder"), filled)
    for entry in asks:
        _refund(world, name, cfg, {**entry}, keep=entry["qty"] - ask_left[id(entry)])
    _close(world, name, cfg, lot, [(who, q, price) for who, q in bought.items()],
           note=f"double auction cleared at {fmt(price, 4)}" if trades else "no bid met an ask")


def _item_reserves(world: Any, name: str, cfg: AuctionConfig) -> Dict[str, float]:
    return {item: number(world, cfg.reserves.get(item, cfg.reserve), f"mechanisms.{name}.reserves.{item}") for item in cfg.items}


def _bid_package(world: Any, name: str, cfg: AuctionConfig, trader: Entity, price: float, items: Any,
                 lot: Dict[str, Any]) -> str:
    """A sealed XOR package bid: a new package, or a new price for one the bidder already bid on."""
    left = list(world.props.get(f"{name}_items") or [])
    if not isinstance(items, (list, tuple)) or not items or not all(isinstance(i, str) for i in items):
        raise Abort(f"List the items you want together, e.g. [{', '.join(left[:2])}].")
    wanted = list(dict.fromkeys(items))
    missing = [item for item in wanted if item not in left]
    if missing:
        raise Abort(f"Not for sale now: {', '.join(missing)} (for sale: {', '.join(left) or 'nothing'}).")
    reserves = _item_reserves(world, name, cfg)
    floor = sum(reserves[item] for item in wanted)
    if price < floor - 1e-9:
        raise Abort(f"Your bid for {' + '.join(wanted)} must be at least {fmt(floor, 4)} (the items' reserves).")
    bids = lot["bids"]
    mine = [b for b in bids if b["bidder"] == trader.id]
    same = next((b for b in mine if set(b["items"]) == set(wanted)), None)
    if same is None and len(mine) >= cfg.packages:
        raise Abort(f"You already hold {cfg.packages} package bids; bid on one of those packages again to change its price.")
    if same is None and len(bids) >= MAX_PACKAGE_BIDS:
        raise Abort(f"This lot already holds {MAX_PACKAGE_BIDS} bids, the most it can settle exactly.")
    seq = int(world.props.get(f"{name}_seq") or 0) + 1
    world.set_world(f"{name}_seq", seq)
    kept = [b for b in bids if b is not same] + [{"bidder": trader.id, "price": clean(price), "qty": 1, "seq": seq,
                                                  "side": "bid", "items": wanted}]
    held = max((b["price"] for b in mine), default=0.0)
    needed = max(b["price"] for b in kept if b["bidder"] == trader.id)
    cash, escrow = Account(trader, cfg.currency), Account(trader, f"{name}_escrow")
    if needed > held:
        move(world, cash, escrow, clean(needed - held), what="cash")
    elif held > needed:
        move(world, escrow, cash, clean(held - needed), what="escrow")
    lot["bids"] = kept
    world.set_world(f"{name}_lot", lot)
    return _receipt(world, name, f"Your sealed bid of {fmt(price, 4)} for {' + '.join(wanted)} is in. You win at most one of "
                                 f"your packages, so {fmt(needed, 4)} (your highest bid) is held.")


def _clear_packages(world: Any, name: str, cfg: AuctionConfig, lot: Dict[str, Any]) -> None:
    payee, source = _payee(world, name, cfg)
    ordered = sorted(lot["bids"], key=lambda b: b["seq"])
    if cfg.ties == "random":
        world.rng.shuffle(ordered)
    bids = [PackageBid(b["bidder"], tuple(b["items"]), float(b["price"])) for b in ordered]
    try:
        winners, _ = settle(bids, _item_reserves(world, name, cfg), cfg.payment)
    except SearchLimit as exc:
        raise RunError(f"the {name} auction cannot settle its lot exactly: {exc} ({len(bids)} bids on {len(cfg.items)} items)",
                       f"mechanisms.{name}.packages") from None
    won = {w["bidder"]: w for w in winners}
    left = list(world.props.get(f"{name}_items") or [])
    for bidder in dict.fromkeys(b["bidder"] for b in ordered):
        owner = entity_of(world, bidder, f"mechanisms.{name}", "a bidder")
        escrow = Account(owner, f"{name}_escrow")
        win = won.get(bidder)
        pays = clean(win["pays"]) if win else 0.0
        if pays:
            move(world, escrow, payee, pays, what="escrow")
        change = clean(balance(world, escrow))
        if change:
            move(world, escrow, Account(owner, cfg.currency), change, what="escrow")
        if win:
            move(world, source, Account(owner, f"{name}_units"), len(win["items"]), what="units")
            owned = cast(List[str], owner.properties.get(f"{name}_items") or [])
            world.set_prop(owner, f"{name}_items", [*owned, *win["items"]])
            left = [item for item in left if item not in win["items"]]
            _won(world, name, owner, len(win["items"]))
    world.set_world(f"{name}_items", left)
    note = "VCG price" if cfg.payment == "vcg" else "pays its bid"
    _close(world, name, cfg, lot, [(w["bidder"], len(w["items"]), w["pays"]) for w in winners],
           note=note if winners else ("no bid met the reserves" if bids else "no bids"),
           packages={w["bidder"]: w["items"] for w in winners})


def tick(world: Any, name: str) -> None:
    """End of a round for open-outcry lots: the Dutch clock falls; an English lot without new bids closes."""
    cfg = auction_config(world, name)
    lot = _lot(world, name)
    if not lot.get("open"):
        return
    if cfg.format == "dutch":
        price = clean(float(lot["price"]) - cfg.decrement)
        if price < _reserve(world, name, cfg) - 1e-9:
            _close(world, name, cfg, lot, [], note="the clock fell below the reserve")
        else:
            world.set_world(f"{name}_lot", {**lot, "price": price})
    elif cfg.format == "english" and world.round - int(lot["last_bid"]) >= cfg.timeout:
        leader = lot.get("leader")
        if not leader:
            _close(world, name, cfg, lot, [], note="no bids")
            return
        payee, source = _payee(world, name, cfg)
        owner = entity_of(world, leader, f"mechanisms.{name}", "the leader")
        move(world, Account(owner, f"{name}_escrow"), payee, float(lot["price"]), what="escrow")
        move(world, source, Account(owner, f"{name}_units"), 1, what="units")
        _won(world, name, owner, 1)
        _close(world, name, cfg, lot, [(owner.id, 1, float(lot["price"]))], note="going, going, gone")


def audit(world: Any, name: str) -> List[str]:
    cfg = auction_config(world, name)
    problems: List[str] = []
    lot = world.props.get(f"{name}_lot") or {}
    escrow: Dict[str, float] = {}
    escrow_units: Dict[str, float] = {}
    for entry in lot.get("bids", []) if lot.get("open") else []:
        if cfg.format == "combinatorial":  # XOR bids: the highest one is held
            escrow[entry["bidder"]] = max(escrow.get(entry["bidder"], 0.0), entry["price"])
            continue
        if cfg.reverse:  # a tender's offers hold nothing
            continue
        target = escrow if entry["side"] == "bid" else escrow_units
        target[entry["bidder"]] = target.get(entry["bidder"], 0.0) + (entry["price"] * entry["qty"] if entry["side"] == "bid" else entry["qty"])
    if cfg.format == "combinatorial":
        held = list(world.props.get(f"{name}_items") or [])
        held += [item for party in _parties(world, cfg) for item in cast(List[str], party.properties.get(f"{name}_items") or [])]
        if sorted(held) != sorted(cfg.items):
            problems.append(f"items are not conserved: held {sorted(held)}, sold from {sorted(cfg.items)}")
    for party in _parties(world, cfg):
        for prop, expected in ((f"{name}_escrow", escrow), (f"{name}_escrow_units", escrow_units)):
            have = balance(world, Account(party, prop))
            if abs(have - expected.get(party.id, 0.0)) > 1e-6:
                problems.append(f"{party.id} has {have} in {prop} but its open bids hold {expected.get(party.id, 0.0)}")
        if balance(world, Account(party, f"{name}_units")) < -1e-9:
            problems.append(f"{party.id} has a negative number of units")
    return problems


def _format_check(action: str) -> Callable[[Any, Dict[str, Any], str], List[Tuple[str, str, Optional[str]]]]:
    """Check-time: a bid or ask names only what the auction's format takes."""

    def check(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
        raw = (getattr(checker.c, "mechanisms", None) or {}).get(effect.get("market"))
        chosen = raw.get("format") if isinstance(raw, Mapping) else None
        if chosen not in FORMATS:
            return []
        where = f"a {chosen} auction"
        if action == "ask" and chosen != "double":
            return [(f"{path}.action", f"{where} takes no asks", "only a double auction takes asks; use action bid")]
        if chosen == "combinatorial":
            if "package" not in effect:
                return [(path, f"`market.bid` on {where} needs `package`", "list the items bid on together")]
            if "qty" in effect:
                return [(f"{path}.qty", f"{where} takes `package`, not `qty`", "remove qty")]
        elif "package" in effect:
            return [(f"{path}.package", f"`package` belongs to a combinatorial auction, not {where}", "remove package")]
        return []

    return check


#: action → (its keys, the required ones, generated by the mechanism itself, example keys, what it does).
#: `who` is the bidder (default $actor); receipts land in $world.<name>_receipt.
_ACTIONS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], bool, str, str]] = {
    "bid": (("who", "price", "qty", "package"), ("price",), False, '"price": 120',
            "bid on the open lot; `qty` for uniform and double, `package` lists the items of a combinatorial bid"),
    "ask": (("who", "price", "qty"), ("price",), False, '"price": 90, "qty": 1', "offer units in a double auction"),
    "open": ((), (), True, "", "open a lot when there is something to sell"),
    "close": ((), (), True, "", "clear a sealed lot"),
    "tick": ((), (), True, "", "move the English or Dutch clock"),
}


def _runner(action: str) -> Callable[[Any, Dict[str, Any], Dict[str, Any], str], None]:
    def run(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        world = runner.world
        name = effect["market"]
        try:
            if action == "open":
                open_lot(world, name)
            elif action == "close":
                close_sealed(world, name)
            elif action == "tick":
                tick(world, name)
            else:
                trader = entity_of(world, runner.eval(effect.get("who", "$actor"), vars), f"{where}.who", "a bidder")
                bid(world, name, trader, action, runner.eval(effect["price"], vars), runner.eval(effect.get("qty", 1), vars),
                    runner.eval(effect.get("package"), vars))
        except RunError as exc:
            raise RunError(str(exc), where) from None

    return run


def _register_actions() -> None:
    for action, (keys, required, internal, fields, doc) in _ACTIONS.items():
        example = '{"market": "house", "action": "' + action + '"' + (f", {fields}" if fields else "") + f"}}  ({doc})"
        family_action("market", ("auction",), action, keys=keys, required=required, internal=internal, example=example,
                      check=_format_check(action) if action in ("bid", "ask") else None)(_runner(action))


_register_actions()


def _party_props(name: str, cfg: AuctionConfig, contract: Mapping[str, Any], holder: str) -> Dict[str, Any]:
    props = {**money_prop(contract, holder, cfg.currency),
             f"{name}_units": {"type": "int", "default": 0, "description": f"Units of {cfg.item} held."},
             f"{name}_escrow": {"type": "number", "default": 0, "private": True, "description": "Cash held for open bids."},
             f"{name}_escrow_units": {"type": "int", "default": 0, "private": True},
             f"{name}_won": {"type": "int", "default": 0}}
    if cfg.format == "combinatorial":
        props[f"{name}_items"] = {"type": "list", "default": [], "description": "Items won."}
    return props


def _check_award(cfg: AuctionConfig) -> None:
    """A tender needs a house that buys and pays, and a most-it-pays reserve; a score ranks first-price bids."""
    if cfg.reverse:
        if cfg.format not in ("first_price", "second_price"):
            raise MechanismError(f"a reverse auction is sealed: format first_price or second_price, not {cfg.format}",
                                 "set format: first_price (winner is paid its offer) or second_price", "format")
        if not cfg.house:
            raise MechanismError("a reverse auction needs `house`: the entity that buys and pays the winner",
                                 'e.g. "house": "city" (an entity with cash)', "house")
        if not isinstance(cfg.reserve, str) and cfg.reserve <= 0:
            raise MechanismError("a reverse auction needs `reserve`: the most the house pays per unit",
                                 'e.g. "reserve": 500', "reserve")
    if cfg.score is not None:
        if cfg.format != "first_price":
            raise MechanismError(f"a scored award pays the winner its own bid, so it needs format first_price, not {cfg.format}",
                                 "set format: first_price", "score")
        check_expr(cfg.score, "score", ["it", "price"])


def _check_packages(cfg: AuctionConfig) -> None:
    if cfg.format != "combinatorial":
        if cfg.items or cfg.reserves:
            raise MechanismError("`items` and `reserves` belong to a combinatorial auction", "set format: combinatorial", "items")
        return
    if not cfg.items:
        raise MechanismError("a combinatorial auction needs `items`: the distinct things sold in packages",
                             'e.g. "items": ["north", "south", "east"]', "items")
    if len(set(cfg.items)) != len(cfg.items) or not all(item.strip() for item in cfg.items):
        raise MechanismError("`items` must be distinct, non-empty names", None, "items")
    if len(cfg.items) > MAX_PACKAGE_ITEMS:
        raise MechanismError(f"a combinatorial auction sells at most {MAX_PACKAGE_ITEMS} items at once (exact winner "
                             "determination)", "split the items across lots or auctions", "items")
    unknown = sorted(set(cfg.reserves) - set(cfg.items))
    if unknown:
        raise MechanismError(f"reserves name items that are not for sale: {', '.join(unknown)}", f"items: {', '.join(cfg.items)}",
                             "reserves")


@mode("market", "auction", AuctionConfig,
           "An auction: sealed first_price, second_price (Vickrey), english (ascending, increment, timeout), dutch (falling "
           "clock), double (call market at one price) or uniform (multi-unit, one price). Tools `<name>_bid` (price, qty) "
           "and, for double, `<name>_ask`. Bids escrow cash, asks escrow units; proceeds go to the `house` entity or "
           "$world.<name>_revenue. `reverse: true` makes it a procurement tender (the house buys; the lowest offer at or below "
           "the reserve wins and is paid); `score` awards a first_price lot to the best score instead of the best price. "
           "Each closed lot is posted to the `<name>_results` record, one entry per winner (winner, price, qty, lot, note; "
           "an unsold lot has one entry with winner ''). $auction(<name>).last is the latest closed lot, sold or not: "
           "{lot, winner (the first winner, '' when unsold), winners, price (the first winner's price per unit; in a "
           "uniform auction every winner pays it), qty (units sold), note}, null before the first lot closes; output "
           "`<name>_prices` lists the price of every winning entry. The other fields of $auction(name) describe the open lot; $auction_text(name, viewer) describes it.",
           example={"format": "second_price", "who": "collector", "item": "a painting", "stock": 3, "reserve": 50})
def _expand_auction(name: str, cfg: AuctionConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    types = contract.get("types") or {}
    for field, kind in (("who", cfg.who), ("sellers", cfg.sellers)):
        if kind is not None and kind not in types:
            raise MechanismError(f"{field} '{kind}' is not a declared type", f"types: {', '.join(types) or 'none'}", field)
    if cfg.format == "dutch" and cfg.start_price is None:
        raise MechanismError("a dutch auction needs `start_price`", None, "start_price")
    if cfg.format not in ("uniform", "double") and cfg.units != 1:
        raise MechanismError("only uniform and double auctions trade several units at once", "set units: 1", "units")
    _check_packages(cfg)
    _check_award(cfg)
    packaged = cfg.format == "combinatorial"
    party_types = {cfg.who: {"props": _party_props(name, cfg, contract, cfg.who)}}
    if cfg.format == "double":
        party_types[cfg.sellers or cfg.who] = {"props": _party_props(name, cfg, contract, cfg.sellers or cfg.who)}
    if cfg.house:
        entity = (contract.get("entities") or {}).get(cfg.house)
        if not isinstance(entity, Mapping) or entity.get("type") not in types:
            raise MechanismError(f"house '{cfg.house}' is not a declared entity", "declare it under entities", "house")
        party_types[entity["type"]] = {"props": _party_props(name, cfg, contract, entity["type"])}
    sealed = cfg.format in SEALED
    single = cfg.format not in ("uniform", "double")
    receipt = f"{{$world.{name}_receipt}}"
    rules = {"first_price": "Sealed bids; the highest bid at or above the reserve wins and pays its bid.",
             "second_price": "Sealed bids; the highest bid wins and pays the second-highest bid (or the reserve), so bidding "
                             "your true value is safe.",
             "reverse_first_price": "Sealed offers to supply the house; the lowest offer at or below the reserve (the most "
                                    "the house pays) wins and is paid its offer.",
             "reverse_second_price": "Sealed offers to supply the house; the lowest offer at or below the reserve wins and is "
                                     "paid the second-lowest offer (or the reserve), so offering your true cost is safe.",
             "english": f"Open bids; beat the high bid by at least {fmt(cfg.increment, 4)}. The lot closes after "
                        f"{cfg.timeout} round(s) without a new bid; the winner pays its bid.",
             "dutch": "The clock price falls every round; the first bid at or above it takes the lot at the clock price.",
             "double": "Sealed bids to buy and asks to sell, cleared together at one market-clearing price (the middle of the range that clears).",
             "uniform": f"Sealed bids for up to {cfg.units} units; the highest bids win and all pay one clearing price.",
             "combinatorial": f"Sealed bids on packages of {', '.join(cfg.items)}: bid on up to {cfg.packages} packages; you win "
                              "at most one of them and each item goes to one winner, chosen to raise the most over the reserves. "
                              + ("Winners pay only the value their win takes from the other bidders, so bidding what a package is "
                                 "worth to you is safe." if cfg.payment == "vcg" else "Winners pay their bids.")}[
        f"reverse_{cfg.format}" if cfg.reverse else cfg.format]
    if cfg.score is not None:
        rules = ("Sealed offers to supply the house; among offers at or below the reserve, the best score wins and is paid "
                 if cfg.reverse else "Sealed bids; among bids at or above the reserve, the best score wins and pays ") + \
                f"its own price. Score: {cfg.score} ($price is your price, $it you)."
    escrow = (" Your highest package bid is held until the lot closes; you get back whatever you do not pay." if packaged else
              " Nothing is held: the house pays the winner when the lot closes." if cfg.reverse else
              " Your bid's full amount is held until the lot closes; you get back whatever you do not pay.")
    amount = {"qty": {"type": "int", "min": 1, "max": 1 if single else cfg.units, "default": 1, "description": "Units wanted."}}
    if packaged:
        amount = {"package": {"type": "list", "items": {"type": "enum", "values": cfg.items}, "min_items": 1,
                              "max_items": len(cfg.items), "description": "The items you want together."}}
    price: Dict[str, Any] = {"type": "number", "min": f"$max(0.0001, $auction({name}).min_bid)",  # a price is positive
                             "max": f"$actor.{cfg.currency} + $actor.{name}_escrow" if packaged else f"$actor.{cfg.currency}",
                             "description": "Price for the whole package." if packaged else "Price per unit."}
    when = [{"expr": f"$auction({name}).open", "why": "No lot is open."},
            {"expr": f"$actor.{cfg.currency} > 0", "why": "You have no cash."}]
    if cfg.reverse:
        price = {"type": "number", "min": 0.0001, "max": f"$auction({name}).reserve",
                 "description": "Price per unit you want to be paid."}
        when = when[:1]
    actions: Dict[str, Any] = {
        f"{name}_bid": {
            "by": cfg.who, "description": f"{'Offer to supply' if cfg.reverse else 'Bid for'} {cfg.item}. {rules}{escrow}",
            "params": {**amount, "price": price}, "when": when,
            "do": [{"market": name, "action": "bid", "price": "$params.price",
                    **({"package": "$params.package"} if packaged else {"qty": "$params.qty"})}],
            "outcome": receipt, "private": sealed, "terminal": sealed and not packaged,
        },
    }
    if cfg.format == "english":
        actions[f"{name}_bid"]["when"].append({"expr": f"$auction({name}).leader != $actor.id", "why": "You hold the high bid."})
    if cfg.format == "double":
        actions[f"{name}_ask"] = {
            "by": cfg.sellers or cfg.who, "description": f"Offer {cfg.item} for sale. {rules} Your units are held until the lot closes.",
            "params": {"price": {"type": "number", "min": 0.0001, "description": "Lowest price per unit you accept."},
                       "qty": {"type": "int", "min": 1, "max": f"$min($actor.{name}_units, {cfg.units})", "default": 1, "description": "Units offered."}},
            "when": [{"expr": f"$auction({name}).open", "why": "No lot is open."},
                     {"expr": f"$actor.{name}_units > 0", "why": "You have nothing to sell."}],
            "do": [{"market": name, "action": "ask", "price": "$params.price", "qty": "$params.qty"}],
            "outcome": receipt, "private": True, "terminal": True,
        }
    winner_text = ("{$entity($it.winner).name if $it.winner else 'unsold'}" +
                   ("{$' won ' + $join($it.items, ' + ') + ' for ' + $fmt($it.price, 'money') if $it.winner else ''}" if packaged else
                    "{$' × ' + $text($it.qty) + ' at ' + $fmt($it.price, 'money') if $it.winner else ''}"))
    fragment: Dict[str, Any] = {
        "types": party_types,
        "world": {f"{name}_lot": {"type": "map", "default": {}}, f"{name}_seq": {"type": "int", "default": 0},
                  **({f"{name}_items": {"type": "list", "default": list(cfg.items), "description": "Items still for sale."}}
                     if packaged else {}),
                  f"{name}_revenue": {"type": "number", "default": 0, "description": "House proceeds."},
                  f"{name}_stock": {"type": "int", "default": len(cfg.items) if packaged else
                                    0 if (cfg.house and not cfg.reverse) or cfg.format == "double" else cfg.stock,
                                    "description": "Units the house still has to sell."},
                  f"{name}_sold": {"type": "int", "default": 0},
                  f"{name}_receipt": {"type": "text", "default": ""}},
        "records": {f"{name}_results": {"fields": {"lot": "int", "winner": "text", "price": "number", "qty": "int", "note": "text",
                                                   **({"items": "list"} if packaged else {})},
                                        "show": f"Lot {{lot}}: {winner_text} ({{note}})",
                                        "description": "Closed lots."}},
        "actions": actions,
        "events": [{"name": f"{name}_open", "phase": "start", "do": [{"market": name, "action": "open"}]}],
        "views": {f"{name}_lot": {"for": sorted({cfg.who, cfg.sellers or cfg.who}), "show": f"{{$auction_text({name}, $actor)}}"},
                  f"{name}_recent": {"for": sorted({cfg.who, cfg.sellers or cfg.who}), "title": "Recent results",
                                     "of": f"$reverse($slice($records({name}_results), -3))",
                                     "show": f"Lot {{lot}}: {winner_text}"}},
        "metrics": {f"{name}_sold": f"$world.{name}_sold", f"{name}_revenue": f"$world.{name}_revenue"},
        "outputs": {f"{name}_sold": {"expr": f"$world.{name}_sold", "type": "int", "description": "Units sold."},
                    f"{name}_revenue": {"expr": f"$world.{name}_revenue", "type": "number", "description": "House proceeds."},
                    f"{name}_prices": {"expr": f"$map($filter($records({name}_results), $it.winner != ''), $it.price)",
                                       "type": "list", "description": "Price per unit of each winning entry (one per winner of a lot)."}},
    }
    names = list(actions)
    if not sealed:
        fragment["events"].append({"name": f"{name}_tick", "phase": "end", "do": [{"market": name, "action": "tick"}]})
    if cfg.stage is None:
        stage: Dict[str, Any] = {"name": name, "turns": "simultaneous" if sealed else "sequential", "actions": names,
                                 "when": f"$auction({name}).open", "brief": f"{rules}"}
        if not sealed:
            stage["order"] = "random"
        if sealed:
            stage["on_exit"] = [{"market": name, "action": "close"}]
        if packaged:
            stage.update(max_actions=cfg.packages, max_calls=cfg.packages + 4)
        fragment["stages"] = [stage]
    else:
        hook: Dict[str, Any] = {"actions": names, "on_exit": [{"market": name, "action": "close"}] if sealed else []}
        if packaged:
            hook["max_actions"] = cfg.packages
        fragment["stage_hooks"] = {cfg.stage: hook}
    if cfg.conserve:
        fragment["invariants"] = [{"expr": f"$auction_ok({name})",
                                   "why": f"The {name} auction's escrow matches its open bids and every item is held once."}]
    return fragment
