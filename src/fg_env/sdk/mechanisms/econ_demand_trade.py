"""The ``economy.demand`` round: returns, promotions and prices at its start, demand and sales at its end — and the stock
actions (``receive``, ``remove``) every other change of stock goes through, so ``$stock_conserved`` can prove it.

Random draws come from streams named by the mechanism, the purpose, the item, the segment and the round (and the counts
pattern's own streams for demand), never from the run's shared stream: arms of an experiment see the same luck, and a
snapshot, clone or fork needs no state beyond the world's properties.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from ...entity import Entity
from ..errors import RunError
from ..expr import ExprError
from ..patterns.base import KINDS
from ..patterns.observe import count_quantile
from ..registry import family_action
from ..world import Abort
from .econ_assets import burn_money, mint_money
from .econ_base import DEMAND, bump, cached, config_of, entity_of, props, whole
from .econ_demand import SEGMENT_TOTALS, DemandConfig, FactorRef, SegmentSpec, segments_of

__all__ = ["number_of", "binomial", "stream", "stock_in", "stock_out"]


def number_of(runner: Any, value: Any, vars: Dict[str, Any], where: str, low: Optional[float] = None,
              high: Optional[float] = None) -> float:
    """A config value (a number or an expression over ``vars``) as a finite number within bounds."""
    result = value
    if isinstance(value, str) and "$" not in value:
        try:
            result = float(value)
        except ValueError:
            raise RunError(f"must be a number or an expression with $, got {value!r}", where) from None
    elif isinstance(value, str):
        try:
            result = runner.eval(value, vars)
        except ExprError as exc:
            raise RunError(str(exc), where) from None
    if isinstance(result, bool) or not isinstance(result, (int, float)) or not math.isfinite(result):
        raise RunError(f"must give a number, got {result!r}", where)
    if (low is not None and result < low) or (high is not None and result > high):
        span = f"at least {low:g}" if high is None else (f"at most {high:g}" if low is None else f"from {low:g} to {high:g}")
        raise RunError(f"must be {span}, got {result:g}", where)
    return float(result)


def stream(world: Any, name: str, *parts: Any) -> Any:
    return world.seeds.rng("demand", name, *parts)


def binomial(rng: Any, n: int, p: float) -> int:
    """How many of ``n`` independent trials succeed with chance ``p`` (normal approximation for large ``n``)."""
    if n <= 0 or p <= 0:
        return 0
    if p >= 1:
        return n
    if n <= 64:
        return sum(1 for _ in range(n) if rng.random() < p)
    return max(0, min(n, round(rng.gauss(n * p, math.sqrt(n * p * (1 - p))))))


def stock_in(world: Any, name: str, config: DemandConfig, item: Entity, qty: int, flow: str) -> None:
    """``qty`` units enter an item's stock through ``flow``."""
    assert config.stock is not None
    if qty:
        world.set_prop(item, config.stock, int(props(item)[config.stock]) + qty)
        bump(world, f"{name}_stock_flows", flow, qty)


def stock_out(world: Any, name: str, config: DemandConfig, item: Entity, qty: int, flow: str) -> None:
    """``qty`` units leave an item's stock through ``flow``; refused when fewer are on hand."""
    assert config.stock is not None
    have = int(props(item)[config.stock])
    if qty > have:
        raise Abort(f"Only {have} units of {item.name} are in stock; {qty} are needed.")
    if qty:
        world.set_prop(item, config.stock, have - qty)
        bump(world, f"{name}_stock_flows", flow, -qty)


# ---------------------------------------------------------------------------
# What each segment reads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Read:
    """A pattern read for an item: whether it takes every item's price, a driver, and a key."""

    pattern: str
    key: Optional[str]
    driver: Optional[str]
    prices: bool
    responds: bool
    keyed: bool


_Term = Union[float, str, _Read]


@dataclass(frozen=True)
class _Segment:
    name: str
    spec: SegmentSpec
    rate: _Term
    factors: Tuple[_Term, ...]
    noise_keyed: bool
    main: bool


def _term(world: Any, factor: Any) -> _Term:
    if isinstance(factor, (int, float)):
        return float(factor)
    if isinstance(factor, str) and "$" in factor:
        return factor
    ref = factor if isinstance(factor, FactorRef) else FactorRef(pattern=factor)
    cfg = world.patterns.configs[ref.pattern]
    prices = cfg.kind == "cross_price"
    return _Read(ref.pattern, ref.key, ref.driver, prices, not prices and bool(KINDS[cfg.kind].arg_names(cfg)), cfg.keyed)


