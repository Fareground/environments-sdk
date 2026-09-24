"""Coded trader strategies for the order book, ported from the Fareground Exchange population.

A trader whose ``<book>_strategy`` property names a strategy acts through the ``<book>_algo`` tool
(the generated ``<book>_algo`` policy calls it), so a crowd needs no custom code. Each strategy is a
few lines of behaviour with per-trader parameters drawn once from the run's seeded randomness and
kept in the trader's ``<book>_algo`` property, so a crowd of one kind still shows dispersion and a
snapshot resumes it exactly.

Sizes are in multiples of the book's ``base_qty`` (speculative sizes also by its ``flow_scale``); volatility
is the realised per-round volatility of recent closes (or the book's ``volatility`` before there is a tape), except
for market makers.
A strategy with a ``stop_loss`` parameter liquidates its position at market, before anything else it does,
once the loss passes ``stop_loss`` × volatility (clamped to 2–15%) of the position's entry value.

* ``market_maker`` — requotes both sides around a reference price it learns from order flow (last round's net
  aggressive flow moves it by up to ``impact`` volatilities, and it leans toward the last trade), skews on inventory,
  widens with the book's ``volatility`` and with how one-sided (toxic) recent flow was, and hedges with a market
  order past its inventory limit. It prices by the configured volatility, not the tape's: a measured one would
  include its own bid-ask bounce and feed back into ever wider quotes.
* ``momentum`` — buys strength and sells weakness over a lookback; closes when the trend fades.
* ``mean_reversion`` — fades stretched moves with limit orders inside the spread; exits on reversion.
* ``fundamentalist`` — trades toward a noisy private estimate of the fair value (the book's ``fair_value``, by
  default a random walk at its ``volatility``); patient orders rest.
* ``noise`` — random arrivals, mostly market orders, fat-tailed sizes, herding on the last move and the
  book's ``sentiment``.
* ``passive`` — index-like flow: one random side per round, worked with market orders.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from ..errors import RunError
from ..expr import ExprError, compile_expr
from ..expr.objects import Entity
from ..world.live import Abort
from .book_rules import venue
from .common import lot_floor, number
from .ledger import Account, balance
from .market_stats import log_returns, stdev
from .order_book import OrderBookConfig, book_config, cancel_all, place, props_for, short_room, top

__all__ = ["DEFAULTS", "run_algo"]

#: Default parameters per strategy (overridden by a crowd's ``params``).
DEFAULTS: dict[str, dict[str, float]] = {
    "market_maker": {"activity": 1.0, "half_spread_ticks": 2.0, "vol_mult": 0.5, "quote_mult": 1.0,
                     "inventory_mult": 8.0, "layers": 2, "position_mult": 16.0, "impact": 1.0, "toxicity_mult": 2.0},
    "momentum": {"activity": 0.5, "lookback": 5, "threshold_sigma": 0.5, "size_mult": 1.0, "position_mult": 4.0},
    "mean_reversion": {"activity": 0.5, "window": 10, "z_threshold": 1.2, "size_mult": 0.8, "position_mult": 4.0},
    "fundamentalist": {"activity": 0.4, "noise_sigma": 0.8, "margin_sigma": 0.5, "patience": 0.7, "size_mult": 1.0,
                       "position_mult": 10.0, "value_rounds": 1},
    "noise": {"activity": 0.6, "market_prob": 0.6, "herding": 0.15, "size_mult": 0.5, "size_sigma": 0.7,
              "position_mult": 6.0, "sentiment_sensitivity": 0.35},
    "passive": {"activity": 0.5, "size_mult": 0.3, "position_mult": 8.0, "direction_bias": 0.0, "side_rounds": 1},
}
#: How far a market maker's reference leans toward the last trade each round, and how much of its toxicity reading
#: carries over from the round before.
LAST_WEIGHT, TOXICITY_MEMORY = 0.3, 0.7
#: Per-trader dispersion: parameter → (low, high) multiplier drawn once.
_DISPERSION: dict[str, dict[str, tuple]] = {
    "market_maker": {"half_spread_ticks": (0.7, 1.6), "quote_mult": (0.6, 1.5), "inventory_mult": (0.7, 1.3),
                     "vol_mult": (0.7, 1.3)},
    "momentum": {"lookback": (0.4, 2.0), "threshold_sigma": (0.6, 1.8), "size_mult": (0.5, 1.6)},
    "mean_reversion": {"window": (0.5, 1.8), "z_threshold": (0.7, 1.4), "size_mult": (0.5, 1.5)},
    "fundamentalist": {"noise_sigma": (0.5, 1.6), "margin_sigma": (0.6, 1.8), "patience": (0.8, 1.2),
                       "size_mult": (0.6, 1.6)},
    "noise": {"market_prob": (0.8, 1.2), "herding": (0.3, 1.7)},
    "passive": {"size_mult": (0.6, 1.4)},
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class _View:
    """What a coded trader sees when it acts."""

    def __init__(self, world: Any, name: str, cfg: OrderBookConfig, trader: Entity, state: dict[str, Any]):
        self.world, self.name, self.cfg, self.trader, self.state = world, name, cfg, trader, state
        self.venue = venue(world, name)
        self.last, self.bid, self.ask, self.mid = top(world, name)
        self.tick, self.lot = self.venue.tick, self.venue.lot
        self.prices: list[float] = list(world.props.get(f"{name}_closes") or []) + [self.last]
        assumed = _setting(world, name, "volatility", cfg.volatility, trader, 0.0)
        if assumed <= 0:
            raise RunError(f"volatility must be above 0, got {assumed!r}", f"mechanisms.{name}.volatility")
        rets = log_returns(self.prices[-31:]) if cfg.measure_volatility else []
        self.assumed = assumed
        self.sigma = stdev(rets) if len(rets) >= 5 and stdev(rets) > 0 else assumed
        self.base = _setting(world, name, "base_qty", cfg.base_qty, trader, self.venue.lot * 10)
        if self.base <= 0:
            raise RunError(f"base_qty must be above 0, got {self.base!r}", f"mechanisms.{name}.base_qty")
        self.flow = max(0.0, _setting(world, name, "flow_scale", cfg.flow_scale, trader, 1.0))
        self.sentiment = _clamp(_setting(world, name, "sentiment", cfg.sentiment, trader, 0.0), -1.0, 1.0)
        p = props_for(name)
        self.position = (balance(world, Account(trader, p["shares"]))
                         + balance(world, Account(trader, p["reserved_shares"])))
        self.inventory = self.position - float(state.get("start", 0.0))
        self.cash = balance(world, Account(trader, cfg.currency))
        self.orders: list[str] = []

    def room(self, side: str, mult: float) -> float:
        cap = mult * self.base
        return max(0.0, cap - self.inventory) if side == "buy" else max(0.0, cap + self.inventory)

    def order(self, side: str, qty: float, price: float | None = None, kind: str | None = None) -> None:
        """Submit one order; a leg that is not possible (cash, shares, band) is skipped without undoing the others."""
        qty = lot_floor(qty, self.lot)
        if qty <= 0 or (price is not None and price <= 0):
            return
        if side == "buy":
            quoted = price if price is not None else (self.ask or self.last)
            unit = quoted * (1 + max(self.venue.maker, self.venue.taker))
            qty = (min(qty, lot_floor(balance(self.world, Account(self.trader, self.cfg.currency)) / unit, self.lot))
                   if unit > 0 else 0)
        else:
            free = balance(self.world, Account(self.trader, props_for(self.name)["shares"]))
            qty = min(qty,
                      lot_floor(max(0.0, free + short_room(self.world, self.name, self.cfg, self.trader)), self.lot))
        if qty <= 0:
            return
        journal = self.world.journal
        mark = journal.mark()
        try:
            self.orders.append(place(self.world, self.name, self.trader, side, qty, price, kind))
        except Abort:
            journal.rollback(mark)


def run_algo(world: Any, name: str, trader: Entity) -> str:
    """Let the trader's coded strategy act once. Returns the receipt."""
    cfg = book_config(world, name)
    p = props_for(name)
    strategy = str(trader.properties.get(p["strategy"]) or "")
    decide = _STRATEGIES.get(strategy)
    if decide is None:
        raise Abort(f"You have no trading strategy on {cfg.instrument or name} (strategies: {', '.join(_STRATEGIES)}).")
    stored: Any = trader.properties.get(p["algo"]) or {}
    state: dict[str, Any] = dict(stored)
    rng = world.rng
    if "p" not in state:
        where = f"mechanisms.{name}.crowd.{strategy}.params"
        overrides = {key: number(world, raw, f"{where}.{key}", actor=trader)
                     for key, raw in _overrides(cfg, strategy).items()}
        params = {**DEFAULTS[strategy], **overrides}
        for key, (low, high) in _DISPERSION[strategy].items():
            params[key] = params[key] * rng.uniform(low, high)
        state["p"] = params
        state["start"] = (balance(world, Account(trader, p["shares"]))
                          + balance(world, Account(trader, p["reserved_shares"])))
    receipt = "Your strategy stayed out this turn."
    guarded = state["p"].get("stop_loss", 0) > 0
    view = _View(world, name, cfg, trader, state) if guarded and not world.props.get(f"{name}_halted") else None
    if world.props.get(f"{name}_halted"):
        receipt = "Trading is halted; your strategy waits."
    elif view is not None and _stopped_out(view, state["p"]):
        receipt = "Stop-loss liquidation: " + " ".join(view.orders)
    elif rng.random() < state["p"]["activity"]:
        view = view or _View(world, name, cfg, trader, state)
        decide(view, state["p"], rng)
        receipt = " ".join(view.orders) if view.orders else "Your strategy placed no orders."
    world.set_prop(trader, p["algo"], _copy_state(state))
    world.set_world(f"{name}_receipt", receipt)
    return receipt


