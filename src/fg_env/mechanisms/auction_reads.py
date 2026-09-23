"""What expressions and views read of an auction: ``$auction``, ``$auction_text``, ``$auction_ok``.

The open lot is read from the auction's world props; the latest closed lot's result from its ``<name>_results``
record, so it stays readable after the lot closes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, function
from .auctions import _item_reserves, _reserve, audit, auction_config, min_bid
from .common import fmt

__all__ = ["describe", "last_result"]


def describe(world: Any, name: str, viewer: Optional[Entity]) -> str:
    """The lot as one plain sentence for a view."""
    cfg = auction_config(world, name)
    lot = world.props.get(f"{name}_lot") or {}
    label = {"first_price": "Sealed-bid auction (highest bid wins and pays its bid)",
             "second_price": "Sealed-bid Vickrey auction (highest bid wins and pays the second-highest bid)",
             "english": "English auction (open ascending bids)", "dutch": "Dutch auction (falling clock; first taker wins)",
             "double": "Double auction (sealed bids and asks cleared at one price)",
             "uniform": f"Uniform-price auction ({lot.get('units') or cfg.units} units to the highest bids at one price)",
             "combinatorial": "Combinatorial auction (sealed package bids; you win at most one package; winners pay "
                              + ("VCG prices)" if cfg.payment == "vcg" else "their bids)")}[cfg.format]
    if cfg.reverse:
        label = ("Sealed tender (lowest offer wins and is paid its offer)" if cfg.format == "first_price" else
                 "Sealed Vickrey tender (lowest offer wins and is paid the second-lowest offer)")
    if cfg.score is not None:
        label = f"Sealed {'tender' if cfg.reverse else 'auction'} (best score wins: {cfg.score})"
    if not lot.get("open"):
        return f"{label}: no lot is open right now."
    reserve = _reserve(world, name, cfg)
    parts = [f"{label}: lot {lot['number']}, {cfg.item}"]
    if cfg.format == "combinatorial":
        reserves = _item_reserves(world, name, cfg)
        left = world.props.get(f"{name}_items") or []
        parts.append("for sale: " + ", ".join(f"{item} (reserve {fmt(reserves[item], 4)})" if reserves[item] else item for item in left))
        mine = [b for b in lot.get("bids", []) if viewer is not None and b["bidder"] == viewer.id]
        if mine:
            parts.append("your bids: " + "; ".join(f"{' + '.join(b['items'])} at {fmt(b['price'], 4)}" for b in mine))
        parts.append(f"{len(lot.get('bids', []))} sealed bid(s) in")
    elif cfg.format == "english":
        leader = world.entity(lot.get("leader")) if lot.get("leader") else None
        parts.append(f"high bid {fmt(lot['price'], 4)} by {'you' if leader is viewer and viewer is not None else leader.name}"
                     if leader else f"no bids yet (reserve {fmt(reserve, 4)})")
        parts.append(f"next bid at least {fmt(min_bid(world, name), 4)}; closes after {cfg.timeout} round(s) without a bid")
    elif cfg.format == "dutch":
        parts.append(f"clock price {fmt(lot['price'], 4)}, falling {fmt(cfg.decrement, 4)} a round, reserve {fmt(reserve, 4)}")
    else:
        if cfg.reverse:
            parts.append(f"the house pays at most {fmt(reserve, 4)}")
        elif reserve and cfg.format != "double":
            parts.append(f"reserve {fmt(reserve, 4)}")
        mine = [b for b in lot.get("bids", []) if viewer is not None and b["bidder"] == viewer.id]
        if mine:
            parts.append("your " + "; ".join(f"{b['side']} {fmt(b['price'], 4)} × {b['qty']}" for b in mine))
        parts.append(f"{len(lot.get('bids', []))} sealed bid(s) in")
    return ", ".join(parts) + "."


def _auction(call: Call) -> str:
    try:
        auction_config(call.scope.world, call.arg(0))
    except RunError as exc:
        raise ExprError(f"${call.name}: {exc}", call.source) from None
    return str(call.arg(0))


@function("auction(name)", "An auction's state: {format, open, lot, price, leader, min_bid, reserve, bids, sold, revenue, stock, "
          "items, last}. price, leader and bids describe the open lot; items what a combinatorial lot still has for sale; "
          "last the latest closed lot's result {lot, winner, winners, price, qty, note} (winner: the first winner's id, '' "
          "when unsold), kept until another lot closes, null before any has.", min_args=1, max_args=1)
def _auction_function(call: Call) -> Dict[str, Any]:
    name = _auction(call)
    world: Any = call.scope.world
    cfg = auction_config(world, name)
    lot = world.props.get(f"{name}_lot") or {}
    return {"format": cfg.format, "open": bool(lot.get("open")), "lot": lot.get("number", 0), "price": lot.get("price"),
            "leader": lot.get("leader"), "min_bid": min_bid(world, name), "reserve": _reserve(world, name, cfg),
            "bids": len(lot.get("bids", [])) if lot.get("open") else 0, "sold": world.props.get(f"{name}_sold") or 0,
            "revenue": world.props.get(f"{name}_revenue") or 0, "stock": world.props.get(f"{name}_stock") or 0,
            "items": list(world.props.get(f"{name}_items") or []), "last": last_result(world, name)}


def last_result(world: Any, name: str) -> Optional[Dict[str, Any]]:
    """The latest closed lot's result, read from its results record (one entry per winner), or None."""
    entries = world.records_store.get(f"{name}_results") or []
    if not entries:
        return None
    number = entries[-1]["lot"]
    rows = [entry for entry in entries if entry["lot"] == number]
    winners = [row["winner"] for row in rows if row["winner"]]
    return {"lot": number, "winner": winners[0] if winners else "", "winners": winners, "price": rows[0]["price"],
            "qty": sum(int(row["qty"] or 0) for row in rows), "note": rows[0]["note"]}


@function("auction_text(name, viewer?)", "The open lot as one plain sentence (what is sold, prices, your bids).", min_args=1, max_args=2)
def _text_function(call: Call) -> str:
    name = _auction(call)
    return describe(call.scope.world, name, call.scope.world.entity(call.arg(1)) if len(call) > 1 else None)


@function("auction_ok(name)", "True while an auction's escrow matches its open bids and every item is held once.", min_args=1, max_args=1)
def _ok_function(call: Call) -> bool:
    return not audit(call.scope.world, _auction(call))
