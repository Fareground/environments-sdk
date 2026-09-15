"""The ``market`` family's ``order_book`` mode: one instrument on a continuous limit order book.

Expands into trader props, world props (the book, last price, bar, fees), the ``<name>_tape`` and
``<name>_bars`` records, tools ``<name>_buy``/``_sell``/``_cancel``/``_cancel_all``/``_algo`` with
numeric bounds, a trading stage (or hooks into a declared one), open/close events, views (market,
depth, own orders, tape, account), the ``<name>_algo`` policy and crowd subtypes for coded traders,
metrics, outputs and the conservation invariant. Everything is ordinary contract data backed by
the native engine in :mod:`.order_book`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

from ..registry import MechanismError, mode
from .common import fmt
from .order_book import OrderBookConfig, props_for

_LABELS = {"market_maker": "Market maker", "momentum": "Momentum trader", "mean_reversion": "Mean reverter",
           "fundamentalist": "Fundamentalist", "noise": "Noise trader"}


def _actions(name: str, cfg: OrderBookConfig, qty_type: str) -> Dict[str, Any]:
    unit = cfg.instrument or name
    p = props_for(name)
    lot = int(cfg.lot_size) if qty_type == "int" else cfg.lot_size
    rules = (f"Tick {fmt(cfg.tick_size, 6)}, lot {fmt(cfg.lot_size, 6)}; fees {fmt(cfg.maker_fee_bps)} bps when your resting "
             f"order fills, {fmt(cfg.taker_fee_bps)} bps when you trade against the book.")
    receipt = f"{{$world.{name}_receipt}}"
    manual = {"expr": f"$actor.{p['strategy']} == ''", "why": f"Your coded strategy trades for you; use {name}_algo."}
    halted = {"expr": f"not $world.{name}_halted",
              "why": "Trading is halted by the circuit breaker; you can only cancel orders."}
    has_orders = {"expr": f"$len($book_orders({name}, $actor)) > 0", "why": "You have no resting orders."}

    def order(side: str) -> Dict[str, Any]:
        other = "sell" if side == "buy" else "buy"
        edge = "at or below" if side == "buy" else "at or above"
        reserve = "cash (price × qty + maker fee)" if side == "buy" else "shares"
        bound = "max_buy" if side == "buy" else "max_sell"
        return {
            "by": cfg.who,
            "description": (f"{side.capitalize()} {unit}. With a price it is a limit order: it trades at once against {other} "
                            f"orders {edge} your price (best price first, then oldest), possibly in part, and the rest rests "
                            f"on the book with its {reserve} reserved. Without a price it is a market order that trades within "
                            f"{cfg.collar_pct:.0%} of the best {'ask' if side == 'buy' else 'bid'}; the rest is cancelled. "
                            + rules),
            "params": {
                "qty": {"type": qty_type, "min": lot, "max": f"$book_account({name}, $actor).{bound}",
                        "description": f"Quantity, a multiple of {fmt(cfg.lot_size, 6)}."},
                "price": {"type": "number", "min": f"$book({name}).band_low", "max": f"$book({name}).band_high",
                          "required": False, "description": "Limit price; leave out for a market order."},
            },
            "when": [manual, halted, {"expr": f"$book_account({name}, $actor).{bound} >= {lot}",
                              "why": ("You have no free cash to buy with; cancel a resting buy to free some." if side == "buy"
                                      else "You have no free shares to sell; cancel a resting sell to free some.")}],
            "do": [{"market": name, "action": side, "qty": "$params.qty", "price": "$params.price"}],
            "outcome": receipt, "private": True,
        }

    return {
        f"{name}_buy": order("buy"),
        f"{name}_sell": order("sell"),
        f"{name}_cancel": {
            "by": cfg.who, "description": f"Cancel one of your resting {unit} orders and release what it reserved.",
            "params": {"order": {"type": "enum", "values": f"$map($book_orders({name}, $actor), $it.id)",
                                 "description": "Id of your resting order."}},
            "when": [manual, has_orders], "do": [{"market": name, "action": "cancel", "order": "$params.order"}],
            "outcome": receipt, "private": True,
        },
        f"{name}_cancel_all": {
            "by": cfg.who, "description": f"Cancel all your resting {unit} orders at once (before requoting).",
            "when": [manual, has_orders], "per_turn": 1,
            "do": [{"market": name, "action": "cancel_all"}], "outcome": receipt, "private": True,
        },
        f"{name}_algo": {
            "by": cfg.who, "description": f"Let your coded {unit} trading strategy act for this turn.",
            "when": [{"expr": f"$actor.{p['strategy']} != ''", "why": "You have no coded strategy."}],
            "do": [{"market": name, "action": "algo"}], "outcome": receipt, "private": True,
            "terminal": True,
        },
    }


def _views(name: str, cfg: OrderBookConfig) -> Dict[str, Any]:
    unit = cfg.instrument or name
    book = f"$book({name})"
    acct = f"$book_account({name}, $actor)"
    return {
        f"{name}_market": {
            "for": cfg.who, "title": unit,
            "show": (f"Last {{{book}.last|money}} · best bid {{{book}.bid|money}} × {{{book}.bid_qty}} · best ask "
                     f"{{{book}.ask|money}} × {{{book}.ask_qty}} · this round: open {{{book}.open|money}}, high "
                     f"{{{book}.high|money}}, low {{{book}.low|money}}, volume {{{book}.round_volume}} · VWAP "
                     f"{{{book}.vwap|money}} · limit prices {{{book}.band_low|money}}–{{{book}.band_high|money}}"
                     f"{{$' · TRADING HALTED' if {book}.halted else ''}}"),
        },
        f"{name}_depth": {
            "for": cfg.who, "title": f"{unit} order book (asks above, bids below)",
            "of": f"$book_depth({name}, {cfg.depth_levels}, $actor)", "bullet": False,
            "show": "{side} {qty} @ {price|money} ({orders} order(s){$', yours ' + $text($it.mine) if $it.mine > 0 else ''})",
            "empty": "The book is empty.",
        },
        f"{name}_orders": {
            "for": cfg.who, "title": f"Your resting {unit} orders", "of": f"$book_orders({name}, $actor)",
            "show": "[{id}] {side} {qty} @ {price|money}, placed round {round}", "empty": "You have no resting orders.",
        },
        f"{name}_tape": {
            "for": cfg.who, "title": f"Recent {unit} trades (newest first)",
            "of": f"$reverse($slice($records({name}_tape), -5))",
            "show": "round {round}: {qty} @ {price|money} ({aggressor}-initiated)", "empty": "No trades yet.",
        },
        f"{name}_account": {
            "for": cfg.who, "title": f"Your {unit} account",
            "show": (f"Cash {{{acct}.cash|money}} free + {{{acct}.reserved_cash|money}} reserved · shares {{{acct}.shares}} "
                     f"free + {{{acct}.reserved_shares}} reserved · equity {{{acct}.equity|money}} · P&L {{{acct}.pnl|money}}"
                     f" · fees paid {{{acct}.fees_paid|money}}"),
        },
    }


@mode("market", "order_book", OrderBookConfig,
           "One instrument on a continuous limit order book with price-time priority, partial fills, tick and lot sizes, "
           "maker/taker fees, market-order collars, optional short selling, order expiry and a circuit breaker. Tools "
           "`<name>_buy` / `<name>_sell` (limit with a price, market without), `<name>_cancel`, `<name>_cancel_all` and "
           "`<name>_algo` (coded strategy). Resting orders reserve cash or shares; trades settle with conserved transfers "
           "and fees go to $world.<name>_fees. Read the book with $book(name), $book_depth(name, levels, viewer), "
           "$book_orders(name, trader), $book_account(name, trader); trades are in the `<name>_tape` record and per-round "
           "OHLCV bars in `<name>_bars`. `crowd` adds coded traders (market_maker, momentum, mean_reversion, fundamentalist, "
           "noise) as subtypes `<name>_<strategy>`; any trader whose `<name>_strategy` prop names a strategy trades only "
           "through `<name>_algo` (the `<name>_algo` policy calls it). Metrics <name>_price, _volume, _spread, _orders feed $market_realism.",
           example={"who": "trader", "start_price": 50, "tick_size": 0.01, "taker_fee_bps": 5,
                    "halt_pct": 0.1, "crowd": {"market_maker": {"count": 2, "cash": 20000, "shares": 400},
                                               "noise": {"count": 6, "cash": 5000, "shares": 100}}}, was="order_book")
def _expand_order_book(name: str, cfg: OrderBookConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    types = contract.get("types") or {}
    if cfg.who not in types:
        raise MechanismError(f"who '{cfg.who}' is not a declared type", f"types: {', '.join(types) or 'none'}", "who")
    if not _is_agent(types, cfg.who):
        raise MechanismError(f"who '{cfg.who}' must be an agent type", "set \"agent\": true on it", "who")
    p = props_for(name)
    qty_type = "int" if float(cfg.lot_size).is_integer() else "number"
    unit = cfg.instrument or name
    fragment: Dict[str, Any] = {
        "types": {cfg.who: {"props": {
            cfg.currency: {"type": "number", "default": 0, "description": "Free cash."},
            p["shares"]: {"type": qty_type, "default": 0, "description": f"Free {unit} shares."},
            p["reserved_cash"]: {"type": "number", "default": 0, "private": True, "description": f"Cash reserved by resting {unit} buys."},
            p["reserved_shares"]: {"type": qty_type, "default": 0, "private": True, "description": f"Shares reserved by resting {unit} sells."},
            p["fees_paid"]: {"type": "number", "default": 0, "private": True},
            p["start_value"]: {"type": "number", "default": 0, "private": True, "description": "Equity when trading opened."},
            p["strategy"]: {"type": "text", "default": "", "private": True, "description": "Coded strategy, if any."},
            p["algo"]: {"type": "map", "default": {}, "private": True},
        }}},
        "world": {
            f"{name}_bids": {"type": "list", "default": []}, f"{name}_asks": {"type": "list", "default": []},
            f"{name}_seq": {"type": "int", "default": 0},
            f"{name}_last": {"type": "number", "default": cfg.start_price, "description": f"Last traded {unit} price."},
            f"{name}_ref": {"type": "number", "default": cfg.start_price, "description": "Circuit-breaker reference price."},
            f"{name}_halted": {"type": "bool", "default": False}, f"{name}_halt_until": {"type": "int", "default": 0},
            f"{name}_halts": {"type": "int", "default": 0},
            f"{name}_fees": {"type": "number", "default": 0, "description": "Fees collected by the venue."},
            f"{name}_bar": {"type": "map", "default": {}}, f"{name}_volume": {"type": "number", "default": 0},
            f"{name}_notional": {"type": "number", "default": 0}, f"{name}_trades": {"type": "int", "default": 0},
            f"{name}_closes": {"type": "list", "default": []}, f"{name}_supply": {"type": "map", "default": {}},
            f"{name}_receipt": {"type": "text", "default": ""},
        },
        "records": {
            f"{name}_tape": {"fields": {"price": "number", "qty": "number", "aggressor": "text"}, "keep": cfg.tape,
                             "notify": False, "show": "{qty} @ {price|money} ({aggressor}-initiated)",
                             "description": f"Recent {unit} trades."},
            f"{name}_bars": {"fields": {"open": "number", "high": "number", "low": "number", "close": "number",
                                        "volume": "number", "vwap": "number", "trades": "int"}, "notify": False,
                             "show": "round {round}: O {open|money} H {high|money} L {low|money} C {close|money} V {volume}",
                             "description": f"{unit} OHLCV per round."},
        },
        "actions": _actions(name, cfg, qty_type),
        "events": [{"name": f"{name}_open", "phase": "start", "do": [{"market": name, "action": "open"}]},
                   {"name": f"{name}_close", "phase": "end", "do": [{"market": name, "action": "close"}]}],
        "views": _views(name, cfg),
        "policies": {f"{name}_algo": {"rules": [{"do": f"{name}_algo"}, {"do": "pass"}]}},
        "metrics": {f"{name}_price": f"$world.{name}_last", f"{name}_volume": f"$get($world.{name}_bar, volume, 0)",
                    f"{name}_spread": f"$book({name}).spread or 0", f"{name}_orders": f"$book({name}).orders"},
        "outputs": {
            f"{name}_last_price": {"expr": f"$world.{name}_last", "type": "number", "description": f"Last {unit} price."},
            f"{name}_vwap": {"expr": f"$book({name}).vwap or $world.{name}_last", "type": "number",
                             "description": "Volume-weighted average trade price."},
            f"{name}_volume": {"expr": f"$world.{name}_volume", "type": "number", "description": "Quantity traded."},
            f"{name}_trades": {"expr": f"$world.{name}_trades", "type": "int", "description": "Number of fills."},
            f"{name}_halts": {"expr": f"$world.{name}_halts", "type": "int", "description": "Circuit-breaker halts."},
            f"{name}_fees": {"expr": f"$round($world.{name}_fees, 4)", "type": "number", "description": "Fees collected."},
            f"{name}_volatility": {"expr": f"$realized_vol($series.{name}_price)", "type": "number",
                                   "description": "Standard deviation of per-round log returns."},
        },
    }
    names: List[str] = list(fragment["actions"])
    if cfg.stage is None:
        fragment["stages"] = [{"name": name, "turns": "sequential", "actions": names, "max_actions": cfg.max_actions,
                               "order": f"0 if $get($it, '{p['strategy']}', '') == 'market_maker' else 1 + $random()",
                               "brief": f"Trade {unit}: buy, sell, cancel, or end your turn."}]
    else:
        fragment["stage_hooks"] = {cfg.stage: {"actions": names}}
    if cfg.conserve:
        fragment["invariants"] = [{"expr": f"$book_ok({name})",
                                   "why": f"The {unit} book conserves cash and shares, reserves match resting orders, "
                                          "balances stay within limits and the book is never crossed."}]
    if cfg.crowd:
        fragment["population"] = []
        for kind, spec in cfg.crowd.items():
            fragment["types"][f"{name}_{kind}"] = {
                "extends": cfg.who, "policy": f"{name}_algo", "description": f"Coded {_LABELS[kind].lower()}.",
                "props": {p["strategy"]: {"type": "text", "default": kind, "private": True}}}
            fragment["population"].append({"type": f"{name}_{kind}", "count": spec.count, "name": f"{_LABELS[kind]} {{$i}}",
                                           "props": {cfg.currency: spec.cash, p["shares"]: spec.shares}})
    return fragment


def _is_agent(types: Mapping[str, Any], name: str) -> bool:
    seen = set()
    current: Any = name
    while isinstance(current, str) and current in types and current not in seen:
        seen.add(current)
        spec = types[current] or {}
        if spec.get("agent"):
            return True
        current = spec.get("extends")
    return False
