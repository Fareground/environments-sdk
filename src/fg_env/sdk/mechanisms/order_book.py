"""A price-time priority limit order book: native matching, reservations and settlement.

The native engine of the ``market`` family's ``order_book`` mode. State of a book named ``acme`` lives
in world props (``acme_bids``, ``acme_asks`` sorted best first, ``acme_last``, ``acme_bar`` …) and trader props (``acme_shares``, ``acme_reserved_cash``,
``acme_reserved_shares`` …); the trade tape and per-round OHLCV bars are records. Every change
goes through the world's journaled API and every value moved is a conserved :func:`~.ledger.move`,
so a failed order rolls back completely and cash + shares are conserved exactly.

Rules:

* Prices snap to the tick (buys down, sells up: never worse than asked); quantities snap down to
  the lot.
* An incoming order trades against the best opposite price, oldest first at that price,
  possibly in part. A limit order's remainder rests; a market order walks at most ``collar_pct``
  from the touch and its remainder is cancelled.
* A resting buy reserves ``qty × price × (1 + maker fee)`` cash, a resting sell its shares. A
  fill against a resting order charges the maker fee to its owner and the taker fee to the
  aggressor; fees go to the book's fee account ``<name>_fees``.
* Self-trade prevention: an order never trades with its owner's resting orders on the other side;
  those are cancelled (reservations returned) as the order reaches them.
* Circuit breaker: a trade printing more than ``halt_pct`` from the round's reference price halts
  the book for the rest of the round and ``halt_rounds`` more; the aggressor's remainder is cancelled.
"""
from __future__ import annotations

import bisect
import math
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, function
from ..registry import family_action, uses_of
from ..world import Abort
from ._common import ToolsSetting, tools_field
from .common import config_of, entity_of, fmt, lot_floor, number
from .ledger import EPS, Account, balance, clean, move

__all__ = ["OrderBookConfig", "CrowdSpec", "STRATEGIES", "book_config", "place", "cancel", "cancel_all",
           "open_round", "close_round", "quote", "depth", "account", "audit", "props_for"]

KEY = "market.order_book"
STRATEGIES = ("market_maker", "momentum", "mean_reversion", "fundamentalist", "noise")
#: Closes kept for coded strategies (the full history is in the metric series and the bars record).
CLOSES_WINDOW = 256


class CrowdSpec(BaseModel):
    """A group of coded traders generated for the book."""

    model_config = ConfigDict(extra="forbid")

    count: Union[int, str] = Field(..., description="How many (number or expression).")
    cash: Union[float, str] = Field(0, description="Starting cash each (number or expression).")
    shares: Union[float, str] = Field(0, description="Starting shares each (number or expression).")
    params: Dict[str, float] = Field({}, description="Strategy parameter overrides (see the guide).")


