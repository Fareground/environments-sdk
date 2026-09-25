"""Automated market makers for prediction markets: LMSR and constant-product (CPMM).

The ``market`` family's ``prediction`` mode sells shares in each outcome; a share of the winning outcome pays 1 at
resolution. The market maker always quotes, so traders buy or sell by quantity (``shares``) or by
money (``spend`` / ``receive``), and prices move with every trade.

* ``lmsr`` — Hanson's logarithmic market scoring rule. Cost ``C(q) = b·ln Σ exp(q_i / b)``, price
  ``p_i = softmax(q / b)_i``. The market is seeded with ``b·ln n`` (its worst-case loss), so the
  vault (``C(q)``) always covers the winning shares.
* ``cpmm`` — the fixed-product market maker for n outcomes (Gnosis/Polymarket style). Money buys
  complete sets into every pool, shares of the bought outcome come out so ``Π pools`` stays
  constant; ``p_i = (1/pool_i) / Σ 1/pool_j``. The vault holds one unit per complete set, so it
  always equals what every outcome's shares are owed.

State: world props ``<name>_q`` (LMSR net shares sold, or CPMM pools), ``<name>_vault`` (collateral),
``<name>_fees``, ``<name>_resolved``; trader prop ``<name>_shares`` ``{outcome: qty}``. Money moves only
with conserved transfers between trader cash, the vault and the fee account.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function
from ..expr.objects import Entity
from ..registry import MechanismError, family_action, mechanism_config, mode
from ..world.abort import Abort
from ._common import entity_of, fmt
from .econ_base import money_prop
from .expressions import Expr
from .ledger import EPS, Account, balance, clean, move

KEY = "market.prediction"

__all__ = ["lmsr_cost", "lmsr_prices", "lmsr_shares_for_cost", "lmsr_shares_for_refund", "cpmm_prices", "cpmm_buy",
           "cpmm_sell", "cpmm_cost_for_shares", "cpmm_shares_for_refund", "PredictionMarketConfig"]


# ---------------------------------------------------------------------------
# Pricing math
# ---------------------------------------------------------------------------


def lmsr_cost(q: Sequence[float], b: float) -> float:
    """``b · ln Σ exp(q_i / b)``, computed stably."""
    top = max(q)
    return top + b * math.log(sum(math.exp((x - top) / b) for x in q))


def lmsr_prices(q: Sequence[float], b: float) -> list[float]:
    top = max(q)
    weights = [math.exp((x - top) / b) for x in q]
    total = sum(weights)
    return [w / total for w in weights]


def lmsr_shares_for_cost(q: Sequence[float], b: float, i: int, cost: float) -> float:
    """Shares of outcome ``i`` that ``cost`` buys."""
    target = lmsr_cost(q, b) + cost
    rest = sum(math.exp((x - target) / b) for j, x in enumerate(q) if j != i)
    return target + b * math.log(1.0 - rest) - q[i]


def lmsr_shares_for_refund(q: Sequence[float], b: float, i: int, refund: float) -> float | None:
    """Shares of outcome ``i`` to sell for ``refund``; None when no quantity pays that much."""
    target = lmsr_cost(q, b) - refund
    rest = sum(math.exp((x - target) / b) for j, x in enumerate(q) if j != i)
    if rest >= 1.0:
        return None
    return q[i] - (target + b * math.log(1.0 - rest))


def cpmm_prices(pools: Sequence[float]) -> list[float]:
    inverse = [1.0 / p for p in pools]
    total = sum(inverse)
    return [x / total for x in inverse]


def cpmm_buy(pools: Sequence[float], i: int, cost: float) -> tuple[float, list[float]]:
    """Shares of ``i`` that ``cost`` buys, and the pools after."""
    k = math.prod(pools)
    grown = [p + cost for p in pools]
    others = math.prod(p for j, p in enumerate(grown) if j != i)
    after = list(grown)
    after[i] = k / others
    return grown[i] - after[i], after


def cpmm_sell(pools: Sequence[float], i: int, shares: float) -> tuple[float, list[float]]:
    """Money returned for selling ``shares`` of ``i``, and the pools after (solved by bisection)."""
    k = math.prod(pools)
    others = [p for j, p in enumerate(pools) if j != i]
    low, high = 0.0, min(others + [pools[i] + shares])

    def excess(r: float) -> float:
        return math.prod(p - r for p in others) * (pools[i] + shares - r) - k

    for _ in range(200):
        mid = (low + high) / 2
        if excess(mid) > 0:
            low = mid
        else:
            high = mid
    refund = low
    after = [p - refund if j != i else p + shares - refund for j, p in enumerate(pools)]
    return refund, after


def cpmm_cost_for_shares(pools: Sequence[float], i: int, shares: float) -> float:
    """Money needed to buy ``shares`` of ``i``."""
    high = max(1.0, shares)
    while cpmm_buy(pools, i, high)[0] < shares:
        high *= 2
    low = 0.0
    for _ in range(200):
        mid = (low + high) / 2
        if cpmm_buy(pools, i, mid)[0] < shares:
            low = mid
        else:
            high = mid
    return high


def cpmm_shares_for_refund(pools: Sequence[float], i: int, refund: float) -> float | None:
    """Shares of ``i`` to sell for ``refund``; None when the pools cannot pay that much."""
    others = [p for j, p in enumerate(pools) if j != i]
    if refund >= min(others):
        return None
    return math.prod(pools) / math.prod(p - refund for p in others) - pools[i] + refund


# ---------------------------------------------------------------------------
# Mechanism config and state
# ---------------------------------------------------------------------------


class PredictionMarketConfig(BaseModel):
    """A market on which outcome will happen, with an automated market maker."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Agent type that trades (subtypes included).")
    outcomes: list[str] = Field(..., min_length=2, description="The possible outcomes; exactly one wins.")
    maker: Literal["lmsr", "cpmm"] = Field("lmsr",
                                           description="lmsr (logarithmic scoring rule) | cpmm (constant product).")
    liquidity: float = Field(100, gt=0,
                             description="LMSR b (higher = prices move less; the market is seeded with b·ln n) or CPMM "
                                         "starting pool per outcome.")
    currency: str = Field("cash", description="Trader property holding money.")
    fee_pct: float = Field(0, ge=0, le=0.2, description="Fee on each trade's value, to $world.<name>_fees.")
    question: str = Field("", description="What the market is about, shown with prices.")
    resolve_at: int | str | None = Field(None,
                                         description="Round at whose end the market resolves (number or expression).")
    resolve_when: Expr | None = Field(None, description="Resolve at the end of the first round this holds (with "
                                                        "`resolve_at`, whichever comes first).")
    outcome: Expr | None = Field(None, description="Expression giving the winning outcome when the market resolves.")
    stage: str | None = Field(None,
                              description="Trade during this declared stage; default: a sequential stage named after "
                                          "the market.")
    max_actions: int = Field(2, ge=1, description="Trades per turn in the generated stage.")
    conserve: bool = Field(True, description="Declare the invariant that the vault covers every share.")


