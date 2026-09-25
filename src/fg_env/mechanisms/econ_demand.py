"""The ``economy`` family's ``demand`` mode: customers' demand for stocked items, drawn from the world's patterns and
served from stock.

Every round each item's expected demand is its segment's ``rate`` times its ``factors`` — patterns such as a season, a
trend, a price elasticity, a promotion or substitution between items — and a counts pattern draws the whole units asked
for. Sales are capped by stock: what could not be sold is kept apart from what was asked (the true demand), part of it
buys a substitute, part waits as a backorder and the rest is lost. Segments (bulk buyers, sales channels) have their
own demand, prices and returns. Revenue can go into a ledger account, and every total is readable per item, group and
segment. The round's work (prices, returns, sales) and the stock actions are in :mod:`.econ_demand_trade`.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..expr import Call, ExprError, compile_expr, function
from ..patterns.base import KINDS, declared
from ..registry import MechanismError, mode
from .econ_base import (
    DEMAND,
    EPS,
    compiles,
    config_of,
    props,
    register_config,
    require_currency,
    require_types,
    valid_name,
)
from .expressions import Expr

__all__ = ["DemandConfig", "SegmentSpec", "FactorRef", "ReturnsSpec", "segments_of", "RECORD_FIELDS"]


class FactorRef(BaseModel):
    """A pattern read for each item: its key, and for a response what it answers."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(..., description="A declared pattern.")
    key: Expr | None = Field(None,
                            description="Its key, as an expression over $it (default: the item, for a pattern with "
                                        "keys).")
    driver: Expr | None = Field(None, description="For a response (elasticity, saturation …): what it is called with, "
                                                 "as an expression over $it and $price (default: $price, the price "
                                                 "paid).")


#: A number, an expression over ``$it`` and ``$price``, a pattern's name, or a pattern read.
Factor = float | str | FactorRef

_RATE = ("Expected units a round for each item before its factors: a number, an expression over $it, or a pattern "
         "(its name, or {pattern, key}; a keyed pattern reads the item).")
_FACTORS = ("Multipliers of the rate, each a pattern name, {pattern, key, driver} or an expression over $it and "
            "$price: a response (elasticity) is called with its driver (default $price), a cross_price pattern with "
            "every item's price, any other pattern (season, promotion, trend) is read for the item.")
_NOISE = ("A counts pattern drawing whole units around the expected demand (Poisson when omitted). An unkeyed one "
          "gives every item and segment its own draw.")


class ReturnsSpec(BaseModel):
    """Units a segment sends back."""

    model_config = ConfigDict(extra="forbid")

    rate: float | str = Field(..., description="Share of units sold that come back (number or expression over $it).")
    delay: int | str = Field(1, description="Rounds until they come back (number or expression over $it).")
    restock: float | str = Field(1.0, description="Share of returned units fit to sell again; the rest are scrapped.")


class SegmentSpec(BaseModel):
    """A customer segment or sales channel with its own demand."""

    model_config = ConfigDict(extra="forbid")

    rate: Factor = Field(..., description=_RATE)
    factors: list[Factor] = Field([], description=_FACTORS)
    noise: str | None = Field(None, description=_NOISE)
    price: Expr | None = Field(None, description="What the segment pays per unit, as an expression over $it and $price "
                                                "(the item's price): \"$price * 0.85\". Default $price.")
    where: Expr | None = Field(None, description="Only items where this holds ($it).")
    returns: ReturnsSpec | None = Field(None, description="Units this segment sends back: {rate, delay, restock}.")