class OrderBookConfig(BaseModel):
    """One instrument traded on a continuous limit order book."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Agent type that trades (subtypes included).")
    start_price: Union[float, str] = Field(..., description="Opening reference price (number or expression).")
    currency: str = Field("cash", description="Trader property holding money (added with 0 if the type lacks it).")
    instrument: str = Field("", description="Display name of the instrument (default: the book's name).")
    tick_size: float = Field(0.01, gt=0, description="Minimum price increment.")
    lot_size: float = Field(1, gt=0, description="Minimum quantity; orders are whole multiples of it.")
    maker_fee_bps: float = Field(0, ge=0, le=1000, description="Fee on fills of resting orders, in basis points of notional.")
    taker_fee_bps: float = Field(0, ge=0, le=1000, description="Fee on fills of incoming orders, in basis points.")
    collar_pct: float = Field(0.05, gt=0, le=1, description="A market order never trades further than this from the touch.")
    price_band_pct: float = Field(0.5, gt=0, le=10, description="Limit prices must be within this fraction of the last price.")
    halt_pct: Optional[float] = Field(None, gt=0, le=1, description="Circuit breaker: halt when a trade moves this far from the round's reference price.")
    halt_rounds: int = Field(1, ge=0, description="Extra rounds a halt lasts after the round it trips.")
    short_limit: float = Field(0, ge=0, description="How far below zero a trader's shares may go (0 = no short selling).")
    order_ttl: Optional[int] = Field(None, ge=1, description="Resting orders expire after this many rounds.")
    max_orders: int = Field(20, ge=1, description="Resting orders one trader may have.")
    depth_levels: int = Field(5, ge=1, le=50, description="Price levels per side shown in the book view.")
    tape: int = Field(50, ge=1, description="Recent trades kept in the <name>_tape record.")
    volatility: float = Field(0.02, gt=0, description="Per-round return volatility coded strategies assume before the tape shows one.")
    base_qty: Optional[float] = Field(None, gt=0, description="Coded strategies' unit of order size (default 10 lots).")
    fair_value: Optional[str] = Field(None, description="Expression for the true value fundamentalists estimate (default: the start price).")
    crowd: Dict[Literal["market_maker", "momentum", "mean_reversion", "fundamentalist", "noise"], CrowdSpec] = Field(
        {}, description="Coded traders by strategy: {market_maker: {count, cash, shares, params}}.")
    stage: Optional[str] = Field(None, description="Trade during this declared stage; default: a sequential stage named after the book.")
    max_actions: int = Field(4, ge=1, description="Actions per turn in the generated stage.")
    conserve: bool = Field(True, description="Declare invariants that cash and shares are conserved and reserves match the book.")
    tools: ToolsSetting = tools_field()


def book_config(world: Any, name: Any) -> OrderBookConfig:
    return config_of(world, name, KEY, OrderBookConfig)


def props_for(name: str) -> Dict[str, str]:
    """Trader property names of the book."""
    return {"shares": f"{name}_shares", "reserved_cash": f"{name}_reserved_cash",
            "reserved_shares": f"{name}_reserved_shares", "fees_paid": f"{name}_fees_paid",
            "start_value": f"{name}_start_value", "strategy": f"{name}_strategy", "algo": f"{name}_algo"}


# ---------------------------------------------------------------------------
# Reading state
# ---------------------------------------------------------------------------


def _ticks(price: float, tick: float) -> int:
    return int(round(price / tick))


def _book(world: Any, name: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    return [dict(o) for o in world.props.get(f"{name}_bids") or []], [dict(o) for o in world.props.get(f"{name}_asks") or []]


def _fees(cfg: OrderBookConfig) -> Tuple[float, float]:
    return cfg.maker_fee_bps / 1e4, cfg.taker_fee_bps / 1e4


def quote(world: Any, name: str) -> Dict[str, Any]:
    """Top of book and round statistics as one map."""
    cfg = book_config(world, name)
    bids, asks = world.props.get(f"{name}_bids") or [], world.props.get(f"{name}_asks") or []
    bid = bids[0]["price"] if bids else None
    ask = asks[0]["price"] if asks else None
    last = float(world.props.get(f"{name}_last") or 0)
    bar = world.props.get(f"{name}_bar") or {}
    volume = float(world.props.get(f"{name}_volume") or 0)
    notional = float(world.props.get(f"{name}_notional") or 0)
    halted = bool(world.props.get(f"{name}_halted"))
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2
    else:
        mid = bid if bid is not None else ask if ask is not None else last
    return {
        "instrument": cfg.instrument or name, "last": last, "bid": bid, "ask": ask, "mid": mid,
        "spread": round(ask - bid, 10) if bid is not None and ask is not None else None,
        "bid_qty": sum(o["qty"] for o in bids if bid is not None and o["price"] == bid),
        "ask_qty": sum(o["qty"] for o in asks if ask is not None and o["price"] == ask),
        "ref": float(world.props.get(f"{name}_ref") or last), "halted": halted,
        "halt_until": world.props.get(f"{name}_halt_until") if halted else None,
        "open": bar.get("open", last), "high": bar.get("high", last), "low": bar.get("low", last),
        "round_volume": bar.get("volume", 0), "round_trades": bar.get("trades", 0),
        "volume": volume, "vwap": notional / volume if volume > 0 else None,
        "trades": world.props.get(f"{name}_trades") or 0, "fees": world.props.get(f"{name}_fees") or 0,
        "halts": world.props.get(f"{name}_halts") or 0, "orders": len(bids) + len(asks),
        "tick": cfg.tick_size, "lot": cfg.lot_size, "maker_fee_bps": cfg.maker_fee_bps, "taker_fee_bps": cfg.taker_fee_bps,
        "band_low": _band(cfg, last)[0], "band_high": _band(cfg, last)[1],
    }


def _band(cfg: OrderBookConfig, last: float) -> Tuple[float, float]:
    low = math.ceil(last * (1 - cfg.price_band_pct) / cfg.tick_size - 1e-9) * cfg.tick_size
    high = math.floor(last * (1 + cfg.price_band_pct) / cfg.tick_size + 1e-9) * cfg.tick_size
    return round(max(cfg.tick_size, low), 10), round(high, 10)


def depth(world: Any, name: str, levels: Optional[int] = None, viewer: Optional[Entity] = None) -> List[Dict[str, Any]]:
    """Price levels as a ladder: asks from the Nth best down to the best, then bids best first."""
    cfg = book_config(world, name)
    n = levels or cfg.depth_levels
    out: Dict[str, List[Dict[str, Any]]] = {}
    for side, orders in (("ask", world.props.get(f"{name}_asks") or []), ("bid", world.props.get(f"{name}_bids") or [])):
        rows: List[Dict[str, Any]] = []
        for order in orders:
            if rows and rows[-1]["price"] == order["price"]:
                row = rows[-1]
            elif len(rows) == n:
                break
            else:
                row = {"side": side, "price": order["price"], "qty": 0, "orders": 0, "mine": 0}
                rows.append(row)
            row["qty"] = clean(row["qty"] + order["qty"])
            row["orders"] += 1
            if viewer is not None and order["owner"] == viewer.id:
                row["mine"] = clean(row["mine"] + order["qty"])
        out[side] = rows
    return list(reversed(out["ask"])) + out["bid"]


def _num(entity: Entity, prop: str) -> float:
    value: Any = entity.properties.get(prop)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def account(world: Any, name: str, trader: Entity) -> Dict[str, Any]:
    cfg = book_config(world, name)
    p = props_for(name)
    cash = balance(world, Account(trader, cfg.currency))
    shares = balance(world, Account(trader, p["shares"]))
    reserved_cash = balance(world, Account(trader, p["reserved_cash"]))
    reserved_shares = balance(world, Account(trader, p["reserved_shares"]))
    last = float(world.props.get(f"{name}_last") or 0)
    position = clean(shares + reserved_shares)
    equity = cash + reserved_cash + position * last
    maker, taker = _fees(cfg)
    low = _band(cfg, last)[0]
    orders = [o for side in ("bids", "asks") for o in world.props.get(f"{name}_{side}") or [] if o["owner"] == trader.id]
    return {
        "cash": cash, "shares": shares, "reserved_cash": reserved_cash, "reserved_shares": reserved_shares,
        "position": position, "equity": clean(equity), "pnl": clean(equity - _num(trader, p["start_value"])),
        "fees_paid": trader.properties.get(p["fees_paid"]) or 0, "orders": len(orders),
        "max_buy": lot_floor(cash / (low * (1 + max(maker, taker))), cfg.lot_size) if low > 0 else 0,
        "max_sell": lot_floor(max(0.0, shares + cfg.short_limit), cfg.lot_size),
    }


# ---------------------------------------------------------------------------
# Changing state
# ---------------------------------------------------------------------------


class _Fill:
    __slots__ = ("qty", "price")

    def __init__(self, qty: float, price: float):
        self.qty, self.price = qty, price


def _receipt(world: Any, name: str, text: str) -> str:
    world.set_world(f"{name}_receipt", text)
    return text


def _release(world: Any, cfg: OrderBookConfig, name: str, order: Mapping[str, Any]) -> None:
    """Return a resting order's reservation to its owner."""
    owner = world.entity(order["owner"])
    if owner is None:
        raise RunError(f"order {order['id']} belongs to unknown trader {order['owner']!r}", f"mechanisms.{name}")
    p = props_for(name)
    if order["side"] == "buy":
        maker, _ = _fees(cfg)
        move(world, Account(owner, p["reserved_cash"]), Account(owner, cfg.currency), order["qty"] * order["price"] * (1 + maker),
             what="reserved cash")
    else:
        move(world, Account(owner, p["reserved_shares"]), Account(owner, p["shares"]), order["qty"], what="reserved shares",
             floor=0.0)


