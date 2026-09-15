"""Response curves: how a quantity answers a driver — price, spend, users, tenure, cumulative output.

A response is called with its driver: ``$pattern.price_effect($it.price)`` (and the key last when it is keyed).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import Field

from .base import Number, PatternConfig, kind

__all__ = ["ElasticityConfig", "CrossPriceConfig", "SaturationConfig", "ThresholdConfig", "LearningCurveConfig",
           "NetworkConfig", "HazardConfig", "driver"]


def driver(ctx: Any, value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ctx.fail(f"{what} must be a finite number, got {value!r}")
    return float(value)


class ElasticityConfig(PatternConfig):
    kind: Literal["elasticity"] = "elasticity"
    elasticity: Number = Field(..., description="% change in quantity per % change in price at the reference (−1.5).")
    reference: Number = Field(1.0, description="The price where the effect is 1.")
    form: Literal["constant", "linear"] = Field("constant", description="constant: (price/reference)^elasticity | "
                                                                       "linear: 1 + elasticity·(price/reference − 1), never below 0.")


@kind("elasticity", "response", "response", ElasticityConfig,
      "Price elasticity: a multiplier on demand for a price, constant-elasticity or linear.",
      example={"kind": "elasticity", "elasticity": "$inputs.elasticity", "reference": 24.99},
      args=("price",), params=("elasticity", "reference"),
      words=lambda cfg: f"{cfg.form} price elasticity {cfg.elasticity} around a price of {cfg.reference}")
def _elasticity(ctx: Any, price: Any) -> float:
    p, ref, e = driver(ctx, price, "the price"), ctx.number("reference"), ctx.number("elasticity")
    if ref <= 0:
        raise ctx.fail(f"`reference` must be more than 0, got {ref:g}")
    if ctx.cfg.form == "linear":
        return max(0.0, 1 + e * (p / ref - 1))
    if p <= 0:
        raise ctx.fail(f"a constant-elasticity price must be more than 0, got {p:g}")
    return float((p / ref) ** e)


class CrossPriceConfig(PatternConfig):
    kind: Literal["cross_price"] = "cross_price"
    reference: Union[Number, Dict[str, Number]] = Field(..., description="Reference price: one for all keys, or {key: price}.")
    own: Number = Field(..., description="Own-price elasticity (on the diagonal).")
    cross: Number = Field(0.0, description="Cross-price elasticity toward the other keys (> 0: substitutes, < 0: complements).")
    groups: Union[Dict[str, str], str, None] = Field(None, description="{key: group}: cross effects only within a group "
                                                                       "(tiers of the same part).")
    matrix: Union[List[List[Number]], str, None] = Field(None, description="Full elasticities instead of own/cross: "
                                                                          "row i is how item i answers each price, in key order.")


@kind("cross_price", "response", "response", CrossPriceConfig,
      "Substitution between items: each item's demand multiplier from every item's price relative to its reference "
      "(own and cross elasticities, or a full matrix). Called with {key: price} and the item's key.",
      example={"kind": "cross_price", "keys": ["economy", "premium"], "reference": {"economy": 20, "premium": 35},
               "own": -1.8, "cross": 0.6},
      args=("prices",), params=("reference", "own", "cross", "groups", "matrix"),
      words=lambda cfg: f"substitution: own elasticity {cfg.own}, cross {cfg.cross}" + (" within groups" if cfg.groups else ""))
def _cross_price(ctx: Any, prices: Any) -> float:
    if not isinstance(prices, Mapping):
        raise ctx.fail(f"the prices must be a map of {{key: price}}, got {type(prices).__name__}")
    keys = ctx.rt.keys(ctx.name, ctx.source)
    reference, groups = ctx.param("reference"), ctx.param("groups")
    matrix = ctx.param("matrix")
    own_index = keys.index(ctx.key)
    total = 0.0
    for j, other in enumerate(keys):
        if other not in prices:
            raise ctx.fail(f"the prices have no price for '{other}'")
        ref = reference.get(other) if isinstance(reference, Mapping) else reference
        p = driver(ctx, prices[other], f"the price of '{other}'")
        ref = driver(ctx, ref, f"the reference price of '{other}'")
        if p <= 0 or ref <= 0:
            raise ctx.fail(f"prices and references must be more than 0 ('{other}': {p:g} vs {ref:g})")
        if matrix is not None:
            e = driver(ctx, matrix[own_index][j], f"`matrix[{own_index}][{j}]`")
        elif other == ctx.key:
            e = ctx.number("own")
        elif groups is None or (isinstance(groups, Mapping) and groups.get(other) == groups.get(ctx.key)):
            e = ctx.number("cross")
        else:
            e = 0.0
        total += e * math.log(p / ref)
    return math.exp(min(total, 700))


class SaturationConfig(PatternConfig):
    kind: Literal["saturation"] = "saturation"
    form: Literal["hill", "logistic", "exponential"] = Field("hill", description="hill: limit·x^shape/(half^shape + x^shape) | "
                                                           "logistic: limit/(1 + e^(−steepness·(x − midpoint))) | exponential: limit·(1 − e^(−x/scale)).")
    limit: Number = Field(1.0, description="The most it gives.")
    base: Number = Field(0.0, description="Added to the result (the level with no driver).")
    half: Number = Field(1.0, description="hill: the driver giving half the limit.")
    shape: Number = Field(1.0, description="hill: steepness (> 1 is S-shaped).")
    midpoint: Number = Field(0.0, description="logistic: the driver at half the limit.")
    steepness: Number = Field(1.0, description="logistic: how sharp the rise is.")
    scale: Number = Field(1.0, description="exponential: the driver that gives 63% of the limit.")


@kind("saturation", "response", "response", SaturationConfig,
      "Diminishing returns: spend, effort or exposure that helps less and less (Hill, logistic or exponential).",
      example={"kind": "saturation", "form": "hill", "limit": 0.35, "half": 2000, "shape": 1.2},
      args=("x",), params=("limit", "base", "half", "shape", "midpoint", "steepness", "scale"),
      words=lambda cfg: f"diminishing returns ({cfg.form}) up to {cfg.limit}")
def _saturation(ctx: Any, x: Any) -> float:
    value, form = driver(ctx, x, "the driver"), ctx.cfg.form
    if form == "hill":
        half, shape = ctx.number("half"), ctx.number("shape")
        if half <= 0:
            raise ctx.fail(f"`half` must be more than 0, got {half:g}")
        response = 0.0 if value <= 0 else value ** shape / (half ** shape + value ** shape)
    elif form == "logistic":
        response = 1 / (1 + math.exp(min(700.0, -ctx.number("steepness") * (value - ctx.number("midpoint")))))
    else:
        scale = ctx.number("scale")
        if scale <= 0:
            raise ctx.fail(f"`scale` must be more than 0, got {scale:g}")
        response = 1 - math.exp(-max(0.0, value) / scale)
    return ctx.number("base") + ctx.number("limit") * response


class ThresholdConfig(PatternConfig):
    kind: Literal["threshold"] = "threshold"
    at: Number = Field(..., description="The tipping point.")
    below: Number = Field(0.0, description="Value below it.")
    above: Number = Field(1.0, description="Value above it.")
    width: Number = Field(0.0, description="0: a hard switch | > 0: a smooth one over about this width.")


@kind("threshold", "response", "response", ThresholdConfig,
      "A threshold or tipping point: one value below it, another above, switching hard or smoothly.",
      example={"kind": "threshold", "at": 0.3, "below": 1, "above": 1.8, "width": 0.05},
      args=("x",), params=("at", "below", "above", "width"),
      words=lambda cfg: f"{cfg.below} below {cfg.at} and {cfg.above} above it")
def _threshold(ctx: Any, x: Any) -> float:
    value, at, width = driver(ctx, x, "the driver"), ctx.number("at"), ctx.number("width", 0)
    below, above = ctx.number("below"), ctx.number("above")
    if width == 0:
        return above if value >= at else below
    share = 1 / (1 + math.exp(min(700.0, -4 * (value - at) / width)))
    return below + (above - below) * share


class LearningCurveConfig(PatternConfig):
    kind: Literal["learning_curve"] = "learning_curve"
    first: Number = Field(..., description="Cost (or time) of the first unit.")
    rate: Number = Field(0.8, description="Progress ratio: each doubling of cumulative units multiplies the cost by it.")
    floor: Number = Field(0.0, description="Lowest it gets.")


@kind("learning_curve", "response", "response", LearningCurveConfig,
      "A learning curve (Wright's law): cost per unit falls by a fixed ratio each time cumulative output doubles.",
      example={"kind": "learning_curve", "first": 120, "rate": 0.85, "floor": 40},
      args=("units",), params=("first", "rate", "floor"),
      words=lambda cfg: f"a learning curve from {cfg.first}, ×{cfg.rate} per doubling of output")
def _learning_curve(ctx: Any, units: Any) -> float:
    cumulative, rate = max(1.0, driver(ctx, units, "the cumulative units")), ctx.number("rate", 1e-9, 1)
    return max(ctx.number("floor"), ctx.number("first") * cumulative ** math.log2(rate))


class NetworkConfig(PatternConfig):
    kind: Literal["network"] = "network"
    form: Literal["power", "log"] = Field("power", description="power: base + strength·users^exponent | log: base + strength·ln(1 + users).")
    strength: Number = Field(..., description="How much users add.")
    exponent: Number = Field(1.0, description="power: 1 linear, 2 Metcalfe-like, < 1 diminishing.")
    base: Number = Field(1.0, description="Value with no users.")


@kind("network", "response", "response", NetworkConfig,
      "Network effects: value (or appeal) growing with the number or share of users.",
      example={"kind": "network", "form": "log", "strength": 0.2},
      args=("users",), params=("strength", "exponent", "base"),
      words=lambda cfg: f"a {cfg.form} network effect of strength {cfg.strength}")
def _network(ctx: Any, users: Any) -> float:
    value = max(0.0, driver(ctx, users, "the users"))
    if ctx.cfg.form == "log":
        return ctx.number("base") + ctx.number("strength") * math.log1p(value)
    return ctx.number("base") + ctx.number("strength") * value ** ctx.number("exponent")


class HazardConfig(PatternConfig):
    kind: Literal["hazard"] = "hazard"
    form: Literal["constant", "weibull", "loglogistic", "table"] = Field(
        "constant", description="constant: the same chance at every age | weibull: rising (shape > 1) or falling (< 1) | "
                                "loglogistic: rising then falling | table: one chance per age.")
    rate: Number = Field(0.05, description="constant: chance per `span`.")
    shape: Number = Field(1.0, description="weibull, loglogistic: the curve's shape.")
    scale: Number = Field(10.0, description="weibull, loglogistic: typical age, in clock units.")
    values: Union[List[Number], str, None] = Field(None, description="table: chance at age 0, 1, 2 … (the last repeats).")
    span: Number = Field(1.0, description="Clock units the chance covers (usually one round).")


def _survival(ctx: Any, age: float) -> float:
    shape, scale = ctx.number("shape"), ctx.number("scale")
    if scale <= 0 or shape <= 0:
        raise ctx.fail("`shape` and `scale` must be more than 0")
    ratio = max(0.0, age) / scale
    if ctx.cfg.form == "weibull":
        return math.exp(-(ratio ** shape))
    return 1 / (1 + ratio ** shape)


@kind("hazard", "population", "response", HazardConfig,
      "A hazard curve: the chance something happens now (churn, failure, leaving) given how long it has lasted — "
      "use as $chance($pattern.churn($it.tenure)).",
      example={"kind": "hazard", "form": "weibull", "shape": 0.7, "scale": 30},
      args=("age",), params=("rate", "shape", "scale", "values", "span"),
      words=lambda cfg: f"a {cfg.form} hazard curve over age")
def _hazard(ctx: Any, age: Any) -> float:
    years, span = driver(ctx, age, "the age"), ctx.number("span", 1e-9)
    form = ctx.cfg.form
    if form == "constant":
        return ctx.number("rate", 0, 1)
    if form == "table":
        values = ctx.numbers("values")
        if not values:
            raise ctx.fail("`values` needs at least one chance")
        return min(1.0, max(0.0, values[min(len(values) - 1, max(0, int(years)))]))
    now, later = _survival(ctx, years), _survival(ctx, years + span)
    return 1.0 if now <= 0 else min(1.0, max(0.0, 1 - later / now))


def _unused(_: Optional[Any]) -> None:  # pragma: no cover
    return None