class DemandConfig(BaseModel):
    """Demand for stocked items."""

    model_config = ConfigDict(extra="forbid")

    items: str = Field(...,
                       description="Entity type of the items (one entity per SKU or product, e.g. generated from a "
                                   "table).")
    rate: Factor = Field(..., description=_RATE + " This is the main segment; `segments` adds others.")
    factors: list[Factor] = Field([], description=_FACTORS)
    noise: str | None = Field(None, description=_NOISE)
    returns: ReturnsSpec | None = Field(None, description="Units the main segment sends back: {rate, delay, restock}.")
    stock: str | None = Field(None, description="Int property of each item holding the units on hand; sales are "
                                                "capped by it (generated when the type lacks it). Without it all "
                                                "demand is served.")
    price: float | str = Field("$it.price", description="Each item's price this round, as an expression over $it; "
                                                        "read at the start of every round into <name>_price.")
    promotion: float | str = Field(0.0, description="Each item's promotion depth this round (0.2 = 20% off), as an "
                                                    "expression over $it; read into <name>_promo before `price`, so "
                                                    "the price and a promotion pattern (input $it.<name>_promo) can "
                                                    "use it.")
    cost: float | str = Field(0.0, description="Unit cost of an item (expression over $it), for margins.")
    group: Expr | None = Field(None,
                              description="Each item's group (a category), as an expression over $it, for totals by "
                                          "group.")
    segment: str = Field("retail", description="Name of the main segment.")
    segments: dict[str, SegmentSpec] = Field({}, description="Further segments with their own demand — bulk buyers, "
                                                             "channels: {name: {rate, factors, noise, price, where, "
                                                             "returns}}, served after the main one, in the order "
                                                             "listed.")
    substitutes: Expr | None = Field(None, description="Items a customer who finds an item out of stock tries instead, "
                                                      "in order: an expression over $it giving ids or entities "
                                                      "([$it.sibling]).")
    spill: float | str = Field(0.0,
                               description="Share of unmet demand that tries the substitutes (expression over $it).")
    backorder: float | str = Field(0.0, description="Share of the demand still unmet that waits for stock (a "
                                                    "backorder, served first when stock arrives) instead of leaving.")
    recent: int = Field(8, ge=1, description="Rounds of sales each item keeps in <name>_recent, oldest first.")
    account: str | None = Field(None, description="Entity id whose ledger balance receives revenue and pays refunds.")
    currency: str | None = Field(None, description="The ledger currency of `account`.")
    record: bool | str = Field(False, description="Post each item's round, per segment, to the record <name>_history "
                                                  "(true, or an expression over $inputs): the columns fit_patterns "
                                                  "reads.")


register_config(DEMAND, DemandConfig)

#: Columns of ``<name>_history`` besides one per factor driver.
RECORD_FIELDS: dict[str, str] = {"time": "text", "item": "text", "segment": "text", "units": "int", "stockout": "int",
                                 "demand": "int", "lost": "int", "stock": "int", "price": "number", "promo": "number"}
_OBSERVATIONS = ("counts", "measurement", "censored", "missing")
#: Measures of ``$demand_totals`` and the item property each sums (the rest are derived).
_ITEM_TOTALS = {"demand": "demand_total", "served": "served_total", "substituted": "substituted_total",
                "backordered": "backordered_total", "lost": "lost_total", "spill_in": "spill_in_total",
                "sold": "sold_total",
                "returned": "returned_total", "revenue": "revenue", "refunds": "refunds", "cogs": "cogs"}
_DERIVED = ("fill_rate", "net_revenue", "margin")
#: Totals kept per segment.
SEGMENT_TOTALS = ("demand", "served", "sold", "lost", "returned", "revenue", "refunds", "cogs")


def segments_of(config: DemandConfig) -> dict[str, SegmentSpec]:
    """Every segment, the main one first."""
    main = SegmentSpec(rate=config.rate, factors=config.factors, noise=config.noise, returns=config.returns)
    return {config.segment: main, **config.segments}


_DOC = ("Customers' demand for stocked items, drawn from patterns and served from stock. Each round an item's "
        "expected demand is `rate` × `factors` (patterns: base, season, trend, price elasticity with its driver, "
        "promotion, cross-price substitution, drivers), a counts pattern (`noise`) draws the units, and sales are "
        "capped by `stock`: unmet demand partly buys `substitutes` (`spill`), partly waits (`backorder`) and the rest "
        "is lost — true demand and lost sales are kept apart. `segments` add bulk buyers or channels with their own "
        "rate, price, items and `returns` (rate, delay, restock). Prices and promotions are read at the start of the "
        "round, sales at its end; revenue goes to a ledger `account`. Item props <name>_price, _promo, _expected, "
        "_variance (of this round's demand), _demand, _sold, _lost, _stockout, _backlog, _recent and totals; outputs "
        "<name>_demand, _sold, _lost, _fill_rate, _revenue, _margin, _returned, by item and by `group`; metrics per "
        "round. Stock changes only through sales, returns and the `receive`/`remove` actions (invariant "
        "$stock_conserved). `record` posts <name>_history rows (time, item, segment, units, stockout, demand, lost, "
        "stock, price, promo, each driver) ready for fit_patterns.")