def _setting(world: Any, name: str, field: str, raw: Any, trader: Entity, default: float) -> float:
    """A book setting that is a number or an expression read now (``$actor`` is the trader)."""
    return default if raw is None else number(world, raw, f"mechanisms.{name}.{field}", actor=trader)


def _stopped_out(v: _View, p: dict[str, float]) -> bool:
    """Track the position's entry price; liquidate at market when its loss passes the stop. True when it did."""
    held, entry = float(v.state.get("held", 0.0)), float(v.state.get("entry", v.last))
    inventory = v.inventory
    if not inventory or not held or (inventory > 0) != (held > 0):
        entry = v.last
    elif abs(inventory) > abs(held):
        entry = (entry * abs(held) + v.last * (abs(inventory) - abs(held))) / abs(inventory)
    v.state["held"], v.state["entry"] = inventory, entry
    limit = _clamp(p["stop_loss"] * v.sigma, 0.02, 0.15) * abs(inventory) * entry
    if not inventory or inventory * (entry - v.last) <= limit:
        return False
    v.order("sell" if inventory > 0 else "buy", abs(inventory), kind="liquidation")
    if not v.orders:
        return False
    v.world.set_world(f"{v.name}_liquidations", int(v.world.props.get(f"{v.name}_liquidations") or 0) + 1)
    return True


