"""The ``market`` family's ``order_book`` mode: one instrument on a continuous limit order book.

Expands into trader props, world props (the book, last price, bar, fees), the ``<name>_tape`` and
``<name>_bars`` records, tools ``<name>_buy``/``_sell``/``_cancel``/``_cancel_all``/``_algo`` with
numeric bounds, a trading stage (or hooks into a declared one), open/close events, views (market,
depth, own orders, tape, account), the ``<name>_algo`` policy and the ``<name>_crowd`` types of coded traders,
metrics, outputs and the accounting invariant. Everything is ordinary contract data backed by
the native engine in :mod:`.order_book`.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..registry import MechanismError, mode
from . import book_functions  # noqa: F401  (registers $book … and the market op's order_book actions)
from ._common import conserve_invariant, display, fmt, in_words, pct, suggest
from .book_rules import rules_default
from .econ_base import money_prop
from .order_book import OrderBookConfig, crowd_type, props_for
from .traders import DEFAULTS

_LABELS = {"market_maker": "Market maker", "momentum": "Momentum trader", "mean_reversion": "Mean reverter",
           "fundamentalist": "Fundamentalist", "noise": "Noise trader", "passive": "Passive flow"}


def _who(name: str, cfg: OrderBookConfig) -> Any:
    """Who trades on the book: the ``who`` type and the book's crowd."""
    return [cfg.who, crowd_type(name)] if cfg.crowd else cfg.who


def _actions(name: str, cfg: OrderBookConfig, qty_type: str) -> dict[str, Any]:
    unit = in_words(cfg.instrument or name, name)  # a tool's description is never worked out
    who = _who(name, cfg)
    p = props_for(name)
    lot: Any = (f"$book({name}).lot" if isinstance(cfg.lot_size, str) else int(cfg.lot_size) if qty_type == "int"
                else cfg.lot_size)
    if any(isinstance(value, str) for value in (cfg.tick_size, cfg.lot_size, cfg.maker_fee_bps, cfg.taker_fee_bps)):
        rules = ("Prices are whole ticks and quantities whole lots; fees are charged when your resting order fills "
                 "and when you trade against the book ($book shows the tick, lot and fees).")
        multiple = "the lot"
    else:
        maker = float(cfg.maker_fee_bps)
        rules = (f"Tick {fmt(float(cfg.tick_size), 6)}, lot {fmt(float(cfg.lot_size), 6)}; "
                 + (f"a rebate of {fmt(-maker)} bps paid to you" if maker < 0 else f"fees {fmt(maker)} bps")
                 + f" when your resting order fills, {fmt(float(cfg.taker_fee_bps))} bps when you trade against the "
                   "book.")
        multiple = fmt(float(cfg.lot_size), 6)
    collar = "the market-order collar" if isinstance(cfg.collar_pct, str) else pct(cfg.collar_pct)
    receipt = f"{{$world.{name}_receipt}}"
    manual = {"expr": f"$actor.{p['strategy']} == ''", "why": f"Your coded strategy trades for you; use {name}_algo."}
    halted = {"expr": f"not $world.{name}_halted",
              "why": "Trading is halted by the circuit breaker; you can only cancel orders."}
    has_orders = {"expr": f"$len($book_orders({name}, $actor)) > 0", "why": "You have no resting orders."}

    def order(side: str) -> dict[str, Any]:
        other = "sell" if side == "buy" else "buy"
        edge = "at or below" if side == "buy" else "at or above"
        reserve = "cash (price × qty + maker fee)" if side == "buy" else "shares"
        bound = "max_buy" if side == "buy" else "max_sell"
        return {
            "by": who,
            "description": (f"{side.capitalize()} {unit}. With a price it is a limit order: it trades at once against "
                            f"{other} orders {edge} your price (best price first, then oldest), possibly in part, and "
                            f"the rest rests on the book with its {reserve} reserved. Without a price it is a market "
                            f"order that trades within {collar} of the best {'ask' if side == 'buy' else 'bid'}; the "
                            "rest is cancelled. "
                            + rules),
            "params": {
                "qty": {"type": qty_type, "min": lot, "max": f"$book_account({name}, $actor).{bound}",
                        "description": f"Quantity, a multiple of {multiple}."},
                "price": {"type": "number", "min": f"$book({name}).band_low", "max": f"$book({name}).band_high",
                          "required": False, "description": "Limit price; leave out for a market order."},
            },
            "when": [manual, halted, {"expr": f"$book_account({name}, $actor).{bound} >= {lot}",
                              "why": ("You have no free cash to buy with; cancel a resting buy to free some." if side
                                      == "buy"
                                      else "You have no free shares to sell; cancel a resting sell to free some.")}],
            "do": [{"market": name, "action": side, "qty": "$params.qty", "price": "$params.price"}],
            "outcome": receipt, "private": True,
        }

    return {
        f"{name}_buy": order("buy"),
        f"{name}_sell": order("sell"),
        f"{name}_cancel": {
            "by": who, "description": f"Cancel one of your resting {unit} orders and release what it reserved.",
            "params": {"order": {"type": "enum", "values": f"$map($book_orders({name}, $actor), $it.id)",
                                 "description": "Id of your resting order."}},
            "when": [manual, has_orders], "do": [{"market": name, "action": "cancel", "order": "$params.order"}],
            "outcome": receipt, "private": True,
        },
        f"{name}_cancel_all": {
            "by": who, "description": f"Cancel all your resting {unit} orders at once (before requoting).",
            "when": [manual, has_orders], "per_turn": 1,
            "do": [{"market": name, "action": "cancel_all"}], "outcome": receipt, "private": True,
        },
        f"{name}_algo": {
            "by": who, "description": f"Let your coded {unit} trading strategy act for this turn.",
            "when": [{"expr": f"$actor.{p['strategy']} != ''", "why": "You have no coded strategy."}],
            "do": [{"market": name, "action": "algo"}], "outcome": receipt, "private": True,
            "terminal": True,
        },
    }


