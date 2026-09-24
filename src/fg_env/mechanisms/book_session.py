"""An order book's rounds and bars: opening and closing each round, the circuit breaker's reference and its
round-end check, and OHLCV bars that span ``bar_rounds`` rounds.

A round opens once and closes once, wherever that happens first: the book's own ``<name>_open``/``<name>_close``
events run after the author's, and an author who needs the closed round (or bar) in an end event runs
``{"market": name, "action": "close"}`` earlier; the book's own close then does nothing that round.
"""
from __future__ import annotations

import math
from typing import Any

from .book_rules import CLOSES_WINDOW, Venue, venue
from .common import fmt, number
from .ledger import clean
from .order_book import KEY, OrderBookConfig, account, book_config, merge_flow, props_for, release, traders, trip

__all__ = ["open_round", "close_round", "start_price"]


def start_price(world: Any, name: str) -> float:
    return number(world, book_config(world, name).start_price, f"mechanisms.{name}.start_price")


def open_round(world: Any, name: str) -> None:
    """Start of a round: baseline P&L (first round) or a step of the default fair value, resume after a
    halt, expire old orders, start a bar when one is due, set the breaker's reference and reset the round's
    statistics."""
    opened = world.props.get(f"{name}_opened")
    if opened == world.round:
        return
    world.set_world(f"{name}_opened", world.round)
    cfg = book_config(world, name)
    v = venue(world, name)
    p = props_for(name)
    last = float(world.props.get(f"{name}_last") or 0)
    if not opened:  # the first round: every trader's P&L starts here
        for trader in traders(world, name, cfg):
            world.set_prop(trader, p["start_value"], account(world, name, trader)["equity"])
    elif cfg.fair_value is None:  # the value starts at the start price and walks from the second round
        _walk_value(world, name, cfg)
    if world.props.get(f"{name}_halted") and world.round > int(world.props.get(f"{name}_halt_until") or 0):
        world.set_world(f"{name}_halted", False)
        world.emit(f"{name}_resume", f"Trading in {cfg.instrument or name} resumes after the circuit-breaker halt.",
                   data={"mechanism": KEY})
    if v.order_ttl is not None:
        _expire(world, name, cfg, v)
    current = world.props.get(f"{name}_current_bar") or {}
    starts_bar = "open" not in current
    if starts_bar:
        world.set_world(f"{name}_current_bar", {"open": last, "high": last, "low": last, "volume": 0, "notional": 0,
                                                "trades": 0, "halted": bool(current.get("halted")), "flow": {}})
    if cfg.halt_reference == "round_open" or (cfg.halt_reference == "bar_open" and starts_bar):
        world.set_world(f"{name}_ref", last)
    elif cfg.halt_reference == "rolling":
        closes = world.props.get(f"{name}_closes") or []
        back = v.halt_window or 1
        world.set_world(f"{name}_ref", closes[-back] if len(closes) >= back else closes[0] if closes else last)
    world.set_world(f"{name}_bar", {"open": last, "high": last, "low": last, "close": last, "volume": 0, "notional": 0,
                                    "trades": 0})


def _walk_value(world: Any, name: str, cfg: OrderBookConfig) -> None:
    """The default fair value: a driftless random walk at the book's per-round ``volatility``, so fundamentalists
    anchor to a value that moves like a real one instead of pinning the price to where it started."""
    sigma = number(world, cfg.volatility, f"mechanisms.{name}.volatility")
    value = float(world.props.get(f"{name}_value") or start_price(world, name))
    world.set_world(f"{name}_value", value * math.exp(world.rng.gauss(0.0, sigma) - sigma * sigma / 2))


def _expire(world: Any, name: str, cfg: OrderBookConfig, v: Venue) -> None:
    for side in ("bids", "asks"):
        orders = world.props.get(f"{name}_{side}") or []
        keep = []
        for order in orders:
            if world.round - order["round"] >= v.order_ttl:  # type: ignore[operator]
                release(world, cfg, v, name, order)
                world.emit(f"{name}_expired", f"Your {order['side']} order {order['id']} for {fmt(order['qty'], 6)} @ "
                                              f"{fmt(order['price'], 4)} expired; its reserve was released.",
                           to=(order["owner"],), data={"mechanism": KEY, "order": order["id"]})
            else:
                keep.append(order)
        if len(keep) != len(orders):
            world.set_world(f"{name}_{side}", keep, trusted=True)


def close_round(world: Any, name: str) -> None:
    """End of a round: the round-end breaker check, the closes window and last round's flow, and the bar — folded
    into the bar in progress, and recorded in ``<name>_bars`` when its last round (or the run's) closes."""
    if world.props.get(f"{name}_closed") == world.round:
        return
    world.set_world(f"{name}_closed", world.round)
    cfg = book_config(world, name)
    v = venue(world, name)
    last = float(world.props.get(f"{name}_last") or 0)
    if cfg.halt_check == "round_end" and v.halt_pct is not None and not world.props.get(f"{name}_halted"):
        _check_breaker(world, name, v, last)
    round_bar: dict[str, Any] = world.props.get(f"{name}_bar") or {}
    closes: list[float] = list(world.props.get(f"{name}_closes") or []) + [last]
    world.set_world(f"{name}_closes", closes[-CLOSES_WINDOW:])
    world.set_world(f"{name}_flow", round_bar.get("flow") or {})
    current: dict[str, Any] = world.props.get(f"{name}_current_bar") or {}
    volume = clean(float(current.get("volume", 0)) + float(round_bar.get("volume", 0)))
    notional = clean(float(current.get("notional", 0)) + float(round_bar.get("notional", 0)))
    bar = {"open": current.get("open", round_bar.get("open", last)),
           "high": max(current.get("high", last), round_bar.get("high", last)),
           "low": min(current.get("low", last), round_bar.get("low", last)),
           "volume": volume, "notional": notional,
           "trades": int(current.get("trades", 0)) + int(round_bar.get("trades", 0)),
           "halted": bool(current.get("halted")),
           "flow": merge_flow(current.get("flow") or {}, round_bar.get("flow") or {})}
    if world.round % v.bar_rounds and world.round != world.rounds:
        world.set_world(f"{name}_current_bar", bar, trusted=True)
        return
    world.post(f"{name}_bars", {"bar": (world.round - 1) // v.bar_rounds + 1, "open": bar["open"], "high": bar["high"],
                                "low": bar["low"], "close": last, "volume": volume,
                                "vwap": notional / volume if volume > 0 else last, "trades": bar["trades"],
                                "halted": bar["halted"], "flow": bar["flow"]}, None, None, f"mechanisms.{name}")
    world.set_world(f"{name}_current_bar", {})


def _check_breaker(world: Any, name: str, v: Venue, last: float) -> None:
    """Halt when the mid has moved at least ``halt_pct`` from the reference price."""
    bids, asks = world.props.get(f"{name}_bids") or [], world.props.get(f"{name}_asks") or []
    if bids and asks:
        mid = (bids[0]["price"] + asks[0]["price"]) / 2
    else:
        mid = bids[0]["price"] if bids else asks[0]["price"] if asks else last
    ref = float(world.props.get(f"{name}_ref") or last)
    if ref > 0 and abs(mid / ref - 1) >= v.halt_pct:  # type: ignore[operator]
        trip(world, name, mid, ref, "the mid")