def _copy_state(state: dict[str, Any]) -> dict[str, Any]:
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in state.items()}


def _period(v: _View, rounds: float) -> int:
    """Which period of ``rounds`` rounds (at least one) the current round is in, counting from 1."""
    return (v.world.round - 1) // max(1, int(rounds)) + 1


def _overrides(cfg: OrderBookConfig, strategy: str) -> dict[str, float | str]:
    crowd = cfg.crowd.get(strategy)  # type: ignore[call-overload]
    return dict(crowd.params) if crowd is not None else {}


def _market_maker(v: _View, p: dict[str, float], rng: Any) -> None:
    cancel_all(v.world, v.name, v.trader)
    last, bid, ask, mid = top(v.world, v.name)
    state = v.state
    if state.get("seen") != v.world.round:  # learn from last round's flow once a round
        net, gross = _outside_flow(v)
        ref = float(state.get("ref", mid if bid is not None and ask is not None else last))
        ref = (1 - LAST_WEIGHT) * ref + LAST_WEIGHT * last
        imbalance = net / (gross + v.base)  # -1 (everyone sold) … 1 (everyone bought); a trickle counts less
        state["ref"] = ref * math.exp(v.assumed * p["impact"] * imbalance)
        state["toxicity"] = (1 - TOXICITY_MEMORY) * abs(imbalance) + TOXICITY_MEMORY * float(state.get("toxicity", 0.0))
        state["seen"] = v.world.round
    centre_price = float(state["ref"])
    quote_qty = max(v.lot, v.base * p["quote_mult"])
    limit = max(quote_qty, v.base * p["inventory_mult"])
    half = max(1.0, p["half_spread_ticks"] + v.assumed * centre_price / v.tick * p["vol_mult"]) \
        * (1 + p["toxicity_mult"] * state["toxicity"])
    inventory = v.inventory
    if abs(inventory) > limit:
        v.order("sell" if inventory > 0 else "buy", min(abs(inventory) - limit / 2, quote_qty))
        held = props_for(v.name)
        inventory = (balance(v.world, Account(v.trader, held["shares"]))
                     + balance(v.world, Account(v.trader, held["reserved_shares"])) - float(v.state.get("start", 0.0)))
    load = _clamp(inventory / limit, -1.5, 1.5)
    centre = centre_price / v.tick - _clamp(inventory / limit, -1.0, 1.0) * half * 0.8
    for layer in range(int(p["layers"])):
        offset = half + layer * max(1.0, half * 0.8)
        bid_t, ask_t = math.floor(centre - offset), math.ceil(centre + offset)
        if ask_t <= bid_t:
            ask_t = bid_t + 1
        size = quote_qty * (1.0 + 0.6 * layer)
        v.order("buy", size * _clamp(1.0 - load, 0.25, 1.75), round(bid_t * v.tick, 10))
        v.order("sell", size * _clamp(1.0 + load, 0.25, 1.75), round(ask_t * v.tick, 10))