def _insert(orders: List[Dict[str, Any]], order: Dict[str, Any], side: str) -> None:
    keys = [(-o["price"] if side == "buy" else o["price"], o["seq"]) for o in orders]
    orders.insert(bisect.bisect(keys, (-order["price"] if side == "buy" else order["price"], order["seq"])), order)


def place(world: Any, name: str, trader: Entity, side: str, qty: Any, price: Any = None) -> str:
    """Submit a buy or sell (limit when ``price`` is given, market otherwise). Returns the receipt text;
    refuses with :class:`Abort` (nothing changes) when the order is not possible."""
    cfg = book_config(world, name)
    p = props_for(name)
    unit = cfg.instrument or name
    if side not in ("buy", "sell"):
        raise Abort(f"side must be buy or sell, not {side!r}.")
    if world.props.get(f"{name}_halted"):
        raise Abort(f"Trading in {unit} is halted by the circuit breaker until round "
                    f"{world.props.get(f'{name}_halt_until')} ends; you can only cancel orders.")
    if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty):
        raise Abort(f"qty must be a number, not {qty!r}.")
    size = lot_floor(float(qty), cfg.lot_size)
    if size <= 0:
        raise Abort(f"The minimum order is {fmt(cfg.lot_size, 6)} shares (one lot).")
    maker, taker = _fees(cfg)
    last = float(world.props.get(f"{name}_last") or 0)
    bids, asks = _book(world, name)
    own, opposite = (bids, asks) if side == "buy" else (asks, bids)
    limit_t: Optional[int] = None
    if price is not None:
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise Abort(f"price must be a positive number, not {price!r}.")
        raw_t = price / cfg.tick_size
        limit_t = int(math.floor(raw_t + 1e-9)) if side == "buy" else int(math.ceil(raw_t - 1e-9))
        low, high = _band(cfg, last)
        if not _ticks(low, cfg.tick_size) <= limit_t <= _ticks(high, cfg.tick_size):
            raise Abort(f"Limit prices must be between {fmt(low, 4)} and {fmt(high, 4)} "
                        f"(within {cfg.price_band_pct:.0%} of the last price {fmt(last, 4)}).")
        if sum(1 for o in own if o["owner"] == trader.id) >= cfg.max_orders:
            raise Abort(f"You already have {cfg.max_orders} resting orders in {unit}; cancel one first.")
    elif not opposite:
        raise Abort(f"There are no {'sell' if side == 'buy' else 'buy'} orders to trade with; place a limit order instead.")
    else:
        touch = opposite[0]["price"]
        edge = touch * (1 + cfg.collar_pct) if side == "buy" else touch * (1 - cfg.collar_pct)
        limit_t = int(math.floor(edge / cfg.tick_size + 1e-9)) if side == "buy" else int(math.ceil(edge / cfg.tick_size - 1e-9))
    limit_price = round(limit_t * cfg.tick_size, 10)
    cash = Account(trader, cfg.currency)
    shares = Account(trader, p["shares"])
    if side == "buy" and price is not None:
        need = size * limit_price * (1 + max(maker, taker))
        have = balance(world, cash)
        if need > have + EPS:
            most = lot_floor(have / (limit_price * (1 + max(maker, taker))), cfg.lot_size)
            raise Abort(f"Not enough free cash: {fmt(size, 6)} @ {fmt(limit_price, 4)} with fees needs {fmt(need)}, you have "
                        f"{fmt(have)}. Buy at most {fmt(most, 6)} at this price, or cancel resting buys.")
    if side == "sell":
        free = balance(world, shares)
        if free - size < -cfg.short_limit - EPS:
            most = lot_floor(max(0.0, free + cfg.short_limit), cfg.lot_size)
            short = f" (short selling up to {fmt(cfg.short_limit, 6)} allowed)" if cfg.short_limit else ""
            raise Abort(f"Not enough free shares: you can sell at most {fmt(most, 6)}{short}; resting sells reserve shares.")
    fees = Account(None, f"{name}_fees")
    fills: List[_Fill] = []
    left, spent, paid, prevented = size, 0.0, 0.0, 0
    ref = float(world.props.get(f"{name}_ref") or last)
    tripped = False
    while left > EPS and opposite:
        best = opposite[0]
        best_t = _ticks(best["price"], cfg.tick_size)
        if (side == "buy" and best_t > limit_t) or (side == "sell" and best_t < limit_t):
            break
        if best["owner"] == trader.id:
            _release(world, cfg, name, best)
            opposite.pop(0)
            prevented += 1
            continue
        px = best["price"]
        q = min(left, best["qty"])
        if side == "buy" and price is None:
            q = min(q, lot_floor(balance(world, cash) / (px * (1 + taker)), cfg.lot_size))
            if q <= 0:
                break
        maker_entity = entity_of(world, best["owner"], f"mechanisms.{name}", "a trader")
        notional = q * px
        if side == "buy":
            move(world, cash, Account(maker_entity, cfg.currency), notional, what="cash")
            move(world, Account(maker_entity, p["reserved_shares"]), shares, q, what="reserved shares")
            move(world, cash, fees, notional * taker, what="cash")
            move(world, Account(maker_entity, cfg.currency), fees, notional * maker, what="cash")
        else:
            move(world, Account(maker_entity, p["reserved_cash"]), cash, notional, what="reserved cash")
            move(world, Account(maker_entity, p["reserved_cash"]), fees, notional * maker, what="reserved cash")
            move(world, shares, Account(maker_entity, p["shares"]), q, what="shares", floor=-cfg.short_limit)
            move(world, cash, fees, notional * taker, what="cash")
        world.set_prop(trader, p["fees_paid"], clean(_num(trader, p["fees_paid"]) + notional * taker))
        world.set_prop(maker_entity, p["fees_paid"],
                       clean(_num(maker_entity, p["fees_paid"]) + notional * maker))
        best["qty"] = clean(best["qty"] - q)
        if best["qty"] <= EPS:
            opposite.pop(0)
        left = clean(left - q)
        spent += notional
        paid += notional * taker
        fills.append(_Fill(q, px))
        world.post(f"{name}_tape", {"price": px, "qty": q, "aggressor": side}, None, None, f"mechanisms.{name}")
        if cfg.halt_pct is not None and abs(px - ref) > cfg.halt_pct * ref + 1e-12:
            tripped = True
            break
    rested = 0.0
    if left > EPS and price is not None and not tripped:
        if side == "buy":
            move(world, cash, Account(trader, p["reserved_cash"]), left * limit_price * (1 + maker), what="cash")
        else:
            move(world, shares, Account(trader, p["reserved_shares"]), left, what="shares", floor=-cfg.short_limit)
        seq = int(world.props.get(f"{name}_seq") or 0) + 1
        world.set_world(f"{name}_seq", seq)
        _insert(own, {"id": f"{name}_{seq}", "owner": trader.id, "side": side, "price": limit_price, "qty": left,
                      "seq": seq, "round": world.round}, side)
        rested = left
    world.set_world(f"{name}_bids", bids)
    world.set_world(f"{name}_asks", asks)
    filled = clean(sum(f.qty for f in fills))
    if fills:
        _record_fills(world, name, fills)
    if tripped:
        _trip(world, cfg, name, fills[-1].price, ref)
    parts = [f"{side.upper()} {fmt(size, 6)} {unit} {'@ ' + fmt(limit_price, 4) if price is not None else 'at market'}: "
             f"filled {fmt(filled, 6)}" + (f" at avg {fmt(spent / filled, 4)}" if filled else "")]
    if paid:
        parts.append(f"fees {fmt(paid, 4)}")
    if rested:
        parts.append(f"resting {fmt(rested, 6)} as order {name}_{world.props.get(f'{name}_seq')}")
    cancelled = clean(size - filled - rested)
    if cancelled > EPS:
        why = "the circuit breaker halted trading" if tripped else (
            "not enough cash" if side == "buy" and price is None and opposite and _ticks(opposite[0]["price"], cfg.tick_size) <= limit_t
            else f"no more liquidity within the {cfg.collar_pct:.0%} collar" if price is None else "")
        parts.append(f"{fmt(cancelled, 6)} cancelled" + (f" ({why})" if why else ""))
    if prevented:
        parts.append(f"{prevented} of your own opposite order(s) cancelled instead of trading with you")
    return _receipt(world, name, ", ".join(parts) + ".")


