"""A price-time priority limit order book: native matching, reservations and settlement.

The native engine of the ``market`` family's ``order_book`` mode. State of a book named ``acme`` lives in world props
(``acme_bids``, ``acme_asks`` sorted best first, ``acme_last``, ``acme_bar`` …) and trader props (``acme_shares``,
``acme_reserved_cash``, ``acme_reserved_shares`` …); the trade tape and OHLCV bars are records. Every change goes
through the world's journaled API and every value moved is a conserved :func:`~.ledger.move`, so a failed order rolls
back completely and the book never creates or destroys cash or shares. The venue's numbers (tick, lot, fees, limits) are
resolved once into ``acme_rules`` (:mod:`.book_rules`); rounds and bars open and close in :mod:`.book_session`.

Rules:

* Prices snap to the tick (buys down, sells up: never worse than asked); quantities snap down to
  the lot.
* An incoming order trades against the best opposite price, oldest first at that price,
  possibly in part. A limit order's remainder rests; a market order walks at most ``collar_pct``
  from the touch and its remainder is cancelled.
* A resting buy reserves ``qty × price × (1 + maker fee)`` cash, a resting sell its shares. A
  fill against a resting order charges the maker fee to its owner and the taker fee to the
  aggressor; fees go to the book's fee account ``<name>_fees``. A negative maker fee is a rebate
  the fee account pays the resting order's owner out of the fill's taker fee.
* Self-trade prevention: an order never trades with its owner's resting orders on the other side;
  those are cancelled (reservations returned) as the order reaches them.
* Circuit breaker (``halt_check: trade``): a trade printing more than ``halt_pct`` from the reference price halts
  the book and the aggressor's remainder is cancelled; ``halt_check: round_end`` checks the mid when the round closes.
"""
from __future__ import annotations

import bisect
import math
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..errors import RunError
from ..expr import is_expr
from ..expr.objects import Entity
from ..registry import mechanism_config
from ..world.abort import Abort
from ..world.props import prop_type
from ._common import entity_of, fmt, lot_floor, pct
from .book_rules import Venue, venue
from .expressions import EachCrowd, Expr
from .ledger import EPS, Account, balance, clean, move

__all__ = ["OrderBookConfig", "CrowdSpec", "STRATEGIES", "book_config", "place", "cancel", "cancel_all", "quote",
           "depth", "account", "audit", "crowd_type", "props_for", "short_room", "top", "traders", "trip", "bar_end"]

KEY = "market.order_book"
STRATEGIES = ("market_maker", "momentum", "mean_reversion", "fundamentalist", "noise", "passive")

_Positive = Annotated[float, Field(gt=0)]
_Share = Annotated[float, Field(gt=0, le=1)]
_Fee = Annotated[float, Field(ge=0, le=1000)]
_MakerFee = Annotated[float, Field(ge=-1000, le=1000)]
_NonNegative = Annotated[float, Field(ge=0)]
_Count = Annotated[int, Field(ge=1)]
_EXPR = " (number or expression over $inputs, resolved when the world is built)"


class CrowdSpec(BaseModel):
    """A group of coded traders generated for the book."""

    model_config = ConfigDict(extra="forbid")

    count: int | str = Field(..., description="How many (number or expression).")
    cash: float | str = Field(0.0, description="Starting cash each (number or expression).")
    shares: float | str = Field(0.0, description="Starting shares each (number or expression).")
    params: dict[str, float | str] = Field({},
                                           description="Strategy parameter overrides (see the guide): numbers, or "
                                                       "expressions read once per trader when its parameters are "
                                                       "drawn.")