def _views(name: str, cfg: OrderBookConfig) -> dict[str, Any]:
    unit = display(cfg.instrument or name)
    who = _who(name, cfg)
    book = f"$book({name})"
    acct = f"$book_account({name}, $actor)"
    return {
        f"{name}_market": {
            "for": who, "title": unit,
            "show": (f"Last {{{book}.last|money}} · best bid {{{book}.bid|money}} × {{{book}.bid_qty}} · best ask "
                     f"{{{book}.ask|money}} × {{{book}.ask_qty}} · this round: open {{{book}.open|money}}, high "
                     f"{{{book}.high|money}}, low {{{book}.low|money}}, volume {{{book}.round_volume}} · VWAP "
                     f"{{{book}.vwap|money}} · limit prices {{{book}.band_low|money}}–{{{book}.band_high|money}}"
                     f"{{$' · TRADING HALTED' if {book}.halted else ''}}"),
        },
        f"{name}_depth": {
            "for": who, "title": f"{unit} order book (asks above, bids below)",
            "of": f"$book_depth({name}, {cfg.depth_levels}, $actor)", "bullet": False,
            "show": "{side} {qty} @ {price|money} ({orders} "
                    "order(s){$', yours ' + $text($it.mine) if $it.mine > 0 else ''})",
            "empty": "The book is empty.",
        },
        f"{name}_orders": {
            "for": who, "title": f"Your resting {unit} orders", "of": f"$book_orders({name}, $actor)",
            "show": "[{id}] {side} {qty} @ {price|money}, placed round {round}", "empty": "You have no resting orders.",
        },
        f"{name}_tape": {
            "for": who, "title": f"Recent {unit} trades (newest first)",
            "of": f"$reverse($slice($records({name}_tape), -5))",
            "show": "round {round}: {qty} @ {price|money} ({aggressor}-initiated)", "empty": "No trades yet.",
        },
        f"{name}_account": {
            "for": who, "title": f"Your {unit} account",
            "show": (f"Cash {{{acct}.cash|money}} free + {{{acct}.reserved_cash|money}} reserved · shares "
                     f"{{{acct}.shares}} free + {{{acct}.reserved_shares}} reserved · equity {{{acct}.equity|money}} · "
                     f"P&L {{{acct}.pnl|money}} · fees paid {{{acct}.fees_paid|money}}"),
        },
    }