def plan(world: Any, name: str, config: DemandConfig) -> List[_Segment]:
    """Every segment with its reads resolved against the patterns (once per contract)."""
    def build() -> List[_Segment]:
        out = []
        for index, (segment, spec) in enumerate(segments_of(config).items()):
            noise_keyed = spec.noise is not None and world.patterns.configs[spec.noise].keyed
            out.append(_Segment(segment, spec, _term(world, spec.rate), tuple(_term(world, f) for f in spec.factors),
                                noise_keyed, index == 0))
        return out

    return cached(world, ("demand-plan", name), build)  # type: ignore[no-any-return]


def _read(runner: Any, term: _Term, item: Entity, vars: Dict[str, Any], prices: Dict[str, float], where: str) -> float:
    if isinstance(term, float):
        return term
    if isinstance(term, str):
        return number_of(runner, term, vars, where, low=0)
    args: List[Any] = []
    if term.prices:
        args.append(prices)
    elif term.responds:
        args.append(number_of(runner, term.driver, vars, where) if term.driver else vars["price"])
    if term.keyed:
        args.append(runner.eval(term.key, vars) if term.key else item)
    try:
        value = runner.world.patterns.call(term.pattern, args, where)
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise RunError(f"pattern '{term.pattern}' gave {value!r}; demand factors must be numbers ≥ 0", where)
    return float(value)


def _draw(runner: Any, name: str, segment: _Segment, item: Entity, mean: float, where: str) -> Tuple[int, float]:
    """Units asked for around ``mean``, and the variance of that draw."""
    world = runner.world
    noise = segment.spec.noise
    if noise is None:
        u = stream(world, name, "demand", segment.name, item.id, world.round).random()
        return count_quantile(min(max(u, 1e-12), 1 - 1e-12), mean, None), mean
    key: Any = item if segment.noise_keyed else (item.id if segment.main else f"{item.id}:{segment.name}")
    try:
        units = world.patterns.call(noise, [mean, key], where)
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    runtime = world.patterns
    if runtime.configs[noise].dist == "poisson":
        return int(units), mean
    dispersion = float(runtime.param(noise, item.id if segment.noise_keyed else None, "dispersion", where))
    return int(units), mean + mean * mean / dispersion


# ---------------------------------------------------------------------------
# The start of a round: returns, promotions, prices
# ---------------------------------------------------------------------------


@family_action("economy", ("demand",), "open", internal=True,
               example='{"economy": "shop", "action": "open"}  (take back returns due, then read promotions and prices)')
