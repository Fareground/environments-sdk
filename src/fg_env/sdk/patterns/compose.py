"""Composition: patterns built from other patterns — demand = base × season × trend × promotion."""
from __future__ import annotations

import math
from fractions import Fraction

from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..expr import ExprError, Scope, compile_expr
from .base import Number, PatternConfig, kind
from .product_math import product

__all__ = ["ProductConfig", "SumConfig", "Operand", "operand_names"]


class Operand(BaseModel):
    """A pattern combined under another key: per-category seasonality inside per-SKU demand."""

    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(..., description="The pattern's name.")
    key: str = Field(..., description="Its key, as an expression over $key, $row and $inputs ($row.category).")


class _Combined(PatternConfig):
    of: List[Union[str, Operand]] = Field(..., min_length=1, description="The patterns combined: names (keyed ones get "
                                                                         "this pattern's key) or {pattern, key}.")

    @model_validator(mode="after")
    def _unique(self) -> "_Combined":
        names = operand_names(self)
        if len(set(names)) != len(names):
            raise ValueError("`of` names a pattern twice")
        return self


def operand_names(cfg: Any) -> List[str]:
    return [item if isinstance(item, str) else item.pattern for item in cfg.of]


def operand_key(ctx: Any, index: int) -> Optional[str]:
    """The key operand ``index`` is read with: this pattern's own key, or its `key` expression (fixed per run)."""
    from .runtime import key_text

    item = ctx.cfg.of[index]
    if isinstance(item, str):
        return ctx.key if ctx.rt.configs[item].keyed else None

    def build() -> str:
        scope = Scope({"inputs": ctx.world.inputs, "key": ctx.key, "row": ctx.row()}, ctx.world)
        try:
            return key_text(compile_expr(item.key)(scope))
        except (ExprError, ValueError) as exc:
            raise ctx.fail(f"`of[{index}].key`: {getattr(exc, 'detail', exc)}") from None

    return str(ctx.cached(f"operand {index}", build))


def _operands(ctx: Any) -> List[float]:
    values = []
    for index, name in enumerate(operand_names(ctx.cfg)):
        value = ctx.operand(name, operand_key(ctx, index))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ctx.fail(f"'{name}' gave {value!r}, not a number, so it cannot be combined")
        values.append(float(value))
    return values


class ProductConfig(_Combined):
    kind: Literal["product"] = "product"
    scale: Number = Field(1.0, description="Multiplies the product (a base level).")


@kind("product", "composition", "composite", ProductConfig,
      "A product of patterns: a base level times every factor — decompose shows each factor's share; fit estimates "
      "the base and every factor together.",
      example={"kind": "product", "of": ["trend", {"pattern": "season", "key": "$row.category"}], "scale": "$row.base",
               "table": "$inputs.skus", "column": "sku"},
      params=("scale",), words=lambda cfg: f"{cfg.scale} × " + " × ".join(operand_names(cfg)))
def _product(ctx: Any) -> float:
    scale = ctx.number("scale")
    return product(_operands(ctx), start=scale)


class SumConfig(_Combined):
    kind: Literal["sum"] = "sum"
    weights: Optional[List[Number]] = Field(None, description="One weight per pattern (default 1 each).")
    base: Number = Field(0.0, description="Added to the sum.")

    @model_validator(mode="after")
    def _weights(self) -> "SumConfig":
        if self.weights is not None and len(self.weights) != len(self.of):
            raise ValueError(f"`weights` needs one weight per pattern in `of` ({len(self.of)})")
        return self


@kind("sum", "composition", "composite", SumConfig,
      "A weighted sum of patterns plus a base: level + seasonal swing + noise.",
      example={"kind": "sum", "of": ["normal_temp", "anomaly"], "base": 0},
      params=("weights", "base"), words=lambda cfg: " + ".join(operand_names(cfg)) + (f" + {cfg.base}" if cfg.base else ""))
def _sum(ctx: Any) -> float:
    weights = ctx.numbers("weights") if ctx.cfg.weights is not None else [1.0] * len(ctx.cfg.of)
    terms = [ctx.number("base"), *(w * v for w, v in zip(weights, _operands(ctx)))]
    if not all(math.isfinite(term) for term in terms):
        raise ctx.fail("weighted terms must be finite; rescale the weights or factors")
    try:
        # Include the base in the compensated sum; adding it afterwards can
        # erase a small residual between otherwise cancelling business drivers.
        return math.fsum(terms)
    except OverflowError:
        # fsum can overflow before later terms cancel. Exact binary fractions
        # are a rare fallback, preserving finite results regardless of order.
        try:
            return float(sum((Fraction(term) for term in terms), Fraction()))
        except OverflowError:
            raise ctx.fail("sum is outside the finite numeric range; rescale the base or factors") from None