def market_config(world: Any, name: Any) -> PredictionMarketConfig:
    return mechanism_config(world, name, KEY, PredictionMarketConfig)


def _state(world: Any, name: str, cfg: PredictionMarketConfig) -> list[float]:
    q = world.props.get(f"{name}_q") or {}
    return [float(q.get(o, 0.0 if cfg.maker == "lmsr" else cfg.liquidity)) for o in cfg.outcomes]


def prices(world: Any, name: str) -> dict[str, float]:
    cfg = market_config(world, name)
    state = _state(world, name, cfg)
    values = lmsr_prices(state, cfg.liquidity) if cfg.maker == "lmsr" else cpmm_prices(state)
    return dict(zip(cfg.outcomes, values))


def _holdings(trader: Entity, name: str) -> dict[str, float]:
    held: Any = trader.properties.get(f"{name}_shares") or {}
    return {k: float(v) for k, v in held.items()}


def _index(cfg: PredictionMarketConfig, outcome: Any) -> int:
    if outcome not in cfg.outcomes:
        raise Abort(f"outcome must be one of {', '.join(cfg.outcomes)}, not {outcome!r}.")
    return cfg.outcomes.index(outcome)


def _positive(value: Any, what: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise Abort(f"{what} must be a positive number, not {value!r}.")
    return float(value)


def trade(world: Any, name: str, trader: Entity, side: str, outcome: Any, shares: Any = None,
          amount: Any = None) -> str:
    """Buy or sell one outcome by ``shares`` or by money (``amount``: spend for a buy, receive for a sell)."""
    cfg = market_config(world, name)
    if world.props.get(f"{name}_resolved"):
        raise Abort(f"The market has resolved ({world.props[f'{name}_resolved']} won); trading is over.")
    i = _index(cfg, outcome)
    n, money = _positive(shares, "shares"), _positive(amount, "spend" if side == "buy" else "receive")
    if n is None and money is None:
        raise Abort(f"Give shares, {'spend' if side == 'buy' else 'receive'}, or both (then the money is your limit).")
    state = _state(world, name, cfg)
    b, fee = cfg.liquidity, cfg.fee_pct
    held = _holdings(trader, name)
    cash, vault, fees = Account(trader, cfg.currency), Account(None, f"{name}_vault"), Account(None, f"{name}_fees")
    if side == "buy":
        if n is None:
            assert money is not None
            value = money / (1 + fee)
            n = lmsr_shares_for_cost(state, b, i, value) if cfg.maker == "lmsr" else cpmm_buy(state, i, value)[0]
        else:
            value = lmsr_cost(state[:i] + [state[i] + n] + state[i + 1:], b) - lmsr_cost(state, b) \
                if cfg.maker == "lmsr" else cpmm_cost_for_shares(state, i, n)
            if money is not None and value * (1 + fee) > money + EPS:
                raise Abort(f"{fmt(n, 4)} {outcome} shares cost {fmt(value * (1 + fee), 4)} now, more than your limit "
                            f"{fmt(money, 4)}.")
        n = clean(n)
        total = value * (1 + fee)
        if total > balance(world, cash) + EPS:
            raise Abort(f"{fmt(n, 4)} {outcome} shares cost {fmt(total, 4)} with fees; you have "
                        f"{fmt(balance(world, cash), 4)}.")
        move(world, cash, vault, value, what="cash")
        move(world, cash, fees, value * fee, what="cash")
        if cfg.maker == "lmsr":
            state[i] += n
        else:
            grown = [p + value for p in state]
            grown[i] -= n
            state = grown
        held[outcome] = clean(held.get(outcome, 0.0) + n)
        verb = f"Bought {fmt(n, 4)} {outcome} for {fmt(total, 4)}"
    else:
        have = held.get(outcome, 0.0)
        if n is None:
            assert money is not None
            value = money / (1 - fee)
            found = (lmsr_shares_for_refund(state, b, i, value) if cfg.maker == "lmsr"
                     else cpmm_shares_for_refund(state, i, value))
            if found is None or found > have + EPS:
                raise Abort(f"Selling all your {fmt(have, 4)} {outcome} shares would not return {fmt(money, 4)}.")
            n = clean(min(found, have))
        elif n > have + EPS:
            raise Abort(f"You hold only {fmt(have, 4)} {outcome} shares.")
        else:
            n = min(n, have)
            state_after = state[:i] + [state[i] - n] + state[i + 1:]
            value = (lmsr_cost(state, b) - lmsr_cost(state_after, b) if cfg.maker == "lmsr"
                     else cpmm_sell(state, i, n)[0])
            if money is not None and value * (1 - fee) < money - EPS:
                raise Abort(f"Selling {fmt(n, 4)} {outcome} shares returns {fmt(value * (1 - fee), 4)} now, less than "
                            f"your minimum {fmt(money, 4)}.")
        move(world, vault, cash, value * (1 - fee), what="vault money")
        move(world, vault, fees, value * fee, what="vault money")
        if cfg.maker == "lmsr":
            state[i] -= n
        else:
            state = [p - value if j != i else p + n - value for j, p in enumerate(state)]
        held[outcome] = clean(have - n)
        verb = f"Sold {fmt(n, 4)} {outcome} for {fmt(value * (1 - fee), 4)}"
    world.set_world(f"{name}_q", {o: clean(v) for o, v in zip(cfg.outcomes, state)})
    world.set_prop(trader, f"{name}_shares", {k: v for k, v in held.items() if v > EPS})
    world.set_world(f"{name}_volume", clean(float(world.props.get(f"{name}_volume") or 0) + value))
    now = prices(world, name)
    text = f"{verb}. Prices now: " + ", ".join(f"{o} {p:.1%}" for o, p in now.items()) + "."
    world.set_world(f"{name}_receipt", text)
    return text


def resolve(world: Any, name: str, winner: Any) -> None:
    cfg = market_config(world, name)
    if world.props.get(f"{name}_resolved"):
        return
    if winner not in cfg.outcomes:
        raise RunError(f"the winning outcome must be one of {', '.join(cfg.outcomes)}, got {winner!r}",
                       f"mechanisms.{name}.outcome")
    paid = 0.0
    for trader in world.entities_of(cfg.who):
        held = _holdings(trader, name)
        owed = held.get(winner, 0.0)
        if owed > 0:
            move(world, Account(None, f"{name}_vault"), Account(trader, cfg.currency), owed, what="vault money")
            paid += owed
        if held:
            world.set_prop(trader, f"{name}_shares", {})
    world.set_world(f"{name}_resolved", winner)
    world.set_world(f"{name}_payout", clean(paid))
    subject = cfg.question or "The market"
    world.emit(name, f"{subject}: {winner} won. Each {winner} share paid 1 ({fmt(paid, 2)} in total).",
               data={"mechanism": KEY, "winner": winner, "paid": paid})


def audit(world: Any, name: str) -> list[str]:
    cfg = market_config(world, name)
    problems: list[str] = []
    traders = world.entities_of(cfg.who)
    vault = float(world.props.get(f"{name}_vault") or 0)
    if vault < -1e-6:
        problems.append("the vault is negative")
    if world.props.get(f"{name}_resolved"):
        return problems
    held = {o: sum(_holdings(t, name).get(o, 0.0) for t in traders) for o in cfg.outcomes}
    state = _state(world, name, cfg)
    tolerance = 1e-6 * max(1.0, vault)
    for o, s in zip(cfg.outcomes, state):
        owed = held[o] if cfg.maker == "lmsr" else vault - s
        if abs(held[o] - owed) > tolerance or held[o] - vault > tolerance:
            problems.append(f"{o}: traders hold {held[o]} shares, the vault owes {owed} and holds {vault}")
    if cfg.maker == "lmsr" and abs(vault - lmsr_cost(state, cfg.liquidity)) > tolerance:
        problems.append(f"the LMSR vault {vault} differs from its cost function {lmsr_cost(state, cfg.liquidity)}")
    if cfg.maker == "cpmm":
        k = cfg.liquidity ** len(cfg.outcomes)
        if abs(math.prod(state) - k) > 1e-6 * k or min(state) <= 0:
            problems.append("the CPMM pools lost their constant product")
    return problems


# ---------------------------------------------------------------------------
# Functions, op, mechanism
# ---------------------------------------------------------------------------


def _market(call: Call) -> str:
    try:
        market_config(call.scope.world, call.arg(0))
    except RunError as exc:
        raise ExprError(f"${call.name}: {exc}", call.source) from None
    return str(call.arg(0))


@function("amm(name)", "A prediction market: {prices: {outcome: price}, vault, fees, volume, resolved, payout, maker, "
          "liquidity, question}.", min_args=1, max_args=1, family="market")
def _amm_function(call: Call) -> dict[str, Any]:
    name = _market(call)
    world: Any = call.scope.world
    cfg = market_config(world, name)
    return {"prices": prices(world, name), "vault": world.props.get(f"{name}_vault"),
            "fees": world.props.get(f"{name}_fees"),
            "volume": world.props.get(f"{name}_volume"), "resolved": world.props.get(f"{name}_resolved") or None,
            "payout": world.props.get(f"{name}_payout"), "maker": cfg.maker, "liquidity": cfg.liquidity,
            "question": cfg.question}


@function("amm_outcomes(name, viewer?)",
          "Each outcome of a prediction market: [{outcome, price, held}] (held by the viewer).", min_args=1, max_args=2,
          family="market")
def _outcomes_function(call: Call) -> list[dict[str, Any]]:
    name = _market(call)
    viewer = call.scope.world.entity(call.arg(1)) if len(call) > 1 else None
    held = _holdings(viewer, name) if viewer is not None else {}
    return [{"outcome": o, "price": p, "held": held.get(o, 0)} for o, p in prices(call.scope.world, name).items()]


@function("amm_cost(name, outcome, shares)", "What buying `shares` of `outcome` costs now, fees included.", min_args=3,
          max_args=3, family="market")
def _cost_function(call: Call) -> float:
    name = _market(call)
    world = call.scope.world
    cfg = market_config(world, name)
    if call.arg(1) not in cfg.outcomes:
        raise ExprError(f"$amm_cost: outcome must be one of {', '.join(cfg.outcomes)}", call.source)
    i, n = cfg.outcomes.index(call.arg(1)), call.number(2)
    state = _state(world, name, cfg)
    value = lmsr_cost(state[:i] + [state[i] + n] + state[i + 1:], cfg.liquidity) - lmsr_cost(state, cfg.liquidity) \
        if cfg.maker == "lmsr" else cpmm_cost_for_shares(state, i, n)
    return value * (1 + cfg.fee_pct)


@function("amm_ok(name)", "True while a prediction market's vault covers every share.", min_args=1, max_args=1,
          family="market")
def _ok_function(call: Call) -> bool:
    return not audit(call.scope.world, _market(call))


def _size_check(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
    """Check-time: a trade says how much, in shares, in money or both."""
    money = _MONEY[str(effect.get("action"))]
    if "shares" in effect or money in effect:
        return []
    return [(path, f"`market.{effect.get('action')}` needs `shares`, `{money}` (money) or both",
             f"with both, {money} is the limit")]


#: The money key of a trade: the most a buy spends, the least a sell receives (the tools' own words).
_MONEY = {"buy": "spend", "sell": "receive"}


#: action → (its keys, the required ones, generated by the mechanism itself, example keys, what it does).
#: `who` is the trader (default $actor); receipts land in $world.<name>_receipt.
_ACTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...], bool, str, str]] = {
    "buy": (("who", "outcome", "shares", "spend"), ("outcome",), False, '"outcome": "yes", "spend": 20',
            "buy one outcome by `shares` or money (`spend`, the most paid when both are given)"),
    "sell": (("who", "outcome", "shares", "receive"), ("outcome",), False, '"outcome": "yes", "shares": 5',
             "sell one outcome by `shares` or money (`receive`, the least accepted when both are given)"),
    "resolve": (("outcome",), ("outcome",), False, '"outcome": "$world.truth"',
                "pay 1 per winning share and close trading"),
}


