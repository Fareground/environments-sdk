"""Explicit numeric unit conversions; labels and unknown units never imply conversion."""
from __future__ import annotations

import datetime
import math

from ..contract.quantity import Quantity, convert
from ..expr import Call, function
from ._args import fail, number_arg


@function("convert(value, from_unit, to_unit)",
          "Convert numeric units explicitly, e.g. $convert(30, minute, hour). Units may be named or dimension/scale "
          "objects. Absolute temperatures and intervals are distinct; currency rates are never inferred.",
          min_args=3, max_args=3)
def convert_quantity(call: Call) -> float:
    try:
        return convert(number_arg(call, 0), Quantity.model_validate(call.arg(1)), Quantity.model_validate(call.arg(2)))
    except (ValueError, OverflowError) as exc:
        raise fail(call, str(exc)) from None


@function("quantity(value, unit)",
          "Declare the unit of a number already expressed in that unit; does not convert or validate its source. "
          "Use for dimensional constants; contract inputs/outputs can instead declare quantity metadata.",
          min_args=2, max_args=2)
def quantity(call: Call) -> float:
    try:
        Quantity.model_validate(call.arg(1))
    except ValueError as exc:
        raise fail(call, str(exc)) from None
    return number_arg(call, 0)


@function("convert_currency(value, from_currency, to_currency, rate, as_of, source)",
          "Multiply by an explicit positive target-per-source exchange rate, with ISO date and nonempty source "
          "reference. Records an authored conversion assumption; does not fetch or authenticate rates.",
          min_args=6, max_args=6)
def convert_currency(call: Call) -> float:
    try:
        units = [Quantity.model_validate(call.arg(i)) for i in (1, 2)]
        if any(len(unit.dimensions) != 1 or not next(iter(unit.dimensions)).startswith("currency:")
               or next(iter(unit.dimensions.values())) != 1 or unit.absolute for unit in units):
            raise ValueError("from_currency and to_currency must be currency units")
        date, source = call.arg(4), call.arg(5)
        if not isinstance(date, str) or datetime.date.fromisoformat(date).isoformat() != date:
            raise ValueError("as_of must be an ISO date YYYY-MM-DD")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("an explicit rate source reference is required")
        rate = number_arg(call, 3)
        if rate <= 0:
            raise ValueError("rate must be positive target-currency units per source-currency unit")
        result = number_arg(call, 0) * rate
        if not math.isfinite(result):
            raise ValueError("conversion did not produce a finite number")
        return result
    except (ValueError, OverflowError) as exc:
        raise fail(call, str(exc)) from None