@mode("market", "order_book", OrderBookConfig,
           "One instrument on a continuous limit order book with price-time priority, partial fills, tick and lot "
           "sizes, maker/taker fees, market-order collars, optional short selling, order expiry, OHLCV bars of "
           "`bar_rounds` rounds and a circuit breaker (measured from the round's open, the bar's open or a rolling "
           "window; checked on every trade or at each round's end; halting for some rounds or to the end of the bar). "
           "The venue's numbers (tick_size, lot_size, fees, collar_pct, price_band_pct, halt_pct, halt_rounds, "
           "halt_window, short_limit, max_short_leverage, order_ttl, max_orders, bar_rounds) may be expressions over "
           "$inputs (like world defaults), resolved once when the world is built into $world.<name>_rules, so they "
           "can follow the price level and be swept or calibrated. Tools `<name>_buy` / `<name>_sell` (limit with a "
           "price, market without), `<name>_cancel`, `<name>_cancel_all` and `<name>_algo` (coded strategy). Each "
           "`who` trader holds the instrument in `<name>_shares` and money in `currency` (give traders their starting "
           "position there, e.g. \"acme_shares\": 100). Resting orders reserve cash or shares; trades settle with "
           "conserved transfers and fees go to $world.<name>_fees. Read the book with $book(name), $book_depth(name, "
           "levels, viewer), $book_orders(name, trader), $book_account(name, trader); trades are in the `<name>_tape` "
           "record and OHLCV bars {bar, open, high, low, close, volume, vwap, trades, halted, flow} in "
           "`<name>_bars`; $book(name).bar is the bar in progress. The book opens and closes each round once, in its "
           "own start and end events, which run after yours: an end event that reads the round or bar the book closes "
           "runs {\"market\": name, \"action\": \"close\"} first. `crowd` adds coded traders (market_maker, "
           "momentum, mean_reversion, fundamentalist, noise, passive) as types `<name>_<strategy>` extending "
           "`<name>_crowd`, which holds what a `who` trader holds but is not of that type, so other mechanisms on "
           "`who` (a ballot, a winner's candidates) leave the crowd out; any trader whose `<name>_strategy` prop names "
           "a strategy trades only through `<name>_algo` (the `<name>_algo` policy calls it). A strategy with a "
           "`stop_loss` param (in multiples of the per-round volatility) liquidates a losing position at market. "
           "$book(name).flow is the last round's aggressive quantity by trader kind. Fundamentalists estimate "
           "`fair_value`, by default $world.<name>_value: a random walk from the start price at the book's "
           "`volatility`. Metrics <name>_price (last trade), _mid (mid quote: returns without the bid-ask bounce; "
           "the one side's best price while the other side is empty), _volume, _spread (null while a side of the "
           "book is empty), _orders feed $market_realism. The `idle` participant idles the crowd too: its traders "
           "are agents, and a participant given for every agent plays them all.",
           example={"who": "trader", "start_price": 50, "tick_size": 0.01, "taker_fee_bps": 5,
                    "halt_pct": 0.1, "crowd": {"market_maker": {"count": 2, "cash": 20000, "shares": 400},
                                               "noise": {"count": 6, "cash": 5000, "shares": 100}}},
           context={"types": {"trader": {"agent": True, "props": {"cash": 1000}}}})
