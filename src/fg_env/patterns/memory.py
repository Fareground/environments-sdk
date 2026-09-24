"""Memory patterns: effects that carry over from earlier rounds — adstock and lags, promotions with a dip after,
reference prices, habit and fatigue.

Each declares an ``input`` read from the running world every round (any root, plus ``$key``, ``$row`` and ``$it``
for entity keys). A read during a round combines the input as it is now with the state carried from earlier
rounds; at the end of every round (after end events, before metrics) the input is committed into the state, which
lives in the world property ``patterns_memory`` — journaled, and carried by snapshots, clones and forks.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import Number, PatternConfig, kind
from .responses import driver

__all__ = ["CarryoverConfig", "PromotionConfig", "ReferencePriceConfig", "HabitConfig"]


class _Memory(PatternConfig):
    input: str = Field(..., description="What drives it, read every round: an expression over the world "
                                        "($world.promo_spend, $it.price with entity keys).")
    every: float | None = Field(None, gt=0, description="Clock units per step of carry-over (default: one round).")


def _retain(ctx: Any) -> float:
    if getattr(ctx.cfg, "half_life", None) is not None:
        half = ctx.number("half_life")
        if half <= 0:
            raise ctx.fail(f"`half_life` must be more than 0, got {half:g}")
        return 0.5 ** (1 / half)
    return ctx.number("retain", 0, 1)


def _elapsed(ctx: Any, state: dict[str, Any] | None) -> float:
    """Steps since the state was committed (1 on round clocks)."""
    return 1.0 if state is None else max(0.0, (ctx.t - float(state["t"])) / ctx.every())


def _current(ctx: Any) -> tuple[float, dict[str, Any] | None, bool]:
    """``(input, state, committed)``: this round's input and the state before it; after the round's commit, the
    committed input and the state it was combined with."""
    state = ctx.state()
    if state is not None and abs(float(state["t"]) - ctx.t) <= 1e-9:
        return float(state["x"]), state.get("before"), True
    return driver(ctx, ctx.input(), "`input`"), state, False


def _saved(ctx: Any, x: Any, state: dict[str, Any] | None, **values: Any) -> dict[str, Any]:
    before = state.get("before") if state is not None and abs(float(state["t"]) - ctx.t) <= 1e-9 else state
    return {"t": ctx.t, "x": driver(ctx, x, "`input`"), "before": before, **values}


# ---------------------------------------------------------------------------
# carryover
# ---------------------------------------------------------------------------


class CarryoverConfig(_Memory):
    kind: Literal["carryover"] = "carryover"
    retain: Number = Field(0.5, description="Share carried into the next step (adstock decay).")
    half_life: Number | None = Field(None, description="Steps for a past input's effect to halve (instead of retain).")
    lag: int = Field(0, ge=0, description="Steps before an input starts to count.")
    form: Literal["sum", "average"] = Field("sum", description="sum: input + retain·stock (adstock) | average: (1 − "
                                                               "retain)·input + retain·stock (a moving average).")
    start: Number = Field(0.0, description="The stock before round 1 (and the input before a lag has filled).")


def _lagged(ctx: Any, x: float, before: dict[str, Any] | None) -> float:
    lag = ctx.cfg.lag
    if lag == 0:
        return x
    history: list[float] = list((before or {}).get("inputs") or [])
    return history[-lag] if len(history) >= lag else ctx.number("start")


def _stock(ctx: Any, x: float, before: dict[str, Any] | None) -> float:
    retain = _retain(ctx) ** _elapsed(ctx, before)
    used = _lagged(ctx, x, before)
    previous = float(before["s"]) if before is not None else ctx.number("start")
    if ctx.cfg.form == "average":
        return (1 - retain) * used + retain * previous
    return used + retain * previous


def _carryover_commit(ctx: Any, x: Any, state: dict[str, Any] | None) -> dict[str, Any]:
    value, before, _ = _current(ctx) if state is not None and abs(float(state["t"]) - ctx.t) <= 1e-9 else (
        driver(ctx, x, "`input`"), state, False)
    inputs = list((before or {}).get("inputs") or [])
    if ctx.cfg.lag:
        inputs = (inputs + [value])[-ctx.cfg.lag:]
    return {**_saved(ctx, value, state), "s": _stock(ctx, value, before), "inputs": inputs}


@kind("carryover", "memory", "memory", CarryoverConfig,
      "Carry-over (adstock) and lags: past inputs keep counting, fading by `retain` each step — advertising, "
      "word of mouth, a backlog.",
      example={"kind": "carryover", "input": "$world.ad_spend", "half_life": 2, "lag": 1},
      params=("retain", "half_life", "start"), commit=_carryover_commit,
      words=lambda cfg: f"carry-over of {cfg.input} ({cfg.form}, retain "
                        f"{cfg.half_life and f'half-life {cfg.half_life}' or cfg.retain}"
                        + (f", lag {cfg.lag}" if cfg.lag else "") + ")")
def _carryover(ctx: Any) -> float:
    x, before, _ = _current(ctx)
    return _stock(ctx, x, before)


# ---------------------------------------------------------------------------
# promotion
# ---------------------------------------------------------------------------


class PromotionConfig(_Memory):
    kind: Literal["promotion"] = "promotion"
    lift: Number = Field(...,
                         description="Extra demand per unit of promotion intensity (form linear: 0.6 = +60% at 1).")
    form: Literal["linear", "exponential"] = Field("linear", description="linear: 1 + lift·intensity | exponential: "
                                                                         "e^(lift·intensity) (log-linear; 20% off at "
                                                                         "lift 1.65 ≈ +39%).")
    dip: Number = Field(0.0,
                        description="Demand lost after a promotion per unit of promotion still remembered "
                                    "(pull-forward).")
    retain: Number = Field(0.5,
                           description="Share of the remembered promotion kept each step (how long the dip lasts).")
    half_life: Number | None = Field(None,
                                     description="Steps for the remembered promotion to halve (instead of retain).")


def _pressure(ctx: Any, before: dict[str, Any] | None) -> float:
    return 0.0 if before is None else float(before["p"]) * _retain(ctx) ** _elapsed(ctx, before)


def _promotion_commit(ctx: Any, x: Any, state: dict[str, Any] | None) -> dict[str, Any]:
    value, before, committed = _current(ctx)
    if not committed:
        value, before = driver(ctx, x, "`input`"), state
    return {**_saved(ctx, value, state), "p": _pressure(ctx, before) + value}


@kind("promotion", "response", "memory", PromotionConfig,
      "Promotion lift with a post-promotion dip: demand rises by `lift` × intensity while a promotion runs, then "
      "dips while customers work through what they bought early.",
      example={"kind": "promotion", "input": "$it.promo", "keys": "sku", "lift": 0.8, "dip": 0.25, "half_life": 1.5},
      params=("lift", "dip", "retain", "half_life"), commit=_promotion_commit,
      words=lambda cfg: f"promotion lift {cfg.lift} from {cfg.input}, dipping by {cfg.dip} afterwards")
def _promotion(ctx: Any) -> float:
    x, before, _ = _current(ctx)
    if x > 0:
        lift = ctx.number("lift")
        return math.exp(min(700.0, lift * x)) if ctx.cfg.form == "exponential" else 1 + lift * x
    return max(0.0, 1 - ctx.number("dip") * min(1.0, _pressure(ctx, before)))


# ---------------------------------------------------------------------------
# reference price
# ---------------------------------------------------------------------------


class ReferencePriceConfig(_Memory):
    kind: Literal["reference_price"] = "reference_price"
    retain: Number = Field(0.7, description="Weight of the old reference when it updates toward the price paid.")
    gain: Number = Field(1.0, description="Demand gained per share the price is below the reference.")
    loss: Number = Field(2.0, description="Demand lost per share the price is above it (losses loom larger).")
    output: Literal["effect", "reference"] = Field("effect",
                                                   description="effect: the demand multiplier | reference: the "
                                                               "remembered price.")


def _reference(ctx: Any, x: float, before: dict[str, Any] | None) -> float:
    return x if before is None else float(before["r"])


def _reference_commit(ctx: Any, x: Any, state: dict[str, Any] | None) -> dict[str, Any]:
    value, before, committed = _current(ctx)
    if not committed:
        value, before = driver(ctx, x, "`input`"), state
    reference = _reference(ctx, value, before)
    retain = ctx.number("retain", 0, 1) ** _elapsed(ctx, before)
    return {**_saved(ctx, value, state), "r": retain * reference + (1 - retain) * value}


@kind("reference_price", "response", "memory", ReferencePriceConfig,
      "Reference-price effects: customers remember past prices; a price under the memory lifts demand, one above cuts "
      "it more (loss aversion). The memory drifts toward prices paid.",
      example={"kind": "reference_price", "input": "$it.price", "keys": "sku", "retain": 0.8, "gain": 0.8, "loss": 1.6},
      params=("retain", "gain", "loss"), commit=_reference_commit,
      words=lambda cfg: f"reference price remembered from {cfg.input} (gain {cfg.gain}, loss {cfg.loss})")
def _reference_price(ctx: Any) -> float:
    x, before, _ = _current(ctx)
    reference = _reference(ctx, x, before)
    if ctx.cfg.output == "reference":
        return reference
    if reference <= 0:
        raise ctx.fail(f"the remembered price must be more than 0, got {reference:g}")
    below, above = max(0.0, reference - x) / reference, max(0.0, x - reference) / reference
    return max(0.0, 1 + ctx.number("gain") * below - ctx.number("loss") * above)


# ---------------------------------------------------------------------------
# habit and fatigue
# ---------------------------------------------------------------------------


class HabitConfig(_Memory):
    kind: Literal["habit"] = "habit"
    form: Literal["habit", "fatigue"] = Field("habit",
                                              description="habit: 1 + strength·S/(1 + S), growing with repetition | "
                                                          "fatigue: 1/(1 + strength·S), wearing out with exposure.")
    strength: Number = Field(..., description="How strongly the remembered exposure S acts.")
    retain: Number = Field(0.8, description="Share of S kept each step.")
    half_life: Number | None = Field(None, description="Steps for S to halve (instead of retain).")

    @model_validator(mode="after")
    def _positive(self) -> HabitConfig:
        if isinstance(self.strength, (int, float)) and self.strength < 0:
            raise ValueError("strength must be ≥ 0")
        return self


def _exposure(ctx: Any, x: float, before: dict[str, Any] | None) -> float:
    previous = 0.0 if before is None else float(before["s"])
    return max(0.0, x) + _retain(ctx) ** _elapsed(ctx, before) * previous


def _habit_commit(ctx: Any, x: Any, state: dict[str, Any] | None) -> dict[str, Any]:
    value, before, committed = _current(ctx)
    if not committed:
        value, before = driver(ctx, x, "`input`"), state
    return {**_saved(ctx, value, state), "s": _exposure(ctx, value, before)}


@kind("habit", "population", "memory", HabitConfig,
      "Habit and fatigue: repeated exposure (purchases, ads, messages) builds a habit that raises response, or a "
      "fatigue that wears it down; both fade when exposure stops.",
      example={"kind": "habit", "form": "fatigue", "input": "$it.ads_seen", "keys": "viewer", "strength": 0.3,
               "half_life": 3},
      params=("strength", "retain", "half_life"), commit=_habit_commit,
      words=lambda cfg: f"{cfg.form} from {cfg.input} (strength {cfg.strength})")
def _habit(ctx: Any) -> float:
    x, before, _ = _current(ctx)
    stock = _exposure(ctx, x, before)
    if ctx.cfg.form == "fatigue":
        return 1 / (1 + ctx.number("strength") * stock)
    return 1 + ctx.number("strength") * stock / (1 + stock)


def _unused(value: float) -> float:  # pragma: no cover
    return math.fsum([value])