def _record_fills(world: Any, name: str, fills: List[_Fill]) -> None:
    bar = dict(world.props.get(f"{name}_bar") or {})
    volume = sum(f.qty for f in fills)
    notional = sum(f.qty * f.price for f in fills)
    prices = [f.price for f in fills]
    last = float(world.props.get(f"{name}_last") or prices[0])
    bar.setdefault("open", last)
    bar["high"] = max([bar.get("high", prices[0])] + prices)
    bar["low"] = min([bar.get("low", prices[0])] + prices)
    bar["close"] = prices[-1]
    bar["volume"] = clean(float(bar.get("volume", 0)) + volume)
    bar["notional"] = clean(float(bar.get("notional", 0)) + notional)
    bar["trades"] = int(bar.get("trades", 0)) + len(fills)
    world.set_world(f"{name}_bar", bar)
    world.set_world(f"{name}_last", prices[-1])
    world.set_world(f"{name}_volume", clean(float(world.props.get(f"{name}_volume") or 0) + volume))
    world.set_world(f"{name}_notional", clean(float(world.props.get(f"{name}_notional") or 0) + notional))
    world.set_world(f"{name}_trades", int(world.props.get(f"{name}_trades") or 0) + len(fills))


def _trip(world: Any, cfg: OrderBookConfig, name: str, px: float, ref: float) -> None:
    until = world.round + cfg.halt_rounds
    world.set_world(f"{name}_halted", True)
    world.set_world(f"{name}_halt_until", until)
    world.set_world(f"{name}_halts", int(world.props.get(f"{name}_halts") or 0) + 1)
    more = f" and {cfg.halt_rounds} more round(s)" if cfg.halt_rounds else ""
    world.emit(f"{name}_halt", f"CIRCUIT BREAKER on {cfg.instrument or name}: a trade at {fmt(px, 4)} moved more than "
                               f"{cfg.halt_pct:.0%} from the reference {fmt(ref, 4)}. Trading is halted for the rest of "
                               f"this round{more}.", data={"mechanism": KEY, "price": px, "until": until})