def _outside_flow(v: _View) -> tuple[float, float]:
    """Last round's aggressive (net, gross) quantity from everyone but market makers."""
    flow = v.world.props.get(f"{v.name}_flow") or {}
    sides = [sides for kind, sides in flow.items() if kind != "market_maker"]
    return (sum(float(s.get("buy", 0)) - float(s.get("sell", 0)) for s in sides),
            sum(float(s.get("buy", 0)) + float(s.get("sell", 0)) for s in sides))


def _momentum(v: _View, p: dict[str, float], rng: Any) -> None:
    lookback = max(2, int(p["lookback"]))
    if len(v.prices) <= lookback or v.prices[-lookback - 1] <= 0:
        return
    signal = v.prices[-1] / v.prices[-lookback - 1] - 1.0
    threshold = max(1e-6, p["threshold_sigma"] * v.sigma * math.sqrt(lookback))
    if v.inventory > 0 and signal < 0:
        v.order("sell", v.inventory)
        return
    if v.inventory < 0 and signal > 0:
        v.order("buy", -v.inventory)
        return
    qty = v.base * v.flow * p["size_mult"] * _clamp(abs(signal) / threshold, 1.0, 3.0)
    if signal > threshold:
        v.order("buy", min(qty, v.room("buy", p["position_mult"])))
    elif signal < -threshold:
        v.order("sell", min(qty, v.room("sell", p["position_mult"])))


def _mean_reversion(v: _View, p: dict[str, float], rng: Any) -> None:
    window = max(4, int(p["window"]))
    if len(v.prices) < window:
        return
    recent = v.prices[-window:]
    mean = sum(recent) / len(recent)
    std = math.sqrt(sum((x - mean) ** 2 for x in recent) / len(recent))
    if std <= 0:
        return
    z = (v.prices[-1] - mean) / std
    if v.inventory and abs(z) < 0.3:
        v.order("sell" if v.inventory > 0 else "buy", abs(v.inventory))
        return
    qty = v.base * p["size_mult"] * _clamp(abs(z) / p["z_threshold"], 1.0, 2.5)
    if z > p["z_threshold"] and v.ask is not None:
        price = v.ask - v.tick if v.bid is not None and v.ask - v.bid > v.tick * 1.5 else v.ask
        v.order("sell", min(qty, v.room("sell", p["position_mult"])), price)
    elif z < -p["z_threshold"] and v.bid is not None:
        price = v.bid + v.tick if v.ask is not None and v.ask - v.bid > v.tick * 1.5 else v.bid
        v.order("buy", min(qty, v.room("buy", p["position_mult"])), price)