class OrderBookConfig(BaseModel):
    """One instrument traded on a continuous limit order book."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Agent type that trades (subtypes included).")
    start_price: _Positive | str = Field(..., description="Opening reference price, above 0 (number or expression).")
    currency: str = Field("cash", description="Trader property holding money (added with 0 if the type lacks it).")
    instrument: str = Field("", description="Display name of the instrument (default: the book's name).")
    tick_size: _Positive | str = Field(0.01, description="Minimum price increment" + _EXPR + ".")
    lot_size: _Positive | str = Field(1.0, description="Minimum quantity; orders are whole multiples of it" + _EXPR
                                      + ". A literal whole lot makes order quantities integers.")
    maker_fee_bps: _MakerFee | str = Field(0.0,
                                           description="Fee on fills of resting orders, in basis points of notional"
                                           + _EXPR + "; negative is a rebate, at most the taker fee that pays it.")
    taker_fee_bps: _Fee | str = Field(0.0, description="Fee on fills of incoming orders, in basis points" + _EXPR + ".")
    collar_pct: _Share | str = Field(0.05,
                                     description="A market order never trades further than this from the touch" + _EXPR
                                     + ".")
    price_band_pct: Annotated[float, Field(gt=0, le=10)] | str = Field(
        0.5, description="Limit prices must be within this fraction of the round's opening price" + _EXPR + ".")
    halt_pct: Annotated[float, Field(ge=0, le=1)] | str | None = Field(
        None,
        description="Circuit breaker: halt when the price moves this far from the reference price" + _EXPR
        + "; null or 0 = no breaker.")
    halt_reference: Literal["round_open", "bar_open", "rolling"] = Field(
        "round_open", description="What the breaker measures a move from: round_open (the last price when the round "
                                  "opened), bar_open (when the bar opened; on a continuous book that is also the "
                                  "previous bar's close) or rolling (the close `halt_window` rounds back).")
    halt_window: Annotated[int, Field(ge=1, le=256)] | str | None = Field(
        None, description="Rounds a rolling reference looks back (1 = the round's open)" + _EXPR + ".")
    halt_check: Literal["trade", "round_end"] = Field(
        "trade", description="When the breaker looks: trade (every trade price; the order that trips it stops there) "
                             "or round_end (the mid when each round closes, before its bar is recorded).")
    halt_until: Literal["rounds", "bar_end"] = Field(
        "rounds", description="How long a halt lasts: rounds (the rest of the round and `halt_rounds` more) or "
                              "bar_end (to the end of the bar it trips in).")
    halt_rounds: Annotated[int, Field(ge=0)] | str = Field(1,
                                                           description="Extra rounds a halt lasts after the round it "
                                                                       "trips (halt_until rounds)" + _EXPR + ".")
    short_limit: _NonNegative | str = Field(0.0,
                                            description="How far below zero a trader's shares may go (0 = no short "
                                                        "selling)" + _EXPR + ".")
    max_short_leverage: _Positive | str | None = Field(
        None, description="A sell is refused when the short position it could leave would be worth more than this "
                          "multiple of the trader's equity (short_limit stays the absolute cap)" + _EXPR + ".")
    order_ttl: _Count | str | None = Field(None,
                                           description="Resting orders expire after this many rounds" + _EXPR + ".")
    max_orders: _Count | str = Field(20, description="Resting orders one trader may have" + _EXPR + ".")
    bar_rounds: _Count | str = Field(
        1, description="Rounds in one OHLCV bar of the `<name>_bars` record (a bar of several passes)" + _EXPR
                       + "; $book(name).bar is the bar in progress.")
    depth_levels: int = Field(5, ge=1, le=50, description="Price levels per side shown in the book view.")
    tape: int = Field(50, ge=1, description="Recent trades kept in the <name>_tape record.")
    volatility: float | str = Field(0.02,
                                    description="Per-round return volatility the default fair value walks at, that "
                                                "market makers "
                                                    "price their spread and their reading of order flow by, and "
                                                    "other coded "
                                                    "strategies assume before the tape shows one (number or "
                                                    "expression). Market "
                                                    "makers move their quotes with net order flow, so informed "
                                                    "traders carry the "
                                                    "price toward the value; without fundamentalists it wanders "
                                                    "with the noise.")
    measure_volatility: bool = Field(True,
                                     description="Coded strategies other than market makers measure volatility from "
                                                 "recent closes; false makes them always assume `volatility` (a "
                                                 "calibrated value).")
    base_qty: float | EachCrowd | None = Field(None,
                                         description="Coded strategies' unit of order size (default 10 lots); an "
                                                     "expression is read on every turn, so a controller can steer it.")
    flow_scale: EachCrowd | None = Field(None,
                                   description="Expression multiplying speculative order sizes (momentum, noise, "
                                               "passive); default 1.")
    sentiment: EachCrowd | None = Field(None,
                                  description="Expression for market sentiment in [-1, 1] that tilts noise traders "
                                              "toward buying or selling; default 0.")
    fair_value: Expr | None = Field(None, description="Expression for the true value fundamentalists estimate "
                                                     "(default: $world.<name>_value, a random walk from the start "
                                                     "price at `volatility`).")
    crowd: dict[Literal["market_maker", "momentum", "mean_reversion", "fundamentalist", "noise", "passive"],
                CrowdSpec] = Field(
        {}, description="Coded traders by strategy: {market_maker: {count, cash, shares, params}}.")
    stage: str | None = Field(None,
                              description="Trade during this declared stage; default: a sequential stage named after "
                                          "the book.")
    max_actions: int = Field(4, ge=1, description="Actions per turn in the generated stage.")
    conserve: bool | Literal["action", "round", "end"] = Field(
        "round", description="Declare the invariant that reserves match the book and balances stay within limits: "
                             "round (the default: after every round), true or action (after every action: each check "
                             "goes over every trader, so a round costs the square of the crowd), end (once, when the "
                             "run finishes), or false.")

    @field_validator("start_price")
    @classmethod
    def _price_or_expression(cls, value: Any) -> Any:
        if isinstance(value, str) and not is_expr(value):
            raise ValueError(f"must be a number above 0 or an expression with $, got {value!r}")
        return value


_CONFIGS: dict[tuple[int, str], tuple[Any, OrderBookConfig]] = {}


def book_config(world: Any, name: Any) -> OrderBookConfig:
    """The book's validated config, looked up once per contract object (a clone shares its contract)."""
    contract = world.contract
    hit = _CONFIGS.get((id(contract), name)) if isinstance(name, str) else None
    if hit is not None and hit[0] is contract:
        return hit[1]
    config = mechanism_config(world, name, KEY, OrderBookConfig)
    if len(_CONFIGS) >= 256:
        _CONFIGS.clear()
    _CONFIGS[(id(contract), name)] = (contract, config)
    return config