def cancel(world: Any, name: str, trader: Entity, order_id: Any) -> str:
    cfg = book_config(world, name)
    bids, asks = _book(world, name)
    for orders, side in ((bids, "bids"), (asks, "asks")):
        for index, order in enumerate(orders):
            if order["id"] == order_id and order["owner"] == trader.id:
                _release(world, cfg, name, order)
                orders.pop(index)
                world.set_world(f"{name}_{side}", orders)
                return _receipt(world, name, f"Cancelled {order['side']} {fmt(order['qty'], 6)} @ {fmt(order['price'], 4)}.")
    raise Abort(f"You have no resting order {order_id!r} in {cfg.instrument or name}.")


def cancel_all(world: Any, name: str, trader: Entity) -> str:
    cfg = book_config(world, name)
    bids, asks = _book(world, name)
    count = 0
    for orders, side in ((bids, "bids"), (asks, "asks")):
        keep = []
        for order in orders:
            if order["owner"] == trader.id:
                _release(world, cfg, name, order)
                count += 1
            else:
                keep.append(order)
        if len(keep) != len(orders):
            world.set_world(f"{name}_{side}", keep)
    return _receipt(world, name, f"Cancelled {count} order(s).")


def _siblings(world: Any, name: str, cfg: OrderBookConfig) -> List[str]:
    """Books sharing this book's traders and cash property (their reserves and fees hold the same cash)."""
    return [n for n, raw in uses_of(world.contract.mechanisms, KEY).items()
            if raw.get("who") == cfg.who and raw.get("currency", "cash") == cfg.currency]