@mode("economy", "demand", DemandConfig, _DOC,
      example={"items": "sku", "stock": "stock", "price": "$it.list_price * (1 - $it.shop_promo)",
               "promotion": "0.2 if $pattern.promo_week($it.category) > 0 else 0", "cost": "$it.unit_cost",
               "group": "$it.category", "rate": "demand",
               "factors": [{"pattern": "price_effect", "key": "$it.category", "driver": "$price / $it.list_price"},
                           "promo"],
               "noise": "sales", "substitutes": "[$it.sibling]", "spill": 0.3,
               "segments": {"repair_shops": {"rate": "$it.shop_rate", "price": "$price * 0.85"}}})
def _expand_demand(name: str, config: DemandConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    items = config.items
    require_types(contract, [items], "items")
    segments = segments_of(config)
    if config.segment in config.segments:
        raise MechanismError(f"'{config.segment}' is the main segment and a listed segment", "rename one of them",
                             "segments")
    patterns = declared(contract)
    drivers: list[str] = []
    for segment, spec in segments.items():
        path = "" if segment == config.segment else f"segments.{segment}."
        if not valid_name(segment):
            raise MechanismError(f"segment '{segment}' is not a valid name", "use letters, digits and _",
                                 f"{path}".rstrip(".") or "segment")
        drivers += _check_segment(spec, patterns, path)
    for field in ("price", "promotion", "cost", "group", "substitutes", "spill", "backorder", "record"):
        compiles(getattr(config, field), field)
    if (config.account is None) != (config.currency is None):
        raise MechanismError("`account` and `currency` go together",
                             "give both to pay revenue into a ledger, or neither", "account")
    if config.currency is not None:
        require_currency(contract, config.currency)
    if config.stock is not None and not valid_name(config.stock):
        raise MechanismError(f"'{config.stock}' is not a property name", "use letters, digits and _", "stock")
    fragment: dict[str, Any] = {
        "types": {items: {"props": _item_props(name, config)}},
        "world": _world_props(name, config),
        "events": [{"name": f"{name}: prices and returns", "phase": "start",
                    "do": [{"economy": name, "action": "open"}]},
                   {"name": f"{name}: sales", "phase": "end", "do": [{"economy": name, "action": "trade"}]}],
        "metrics": {f"{name}_{measure}": {"expr": f"$sum({items}, $it.{name}_{measure})", "unit": "units"}
                    for measure in ("demand", "sold", "lost")},
        "outputs": _outputs(name, config),
    }
    if config.stock is not None:
        fragment["metrics"][f"{name}_stock"] = {"expr": f"$sum({items}, $it.{config.stock})", "unit": "units"}
        fragment["invariants"] = [{"expr": f"$stock_conserved('{name}')", "check": "round",
                                   "why": f"Stock of {items} changes only by {name}'s sales and returns and its "
                                          "receive and remove actions."}]
    if config.record is not False:
        clash = [d for d in drivers if d in RECORD_FIELDS]
        if clash:
            raise MechanismError(f"a factor driver is recorded in a column named after its pattern, and '{clash[0]}' "
                                 "is already a column", "rename the pattern", "record")
        fields = {**RECORD_FIELDS, **{driver: "number" for driver in drivers}}
        fragment["records"] = {f"{name}_history": {"fields": fields, "notify": False,
                                                   "description": f"Every item's round per segment ({name})."}}
    return fragment


def _check_segment(spec: SegmentSpec, patterns: Mapping[str, Any], path: str) -> list[str]:
    """Check one segment's reads; returns the patterns whose drivers are recorded."""
    drivers = []
    for field, factor in [("rate", spec.rate), *[(f"factors[{i}]", f) for i, f in enumerate(spec.factors)]]:
        where = f"{path}{field}"
        if isinstance(factor, str) and "$" in factor:
            compiles(factor, where)
            continue
        if isinstance(factor, (int, float)):
            continue
        ref = factor if isinstance(factor, FactorRef) else FactorRef(pattern=factor)
        kind = _pattern_kind(patterns, ref.pattern, where)
        if kind in _OBSERVATIONS:
            raise MechanismError(f"'{ref.pattern}' is a {kind} pattern, which observes demand rather than scaling it",
                                 "name a counts pattern as `noise`" if kind == "counts"
                                 else "remove it from the factors", where)
        compiles(ref.key, f"{where}.key")
        compiles(ref.driver, f"{where}.driver")
        if ref.driver is not None and ref.pattern not in drivers:
            drivers.append(ref.pattern)
    if spec.noise is not None and _pattern_kind(patterns, spec.noise, f"{path}noise") != "counts":
        raise MechanismError(f"'{spec.noise}' is not a counts pattern",
                             "declare {\"kind\": \"counts\", ...} and name it", f"{path}noise")
    compiles(spec.price, f"{path}price")
    compiles(spec.where, f"{path}where")
    if spec.returns is not None:
        for field in ("rate", "delay", "restock"):
            compiles(getattr(spec.returns, field), f"{path}returns.{field}")
    return drivers


def _pattern_kind(patterns: Mapping[str, Any], name: str, where: str) -> str | None:
    spec = patterns.get(name)
    if not isinstance(spec, Mapping):
        listed = ", ".join(patterns) or "none"
        raise MechanismError(f"'{name}' is not a declared pattern",
                             f"patterns: {listed} (or write an expression with $)", where)
    kind = spec.get("kind")
    return kind if isinstance(kind, str) and kind in KINDS else None


def _item_props(name: str, config: DemandConfig) -> dict[str, Any]:
    whole = {"type": "int", "default": 0, "min": 0}
    amount = {"type": "number", "default": 0.0}
    out: dict[str, Any] = {
        f"{name}_price": {**amount, "description": "Price this round."},
        f"{name}_promo": {**amount, "description": "Promotion depth this round."},
        f"{name}_expected": {**amount, "description": "Expected demand this round, every segment."},
        f"{name}_variance": {**amount, "description": "Variance of this round's demand."},
        f"{name}_demand": {**whole, "description": "Units asked for this round."},
        f"{name}_sold": {**whole,
                         "description": "Units sold this round (to its own customers, as a substitute, and backorders "
                                        "filled)."},
        f"{name}_lost": {**whole, "description": "Units of this round's demand that bought nothing."},
        f"{name}_stockout": {"type": "bool", "default": False,
                             "description": "Demand went unmet from stock this round."},
        f"{name}_backlog": {**whole, "description": "Units on backorder."},
        f"{name}_recent": {"type": "list", "default": [],
                           "description": "Units sold in the last rounds, oldest first."},
        f"{name}_expected_recent": {"type": "list", "default": [],
                           "description": "Expected demand in the last rounds, oldest first."},
        f"{name}_variance_recent": {"type": "list", "default": [],
                           "description": "Its variance in the last rounds, oldest first."},
        f"{name}_rounds_out": {**whole, "description": "Rounds with unmet demand."},
    }
    for measure, prop in _ITEM_TOTALS.items():
        out[f"{name}_{prop}"] = dict(amount if measure in ("revenue", "refunds", "cogs") else whole)
    if config.stock is not None:
        out[config.stock] = {**whole, "description": "Units on hand."}
    return out


def _world_props(name: str, config: DemandConfig) -> dict[str, Any]:
    out: dict[str, Any] = {
        f"{name}_returns": {"type": "list", "default": [],
                            "description": "Returns on their way: [round due, item, segment, units, unit price]."},
        f"{name}_segments": {"type": "map", "default": {}, "description": "Totals per segment."},
    }
    if config.stock is not None:
        out[f"{name}_stock_start"] = {"type": "number", "default": f"$sum({config.items}, $it.{config.stock})",
                                      "description": "Units on hand when the run began."}
        out[f"{name}_stock_flows"] = {"type": "map", "default": {},
                                      "description": "Units that entered (+) or left (−) stock, by flow."}
    return out


def _outputs(name: str, config: DemandConfig) -> dict[str, Any]:
    formats = {"fill_rate": "pct", "revenue": "money"}  # the headline outcomes reports lead with
    out: dict[str, Any] = {}
    for measure in ("demand", "sold", "lost", "fill_rate", "revenue", "margin", "returned"):
        out[f"{name}_{measure}"] = {"expr": f"$demand_totals('{name}', '{measure}')", "type": "number",
                                    **({"format": formats[measure]} if measure in formats else {})}
    for measure in ("sold", "lost", "fill_rate"):
        out[f"{name}_{measure}_by_item"] = {"expr": f"$demand_totals('{name}', '{measure}', 'item')", "type": "map"}
    if config.group is not None:
        for measure in ("sold", "lost", "fill_rate", "revenue", "margin"):
            out[f"{name}_{measure}_by_group"] = {"expr": f"$demand_totals('{name}', '{measure}', 'group')",
                                                 "type": "map"}
    if config.segments:
        for measure in ("sold", "fill_rate", "revenue", "returned"):
            out[f"{name}_{measure}_by_segment"] = {"expr": f"$demand_totals('{name}', '{measure}', 'segment')",
                                                   "type": "map"}
    return out


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _value(measure: str, totals: Mapping[str, float]) -> float:
    if measure == "fill_rate":
        return round(totals["served"] / totals["demand"], 4) if totals["demand"] else 1.0
    if measure == "net_revenue":
        return round(totals["revenue"] - totals["refunds"], 2)
    if measure == "margin":
        return round(totals["revenue"] - totals["refunds"] - totals["cogs"], 2)
    value = totals[measure]
    return round(value, 2) if measure in ("revenue", "refunds", "cogs") else value


def totals(world: Any, name: str, measure: str, by: str | None, where: str) -> Any:
    """A demand mechanism's total of ``measure``, overall or ``{key: total}`` by item, group or segment."""
    config: DemandConfig = config_of(world, name, DEMAND, where)
    known = [*_ITEM_TOTALS, *_DERIVED]
    if measure not in known:
        raise ExprError(f"$demand_totals: unknown measure '{measure}' (measures: {', '.join(known)})", where)
    needed = ["served", "demand"] if measure == "fill_rate" else (
        ["revenue", "refunds", "cogs"] if measure in ("margin", "net_revenue") else [measure])
    if by == "segment":
        if any(m not in SEGMENT_TOTALS for m in needed):
            raise ExprError(f"$demand_totals: '{measure}' is not kept per segment (per segment: "
                            f"{', '.join([*SEGMENT_TOTALS, *_DERIVED])})", where)
        kept = world.props.get(f"{name}_segments") or {}
        return {segment: _value(measure, {m: float((kept.get(segment) or {}).get(m, 0)) for m in SEGMENT_TOTALS})
                for segment in segments_of(config)}
    if by not in (None, "item", "group"):
        raise ExprError(f"$demand_totals: `by` is item, group or segment, got {by!r}", where)
    if by == "group" and config.group is None:
        raise ExprError(f"$demand_totals: '{name}' declares no `group`", where)
    grouped: dict[Any, dict[str, float]] = {}
    group = compile_expr(config.group) if by == "group" and config.group else None
    for item in world.entities_of(config.items):
        key = None if by is None else item.id if by == "item" else group(world.evaluation.scope(it=item))  # type: ignore[misc]
        sums = grouped.setdefault(key, {m: 0.0 for m in _ITEM_TOTALS})
        for m in needed:
            sums[m] += float(props(item).get(f"{name}_{_ITEM_TOTALS[m]}") or 0)
    if by is None:
        return _value(measure, grouped.get(None) or {m: 0.0 for m in _ITEM_TOTALS})
    return {str(key): _value(measure, sums) for key, sums in grouped.items()}


@function("demand_totals(mechanism, measure, by?)",
          "A demand mechanism's run total: demand, served, substituted, backordered, lost, spill_in, sold, returned, "
          "revenue, refunds, cogs, fill_rate, net_revenue or margin — overall, or {key: total} by 'item', 'group' "
          "or 'segment'.",
          min_args=2, max_args=3, family="economy")
def _demand_totals(call: Call) -> Any:
    name, measure, by = call.arg(0), call.arg(1), call.arg(2)
    return totals(call.scope.world, str(name), str(measure), None if by is None else str(by), call.source)


def stock_balance(world: Any, name: str, where: str) -> float:
    """Units on hand minus what the start and every recorded flow account for (0 when stock is conserved)."""
    config: DemandConfig = config_of(world, name, DEMAND, where)
    if config.stock is None:
        raise ExprError(f"'{name}' keeps no stock (set `stock`)", where)
    on_hand = sum(float(props(item).get(config.stock) or 0) for item in world.entities_of(config.items) if item.alive)
    flows = world.props.get(f"{name}_stock_flows") or {}
    return on_hand - float(world.props.get(f"{name}_stock_start") or 0) - sum(float(v) for v in flows.values())


@function("stock_conserved(mechanism)", "True while a demand mechanism's stock equals its starting stock plus every "
          "recorded flow in and out (sales, returns, receive and remove).", min_args=1, max_args=1, family="economy")
def _stock_conserved(call: Call) -> bool:
    return abs(stock_balance(call.scope.world, str(call.arg(0)), call.source)) < EPS