@lru_cache(maxsize=256)
def props_for(name: str) -> dict[str, str]:
    """Trader property names of the book (one shared map per name: read it, never change it)."""
    return {"shares": f"{name}_shares", "reserved_cash": f"{name}_reserved_cash",
            "reserved_shares": f"{name}_reserved_shares", "fees_paid": f"{name}_fees_paid",
            "start_value": f"{name}_start_value", "strategy": f"{name}_strategy", "algo": f"{name}_algo"}


# ---------------------------------------------------------------------------
# Reading state
# ---------------------------------------------------------------------------


def _ticks(price: float, tick: float) -> int:
    return int(round(price / tick))


def _book(world: Any, name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Both sides as fresh lists sharing the stored orders: an order is never changed in place (a partial fill
    replaces it with a copy), so the lists can be stored back without copying every order."""
    return list(world.props.get(f"{name}_bids") or []), list(world.props.get(f"{name}_asks") or [])


def _touch_qty(orders: list[dict[str, Any]]) -> float:
    """Quantity resting at the best price of one side."""
    total: float = 0
    for order in orders:
        if order["price"] != orders[0]["price"]:
            break
        total += order["qty"]
    return total


def bar_end(world: Any, v: Venue) -> int:
    """The last round of the bar the current round belongs to."""
    return ((max(1, world.round) - 1) // v.bar_rounds + 1) * v.bar_rounds


def quote(world: Any, name: str) -> dict[str, Any]:
    """Top of book, round and bar statistics as one map."""
    cfg = book_config(world, name)
    v = venue(world, name)
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
    low, high = band(v, band_anchor(world, name))
    return {
        "instrument": cfg.instrument or name, "last": last, "bid": bid, "ask": ask, "mid": mid,
        "spread": round(ask - bid, 10) if bid is not None and ask is not None else None,
        "bid_qty": _touch_qty(bids), "ask_qty": _touch_qty(asks),
        "ref": float(world.props.get(f"{name}_ref") or last), "halted": halted,
        "halt_until": world.props.get(f"{name}_halt_until") if halted else None,
        "open": bar.get("open", last), "high": bar.get("high", last), "low": bar.get("low", last),
        "round_volume": bar.get("volume", 0), "round_trades": bar.get("trades", 0),
        "volume": volume, "vwap": notional / volume if volume > 0 else None,
        "trades": world.props.get(f"{name}_trades") or 0, "fees": world.props.get(f"{name}_fees") or 0,
        "halts": world.props.get(f"{name}_halts") or 0, "orders": len(bids) + len(asks),
        "flow": world.props.get(f"{name}_flow") or {}, "liquidations": world.props.get(f"{name}_liquidations") or 0,
        "bar": running_bar(world, name, v), "bar_rounds": v.bar_rounds,
        "tick": v.tick, "lot": v.lot, "maker_fee_bps": v.maker_fee_bps, "taker_fee_bps": v.taker_fee_bps,
        "short_limit": v.short_limit, "halt_pct": v.halt_pct, "band_low": low, "band_high": high,
        "heat": heat(world, name),
    }


#: Rounds of closes the market's heat compares (recent against usual), and the range it stays in.
HEAT_ROUNDS, USUAL_ROUNDS, HEAT_RANGE = 8, 64, (0.5, 2.0)


def heat(world: Any, name: str) -> float:
    """How hot the market runs: the root-mean-square move of the last ``HEAT_ROUNDS`` closes against that of the last
    ``USUAL_ROUNDS`` (1 = as usual), within ``HEAT_RANGE``. Coded traders act that much more often, so busy spells
    follow big moves and volatility clusters, as in real markets; the usual level is left to the crowd."""
    closes = (world.props.get(f"{name}_closes") or [])[-USUAL_ROUNDS - 1:]
    moves = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(moves) < 2 * HEAT_ROUNDS:
        return 1.0
    usual = math.sqrt(sum(m * m for m in moves) / len(moves))
    recent = math.sqrt(sum(m * m for m in moves[-HEAT_ROUNDS:]) / HEAT_ROUNDS)
    return max(HEAT_RANGE[0], min(HEAT_RANGE[1], recent / usual)) if usual > 0 else 1.0


def top(world: Any, name: str) -> tuple[float, float | None, float | None, float]:
    """(last, best bid, best ask, mid) without the rest of :func:`quote` (what coded traders read on every turn)."""
    bids, asks = world.props.get(f"{name}_bids"), world.props.get(f"{name}_asks")
    bid = bids[0]["price"] if bids else None
    ask = asks[0]["price"] if asks else None
    last = float(world.props.get(f"{name}_last") or 0)
    if bid is not None and ask is not None:
        return last, bid, ask, (bid + ask) / 2
    return last, bid, ask, bid if bid is not None else ask if ask is not None else last


def running_bar(world: Any, name: str, v: Venue) -> dict[str, Any]:
    """The bar in progress, including the round in progress: {bar, open, high, low, close, volume, vwap, trades,
    halted, flow, ends} (``ends``: the bar's last round). Once a bar's last round has closed, the bar is in
    ``<name>_bars`` and this shows the next one, with no trades yet."""
    last = float(world.props.get(f"{name}_last") or 0)
    done = world.props.get(f"{name}_current_bar") or {}
    # A closed round is already folded into the bar in progress (or recorded with its bar).
    now = {} if world.props.get(f"{name}_closed") == world.round else world.props.get(f"{name}_bar") or {}
    volume = clean(float(done.get("volume", 0)) + float(now.get("volume", 0)))
    notional = float(done.get("notional", 0)) + float(now.get("notional", 0))
    return {"bar": (max(1, world.round) - 1) // v.bar_rounds + 1, "open": done.get("open", now.get("open", last)),
            "high": max(done.get("high", last), now.get("high", last)),
            "low": min(done.get("low", last), now.get("low", last)),
            "close": last, "volume": volume, "vwap": notional / volume if volume > 0 else last,
            "trades": int(done.get("trades", 0)) + int(now.get("trades", 0)), "halted": bool(done.get("halted")),
            "flow": merge_flow(done.get("flow") or {}, now.get("flow") or {}), "ends": bar_end(world, v)}


def merge_flow(total: dict[str, Any], more: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Two flow maps ({kind: {buy, sell}}) added kind by kind."""
    out = {kind: dict(sides) for kind, sides in total.items()}
    for kind, sides in more.items():
        into = out.setdefault(kind, {"buy": 0, "sell": 0})
        into["buy"] = clean(into.get("buy", 0) + sides.get("buy", 0))
        into["sell"] = clean(into.get("sell", 0) + sides.get("sell", 0))
    return out


def band_anchor(world: Any, name: str) -> float:
    """What the price band is measured from: the round's open, so one small print cannot move the band and a run
    of trades cannot walk the price further than the band within a round."""
    last = float(world.props.get(f"{name}_last") or 0)
    return float((world.props.get(f"{name}_bar") or {}).get("open", last))


def band(v: Venue, anchor: float) -> tuple[float, float]:
    low = math.ceil(anchor * (1 - v.band) / v.tick - 1e-9) * v.tick
    high = math.floor(anchor * (1 + v.band) / v.tick + 1e-9) * v.tick
    return round(max(v.tick, low), 10), round(high, 10)


def depth(world: Any, name: str, levels: int | None = None, viewer: Entity | None = None) -> list[dict[str, Any]]:
    """Price levels as a ladder: asks from the Nth best down to the best, then bids best first."""
    cfg = book_config(world, name)
    n = levels or cfg.depth_levels
    out: dict[str, list[dict[str, Any]]] = {}
    for side, orders in (("ask", world.props.get(f"{name}_asks") or []),
                         ("bid", world.props.get(f"{name}_bids") or [])):
        rows: list[dict[str, Any]] = []
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


def short_room(world: Any, name: str, cfg: OrderBookConfig, trader: Entity) -> float:
    """Shares the trader may sell below zero: ``short_limit``, capped by ``max_short_leverage`` × equity."""
    v = venue(world, name)
    if v.max_short_leverage is None or v.short_limit <= 0:
        return v.short_limit
    p = props_for(name)
    last = float(world.props.get(f"{name}_last") or 0)
    if last <= 0:
        return 0.0
    position = balance(world, Account(trader, p["shares"])) + balance(world, Account(trader, p["reserved_shares"]))
    equity = (balance(world, Account(trader, cfg.currency)) + balance(world, Account(trader, p["reserved_cash"]))
              + position * last)
    return max(0.0, min(v.short_limit, v.max_short_leverage * equity / last))


def account(world: Any, name: str, trader: Entity) -> dict[str, Any]:
    cfg = book_config(world, name)
    v = venue(world, name)
    p = props_for(name)
    cash = balance(world, Account(trader, cfg.currency))
    shares = balance(world, Account(trader, p["shares"]))
    reserved_cash = balance(world, Account(trader, p["reserved_cash"]))
    reserved_shares = balance(world, Account(trader, p["reserved_shares"]))
    last = float(world.props.get(f"{name}_last") or 0)
    position = clean(shares + reserved_shares)
    equity = cash + reserved_cash + position * last
    low = band(v, band_anchor(world, name))[0]
    orders = [o for side in ("bids", "asks") for o in world.props.get(f"{name}_{side}") or [] if o["owner"]
              == trader.id]
    return {
        "cash": cash, "shares": shares, "reserved_cash": reserved_cash, "reserved_shares": reserved_shares,
        "position": position, "equity": clean(equity), "pnl": clean(equity - _num(trader, p["start_value"])),
        "fees_paid": trader.properties.get(p["fees_paid"]) or 0, "orders": len(orders),
        "max_buy": lot_floor(cash / (low * (1 + max(v.maker, v.taker))), v.lot) if low > 0 else 0,
        "max_sell": lot_floor(max(0.0, shares + short_room(world, name, cfg, trader)), v.lot),
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


def release(world: Any, cfg: OrderBookConfig, v: Venue, name: str, order: dict[str, Any]) -> None:
    """Return a resting order's reservation to its owner."""
    owner = world.entity(order["owner"])
    if owner is None:
        raise RunError(f"order {order['id']} belongs to unknown trader {order['owner']!r}", f"mechanisms.{name}")
    p = props_for(name)
    if order["side"] == "buy":
        move(world, Account(owner, p["reserved_cash"]), Account(owner, cfg.currency),
             order["qty"] * order["price"] * v.hold, what="reserved cash")
    else:
        move(world, Account(owner, p["reserved_shares"]), Account(owner, p["shares"]), order["qty"],
             what="reserved shares", floor=0.0)


def _reconcile_reservations(world: Any, cfg: OrderBookConfig, v: Venue, name: str,
                            owner_ids: set[str]) -> None:
    """Make touched accounts exactly match the authoritative resting book.

    Large calibrated venues move values in the billions.  A resting order can
    therefore be filled by many smaller orders and leave a few millionths of
    currency in its reserve account through ordinary floating-point
    subtraction.  The order book, rather than an accumulated float, is the
    source of truth.  Recompute only the accounts touched by this operation
    and move any numerical remainder back to the free balance while preserving
    each account's total cash and shares.
    """
    if not owner_ids:
        return
    p = props_for(name)
    required_cash = {owner_id: 0.0 for owner_id in owner_ids}
    required_shares = {owner_id: 0.0 for owner_id in owner_ids}
    for order in [o for o in world.props.get(f"{name}_bids") or [] if o["owner"] in owner_ids]:
        required_cash[order["owner"]] += order["qty"] * order["price"] * v.hold
    for order in [o for o in world.props.get(f"{name}_asks") or [] if o["owner"] in owner_ids]:
        required_shares[order["owner"]] += order["qty"]
    for owner_id in owner_ids:
        owner = world.entity(owner_id)
        if owner is None:
            raise RunError(f"order belongs to unknown trader {owner_id!r}", f"mechanisms.{name}")
        cash = balance(world, Account(owner, cfg.currency))
        reserved_cash = balance(world, Account(owner, p["reserved_cash"]))
        shares = balance(world, Account(owner, p["shares"]))
        reserved_shares = balance(world, Account(owner, p["reserved_shares"]))
        expected_cash = clean(required_cash[owner_id])
        expected_shares = clean(required_shares[owner_id])
        _settle(world, owner, p["reserved_cash"], expected_cash)
        _settle(world, owner, cfg.currency, clean(cash + reserved_cash - expected_cash))
        _settle(world, owner, p["reserved_shares"], expected_shares)
        _settle(world, owner, p["shares"], clean(shares + reserved_shares - expected_shares))


def _settle(world: Any, owner: Entity, prop: str, value: float) -> None:
    """Write ``value`` unless the account already holds exactly what writing it would store (an ``int`` property
    stores a whole float as an int): without float dust there is nothing to settle."""
    held = owner.properties.get(prop)
    if held != value or (type(held) is not type(value) and prop_type(world.prop_spec(owner, prop)) != "int"):
        world.set_prop(owner, prop, value)


def _insert(orders: list[dict[str, Any]], order: dict[str, Any], side: str) -> None:
    if side == "buy":
        at = bisect.bisect(orders, (-order["price"], order["seq"]), key=lambda o: (-o["price"], o["seq"]))
    else:
        at = bisect.bisect(orders, (order["price"], order["seq"]), key=lambda o: (o["price"], o["seq"]))
    orders.insert(at, order)


def _limit_ticks(v: Venue, side: str, price: Any, anchor: float, opposite: list[dict[str, Any]]) -> int:
    """The order's worst acceptable price in ticks: its limit, or the collar from the touch for a market order."""
    if price is not None:
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise Abort(f"price must be a positive number, not {price!r}.")
        raw_t = price / v.tick
        limit_t = int(math.floor(raw_t + 1e-9)) if side == "buy" else int(math.ceil(raw_t - 1e-9))
        low, high = band(v, anchor)
        if not _ticks(low, v.tick) <= limit_t <= _ticks(high, v.tick):
            raise Abort(f"Limit prices must be between {fmt(low, 4)} and {fmt(high, 4)} "
                        f"(within {pct(v.band)} of the round's open {fmt(anchor, 4)}).")
        return limit_t
    if not opposite:
        raise Abort(f"There are no {'sell' if side == 'buy' else 'buy'} orders to trade with; place a limit order "
                    "instead.")
    touch = opposite[0]["price"]
    edge = touch * (1 + v.collar) if side == "buy" else touch * (1 - v.collar)
    return int(math.floor(edge / v.tick + 1e-9)) if side == "buy" else int(math.ceil(edge / v.tick - 1e-9))


def place(world: Any, name: str, trader: Entity, side: str, qty: Any, price: Any = None,
          kind: str | None = None) -> str:
    """Submit a buy or sell (limit when ``price`` is given, market otherwise). Returns the receipt text;
    refuses with :class:`Abort` (nothing changes) when the order is not possible. ``kind`` labels the
    aggressive flow (default: the trader's coded strategy, or ``agent``)."""
    cfg = book_config(world, name)
    v = venue(world, name)
    p = props_for(name)
    unit = cfg.instrument or name
    if side not in ("buy", "sell"):
        raise Abort(f"side must be buy or sell, not {side!r}.")
    if world.props.get(f"{name}_halted"):
        raise Abort(f"Trading in {unit} is halted by the circuit breaker until round "
                    f"{world.props.get(f'{name}_halt_until')} ends; you can only cancel orders.")
    if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(qty):
        raise Abort(f"qty must be a number, not {qty!r}.")
    size = lot_floor(float(qty), v.lot)
    if size <= 0:
        raise Abort(f"The minimum order is {fmt(v.lot, 6)} shares (one lot).")
    maker, taker = v.maker, v.taker
    last = float(world.props.get(f"{name}_last") or 0)
    bids, asks = _book(world, name)
    own, opposite = (bids, asks) if side == "buy" else (asks, bids)
    limit_t = _limit_ticks(v, side, price, band_anchor(world, name), opposite)
    if (price is not None and len(own) >= v.max_orders and sum(1 for o in own if o["owner"] == trader.id)
        >= v.max_orders):
        raise Abort(f"You already have {v.max_orders} resting orders in {unit}; cancel one first.")
    limit_price = round(limit_t * v.tick, 10)
    cash = Account(trader, cfg.currency)
    shares = Account(trader, p["shares"])
    if side == "buy" and price is not None:
        need = size * limit_price * (1 + max(maker, taker))
        have = balance(world, cash)
        if need > have + EPS:
            most = lot_floor(have / (limit_price * (1 + max(maker, taker))), v.lot)
            raise Abort(f"Not enough free cash: {fmt(size, 6)} @ {fmt(limit_price, 4)} with fees needs {fmt(need)}, "
                        f"you have {fmt(have)}. Buy at most {fmt(most, 6)} at this price, or cancel resting buys.")
    if side == "sell":
        free = balance(world, shares)
        room = short_room(world, name, cfg, trader)
        if free - size < -room - EPS:
            most = lot_floor(max(0.0, free + room), v.lot)
            short = f" (short selling up to {fmt(room, 6)} allowed now)" if v.short_limit else ""
            raise Abort(f"Not enough free shares: you can sell at most {fmt(most, 6)}{short}; resting sells reserve "
                        "shares.")
    fees = Account(None, f"{name}_fees")
    fills: list[_Fill] = []
    touched_owners = {trader.id}
    left, spent, paid, prevented = size, 0.0, 0.0, 0
    ref = float(world.props.get(f"{name}_ref") or last)
    watch = v.halt_pct is not None and cfg.halt_check == "trade"
    tripped = False
    while left > EPS and opposite:
        best = opposite[0]
        best_t = _ticks(best["price"], v.tick)
        if (side == "buy" and best_t > limit_t) or (side == "sell" and best_t < limit_t):
            break
        if best["owner"] == trader.id:
            release(world, cfg, v, name, best)
            opposite.pop(0)
            prevented += 1
            continue
        px = best["price"]
        q = min(left, best["qty"])
        if side == "buy" and price is None:
            q = min(q, lot_floor(balance(world, cash) / (px * (1 + taker)), v.lot))
            if q <= 0:
                break
        maker_entity = entity_of(world, best["owner"], f"mechanisms.{name}", "a trader")
        touched_owners.add(maker_entity.id)
        notional = q * px
        if side == "buy":
            move(world, cash, Account(maker_entity, cfg.currency), notional, what="cash")
            move(world, Account(maker_entity, p["reserved_shares"]), shares, q, what="reserved shares")
            move(world, cash, fees, notional * taker, what="cash")
            maker_pays, paid_from = Account(maker_entity, cfg.currency), "cash"
        else:
            move(world, Account(maker_entity, p["reserved_cash"]), cash, notional, what="reserved cash")
            move(world, shares, Account(maker_entity, p["shares"]), q, what="shares", floor=-v.short_limit)
            move(world, cash, fees, notional * taker, what="cash")
            maker_pays, paid_from = Account(maker_entity, p["reserved_cash"]), "reserved cash"
        if maker >= 0:
            move(world, maker_pays, fees, notional * maker, what=paid_from)
        else:  # a rebate, out of the taker fee just collected
            move(world, fees, Account(maker_entity, cfg.currency), -notional * maker, what="rebate")
        world.set_prop(trader, p["fees_paid"], clean(_num(trader, p["fees_paid"]) + notional * taker))
        world.set_prop(maker_entity, p["fees_paid"],
                       clean(_num(maker_entity, p["fees_paid"]) + notional * maker))
        remaining = clean(best["qty"] - q)
        if remaining <= EPS:
            opposite.pop(0)
        else:
            opposite[0] = {**best, "qty": remaining}
        left = clean(left - q)
        spent += notional
        paid += notional * taker
        fills.append(_Fill(q, px))
        world.post(f"{name}_tape", {"price": px, "qty": q, "aggressor": side}, None, None, f"mechanisms.{name}")
        if watch and abs(px - ref) > v.halt_pct * ref + 1e-12:  # type: ignore[operator]
            tripped = True
            break
    rested = 0.0
    if left > EPS and price is not None and not tripped:
        if side == "buy":
            move(world, cash, Account(trader, p["reserved_cash"]), left * limit_price * v.hold, what="cash")
        else:
            move(world, shares, Account(trader, p["reserved_shares"]), left, what="shares", floor=-v.short_limit)
        seq = int(world.props.get(f"{name}_seq") or 0) + 1
        world.set_world(f"{name}_seq", seq)
        _insert(own, {"id": f"{name}_{seq}", "owner": trader.id, "side": side, "price": limit_price, "qty": left,
                      "seq": seq, "round": world.round}, side)
        rested = left
    own_side, opposite_side = ("bids", "asks") if side == "buy" else ("asks", "bids")
    if rested:
        world.set_world(f"{name}_{own_side}", own, trusted=True)
    if fills or prevented:
        world.set_world(f"{name}_{opposite_side}", opposite, trusted=True)
    _reconcile_reservations(world, cfg, v, name, touched_owners)
    filled = clean(sum(f.qty for f in fills))
    if fills:
        _record_fills(world, name, fills, side, kind or str(trader.properties.get(p["strategy"]) or "agent"))
    if tripped:
        trip(world, name, fills[-1].price, ref, "a trade at")
    parts = [f"{side.upper()} {fmt(size, 6)} {unit} "
             f"{'@ ' + fmt(limit_price, 4) if price is not None else 'at market'}: filled {fmt(filled, 6)}"
             + (f" at avg {fmt(spent / filled, 4)}" if filled else "")]
    if paid:
        parts.append(f"fees {fmt(paid, 4)}")
    if rested:
        parts.append(f"resting {fmt(rested, 6)} as order {name}_{world.props.get(f'{name}_seq')}")
    cancelled = clean(size - filled - rested)
    if cancelled > EPS:
        why = "the circuit breaker halted trading" if tripped else (
            "not enough cash" if side == "buy" and price is None and opposite and _ticks(opposite[0]["price"], v.tick)
            <= limit_t
            else f"no more liquidity within the {pct(v.collar)} collar" if price is None else "")
        parts.append(f"{fmt(cancelled, 6)} cancelled" + (f" ({why})" if why else ""))
    if prevented:
        parts.append(f"{prevented} of your own opposite order(s) cancelled instead of trading with you")
    return _receipt(world, name, ", ".join(parts) + ".")


def _record_fills(world: Any, name: str, fills: list[_Fill], side: str, kind: str) -> None:
    bar = dict(world.props.get(f"{name}_bar") or {})
    volume = sum(f.qty for f in fills)
    flow = {k: dict(v) for k, v in (bar.get("flow") or {}).items()}
    by_kind = flow.setdefault(kind, {"buy": 0, "sell": 0})
    by_kind[side] = clean(by_kind[side] + volume)
    bar["flow"] = flow
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


def trip(world: Any, name: str, px: float, ref: float, what: str) -> None:
    """Halt the book: until the round `halt_rounds` after this one, or the end of the bar (``halt_until``)."""
    cfg = book_config(world, name)
    v = venue(world, name)
    to_bar_end = cfg.halt_until == "bar_end"
    until = bar_end(world, v) if to_bar_end else world.round + v.halt_rounds
    world.set_world(f"{name}_halted", True)
    world.set_world(f"{name}_halt_until", until)
    world.set_world(f"{name}_halts", int(world.props.get(f"{name}_halts") or 0) + 1)
    current = dict(world.props.get(f"{name}_current_bar") or {})
    current["halted"] = True
    world.set_world(f"{name}_current_bar", current)
    reference = {"round_open": "the round's open", "bar_open": "the bar's open",
                 "rolling": f"the price {v.halt_window or 1} round(s) back"}[cfg.halt_reference]
    lasts = "the rest of this bar" if to_bar_end else "the rest of this round" + (
        f" and {v.halt_rounds} more round(s)" if v.halt_rounds else "")
    beyond = "at least" if cfg.halt_check == "round_end" else "more than"
    world.emit(f"{name}_halt", f"CIRCUIT BREAKER on {cfg.instrument or name}: {what} {fmt(px, 4)} moved "
                               f"{beyond} {pct(v.halt_pct or 0)} from "
                               f"{reference} {fmt(ref, 4)}. Trading is halted for {lasts}.",
               data={"mechanism": KEY, "price": px, "until": until})


def cancel(world: Any, name: str, trader: Entity, order_id: Any) -> str:
    cfg = book_config(world, name)
    v = venue(world, name)
    bids, asks = _book(world, name)
    for orders, side in ((bids, "bids"), (asks, "asks")):
        for index, order in enumerate(orders):
            if order["id"] == order_id and order["owner"] == trader.id:
                release(world, cfg, v, name, order)
                orders.pop(index)
                world.set_world(f"{name}_{side}", orders, trusted=True)
                _reconcile_reservations(world, cfg, v, name, {trader.id})
                return _receipt(world, name,
                                f"Cancelled {order['side']} {fmt(order['qty'], 6)} @ {fmt(order['price'], 4)}.")
    raise Abort(f"You have no resting order {order_id!r} in {cfg.instrument or name}.")


def cancel_all(world: Any, name: str, trader: Entity) -> str:
    cfg = book_config(world, name)
    v = venue(world, name)
    bids, asks = _book(world, name)
    count = 0
    for orders, side in ((bids, "bids"), (asks, "asks")):
        keep = []
        for order in orders:
            if order["owner"] == trader.id:
                release(world, cfg, v, name, order)
                count += 1
            else:
                keep.append(order)
        if len(keep) != len(orders):
            world.set_world(f"{name}_{side}", keep, trusted=True)
    _reconcile_reservations(world, cfg, v, name, {trader.id})
    return _receipt(world, name, f"Cancelled {count} order(s).")


def crowd_type(name: str) -> str:
    """The type every coded crowd trader of the book is: ``<name>_crowd``, beside ``who`` and never one of its subtypes,
    so other mechanisms on ``who`` (a ballot) do not count the crowd."""
    return f"{name}_crowd"


def traders(world: Any, name: str, cfg: OrderBookConfig) -> list[Entity]:
    """Every account of the book: the ``who`` traders and the book's own crowd."""
    kinds = [cfg.who] + ([crowd_type(name)] if cfg.crowd else [])
    return [trader for kind in kinds for trader in world.entities_of(kind)]


def audit(world: Any, name: str) -> list[str]:
    """Every accounting rule the book must keep; empty when all hold."""
    cfg = book_config(world, name)
    v = venue(world, name)
    p = props_for(name)
    problems: list[str] = []
    bids, asks = world.props.get(f"{name}_bids") or [], world.props.get(f"{name}_asks") or []
    reserved_cash: dict[str, float] = {}
    reserved_shares: dict[str, float] = {}
    for side, orders in (("buy", bids), ("sell", asks)):
        for order in orders:
            if order["side"] != side or order["qty"] <= 0:
                problems.append(f"order {order['id']} is malformed ({order})")
            if side == "buy":
                reserved_cash[order["owner"]] = (reserved_cash.get(order["owner"], 0.0)
                                                 + order["qty"] * order["price"] * v.hold)
            else:
                reserved_shares[order["owner"]] = reserved_shares.get(order["owner"], 0.0) + order["qty"]
    if bids and asks and bids[0]["price"] >= asks[0]["price"]:
        problems.append(f"the book is crossed: bid {bids[0]['price']} ≥ ask {asks[0]['price']}")
    for orders, key in ((bids, lambda o: (-o["price"], o["seq"])), (asks, lambda o: (o["price"], o["seq"]))):
        if [key(o) for o in orders] != sorted(key(o) for o in orders):
            problems.append("orders are out of price-time priority")
    tolerance = 1e-6
    for trader in traders(world, name, cfg):
        acct = {"cash": _num(trader, cfg.currency), "shares": _num(trader, p["shares"]),
                "rc": _num(trader, p["reserved_cash"]), "rs": _num(trader, p["reserved_shares"])}
        if abs(acct["rc"] - reserved_cash.get(trader.id, 0.0)) > tolerance * max(1.0, acct["rc"]):
            problems.append(f"{trader.id} reserves {acct['rc']} cash but its resting buys need "
                            f"{reserved_cash.get(trader.id, 0.0)}")
        if abs(acct["rs"] - reserved_shares.get(trader.id, 0.0)) > tolerance:
            problems.append(f"{trader.id} reserves {acct['rs']} shares but its resting sells hold "
                            f"{reserved_shares.get(trader.id, 0.0)}")
        if acct["rc"] < -tolerance or acct["rs"] < -tolerance:
            problems.append(f"{trader.id} has a negative reserve")
        if acct["shares"] < -v.short_limit - tolerance:
            problems.append(f"{trader.id} is short {-acct['shares']} shares, beyond the limit {v.short_limit}")
    return problems