def _open(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: DemandConfig = config_of(world, name, DEMAND, where)
    base = f"mechanisms.{name}"
    pending = list(world.props.get(f"{name}_returns") or [])
    due = [entry for entry in pending if entry[0] <= world.round]
    if due:
        world.set_world(f"{name}_returns", [entry for entry in pending if entry[0] > world.round])
        _take_back(runner, name, config, due, base)
    for item in world.alive_of(config.items):
        scope = {"it": item}
        world.set_prop(item, f"{name}_promo", number_of(runner, config.promotion, scope, f"{base}.promotion"))
        world.set_prop(item, f"{name}_price", number_of(runner, config.price, scope, f"{base}.price", low=0))


def _take_back(runner: Any, name: str, config: DemandConfig, due: List[Any], base: str) -> None:
    world = runner.world
    segments = segments_of(config)
    kept = _segment_store(world, name, config)
    refunds = 0.0
    for _, item_id, segment, units, price in due:
        item = world.entities.get(item_id)
        refund = units * price
        refunds += refund
        totals = kept[segment]
        totals["returned"] += units
        totals["refunds"] += refund
        if item is None or not item.alive:
            continue
        spec = segments[segment].returns
        share = number_of(runner, spec.restock, {"it": item}, f"{base}.returns.restock", 0, 1) if spec else 1.0
        back = binomial(stream(world, name, "restock", segment, item_id, world.round), int(units), share)
        if config.stock is not None:
            stock_in(world, name, config, item, back, "returned")
        cost = number_of(runner, config.cost, {"it": item}, f"{base}.cost", low=0)
        totals["cogs"] -= back * cost
        for prop, delta in ((f"{name}_returned_total", units), (f"{name}_refunds", refund), (f"{name}_cogs", -back * cost)):
            world.set_prop(item, prop, props(item)[prop] + delta)
    world.set_world(f"{name}_segments", kept)
    if config.account is not None and config.currency is not None and refunds:
        account = entity_of(world, config.account, f"{base}.account", "the account")
        try:
            burn_money(world, config.currency, account, refunds, f"{name}_refunds", base)
        except Abort as exc:
            raise RunError(f"{account.name} cannot pay this round's refunds: {exc.reason}", f"{base}.account") from None


# ---------------------------------------------------------------------------
# The end of a round: demand and sales
# ---------------------------------------------------------------------------


class _Tally:
    """One item's round."""

    __slots__ = ("demand", "served", "substituted", "backordered", "lost", "spill_in", "filled", "revenue", "expected",
                 "variance", "stockout", "sold_to")

    def __init__(self) -> None:
        self.demand = self.served = self.substituted = self.backordered = self.lost = self.spill_in = self.filled = 0
        self.revenue = self.expected = self.variance = 0.0
        self.stockout = False
        self.sold_to: Dict[str, List[float]] = {}  # segment → [units, revenue]

    def sell(self, segment: str, units: int, price: float) -> None:
        if units:
            sold = self.sold_to.setdefault(segment, [0, 0.0])
            sold[0] += units
            sold[1] += units * price
            self.revenue += units * price


class _Row:
    """What one segment asked of one item this round (for the record and the unmet pass)."""

    __slots__ = ("segment", "item", "price", "demand", "unmet", "lost", "stock", "drivers")

    def __init__(self, segment: _Segment, item: Entity, price: float, demand: int, unmet: int, stock: int,
                 drivers: Dict[str, float]):
        self.segment, self.item, self.price, self.demand, self.unmet = segment, item, price, demand, unmet
        self.lost, self.stock, self.drivers = 0, stock, drivers


@family_action("economy", ("demand",), "trade", internal=True,
               example='{"economy": "shop", "action": "trade"}  (draw this round\'s demand and sell from stock)')
def _trade(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["economy"]
    config: DemandConfig = config_of(world, name, DEMAND, where)
    base = f"mechanisms.{name}"
    items = list(world.alive_of(config.items))
    stock: Optional[Dict[str, int]] = None if config.stock is None else {i.id: int(props(i)[config.stock]) for i in items}
    tallies = {item.id: _Tally() for item in items}
    _fill_backorders(name, items, stock, tallies)
    prices = {item.id: float(props(item)[f"{name}_price"]) for item in items}
    rows: List[_Row] = []
    for segment in plan(world, name, config):
        rows += _serve(runner, name, config, segment, items, prices, stock, tallies, base)
    backlog = _unmet(runner, name, config, rows, stock, tallies, base)
    _settle(runner, name, config, items, stock, tallies, backlog, rows, base)
    _schedule_returns(runner, name, tallies, base)
    if config.record is True or (isinstance(config.record, str) and runner.eval(config.record, {})):
        _post(runner, name, rows, tallies)


def _fill_backorders(name: str, items: List[Entity], stock: Optional[Dict[str, int]], tallies: Dict[str, _Tally]) -> None:
    for item in items:
        waiting = int(props(item)[f"{name}_backlog"])
        if waiting and stock is not None and stock[item.id]:
            filled = min(waiting, stock[item.id])
            stock[item.id] -= filled
            tallies[item.id].filled = filled


def _serve(runner: Any, name: str, config: DemandConfig, segment: _Segment, items: List[Entity], prices: Dict[str, float],
           stock: Optional[Dict[str, int]], tallies: Dict[str, _Tally], base: str) -> List[_Row]:
    spec, path = segment.spec, base if segment.main else f"{base}.segments.{segment.name}"
    rows = []
    for item in items:
        price = prices[item.id]
        scope: Dict[str, Any] = {"it": item, "price": price}
        if spec.where is not None and not runner.eval(spec.where, scope):
            continue
        if spec.price is not None:
            price = number_of(runner, spec.price, scope, f"{path}.price", low=0)
            scope["price"] = price
        mean = _read(runner, segment.rate, item, scope, prices, f"{path}.rate")
        drivers: Dict[str, float] = {}
        for index, term in enumerate(segment.factors):
            mean *= _read(runner, term, item, scope, prices, f"{path}.factors[{index}]")
            if isinstance(term, _Read) and term.driver is not None:
                drivers[term.pattern] = number_of(runner, term.driver, scope, f"{path}.factors[{index}].driver")
        demand, variance = _draw(runner, name, segment, item, mean, f"{path}.noise")
        on_hand = demand if stock is None else stock[item.id]
        served = min(demand, on_hand)
        if stock is not None:
            stock[item.id] -= served
        tally = tallies[item.id]
        tally.demand += demand
        tally.served += served
        tally.expected += mean
        tally.variance += variance
        tally.stockout = tally.stockout or served < demand
        tally.sell(segment.name, served, price)
        rows.append(_Row(segment, item, price, demand, demand - served, on_hand, drivers))
    return rows


def _unmet(runner: Any, name: str, config: DemandConfig, rows: List[_Row], stock: Optional[Dict[str, int]],
           tallies: Dict[str, _Tally], base: str) -> Dict[str, int]:
    """Unmet demand tries substitutes, then waits or leaves. Returns the backorders placed, by item."""
    world = runner.world
    backlog: Dict[str, int] = {}
    for row in [r for r in rows if r.unmet]:
        item, segment, left = row.item, row.segment, row.unmet
        scope = {"it": item, "price": row.price}
        if config.substitutes is not None and stock is not None:
            share = number_of(runner, config.spill, scope, f"{base}.spill", 0, 1)
            trying = binomial(stream(world, name, "spill", segment.name, item.id, world.round), left, share)
            for other in _substitutes(runner, config, item, base):
                taken = min(trying, stock.get(other.id, 0))
                if not taken:
                    continue
                listed = float(props(other)[f"{name}_price"])
                price = listed if segment.spec.price is None else number_of(
                    runner, segment.spec.price, {"it": other, "price": listed}, f"{base}.segments.{segment.name}.price", low=0)
                stock[other.id] -= taken
                tallies[other.id].spill_in += taken
                tallies[other.id].sell(segment.name, taken, price)
                tallies[item.id].substituted += taken
                trying -= taken
                left -= taken
        share = number_of(runner, config.backorder, scope, f"{base}.backorder", 0, 1)
        waiting = binomial(stream(world, name, "backorder", segment.name, item.id, world.round), left, share)
        backlog[item.id] = backlog.get(item.id, 0) + waiting
        tallies[item.id].backordered += waiting
        tallies[item.id].lost += left - waiting
        row.lost = left - waiting
    return backlog


def _substitutes(runner: Any, config: DemandConfig, item: Entity, base: str) -> List[Entity]:
    world = runner.world
    try:
        listed = runner.eval(config.substitutes, {"it": item})
    except ExprError as exc:
        raise RunError(str(exc), f"{base}.substitutes") from None
    listed = [] if listed is None else listed if isinstance(listed, list) else [listed]
    out = []
    for value in listed:
        other = entity_of(world, value, f"{base}.substitutes", f"a {config.items}")
        if other.id != item.id and world.is_a(other.entity_type, config.items):
            out.append(other)
    return out


def _settle(runner: Any, name: str, config: DemandConfig, items: List[Entity], stock: Optional[Dict[str, int]],
            tallies: Dict[str, _Tally], backlog: Dict[str, int], rows: List[_Row], base: str) -> None:
    """Write the round into the items, the segment totals, the stock flows and the account."""
    world = runner.world
    kept = _segment_store(world, name, config)
    for row in rows:
        totals = kept[row.segment.name]
        totals["demand"] += row.demand
        totals["served"] += row.demand - row.unmet
        totals["lost"] += row.lost
    revenue = 0.0
    sold_units = 0
    for item in items:
        tally, p = tallies[item.id], props(item)
        cost = number_of(runner, config.cost, {"it": item}, f"{base}.cost", low=0)
        if tally.filled:
            tally.sell(config.segment, tally.filled, float(p[f"{name}_price"]))
        sold = tally.served + tally.spill_in + tally.filled
        sold_units += sold
        revenue += tally.revenue
        updates: Dict[str, Any] = {
            "demand": tally.demand, "sold": sold, "lost": tally.lost, "stockout": tally.stockout,
            "expected": tally.expected, "variance": tally.variance,
            "backlog": int(p[f"{name}_backlog"]) - tally.filled + backlog.get(item.id, 0),
            "recent": (list(p[f"{name}_recent"]) + [sold])[-config.recent:],
            "rounds_out": int(p[f"{name}_rounds_out"]) + (1 if tally.stockout else 0)}
        for measure, value in (("demand_total", tally.demand), ("served_total", tally.served),
                               ("substituted_total", tally.substituted), ("backordered_total", tally.backordered),
                               ("lost_total", tally.lost), ("spill_in_total", tally.spill_in), ("sold_total", sold),
                               ("revenue", tally.revenue), ("cogs", sold * cost)):
            updates[measure] = p[f"{name}_{measure}"] + value
        for prop, value in updates.items():
            world.set_prop(item, f"{name}_{prop}", value)
        if stock is not None and config.stock is not None:
            world.set_prop(item, config.stock, stock[item.id])
        for segment, (units, money) in tally.sold_to.items():
            totals = kept[segment]
            totals["sold"] += units
            totals["revenue"] += money
            totals["cogs"] += units * cost
    world.set_world(f"{name}_segments", kept)
    if stock is not None and sold_units:
        bump(world, f"{name}_stock_flows", "sold", -sold_units)
    if config.account is not None and config.currency is not None and revenue:
        account = entity_of(world, config.account, f"{base}.account", "the account")
        mint_money(world, config.currency, account, revenue, f"{name}_sales", base)


def _segment_store(world: Any, name: str, config: DemandConfig) -> Dict[str, Dict[str, Any]]:
    """A fresh copy of the per-segment totals, with every segment and measure present."""
    kept = world.props.get(f"{name}_segments") or {}
    return {segment: {measure: (kept.get(segment) or {}).get(measure, 0) for measure in SEGMENT_TOTALS}
            for segment in segments_of(config)}


def _schedule_returns(runner: Any, name: str, tallies: Dict[str, _Tally], base: str) -> None:
    world = runner.world
    config: DemandConfig = config_of(world, name, DEMAND, base)
    segments = segments_of(config)
    pending = list(world.props.get(f"{name}_returns") or [])
    added = False
    for item_id, tally in tallies.items():
        item = world.entities[item_id]
        for segment, (units, money) in tally.sold_to.items():
            spec = segments[segment].returns
            if spec is None:
                continue
            price = money / units
            scope = {"it": item}
            path = f"{base}.segments.{segment}.returns" if segment != config.segment else f"{base}.returns"
            rate = number_of(runner, spec.rate, scope, f"{path}.rate", 0, 1)
            back = binomial(stream(world, name, "returns", segment, item_id, world.round), int(units), rate)
            if back:
                delay = whole(round(number_of(runner, spec.delay, scope, f"{path}.delay", low=0)), f"{path}.delay")
                pending.append([world.round + max(1, delay), item_id, segment, back, round(price, 6)])
                added = True
    if added:
        world.set_world(f"{name}_returns", pending)


def _post(runner: Any, name: str, rows: List[_Row], tallies: Dict[str, _Tally]) -> None:
    world = runner.world
    date = runner.eval("$clock.date", {})
    time = str(date) if date else str(world.round)
    where = f"mechanisms.{name}.record"
    for row in rows:
        tally, p = tallies[row.item.id], props(row.item)
        units = tally.sold_to.get(row.segment.name, (0, 0.0))[0]
        world.post(f"{name}_history", {
            "time": time, "item": row.item.id, "segment": row.segment.name, "units": units, "stockout": 1 if row.unmet else 0,
            "demand": row.demand, "lost": row.lost, "stock": row.stock, "price": round(row.price, 4),
            "promo": p[f"{name}_promo"], **{k: round(v, 6) for k, v in row.drivers.items()}}, None, None, where)


# ---------------------------------------------------------------------------
# Stock actions
# ---------------------------------------------------------------------------


def _item_and_qty(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> Tuple[DemandConfig, Entity, int]:
    world = runner.world
    config: DemandConfig = config_of(world, effect["economy"], DEMAND, where)
    if config.stock is None:
        raise RunError(f"'{effect['economy']}' keeps no stock: set `stock` to the items' stock property", where)
    item = entity_of(world, runner.eval(effect["item"], vars), where, f"a {config.items}")
    if not world.is_a(item.entity_type, config.items):
        raise RunError(f"{item.id} is not a {config.items}", where)
    return config, item, whole(runner.eval(effect["qty"], vars), where, "qty")


def _flow_name(effect: Dict[str, Any], key: str, where: str) -> str:
    value = effect.get(key)
    if not isinstance(value, str) or not value or value in ("sold", "returned"):
        raise RunError(f"`{key}` names the flow in plain words (not sold or returned), got {value!r}", where)
    return value


@family_action("economy", ("demand",), "receive", keys=("item", "qty", "source"), required=("item", "qty", "source"),
               literal=("source",),
               example='{"economy": "shop", "action": "receive", "item": "$it", "qty": 12, "source": "supplier"}  '
                       '(units enter an item\'s stock from a named source)')
def _receive(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    config, item, qty = _item_and_qty(runner, effect, vars, where)
    stock_in(runner.world, effect["economy"], config, item, qty, _flow_name(effect, "source", where))


@family_action("economy", ("demand",), "remove", keys=("item", "qty", "sink"), required=("item", "qty", "sink"),
               literal=("sink",),
               example='{"economy": "shop", "action": "remove", "item": "$it", "qty": 2, "sink": "damaged"}  '
                       '(units leave an item\'s stock into a named sink; refused when fewer are on hand)')
def _remove(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    config, item, qty = _item_and_qty(runner, effect, vars, where)
    stock_out(runner.world, effect["economy"], config, item, qty, _flow_name(effect, "sink", where))