def _cash_total(world: Any, name: str, cfg: OrderBookConfig) -> float:
    reserves = [f"{book}_reserved_cash" for book in _siblings(world, name, cfg)]
    total = 0.0
    for trader in world.entities_of(cfg.who):
        total += _num(trader, cfg.currency) + sum(_num(trader, prop) for prop in reserves)
    return total + sum(float(world.props.get(f"{prop[:-len('_reserved_cash')]}_fees") or 0) for prop in reserves)


def _share_total(world: Any, name: str, cfg: OrderBookConfig) -> float:
    p = props_for(name)
    return sum(_num(t, p["shares"]) + _num(t, p["reserved_shares"]) for t in world.entities_of(cfg.who))


def rebase(world: Any, name: str) -> None:
    """Take the current cash and share totals as the supply the invariants conserve (at the start, or after an
    author's own effects add or remove cash or shares)."""
    cfg = book_config(world, name)
    world.set_world(f"{name}_supply", {"cash": clean(_cash_total(world, name, cfg)), "shares": clean(_share_total(world, name, cfg))})


def open_round(world: Any, name: str) -> None:
    """Start of a round: baseline supply and P&L (first round), resume after a halt, expire old orders, reset the bar."""
    cfg = book_config(world, name)
    p = props_for(name)
    last = float(world.props.get(f"{name}_last") or 0)
    if not world.props.get(f"{name}_supply"):
        rebase(world, name)
        for trader in world.entities_of(cfg.who):
            world.set_prop(trader, p["start_value"], account(world, name, trader)["equity"])
    if world.props.get(f"{name}_halted") and world.round > int(world.props.get(f"{name}_halt_until") or 0):
        world.set_world(f"{name}_halted", False)
        world.emit(f"{name}_resume", f"Trading in {cfg.instrument or name} resumes after the circuit-breaker halt.",
                   data={"mechanism": KEY})
    if cfg.order_ttl is not None:
        for side in ("bids", "asks"):
            orders = world.props.get(f"{name}_{side}") or []
            keep = []
            for order in orders:
                if world.round - order["round"] >= cfg.order_ttl:
                    _release(world, cfg, name, order)
                    world.emit(f"{name}_expired", f"Your {order['side']} order {order['id']} for {fmt(order['qty'], 6)} @ "
                                                  f"{fmt(order['price'], 4)} expired; its reserve was released.",
                               to=(order["owner"],), data={"mechanism": KEY, "order": order["id"]})
                else:
                    keep.append(dict(order))
            if len(keep) != len(orders):
                world.set_world(f"{name}_{side}", keep)
    world.set_world(f"{name}_ref", last)
    world.set_world(f"{name}_bar", {"open": last, "high": last, "low": last, "close": last, "volume": 0, "notional": 0,
                                    "trades": 0})


