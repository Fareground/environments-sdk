"""Explicit quantity dimensions and scales. Display-unit labels never change numeric values."""
from __future__ import annotations

import math
import re
from typing import Any

from pydantic import Field, GetJsonSchemaHandler, StrictBool, StrictInt, model_validator
from pydantic_core import CoreSchema

from .base import _Model


class Quantity(_Model):
    """One numeric unit: base value = value * scale + offset; absolute quantities have an origin."""

    dimensions: dict[str, StrictInt] = Field(default_factory=dict)
    scale: float = Field(1, gt=0, allow_inf_nan=False)
    offset: float = Field(0, allow_inf_nan=False)
    absolute: StrictBool = False

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> dict[str, Any]:
        structured = handler.resolve_ref_schema(handler(core_schema))
        return {"anyOf": [structured, {"type": "string", "minLength": 1, "maxLength": 200,
                                      "description": "Known unit or compound unit, e.g. minute, item/hour, m^3."}]}

    @model_validator(mode="before")
    @classmethod
    def _named(cls, data: Any) -> Any:
        return named_unit(data) if isinstance(data, str) else data

    @model_validator(mode="after")
    def _bounded(self) -> Quantity:
        if len(self.dimensions) > 16 or any(not key or len(key) > 80 or abs(value) > 12
                                          for key, value in self.dimensions.items()):
            raise ValueError("quantity dimensions require at most 16 names and powers between -12 and 12")
        self.dimensions = {key: value for key, value in self.dimensions.items() if value}
        if self.offset and not self.absolute:
            raise ValueError("an offset requires an absolute quantity")
        return self

    def same(self, other: Quantity) -> bool:
        return (self.dimensions == other.dimensions and self.absolute == other.absolute
                and math.isclose(self.scale, other.scale, rel_tol=1e-12)
                and math.isclose(self.offset, other.offset, rel_tol=1e-12, abs_tol=1e-12))

    def product(self, other: Quantity, power: int = 1) -> Quantity:
        if self.offset or other.offset:
            raise ValueError("offset units cannot be multiplied or divided; "
                             "convert to a zero-origin unit such as kelvin")
        dimensions = dict(self.dimensions)
        for key, value in other.dimensions.items():
            dimensions[key] = dimensions.get(key, 0) + value * power
        return Quantity(dimensions=dimensions, scale=self.scale * other.scale ** power)


def _unit(dimensions: dict[str, int], scale: float = 1, offset: float = 0, absolute: bool = False) -> dict:
    return dict(dimensions=dimensions, scale=scale, offset=offset, absolute=absolute)


_UNITS: dict[str, dict] = {}
for _names, _definition in [
    (("1", "fraction"), _unit({})), (("%", "percent"), _unit({}, .01)),
    (("s", "sec", "second", "seconds"), _unit({"time": 1})),
    (("min", "minute", "minutes"), _unit({"time": 1}, 60)),
    (("h", "hour", "hours"), _unit({"time": 1}, 3600)), (("day", "days"), _unit({"time": 1}, 86400)),
    (("m", "meter", "metre"), _unit({"length": 1})), (("km",), _unit({"length": 1}, 1000)),
    (("cm",), _unit({"length": 1}, .01)), (("mm",), _unit({"length": 1}, .001)),
    (("m3",), _unit({"length": 3})), (("L", "liter", "litre"), _unit({"length": 3}, .001)),
    (("mL",), _unit({"length": 3}, .000001)),
    (("kg",), _unit({"mass": 1})), (("g",), _unit({"mass": 1}, .001)),
    (("item", "items"), _unit({"item": 1})),
    (("K", "kelvin"), _unit({"temperature": 1}, absolute=True)),
    (("degC", "celsius"), _unit({"temperature": 1}, offset=273.15, absolute=True)),
    (("degF", "fahrenheit"), _unit({"temperature": 1}, 5 / 9, 459.67 * 5 / 9, True)),
    (("delta_K", "delta_degC"), _unit({"temperature": 1})),
    (("delta_degF",), _unit({"temperature": 1}, 5 / 9)),
]:
    for _name in _names:
        _UNITS[_name] = _definition


def named_unit(name: str) -> dict:
    """A small explicit registry with products, divisions and integer powers; no inferred custom units or rates."""
    if len(name) > 200:
        raise ValueError("unit name is too long")
    name = name.strip()
    if name in _UNITS:
        return {**_UNITS[name], "dimensions": dict(_UNITS[name]["dimensions"])}
    if name in {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"}:
        return _unit({f"currency:{name}": 1})
    parts = re.split(r"([*/])", name)
    if len(parts) == 1 and not re.fullmatch(r"[^* /^]+\^-?\d+", name):
        raise ValueError(f"unknown unit {name!r}; provide explicit dimensions/scale, "
                         "or leave it an unchecked display label")
    result = Quantity()
    for i in range(0, len(parts), 2):
        match = re.fullmatch(r"([^* /^]+)(?:\^(-?\d+))?", parts[i].strip())
        if not match:
            raise ValueError("use unit products/divisions and integer powers, such as item/hour or m^3")
        power = int(match[2] or 1) * (-1 if i and parts[i - 1] == "/" else 1)
        if abs(power) > 12:
            raise ValueError("unit powers must be between -12 and 12")
        unit = Quantity.model_validate(match[1])
        if unit.offset:
            raise ValueError("offset units cannot occur in compound units; use kelvin or temperature intervals")
        result = result.product(Quantity(dimensions={k: v * power for k, v in unit.dimensions.items()},
                                         scale=unit.scale ** power))
    return result.model_dump()


def convert(value: float, source: Quantity, target: Quantity) -> float:
    """Explicit same-dimension conversion. Cross-currency rates are never guessed."""
    if source.dimensions != target.dimensions or source.absolute != target.absolute:
        raise ValueError("conversion requires matching dimensions and absolute/interval kinds; "
                         "cross-currency conversion requires an explicit dated rate and source")
    result = (value * source.scale + source.offset - target.offset) / target.scale
    if not math.isfinite(result):
        raise ValueError("conversion did not produce a finite number")
    return result