def _fair_value(v: _View) -> float:
    raw = v.cfg.fair_value
    if raw is None:
        return float(v.world.props[f"{v.name}_value"])
    try:
        value = compile_expr(raw)(v.world.scope(actor=v.trader))
    except ExprError as exc:
        raise RunError(str(exc), f"mechanisms.{v.name}.fair_value") from None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunError(f"fair_value must be a number, got {value!r}", f"mechanisms.{v.name}.fair_value")
    return float(value)


def _fundamentalist(v: _View, p: dict[str, float], rng: Any) -> None:
    if v.state.get("noise_round") != _period(v, p["value_rounds"]):
        v.state["noise"] = rng.gauss(0.0, p["noise_sigma"] * v.sigma)
        v.state["noise_round"] = _period(v, p["value_rounds"])
    value = _fair_value(v) * (1.0 + v.state["noise"])
    if value <= 0 or v.mid <= 0:
        return
    gap = value / v.mid - 1.0
    margin = max(1e-6, p["margin_sigma"] * v.sigma)
    if v.inventory and abs(gap) < margin * 0.5 and ((v.inventory > 0 and gap <= 0) or (v.inventory < 0 and gap >= 0)):
        v.order("sell" if v.inventory > 0 else "buy", abs(v.inventory))
        return
    if abs(gap) < margin * 0.25:
        return
    qty = v.base * p["size_mult"] * _clamp(abs(gap) / margin, 0.25, 6.0)
    patient = rng.random() < p["patience"] * _clamp(margin / abs(gap), 0.2, 1.0)
    if gap > 0:
        price = (v.bid if v.bid is not None else v.mid - v.tick) if patient else None
        v.order("buy", min(qty, v.room("buy", p["position_mult"])), price)
    else:
        price = (v.ask if v.ask is not None else v.mid + v.tick) if patient else None
        v.order("sell", min(qty, v.room("sell", p["position_mult"])), price)


def _noise(v: _View, p: dict[str, float], rng: Any) -> None:
    tilt = v.sentiment * p["sentiment_sensitivity"]
    if len(v.prices) >= 3 and v.prices[-3] > 0:
        recent = v.prices[-1] / v.prices[-3] - 1.0
        tilt += _clamp(recent / max(1e-9, v.sigma), -1.0, 1.0) * p["herding"]
    side = "buy" if rng.random() < _clamp(0.5 + 0.5 * tilt, 0.05, 0.95) else "sell"
    qty = min(max(v.lot, v.base * v.flow * p["size_mult"] * math.exp(rng.gauss(0.0, p["size_sigma"]))),
              v.room(side, p["position_mult"]))
    if rng.random() < p["market_prob"]:
        if (v.ask if side == "buy" else v.bid) is not None:
            v.order(side, qty)
        return
    offset = rng.randint(0, 4) * v.tick
    if side == "buy":
        v.order("buy", qty, (v.bid if v.bid is not None else v.mid) - offset)
    else:
        v.order("sell", qty, (v.ask if v.ask is not None else v.mid) + offset)


def _passive(v: _View, p: dict[str, float], rng: Any) -> None:
    if v.state.get("side_round") != _period(v, p["side_rounds"]):
        v.state["side"] = "buy" if rng.random() < _clamp(0.5 + 0.5 * p["direction_bias"], 0.0, 1.0) else "sell"
        v.state["side_round"] = _period(v, p["side_rounds"])
    side = v.state["side"]
    if (v.ask if side == "buy" else v.bid) is None:
        return
    v.order(side, min(v.base * v.flow * p["size_mult"], v.room(side, p["position_mult"])))


_STRATEGIES: dict[str, Callable[[_View, dict[str, float], Any], None]] = {
    "market_maker": _market_maker, "momentum": _momentum, "mean_reversion": _mean_reversion,
    "fundamentalist": _fundamentalist, "noise": _noise, "passive": _passive,
}