def close_round(world: Any, name: str) -> None:
    """End of a round: record the OHLCV bar and extend the closes window."""
    book_config(world, name)
    bar = dict(world.props.get(f"{name}_bar") or {})
    last = float(world.props.get(f"{name}_last") or 0)
    volume = float(bar.get("volume", 0))
    world.post(f"{name}_bars", {"open": bar.get("open", last), "high": bar.get("high", last), "low": bar.get("low", last),
                                "close": last, "volume": volume,
                                "vwap": bar.get("notional", 0) / volume if volume > 0 else last,
                                "trades": bar.get("trades", 0)}, None, None, f"mechanisms.{name}")
    closes = list(world.props.get(f"{name}_closes") or []) + [last]
    world.set_world(f"{name}_closes", closes[-CLOSES_WINDOW:])


def audit(world: Any, name: str) -> List[str]:
    """Every accounting rule the book must keep; empty when all hold."""
    cfg = book_config(world, name)
    p = props_for(name)
    maker, _ = _fees(cfg)
    problems: List[str] = []
    bids, asks = world.props.get(f"{name}_bids") or [], world.props.get(f"{name}_asks") or []
    reserved_cash: Dict[str, float] = {}
    reserved_shares: Dict[str, float] = {}
    for side, orders in (("buy", bids), ("sell", asks)):
        for order in orders:
            if order["side"] != side or order["qty"] <= 0:
                problems.append(f"order {order['id']} is malformed ({order})")
            if side == "buy":
                reserved_cash[order["owner"]] = reserved_cash.get(order["owner"], 0.0) + order["qty"] * order["price"] * (1 + maker)
            else:
                reserved_shares[order["owner"]] = reserved_shares.get(order["owner"], 0.0) + order["qty"]
    if bids and asks and bids[0]["price"] >= asks[0]["price"]:
        problems.append(f"the book is crossed: bid {bids[0]['price']} ≥ ask {asks[0]['price']}")
    for orders, key in ((bids, lambda o: (-o["price"], o["seq"])), (asks, lambda o: (o["price"], o["seq"]))):
        if [key(o) for o in orders] != sorted(key(o) for o in orders):
            problems.append("orders are out of price-time priority")
    traders = world.entities_of(cfg.who)
    tolerance = 1e-6
    for trader in traders:
        acct = {"cash": _num(trader, cfg.currency), "shares": _num(trader, p["shares"]), "rc": _num(trader, p["reserved_cash"]),
                "rs": _num(trader, p["reserved_shares"])}
        if abs(acct["rc"] - reserved_cash.get(trader.id, 0.0)) > tolerance * max(1.0, acct["rc"]):
            problems.append(f"{trader.id} reserves {acct['rc']} cash but its resting buys need {reserved_cash.get(trader.id, 0.0)}")
        if abs(acct["rs"] - reserved_shares.get(trader.id, 0.0)) > tolerance:
            problems.append(f"{trader.id} reserves {acct['rs']} shares but its resting sells hold {reserved_shares.get(trader.id, 0.0)}")
        if acct["cash"] < -tolerance or acct["rc"] < -tolerance or acct["rs"] < -tolerance:
            problems.append(f"{trader.id} has a negative cash or reserve balance")
        if acct["shares"] < -cfg.short_limit - tolerance:
            problems.append(f"{trader.id} is short {-acct['shares']} shares, beyond the limit {cfg.short_limit}")
    supply = world.props.get(f"{name}_supply") or {}
    if supply:
        cash = _cash_total(world, name, cfg)
        if abs(cash - supply["cash"]) > 1e-4 + 1e-9 * abs(supply["cash"]):
            problems.append(f"cash is not conserved: {cash} now vs {supply['cash']} supplied")
        shares = _share_total(world, name, cfg)
        if abs(shares - supply["shares"]) > 1e-6:
            problems.append(f"shares are not conserved: {shares} now vs {supply['shares']} supplied")
    return problems


# ---------------------------------------------------------------------------
# Expression functions and the effect op
# ---------------------------------------------------------------------------


def _name(call: Call) -> str:
    name = call.arg(0)
    try:
        book_config(call.scope.world, name)
    except RunError as exc:
        raise ExprError(f"${call.name}: {exc.message if hasattr(exc, 'message') else exc}", call.source) from None
    return str(name)


def _trader(call: Call, index: int) -> Entity:
    value = call.arg(index)
    found = call.scope.world.entity(value)
    if found is None:
        raise ExprError(f"${call.name}: expected a trader, got {value!r}", call.source)
    return found