def _expand_order_book(name: str, cfg: OrderBookConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    types = contract.get("types") or {}
    if cfg.who not in types:
        raise MechanismError(f"who '{cfg.who}' is not a declared type", f"types: {', '.join(types) or 'none'}", "who")
    if not _is_agent(types, cfg.who):
        raise MechanismError(f"who '{cfg.who}' must be an agent type", "set \"agent\": true on it", "who")
    for strategy, spec in cfg.crowd.items():
        unknown = next((key for key in spec.params if key not in DEFAULTS[strategy]), None)
        if unknown is not None:
            raise MechanismError(f"'{unknown}' is not a parameter of the {strategy} strategy",
                                 suggest(unknown, DEFAULTS[strategy]), f"crowd.{strategy}.params.{unknown}")
    p = props_for(name)
    qty_type = "int" if not isinstance(cfg.lot_size, str) and float(cfg.lot_size).is_integer() else "number"
    unit, shown_unit = in_words(cfg.instrument or name, name), display(cfg.instrument or name)
    fragment: dict[str, Any] = {
        "types": {cfg.who: {"props": {
            **money_prop(contract, cfg.who, cfg.currency, "Free cash."),
            p["shares"]: {"type": qty_type, "default": 0, "description": f"Free {unit} shares."},
            p["reserved_cash"]: {"type": "number", "default": 0, "private": True,
                                 "description": f"Cash reserved by resting {unit} buys."},
            p["reserved_shares"]: {"type": qty_type, "default": 0, "private": True,
                                   "description": f"Shares reserved by resting {unit} sells."},
            p["fees_paid"]: {"type": "number", "default": 0, "private": True},
            p["start_value"]: {"type": "number", "default": 0, "private": True,
                               "description": "Equity when trading opened."},
            p["strategy"]: {"type": "text", "default": "", "private": True, "description": "Coded strategy, if any."},
            p["algo"]: {"type": "map", "default": {}, "private": True},
        }}},
        "world": {
            f"{name}_bids": {"type": "list", "default": []}, f"{name}_asks": {"type": "list", "default": []},
            f"{name}_seq": {"type": "int", "default": 0},
            f"{name}_last": {"type": "number", "default": cfg.start_price, "description": f"Last traded {unit} price."},
            f"{name}_rules": {"type": "map", "default": rules_default(name, cfg),
                              "description": "The venue's rules (tick_size, lot_size, fees, collar, band, breaker, "
                                             "short limits, order expiry, bar_rounds), resolved when the world was "
                                             "built."},
            f"{name}_ref": {"type": "number", "default": cfg.start_price,
                            "description": "Circuit-breaker reference price."},
            f"{name}_halted": {"type": "bool", "default": False}, f"{name}_halt_until": {"type": "int", "default": 0},
            f"{name}_halts": {"type": "int", "default": 0},
            f"{name}_fees": {"type": "number", "default": 0, "description": "Fees collected by the venue."},
            f"{name}_bar": {"type": "map", "default": {}, "description": "The round in progress."},
            f"{name}_current_bar": {"type": "map", "default": {},
                                    "description": "The bar in progress, up to its last closed round."},
            f"{name}_opened": {"type": "int", "default": 0}, f"{name}_closed": {"type": "int", "default": 0},
            f"{name}_volume": {"type": "number", "default": 0},
            f"{name}_notional": {"type": "number", "default": 0}, f"{name}_trades": {"type": "int", "default": 0},
            f"{name}_closes": {"type": "list", "default": []},
            f"{name}_flow": {"type": "map", "default": {},
                             "description": "Last round's aggressive quantity by trader kind."},
            f"{name}_liquidations": {"type": "int", "default": 0, "description": "Stop-loss liquidations so far."},
            f"{name}_receipt": {"type": "text", "default": ""},
            **({f"{name}_value": {"type": "number", "default": cfg.start_price, "private": True,
                                  "description": "The fair value fundamentalists estimate: a random walk from the "
                                                 "start price at the book's volatility."}} if cfg.fair_value is None
               else {}),
        },
        "records": {
            f"{name}_tape": {"fields": {"price": "number", "qty": "number", "aggressor": "text"}, "keep": cfg.tape,
                             "notify": False, "show": "{qty} @ {price|money} ({aggressor}-initiated)",
                             "description": f"Recent {unit} trades."},
            f"{name}_bars": {"fields": {"bar": "int", "open": "number", "high": "number", "low": "number",
                                        "close": "number",
                                        "volume": "number", "vwap": "number", "trades": "int", "halted": "bool",
                                        "flow": "map"},
                             "notify": False,
                             "show": ("round {round}" if cfg.bar_rounds == 1 else "bar {bar}")
                                     + ": O {open|money} H {high|money} L {low|money} C {close|money} V {volume}",
                             "description": f"{unit} OHLCV per {'round' if cfg.bar_rounds == 1 else 'bar'}, with "
                                            "whether a halt tripped in it and its aggressive flow by trader kind."},
        },
        "actions": _actions(name, cfg, qty_type),
        "events": [{"name": f"{name}_open", "phase": "start", "do": [{"market": name, "action": "open"}]},
                   {"name": f"{name}_close", "phase": "end", "do": [{"market": name, "action": "close"}]}],
        "views": _views(name, cfg),
        "policies": {f"{name}_algo": {"rules": [{"do": f"{name}_algo"}, {"do": "pass"}]}},
        "metrics": {f"{name}_price": f"$world.{name}_last", f"{name}_mid": f"$book({name}).mid",
                    f"{name}_volume": f"$get($world.{name}_bar, volume, 0)",
                    f"{name}_spread": f"$book({name}).spread", f"{name}_orders": f"$book({name}).orders"},
        "outputs": {
            f"{name}_last_price": {"expr": f"$world.{name}_last", "type": "number",
                                   "description": f"Last {unit} price."},
            f"{name}_vwap": {"expr": f"$book({name}).vwap or $world.{name}_last", "type": "number",
                             "description": "Volume-weighted average trade price."},
            f"{name}_volume": {"expr": f"$world.{name}_volume", "type": "number", "description": "Quantity traded."},
            f"{name}_trades": {"expr": f"$world.{name}_trades", "type": "int", "description": "Number of fills."},
            f"{name}_halts": {"expr": f"$world.{name}_halts", "type": "int", "description": "Circuit-breaker halts."},
            f"{name}_fees": {"expr": f"$round($world.{name}_fees, 4)", "type": "number",
                             "description": "Fees collected."},
            f"{name}_volatility": {"expr": f"$market_stats($series.{name}_price).sigma", "type": "number",
                                   "description": "Volatility per round: the standard deviation of per-round log "
                                                  "returns."},
        },
    }
    names: list[str] = list(fragment["actions"])
    if cfg.stage is None:
        fragment["stages"] = [{"name": name, "turns": "sequential", "actions": names, "max_actions": cfg.max_actions,
                               "order": f"0 if $it.type == '{name}_market_maker' else 1 + "
                                        "$uniform(0, 1)",  # a type is public
                               "brief": f"Trade {shown_unit}: buy, sell, cancel, or end your turn."}]
    else:
        fragment["stage_hooks"] = {cfg.stage: {"actions": names, "max_actions": cfg.max_actions}}
    invariants = conserve_invariant(cfg.conserve, f"$book_ok({name})",
                                    f"The {shown_unit} book's reserves match its resting orders, balances stay within "
                                    "limits and the book is never crossed.")
    if invariants:
        fragment["invariants"] = invariants
    if cfg.crowd:
        fragment["population"] = []
        fragment["types"][crowd_type(name)] = {
            "agent": True, "policy": f"{name}_algo", "description": f"A coded {unit} trader of the book's crowd.",
            "props": _props_of(types, cfg.who, fragment["types"][cfg.who]["props"])}
        for kind, spec in cfg.crowd.items():
            fragment["types"][f"{name}_{kind}"] = {
                "extends": crowd_type(name), "description": f"Coded {_LABELS[kind].lower()}.",
                "props": {p["strategy"]: {"type": "text", "default": kind, "private": True}}}
            fragment["population"].append({"type": f"{name}_{kind}", "count": spec.count,
                                           "name": f"{_LABELS[kind]} {{$i}}",
                                           "props": {cfg.currency: spec.cash, p["shares"]: spec.shares}})
    return fragment


def _props_of(types: Mapping[str, Any], name: str, generated: Mapping[str, Any]) -> dict[str, Any]:
    """The ``generated`` props overlaid with those ``name`` declares or inherits (on contract data), so a crowd trader
    holds what a trader holds without being one: each override changes only the fields it writes."""
    lineage: list[str] = []
    current: Any = name
    while isinstance(current, str) and isinstance(types.get(current), Mapping) and current not in lineage:
        lineage.insert(0, current)
        current = types[current].get("extends")
    props: dict[str, Any] = dict(generated)
    for kind in lineage:
        for prop, value in (types[kind].get("props") or {}).items():
            inherited = props.get(prop)
            if isinstance(inherited, Mapping) and not isinstance(value, Mapping):
                value = {**inherited, "default": value}
            elif isinstance(inherited, Mapping):
                value = {**inherited, **value}
            props[prop] = value
    return props


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