def _runner(action: str) -> Callable[[Any, dict[str, Any], dict[str, Any], str], None]:
    def run(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        world = runner.world
        name = effect["market"]
        try:
            if action == "resolve":
                resolve(world, name, runner.eval(effect["outcome"], vars))
            else:
                trader = entity_of(world, runner.eval(effect.get("who", "$actor"), vars), f"{where}.who", "a trader")
                trade(world, name, trader, action, runner.eval(effect["outcome"], vars),
                      runner.eval(effect.get("shares"), vars), runner.eval(effect.get(_MONEY[action]), vars))
        except RunError as exc:
            raise RunError(str(exc), where) from None

    return run


def _register_actions() -> None:
    for action, (keys, required, internal, fields, doc) in _ACTIONS.items():
        example = ('{"market": "election", "action": "' + action + '"' + (f", {fields}" if fields else "")
                   + f"}}  ({doc})")
        family_action("market", ("prediction",), action, keys=keys, required=required, internal=internal,
                      example=example, check=_size_check if action in ("buy", "sell") else None)(_runner(action))


_register_actions()


def _metric_key(outcome: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", outcome)


@mode("market", "prediction", PredictionMarketConfig,
           "A market on which of several outcomes happens, priced by an automated market maker (lmsr or cpmm). Tools "
           "`<name>_buy` and `<name>_sell` trade one outcome by shares or by money; each winning share pays 1 when "
           "the market resolves; each trader holds its shares in `<name>_shares` ({outcome: shares}). The market "
           "maker's vault starts with seed money (lmsr: liquidity × ln(outcomes), its worst-case loss; cpmm: "
           "liquidity), which a ledger counts in its starting supply, not in its flows. It resolves at `resolve_at`, "
           "when `resolve_when` holds, or with the op "
           "{\"market\": name, \"action\": \"resolve\", \"outcome\": ...}. Read it with $amm(name), "
           "$amm_outcomes(name, viewer) and $amm_cost(name, outcome, shares); metrics <name>_p_<outcome> track prices.",
           example={"who": "forecaster", "outcomes": ["yes", "no"], "maker": "lmsr", "liquidity": 50,
                    "question": "Will the bill pass?", "resolve_at": 5, "outcome": "$world.truth"})
def _expand_market(name: str, cfg: PredictionMarketConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    types = contract.get("types") or {}
    if cfg.who not in types:
        raise MechanismError(f"who '{cfg.who}' is not a declared type", f"types: {', '.join(types) or 'none'}", "who")
    if len(set(cfg.outcomes)) != len(cfg.outcomes):
        raise MechanismError("outcomes must be distinct", None, "outcomes")
    if (cfg.resolve_at is not None or cfg.resolve_when is not None) and cfg.outcome is None:
        raise MechanismError("resolving automatically needs `outcome`: an expression giving the winner", None,
                             "outcome")
    if cfg.outcome is not None:
        compile_expr(cfg.outcome)
    subsidy = cfg.liquidity * math.log(len(cfg.outcomes)) if cfg.maker == "lmsr" else cfg.liquidity
    start_q = {o: 0.0 if cfg.maker == "lmsr" else cfg.liquidity for o in cfg.outcomes}
    question = f" on: {cfg.question}" if cfg.question else ""
    open_now = {"expr": f"$world.{name}_resolved == ''", "why": "The market has resolved."}
    receipt = f"{{$world.{name}_receipt}}"
    pricing = ("Prices are probabilities set by a logarithmic market scoring rule" if cfg.maker == "lmsr"
               else "Prices are set by a constant-product pool") + "; every trade moves them. A winning share pays 1."
    fee = f" Fee {cfg.fee_pct:.1%} of the trade's value." if cfg.fee_pct else ""
    actions = {
        f"{name}_buy": {
            "by": cfg.who,
            "description": f"Buy shares of one outcome{question}. Give shares (how many), spend (how much money), or "
                           f"both (then spend is the most you pay). {pricing}{fee}",
            "params": {"outcome": {"type": "enum", "values": list(cfg.outcomes), "description": "Outcome to buy."},
                       "shares": {"type": "number", "min": 0.0001, "required": False, "description": "Shares to buy."},
                       "spend": {"type": "number", "min": 0.0001, "max": f"$actor.{cfg.currency}", "required": False,
                  "description": "Money to spend, fees included."}},
            "when": [open_now, {"expr": f"$actor.{cfg.currency} > 0", "why": "You have no cash."}],
            "do": [{"market": name, "action": "buy", "outcome": "$params.outcome", "shares": "$params.shares",
                    "spend": "$params.spend"}],
            "outcome": receipt, "private": True,
        },
        f"{name}_sell": {
            "by": cfg.who,
            "description": f"Sell shares you hold{question}. Give shares (how many), receive (how much money you want "
                           f"back), or both (then receive is the least you accept).{fee}",
            "params": {"outcome": {"type": "enum",
                    "values": f"$map($filter($amm_outcomes({name}, $actor), $it.held > 0), $it.outcome)",
                    "description": "Outcome to sell."},
                       "shares": {"type": "number", "min": 0.0001, "required": False, "description": "Shares to sell."},
                       "receive": {"type": "number", "min": 0.0001, "required": False,
                    "description": "Money to receive after fees."}},
            "when": [open_now,
                     {"expr": f"$any($amm_outcomes({name}, $actor), $it.held > 0)", "why": "You hold no shares."}],
            "do": [{"market": name, "action": "sell", "outcome": "$params.outcome", "shares": "$params.shares",
                    "receive": "$params.receive"}],
            "outcome": receipt, "private": True,
        },
    }
    fragment: dict[str, Any] = {
        "types": {cfg.who: {"props": {**money_prop(contract, cfg.who, cfg.currency),
                                          f"{name}_shares": {"type": "map", "default": {}, "private": True}}}},
        "world": {f"{name}_q": {"type": "map", "default": start_q},
                  f"{name}_vault": {"type": "number", "default": subsidy,
                                    "description": "Collateral held by the market maker."},
                  f"{name}_fees": {"type": "number", "default": 0}, f"{name}_volume": {"type": "number", "default": 0},
                  f"{name}_resolved": {"type": "text", "default": ""},
                  f"{name}_payout": {"type": "number", "default": 0},
                  f"{name}_receipt": {"type": "text", "default": ""}},
        "actions": actions,
        "events": [],
        "views": {
            f"{name}_prices": {"for": cfg.who, "title": cfg.question or f"{name} market",
                               "of": f"$amm_outcomes({name}, $actor)", "show": "{outcome}: {price|pct1}{$' · you "
                                       "hold ' + $text($round($it.held, 2)) if $it.held > 0 else ''}"},
            f"{name}_status": {"for": cfg.who, "when": f"$world.{name}_resolved != ''",
                               "show": f"Resolved: {{$world.{name}_resolved}} won."},
        },
        "metrics": {f"{name}_p_{_metric_key(o)}": f"$get($amm({name}).prices, '{o}')" for o in cfg.outcomes},
        "outputs": {f"{name}_winner": {"expr": f"$world.{name}_resolved", "type": "text",
                                       "description": "Winning outcome."},
                    f"{name}_prices": {"expr": f"$amm({name}).prices", "type": "map", "description": "Final prices."},
                    f"{name}_house_pnl": {"expr": f"$round($world.{name}_vault - {subsidy!r}, 6)", "type": "number",
                                          "description": "What the market maker kept beyond its seed."},
                    f"{name}_volume": {"expr": f"$world.{name}_volume", "type": "number",
                                       "description": "Money traded."}},
    }
    if cfg.resolve_at is not None or cfg.resolve_when is not None:
        event: dict[str, Any] = {"name": f"{name}_resolve", "phase": "end", "once": True,
                                 "do": [{"market": name, "action": "resolve", "outcome": cfg.outcome}]}
        due = [f"$round >= ({cfg.resolve_at})" if cfg.resolve_at is not None else None,
               f"({cfg.resolve_when})" if cfg.resolve_when is not None else None]
        event["when"] = f"({' or '.join(term for term in due if term)}) and $world.{name}_resolved == ''"
        fragment["events"].append(event)
    names = list(actions)
    if cfg.stage is None:
        fragment["stages"] = [{"name": name, "turns": "sequential", "order": "random", "actions": names,
                               "max_actions": cfg.max_actions, "when": f"$world.{name}_resolved == ''",
                               "brief": f"Trade{question}, or end your turn."}]
    else:
        fragment["stage_hooks"] = {cfg.stage: {"actions": names, "max_actions": cfg.max_actions}}
    if cfg.conserve:
        fragment["invariants"] = [{"expr": f"$amm_ok({name})",
                                   "why": f"The {name} market's vault covers every outstanding share."}]
    return fragment