@function("book(name)", "Top of an order book: {last, bid, ask, mid, spread, bid_qty, ask_qty, ref, halted, halt_until, "
          "open, high, low, round_volume, round_trades, volume, vwap, trades, fees, halts, orders, tick, lot, band_low, "
          "band_high}.", min_args=1, max_args=1)
def _book_function(call: Call) -> Dict[str, Any]:
    return quote(call.scope.world, _name(call))


@function("book_depth(name, levels?, viewer?)", "Order book price levels as a ladder (asks high→low, then bids "
          "high→low): [{side, price, qty, orders, mine}]; `mine` is the viewer's own quantity.", min_args=1, max_args=3)
def _depth_function(call: Call) -> List[Dict[str, Any]]:
    name = _name(call)
    levels = call.arg(1)
    if levels is not None and (isinstance(levels, bool) or not isinstance(levels, int) or levels < 1):
        raise ExprError(f"$book_depth: levels must be a whole number ≥ 1, got {levels!r}", call.source)
    return depth(call.scope.world, name, levels, _trader(call, 2) if len(call) > 2 else None)


@function("book_orders(name, trader)", "A trader's resting orders, best price first: [{id, side, price, qty, seq, round}].",
          min_args=2, max_args=2)
def _orders_function(call: Call) -> List[Dict[str, Any]]:
    name, trader = _name(call), _trader(call, 1)
    world: Any = call.scope.world
    return [dict(o) for side in ("bids", "asks") for o in world.props.get(f"{name}_{side}") or [] if o["owner"] == trader.id]


@function("book_account(name, trader)", "A trader's account on a book: {cash, shares, reserved_cash, reserved_shares, "
          "position, equity, pnl, fees_paid, orders, max_buy, max_sell}.", min_args=2, max_args=2)
def _account_function(call: Call) -> Dict[str, Any]:
    return account(call.scope.world, _name(call), _trader(call, 1))


@function("book_ok(name)", "True while the book's accounting holds: cash and shares conserved, reserves equal resting "
          "orders, balances within limits, the book in price-time order and never crossed.", min_args=1, max_args=1)
def _ok_function(call: Call) -> bool:
    return not audit(call.scope.world, _name(call))


#: action → (its keys, the required ones, generated by the mechanism itself, example keys, what it does).
#: `who` is the trader (default $actor); receipts land in $world.<name>_receipt.
_ACTIONS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...], bool, str, str]] = {
    "buy": (("who", "qty", "price"), ("qty",), False, '"qty": 10, "price": 50.5',
            "a limit buy with a price, a market buy without"),
    "sell": (("who", "qty", "price"), ("qty",), False, '"qty": 10', "a limit sell with a price, a market sell without"),
    "cancel": (("who", "order"), ("order",), False, '"order": "$params.order"', "cancel one resting order by id"),
    "cancel_all": (("who",), (), False, "", "cancel every resting order of the trader"),
    "algo": (("who",), (), False, "", "let the trader's coded strategy act once"),
    "rebase": ((), (), False, "", "take current cash and share totals as the supply the invariants conserve"),
    "open": ((), (), True, "", "start a round: expire orders, resume after a halt, reset the bar"),
    "close": ((), (), True, "", "end a round: record the bar"),
}


def _runner(action: str) -> Callable[[Any, Dict[str, Any], Dict[str, Any], str], None]:
    def run(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
        world = runner.world
        name = effect["market"]
        try:
            if action == "open":
                open_round(world, name)
            elif action == "close":
                close_round(world, name)
            elif action == "rebase":
                rebase(world, name)
            else:
                trader = entity_of(world, runner.eval(effect.get("who", "$actor"), vars), f"{where}.who", "a trader")
                if action in ("buy", "sell"):
                    place(world, name, trader, action, runner.eval(effect["qty"], vars), runner.eval(effect.get("price"), vars))
                elif action == "cancel":
                    cancel(world, name, trader, runner.eval(effect["order"], vars))
                elif action == "cancel_all":
                    cancel_all(world, name, trader)
                else:
                    from .traders import run_algo

                    run_algo(world, name, trader)
        except RunError as exc:
            raise RunError(str(exc), where) from None

    return run


def _register_actions() -> None:
    for action, (keys, required, internal, fields, doc) in _ACTIONS.items():
        example = '{"market": "acme", "action": "' + action + '"' + (f", {fields}" if fields else "") + f"}}  ({doc})"
        family_action("market", ("order_book",), action, keys=keys, required=required, internal=internal,
                      example=example, was=("book",))(_runner(action))


_register_actions()


def start_price(world: Any, name: str) -> float:
    return number(world, book_config(world, name).start_price, f"mechanisms.{name}.start_price")
