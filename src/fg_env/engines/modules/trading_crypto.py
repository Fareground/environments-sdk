"""Trading PvP — real-time daytrading competition.

Each agent gets an ISOLATED portfolio. Same live security for everyone
(stock / crypto / forex / commodity), price fetched fresh from Yahoo
Finance each tick. Orders fill at the current tick's price (same-tick
fills — agents see the price, decide, fill immediately).

Per tick:
    1. fetch live price for the configured symbol
    2. emit `trading_pvp_tick` with current price + leaderboard
    3. each agent submits one action (buy/sell/short/cover/close_all/hold)
    4. orders resolve simultaneously at the SAME live price
    5. mark every portfolio to that price; bump round counter

Match ends after `num_ticks` rounds OR if the underlying market closes
mid-match (safety net). At end: leaderboard sorted by portfolio_value,
A unique top portfolio wins; equal top values at cent precision tie.
Emits `trading_pvp_match_over` with portfolio scores and an explicit draw flag.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


STAGE_TICK = "tick"
STAGE_OVER = "over"


class TradingPvPModule(DomainModule):
    """Drives a Trading PvP match — live-price daytrading, isolated portfolios."""

    @property
    def description(self) -> str:
        return ("Trading PvP: 2-10 agents daytrade a real security "
                "(stock / crypto / forex / commodity) with isolated "
                "portfolios. Live price each tick from Yahoo Finance. "
                "Highest portfolio value at the end wins.")

    @property
    def custom_actions(self) -> List[str]:
        return ["buy", "sell", "short", "cover", "close_all", "hold", "discuss"]

    def __init__(self, name: str = "trading_pvp", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Trader")
        self._symbol: str = str(p.get("symbol", "BTC-USD")).strip().upper()
        self._asset_class: str = str(p.get("asset_class", "")).strip().lower() or self._detect_asset_class(self._symbol)
        # Tick cadence — prefer the minute-grained user knob, fall back
        # to legacy `tick_seconds` for matches created before the
        # rename.
        if p.get("tick_minutes") not in (None, 0, "0"):
            self._tick_seconds: int = max(1, int(p.get("tick_minutes")) * 60)
        else:
            self._tick_seconds = max(1, int(p.get("tick_seconds", 60)))
        # `num_ticks` is the user-facing knob; match length =
        # num_ticks × tick_seconds. `duration_minutes` is the legacy
        # fallback (older matches stored their length that way).
        explicit_ticks = p.get("num_ticks")
        if explicit_ticks not in (None, 0, "0"):
            self._num_ticks: int = max(1, int(explicit_ticks))
        elif p.get("duration_minutes"):
            duration_minutes = max(1, int(p.get("duration_minutes")))
            self._num_ticks = max(1, (duration_minutes * 60) // self._tick_seconds)
        else:
            self._num_ticks = 30
        self._starting_cash: float = float(p.get("starting_cash", 100000.0))
        self._shorts_allowed: bool = bool(p.get("shorts_allowed", True))
        # Optional news/research context. Surfaced verbatim in each
        # agent's perception so they can incorporate qualitative signal.
        self._news_context: str = str(p.get("news_context", "") or "")
        # Track whether the underlying market is currently open. The
        # engine pacing layer reads `tick_seconds` to space ticks; this
        # module force-ends the match if the market closes mid-run.
        self._market_closed_mid_match: bool = False

        self._bootstrapped = False
        self._game_over = False
        self._round_did_resolve = False
        self._last_round_resolved = 0

        # Match state
        self._seat_order: List[str] = []
        self._tick_num: int = 0
        # Live price history for the configured symbol — list of
        # {tick, price, t_unix}. The latest entry is the current price.
        self._price_history: List[Dict[str, Any]] = []
        # Per-agent state. Each is {cash, position, entry_avg, realized_pnl,
        # portfolio_value, trades_made}. Position can be negative (short).
        self._portfolios: Dict[str, Dict[str, float]] = {}
        # Per-agent trade log — pid → list of dicts with side/qty/price/tick.
        # Surfaced in get_perception_data so each agent sees its own history.
        self._trade_history: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Asset class auto-detection
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_asset_class(symbol: str) -> str:
        s = symbol.upper()
        if s.endswith("-USD") or s.endswith("-USDT"):
            return "crypto"
        if s.endswith("=X"):
            return "forex"
        if s.endswith("=F"):
            return "commodities"
        return "stocks"

    # ------------------------------------------------------------------
    # Price feed
    # ------------------------------------------------------------------

    def _feed_timestamp(self) -> int:
        return _now_unix()

    def _market_is_open(self) -> bool:
        try:
            from services.market_hours import is_open
            return is_open(self._asset_class)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"trading_pvp market-hours check failed: {e}")
            return True

    def _fetch_live_price(self) -> Optional[float]:
        """Pull the current price for `self._symbol` from yfinance.
        Returns None on failure (caller decides whether to end the match
        or carry forward the last known price)."""
        try:
            from integrations.market_data import fetch_current_price
            return float(fetch_current_price(self._symbol))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"trading_pvp: live price fetch failed for {self._symbol}: {e}")
            return None

    def _seed_interval(self) -> str:
        """Pick a yfinance interval that matches the tick cadence —
        agents and humans see the same time-grain on the chart as the
        trading they're about to do. Largest supported interval <=
        tick_minutes:
            1m  for ticks 1-4
            5m  for ticks 5-14
            15m for ticks 15-29
            30m for ticks 30-59
            60m for ticks 60+
        """
        m = max(1, self._tick_seconds // 60)
        if m < 5:   return "1m"
        if m < 15:  return "5m"
        if m < 30:  return "15m"
        if m < 60:  return "30m"
        return "60m"

    def _fetch_seed_history(self) -> List[Dict[str, Any]]:
        """Pull recent OHLC candles at the tick-matching interval so
        the chart has real visual context at tick 0 instead of starting
        blank. Falls back silently on failure. Seed points get NEGATIVE
        tick numbers (-N..-1) so live ticks land at 0/1+."""
        try:
            from integrations.market_data import fetch_history
            candles = fetch_history(self._symbol, self._seed_interval())
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"trading_pvp: seed history fetch failed for "
                f"{self._symbol}@{self._seed_interval()}: {e}"
            )
            return []
        last = candles[-80:] if len(candles) > 80 else candles
        n = len(last)
        out: List[Dict[str, Any]] = []
        for i, c in enumerate(last):
            price = float(c.get("c", 0) or 0)
            if price <= 0:
                continue
            out.append({
                "tick": -(n - i),
                "price": price,
                "t_unix": int(c.get("t", 0) or 0),
                "seed": True,
            })
        return out

    def _log_trade(self, pid: str, side: str, qty: float, price: float) -> None:
        """Record an executed trade for the per-agent history feed."""
        if pid not in self._trade_history:
            self._trade_history[pid] = []
        self._trade_history[pid].append({
            "tick": self._tick_num,
            "side": side,
            "qty": float(qty),
            "price": float(price),
        })

    def _current_price(self) -> float:
        """The price agents are trading against right now. Falls back to
        the last known price (or starting cash heuristic) if the live
        feed has never returned."""
        if self._price_history:
            return float(self._price_history[-1]["price"])
        return 0.0

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _alive_players(self, state: Any) -> List[Any]:
        return [e for e in state.entities.values()
                if e.entity_type == self._player_type and e.alive]

    def _ensure_setup(self, state: Any) -> None:
        if self._bootstrapped:
            return
        players = self._alive_players(state)
        n = len(players)
        if n < 2:
            logger.warning(f"trading_pvp: needs ≥2 players, got {n}.")
            self._bootstrapped = True
            self._game_over = True
            return
        self._seat_order = [p.id for p in players]
        self._trade_history = {pid: [] for pid in self._seat_order}
        self._portfolios = {
            pid: {
                "cash": self._starting_cash,
                "position": 0.0,
                "entry_avg": 0.0,
                "realized_pnl": 0.0,
                "portfolio_value": self._starting_cash,
                "trades_made": 0,
            }
            for pid in self._seat_order
        }
        # Seed the chart with recent real candles so it isn't blank on
        # tick 0. These have NEGATIVE tick numbers and a `seed: true`
        # flag so the viz can style them differently from live ticks.
        self._price_history = self._fetch_seed_history()
        # Pull the opening live price so the first tick has something
        # to mark against.
        price = self._fetch_live_price() or (
            self._price_history[-1]["price"] if self._price_history else 0.0
        )
        self._price_history.append({
            "tick": 0, "price": price, "t_unix": self._feed_timestamp(),
        })
        self._sync_entities(state)
        self._bootstrapped = True

    def _sync_entities(self, state: Any) -> None:
        price = self._current_price()
        for pid in self._seat_order:
            pf = self._portfolios.get(pid, {})
            ent = state.get_entity(pid)
            if not ent:
                continue
            position = float(pf.get("position", 0.0))
            cash = float(pf.get("cash", 0.0))
            # Mark to market: portfolio_value = cash + position * price.
            # For shorts (position<0), this naturally subtracts (you'd
            # owe |position| units at the current price).
            pv = cash + position * price
            pf["portfolio_value"] = pv
            ent.set("cash", round(cash, 2))
            ent.set("position", round(position, 6))
            ent.set("entry_avg_price", round(float(pf.get("entry_avg", 0.0)), 6))
            ent.set("realized_pnl", round(float(pf.get("realized_pnl", 0.0)), 2))
            ent.set("portfolio_value", round(pv, 2))
            ent.set("trades_made", int(pf.get("trades_made", 0)))

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._ensure_setup(state)
        if self._game_over:
            return []

        events: List[Dict[str, Any]] = []
        if round_number > 1 and round_number - 1 > self._last_round_resolved:
            prev = round_number - 1
            self._round_did_resolve = False
            events.extend(self._resolve_tick(state, prev))
            if self._round_did_resolve:
                self._last_round_resolved = prev
            if self._game_over:
                return events

        # Pull a fresh live price for the next decision window. Skip on
        # the very first tick because bootstrap already pulled one.
        if self._tick_num > 0:
            new_price = self._fetch_live_price()
            if new_price is None and self._price_history:
                # Carry forward last known price rather than crashing.
                new_price = float(self._price_history[-1]["price"])
            if new_price is not None:
                self._price_history.append({
                    "tick": self._tick_num, "price": new_price, "t_unix": self._feed_timestamp(),
                })

        self._sync_entities(state)
        self._tick_num += 1
        leaderboard = self._leaderboard(state)

        events.append({
            "event_type": "trading_pvp_tick",
            "narrative": (f"Tick {self._tick_num}/{self._num_ticks} — "
                          f"{self._symbol} @ {self._current_price():.4f}"),
            "data": {
                "tick": self._tick_num,
                "total_ticks": self._num_ticks,
                "symbol": self._symbol,
                "asset_class": self._asset_class,
                "price": round(self._current_price(), 6),
                "price_history": list(self._price_history),
                "leaderboard": leaderboard,
                "shorts_allowed": self._shorts_allowed,
                "round": round_number,
            },
        })
        return events

    def _leaderboard(self, state: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        price = self._current_price()
        for pid in self._seat_order:
            pf = self._portfolios.get(pid, {})
            ent = state.get_entity(pid)
            position = float(pf.get("position", 0.0))
            cash = float(pf.get("cash", 0.0))
            pv = cash + position * price
            entry_avg = float(pf.get("entry_avg", 0.0))
            unrealized = (price - entry_avg) * position if position else 0.0
            rows.append({
                "entity_id": pid,
                "name": getattr(ent, "name", pid) if ent else pid,
                "cash": round(cash, 2),
                "position": round(position, 6),
                "entry_avg": round(entry_avg, 6),
                "realized_pnl": round(float(pf.get("realized_pnl", 0.0)), 2),
                "unrealized_pnl": round(unrealized, 2),
                "portfolio_value": round(pv, 2),
                "pnl_pct": round(((pv - self._starting_cash) / self._starting_cash) * 100.0, 3) if self._starting_cash else 0.0,
                "trades_made": int(pf.get("trades_made", 0)),
            })
        rows.sort(key=lambda r: -r["portfolio_value"])
        return rows

    # ------------------------------------------------------------------
    # Action resolution
    # ------------------------------------------------------------------

    def _resolve_tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        """Apply every agent's stashed action against the CURRENT live
        price simultaneously. Agents that didn't submit anything are
        treated as `hold`."""
        events: List[Dict[str, Any]] = []
        price = self._current_price()
        if price <= 0:
            # No price data — nothing to fill. Just advance.
            self._round_did_resolve = True
            self._maybe_end_match(state, events)
            return events

        for pid in self._seat_order:
            actor = state.get_entity(pid)
            if actor is None:
                continue
            buy_qty = actor.get(f"_buy_r{round_number}")
            sell_qty = actor.get(f"_sell_r{round_number}")
            short_qty = actor.get(f"_short_r{round_number}")
            cover_qty = actor.get(f"_cover_r{round_number}")
            close_all = actor.get(f"_close_r{round_number}")

            if close_all:
                events.extend(self._do_close_all(state, pid, price))
            elif _positive_float(buy_qty):
                events.extend(self._do_buy(state, pid, float(buy_qty), price))
            elif _positive_float(sell_qty):
                events.extend(self._do_sell(state, pid, float(sell_qty), price))
            elif _positive_float(short_qty) and self._shorts_allowed:
                events.extend(self._do_short(state, pid, float(short_qty), price))
            elif _positive_float(cover_qty):
                events.extend(self._do_cover(state, pid, float(cover_qty), price))
            # else: no submitted order → implicit hold.

        self._round_did_resolve = True
        self._sync_entities(state)
        self._maybe_end_match(state, events)
        return events

    # --- order helpers ---

    def _do_buy(self, state: Any, pid: str, qty: float, price: float) -> List[Dict[str, Any]]:
        pf = self._portfolios[pid]
        # Cap qty by available cash.
        max_qty = pf["cash"] / price if price > 0 else 0.0
        qty = self._order_units(min(qty, max_qty))
        if qty <= 0:
            return []
        cost = qty * price
        old_pos = pf["position"]
        new_pos = old_pos + qty

        if old_pos < 0:
            # Covering an existing short first.
            cover_qty = min(qty, abs(old_pos))
            pf["realized_pnl"] += (pf["entry_avg"] - price) * cover_qty
            remaining_buy = qty - cover_qty
            if remaining_buy > 0:
                # Now opening a fresh long for the leftover.
                pf["entry_avg"] = price
            elif new_pos == 0:
                pf["entry_avg"] = 0.0
        else:
            # Adding to a long → weighted-average entry.
            if new_pos > 0:
                pf["entry_avg"] = (
                    (pf["entry_avg"] * old_pos + price * qty) / new_pos
                )
        pf["position"] = new_pos
        pf["cash"] -= cost
        pf["trades_made"] = pf.get("trades_made", 0) + 1
        self._log_trade(pid, "buy", qty, price)
        return [{
            "event_type": "trading_pvp_order",
            "actor_id": pid,
            "narrative": f"{_actor_name(state, pid)} BUY {qty:.4f} @ {price:.4f}",
            "data": {
                "side": "buy", "qty": round(qty, 6), "price": round(price, 6),
                "tick": self._tick_num,
            },
        }]

    def _do_sell(self, state: Any, pid: str, qty: float, price: float) -> List[Dict[str, Any]]:
        pf = self._portfolios[pid]
        # Can only sell what you own (long position). For shorts, use cover.
        if pf["position"] <= 0:
            return []
        qty = self._order_units(min(qty, pf["position"]))
        if qty <= 0:
            return []
        proceeds = qty * price
        pf["realized_pnl"] += (price - pf["entry_avg"]) * qty
        pf["position"] -= qty
        pf["cash"] += proceeds
        if pf["position"] == 0:
            pf["entry_avg"] = 0.0
        pf["trades_made"] = pf.get("trades_made", 0) + 1
        self._log_trade(pid, "sell", qty, price)
        return [{
            "event_type": "trading_pvp_order",
            "actor_id": pid,
            "narrative": f"{_actor_name(state, pid)} SELL {qty:.4f} @ {price:.4f}",
            "data": {
                "side": "sell", "qty": round(qty, 6), "price": round(price, 6),
                "tick": self._tick_num,
            },
        }]

    def _do_short(self, state: Any, pid: str, qty: float, price: float) -> List[Dict[str, Any]]:
        pf = self._portfolios[pid]
        qty = self._order_units(qty)
        if qty <= 0:
            return []
        # Proceeds add to cash. Position decreases (becomes more negative).
        proceeds = qty * price
        old_pos = pf["position"]
        new_pos = old_pos - qty
        if old_pos <= 0:
            # Adding to existing short — weighted avg entry.
            total_short = -old_pos + qty
            if total_short > 0:
                pf["entry_avg"] = (
                    (pf["entry_avg"] * -old_pos + price * qty) / total_short
                )
        else:
            # Reducing a long via "short" — treat as a sell, then if
            # over, open a fresh short for the remainder.
            sell_qty = min(qty, old_pos)
            pf["realized_pnl"] += (price - pf["entry_avg"]) * sell_qty
            remaining_short = qty - sell_qty
            if remaining_short > 0:
                pf["entry_avg"] = price
            elif new_pos == 0:
                pf["entry_avg"] = 0.0
        pf["position"] = new_pos
        pf["cash"] += proceeds
        pf["trades_made"] = pf.get("trades_made", 0) + 1
        self._log_trade(pid, "short", qty, price)
        return [{
            "event_type": "trading_pvp_order",
            "actor_id": pid,
            "narrative": f"{_actor_name(state, pid)} SHORT {qty:.4f} @ {price:.4f}",
            "data": {
                "side": "short", "qty": round(qty, 6), "price": round(price, 6),
                "tick": self._tick_num,
            },
        }]

    def _do_cover(self, state: Any, pid: str, qty: float, price: float) -> List[Dict[str, Any]]:
        pf = self._portfolios[pid]
        if pf["position"] >= 0:
            return []  # nothing to cover
        qty = self._order_units(min(qty, abs(pf["position"])))
        if qty <= 0:
            return []
        cost = qty * price
        pf["realized_pnl"] += (pf["entry_avg"] - price) * qty
        pf["position"] += qty
        pf["cash"] -= cost
        if pf["position"] == 0:
            pf["entry_avg"] = 0.0
        pf["trades_made"] = pf.get("trades_made", 0) + 1
        self._log_trade(pid, "cover", qty, price)
        return [{
            "event_type": "trading_pvp_order",
            "actor_id": pid,
            "narrative": f"{_actor_name(state, pid)} COVER {qty:.4f} @ {price:.4f}",
            "data": {
                "side": "cover", "qty": round(qty, 6), "price": round(price, 6),
                "tick": self._tick_num,
            },
        }]

    def _do_close_all(self, state: Any, pid: str, price: float) -> List[Dict[str, Any]]:
        pf = self._portfolios[pid]
        pos = pf["position"]
        if pos == 0:
            return []
        if pos > 0:
            return self._do_sell(state, pid, pos, price)
        return self._do_cover(state, pid, abs(pos), price)

    # ------------------------------------------------------------------
    # Match end
    # ------------------------------------------------------------------

    def _maybe_end_match(self, state: Any, events: List[Dict[str, Any]]) -> None:
        if self._game_over:
            return
        # Mid-match safety net: if the underlying market closed since
        # we started, force-end at the last tick we had a live price for.
        if not self._market_closed_mid_match:
            if not self._market_is_open():
                self._market_closed_mid_match = True
                events.append({
                    "event_type": "trading_pvp_market_closed",
                    "narrative": (f"{self._asset_class} market closed mid-match — "
                                  f"settling positions at last known price."),
                    "data": {
                        "asset_class": self._asset_class,
                        "symbol": self._symbol,
                        "tick": self._tick_num,
                    },
                })
        # End condition: configured tick count exhausted OR market closed.
        if self._tick_num < self._num_ticks and not self._market_closed_mid_match:
            return

        # Force-close every open position at the final price so the
        # leaderboard reflects cash settlement.
        final_price = self._current_price()
        if final_price > 0:
            for pid in list(self._seat_order):
                pos = self._portfolios.get(pid, {}).get("position", 0.0)
                if pos > 0:
                    self._do_sell(state, pid, pos, final_price)
                elif pos < 0:
                    self._do_cover(state, pid, abs(pos), final_price)
            self._sync_entities(state)

        leaderboard = self._leaderboard(state)
        leaders = [row for row in leaderboard if row["portfolio_value"] == leaderboard[0]["portfolio_value"]]
        is_draw = len(leaders) > 1
        winner_id = leaders[0]["entity_id"] if len(leaders) == 1 else None
        winner_name = leaders[0]["name"] if len(leaders) == 1 else None
        # Currency is simulated portfolio value, not a wager. Preserve the
        # complete ranking, including equal values, for multiplayer ratings.
        score_map = {row["entity_id"]: row["portfolio_value"] for row in leaderboard}

        self._game_over = True
        events.append({
            "event_type": "trading_pvp_match_over",
            "narrative": (
                f"Match over — {len(leaders)} traders tie at ${leaders[0]['portfolio_value']:.2f}."
                if is_draw else f"Match over — {winner_name} wins with "
                f"${leaderboard[0]['portfolio_value']:.2f} "
                f"({leaderboard[0]['pnl_pct']:+.2f}%)"
                if leaderboard else "Match over."
            ),
            "data": {
                "winner": winner_id,
                "winner_id": winner_id,
                "winner_name": winner_name,
                "score": score_map,
                "leaderboard": leaderboard,
                "symbol": self._symbol,
                "asset_class": self._asset_class,
                "ticks_played": self._tick_num,
                "starting_cash": self._starting_cash,
                "is_draw": is_draw,
                "winners": [row["entity_id"] for row in leaders],
            },
        })
        self._sync_entities(state)

    # ------------------------------------------------------------------
    # Engine hooks
    # ------------------------------------------------------------------

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str], state: Any) -> List[str]:
        """Offer only orders the position allows: sell a long, cover a short."""
        position = float(self._portfolios.get(entity_id, {}).get("position", 0.0))
        blocked = set()
        if position <= 0:
            blocked.add("sell")
        if position >= 0:
            blocked.add("cover")
        if not self._shorts_allowed:
            blocked.add("short")
        return [action for action in valid_actions if action not in blocked]

    def _order_units(self, qty: float) -> float:
        """Stocks trade in whole shares; other assets allow fractional size."""
        return float(math.floor(qty)) if self._asset_class == "stocks" else qty

    def validate_action(self, action_name: str, actor: Any, target: Any, state: Any) -> Optional[str]:
        if not actor or not getattr(actor, "alive", True):
            return "Dead/missing actor."
        if self._game_over:
            return "Match over."
        if action_name == "short" and not self._shorts_allowed:
            return "Shorts disabled for this match."
        return None

    def post_resolution(
        self, actor_id: str, action_name: str, success: bool, result: Any, state: Any,
    ) -> List[Dict[str, Any]]:
        if action_name not in self.custom_actions:
            return []
        actor = state.get_entity(actor_id)
        if actor is None:
            return []
        round_number = state.temporal.current_round
        details = (result.details or {}) if result and getattr(result, "details", None) else {}
        params = details.get("_action_params", {}) or {}
        events: List[Dict[str, Any]] = []

        if action_name in ("buy", "sell", "short", "cover"):
            raw = params.get("qty") or details.get("qty") or 0
            try:
                qty = float(raw)
            except (TypeError, ValueError):
                qty = 0.0
            if qty > 0:
                actor.set(f"_{action_name}_r{round_number}", qty)
        elif action_name == "close_all":
            actor.set(f"_close_r{round_number}", True)
        elif action_name == "hold":
            # Surface the explicit hold so the viz can mark it on the
            # chart (vs. just an absent action). Doesn't change any
            # state — purely informational.
            events.append({
                "event_type": "trading_pvp_hold",
                "actor_id": actor_id,
                "data": {"tick": self._tick_num, "round": round_number},
                "narrative": f"{actor.name} sits out.",
            })
        # `discuss` has no per-tick state to stash.
        return events

    # ------------------------------------------------------------------
    # Perception
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        pf = self._portfolios.get(entity_id, {})
        price = self._current_price()
        leaderboard = self._leaderboard(state)
        position = float(pf.get("position", 0.0))
        entry_avg = float(pf.get("entry_avg", 0.0))
        cash = float(pf.get("cash", 0.0))
        unrealized = (price - entry_avg) * position if position else 0.0
        pv = cash + position * price

        side_label = "FLAT"
        if position > 0:
            side_label = f"LONG {position:.4f}"
        elif position < 0:
            side_label = f"SHORT {abs(position):.4f}"

        # Last few price points for trend reading.
        recent_prices = [round(p["price"], 6) for p in self._price_history[-10:]]

        # Action menu — listed without editorial nudges. The player
        # picks whatever fits their strategy.
        actions_doc = (
            'buy(qty=N) — go long N units. '
            'sell(qty=N) — close N units of a long. '
            'cover(qty=N) — close N units of a short. '
            'close_all() — flatten everything in one shot. '
            'hold() — pass this tick, do nothing.'
        )
        if self._shorts_allowed:
            actions_doc = 'short(qty=N) — go short N units. ' + actions_doc

        # Max-affordable size as a reference anchor (NOT a recommendation).
        max_qty = self._order_units(round(cash / price, 6)) if price > 0 else 1.0

        # Per-agent trade log — pulled from the running history of
        # this player's own orders + holds. Last 8 entries.
        history = self._trade_history.get(entity_id, [])[-8:]

        # Leaderboard summary the agent can see — name + portfolio
        # value + rank, nothing else.
        lb_lines = []
        for i, row in enumerate(leaderboard):
            mark = " (you)" if row["entity_id"] == entity_id else ""
            lb_lines.append(
                f"  {i + 1}. {row['name']}{mark}: ${row['portfolio_value']:.2f} "
                f"({row['pnl_pct']:+.2f}%)"
            )
        leaderboard_str = "\n".join(lb_lines)

        history_str = (
            "\n".join(f"  T{h['tick']}: {h['side'].upper()} "
                      f"{h['qty']:.4f} @ {h['price']:.4f}" for h in history)
            if history else "  (no trades yet)"
        )

        stage_hint = (
            f"Tick {self._tick_num} of {self._num_ticks}. "
            f"Symbol: {self._symbol} @ {price:.4f}.\n"
            f"\n"
            f"Your position: {side_label}. Cash: ${cash:.2f}. "
            f"Portfolio value: ${pv:.2f} (unrealized P&L "
            f"${unrealized:+.2f}).\n"
            f"\n"
            f"Recent prices (oldest → newest): {recent_prices}\n"
            f"\n"
            f"Your trades so far:\n{history_str}\n"
            f"\n"
            f"Leaderboard:\n{leaderboard_str}\n"
            f"\n"
            f"GOAL: end this match with the highest portfolio value of "
            f"anyone in the leaderboard. Nothing else matters. There "
            f"are no risk limits, no penalties for losing money, no "
            f"penalty for sitting out. Bet big, bet small, go all-in, "
            f"do nothing — your call. Max units you could afford right "
            f"now at this price = {max_qty}. The only thing that gets "
            f"scored is your portfolio value vs theirs at the final "
            f"tick.\n"
            f"\n"
            f"Actions: {actions_doc}"
        )

        out = {
            "stage": STAGE_OVER if self._game_over else STAGE_TICK,
            "tick": self._tick_num,
            "total_ticks": self._num_ticks,
            "symbol": self._symbol,
            "asset_class": self._asset_class,
            "current_price": round(price, 6),
            "recent_prices": recent_prices,
            "my_cash": round(cash, 2),
            "my_position": round(position, 6),
            "my_entry_avg": round(entry_avg, 6),
            "my_realized_pnl": round(float(pf.get("realized_pnl", 0.0)), 2),
            "my_unrealized_pnl": round(unrealized, 2),
            "my_portfolio_value": round(pv, 2),
            "leaderboard": leaderboard,
            "shorts_allowed": self._shorts_allowed,
            "stage_hint": stage_hint,
        }
        if self._news_context:
            out["news_context"] = self._news_context
        return out

    @property
    def is_terminal(self) -> bool:
        return self._game_over


# --- helpers ---------------------------------------------------------

def _now_unix() -> int:
    import time as _t
    return int(_t.time())


def _positive_float(x: Any) -> bool:
    try:
        return float(x) > 0
    except (TypeError, ValueError):
        return False


def _actor_name(state: Any, pid: str) -> str:
    ent = state.get_entity(pid)
    return getattr(ent, "name", pid) if ent else pid
