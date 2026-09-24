"""Decision spaces: which inputs an optimiser may set, and the values each may take.

A decision is a declared input with a domain (``decisions={name: spec}``):

* a range — ``{"low": 0, "high": 40, "step": 1}``; ``int`` inputs step by 1 unless told otherwise, a ``number`` input
  without ``step`` is continuous; ``low``/``high`` default to the input's declared ``min``/``max``;
* choices — ``["fifo", "priority"]`` or ``{"values": [...]}`` (``bool`` and ``enum`` inputs offer theirs by default);
* a vector — a ``list`` input (``"length": 24``) or a ``map`` input (``"keys": ["brakes", "wipers"]``), one value per
  position or key, each within ``low``/``high`` (one number, or one per position or key) and on ``step``; optional
  structure: ``"monotone": "increasing" | "decreasing"`` or ``"sum": total | {"min", "max"}``.

``"start"`` is where a local search begins (default: the input's default when it lies in the domain, else the middle).

Internally a candidate decision is a point: one coordinate per scalar, per vector position and per choice (the index of
the value). :meth:`DecisionSpace.snap` puts any point on the grid, inside the bounds and into the structure, so every
search works on valid decisions only.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import runner
from .stats import is_number

__all__ = ["Axis", "Decision", "DecisionSpace", "parse_decisions", "Point"]

Point = tuple[float, ...]

#: Levels a continuous range is cut into for a grid.
GRID_LEVELS = 5
#: A continuous coordinate's local-search step starts at this share of its range …
_FIRST_STEP_SHARE = 0.25
#: … and halves until it is below this share.
_LAST_STEP_SHARE = 1 / 64
#: Sensitivity moves a continuous decision by this share of its range.
_SENSITIVITY_SHARE = 0.05
#: Rounding that keeps stepped values free of float drift (0.1 + 0.2).
_DIGITS = 10
_KEYS = {"low", "high", "step", "values", "length", "keys", "monotone", "sum", "start"}


@dataclass(frozen=True)
class Axis:
    """One coordinate: a numeric range (``step`` ``None`` = continuous) or the indices of ``values``."""

    low: float
    high: float
    step: float | None = None
    values: tuple[Any, ...] | None = None

    @property
    def choice(self) -> bool:
        return self.values is not None

    def snap(self, x: float) -> float:
        if self.values is not None:
            return float(min(len(self.values) - 1, max(0, int(round(x)))))
        x = min(self.high, max(self.low, float(x)))
        if self.step is None:
            return x
        k = min(round((x - self.low) / self.step), math.floor((self.high - self.low) / self.step + 1e-9))
        return round(self.low + k * self.step, _DIGITS)

    def levels(self) -> list[float]:
        if self.values is not None:
            return [float(i) for i in range(len(self.values))]
        if self.step is None:
            return [self.low + (self.high - self.low) * i / (GRID_LEVELS - 1) for i in range(GRID_LEVELS)]
        return [round(self.low + k * self.step, _DIGITS)
                for k in range(math.floor((self.high - self.low) / self.step + 1e-9) + 1)]

    def from_unit(self, u: float) -> float:
        if self.values is not None:
            return float(min(len(self.values) - 1, int(u * len(self.values))))
        return self.snap(self.low + u * (self.high - self.low))

    def to_unit(self, x: float) -> float:
        if self.values is not None:
            return (x + 0.5) / len(self.values)
        return 0.0 if self.high == self.low else (x - self.low) / (self.high - self.low)

    def first_step(self) -> float:
        if self.step is None:
            return (self.high - self.low) * _FIRST_STEP_SHARE
        return max(self.step, self.step * math.floor((self.high - self.low) * _FIRST_STEP_SHARE / self.step))

    def last_step(self) -> float:
        return (self.high - self.low) * _LAST_STEP_SHARE if self.step is None else self.step

    def halve(self, step: float) -> float:
        if self.step is None:
            return step / 2
        return max(self.step, self.step * math.floor(step / 2 / self.step))

    def nudge(self) -> float:
        """The move sensitivity makes: one step, or a small share of a continuous range."""
        return self.step if self.step is not None else (self.high - self.low) * _SENSITIVITY_SHARE


@dataclass(frozen=True)
class Decision:
    name: str
    kind: str  # scalar | choice | list | map
    axes: tuple[Axis, ...]
    keys: tuple[str, ...] = ()
    integer: bool = False
    monotone: str | None = None
    total: tuple[float | None, float | None] | None = None
    start: tuple[float, ...] = ()

    @property
    def vector(self) -> bool:
        return self.kind in ("list", "map")

    def value(self, coords: Sequence[float]) -> Any:
        if self.kind == "choice":
            assert self.axes[0].values is not None
            return self.axes[0].values[int(coords[0])]
        numbers = [int(round(x)) if self.integer else float(x) for x in coords]
        if self.kind == "scalar":
            return numbers[0]
        return dict(zip(self.keys, numbers)) if self.kind == "map" else numbers

    def coords(self, value: Any) -> tuple[float, ...] | None:
        """``value`` as coordinates, or ``None`` when it is not in the domain's shape."""
        if self.kind == "choice":
            values = self.axes[0].values or ()
            return (float(values.index(value)),) if value in values else None
        if self.kind == "scalar":
            return (float(value),) if is_number(value) else None
        if self.kind == "map":
            if not isinstance(value, Mapping) or set(value) != set(self.keys):
                return None
            items = [value[k] for k in self.keys]
        else:
            if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != len(self.axes):
                return None
            items = list(value)
        return tuple(float(v) for v in items) if all(is_number(v) for v in items) else None

    def repair(self, coords: list[float]) -> list[float]:
        """Coordinates on the grid, inside the bounds and in the structure (monotone order, sum)."""
        out = [axis.snap(x) for axis, x in zip(self.axes, coords)]
        if self.monotone:
            out = [axis.snap(x) for axis, x in zip(self.axes, sorted(out, reverse=self.monotone == "decreasing"))]
        if self.total is not None:
            out = _into_total(self.axes, out, self.total)
        return out


def _into_total(axes: Sequence[Axis], values: list[float], total: tuple[float | None, float | None]) -> list[float]:
    """Move values toward the sum's range: step by step on the coordinates with the most room (continuous: in one go).
    """
    low, high = total
    out = list(values)
    for _ in range(sum(len(a.levels()) if a.step is not None else 1 for a in axes)):
        s = math.fsum(out)
        gap = ((low - s) if low is not None and s < low - 1e-9 else (high - s) if high is not None and s > high + 1e-9
               else 0.0)
        if gap == 0.0:
            break
        room = [(a.high - x) if gap > 0 else (x - a.low) for a, x in zip(axes, out)]
        i = max(range(len(out)), key=lambda j: room[j])
        if room[i] <= 1e-12:
            break
        step = axes[i].step
        move = min(abs(gap), room[i]) if step is None else step
        if step is not None and move > room[i] + 1e-9:
            break
        out[i] = axes[i].snap(out[i] + math.copysign(move, gap))
    return out


@dataclass(frozen=True)
class DecisionSpace:
    decisions: tuple[Decision, ...]

    @property
    def axes(self) -> list[Axis]:
        return [axis for d in self.decisions for axis in d.axes]

    @property
    def dims(self) -> int:
        return len(self.axes)

    @property
    def names(self) -> list[str]:
        return [d.name for d in self.decisions]

    def _spans(self) -> list[tuple[Decision, int, int]]:
        spans, at = [], 0
        for d in self.decisions:
            spans.append((d, at, at + len(d.axes)))
            at += len(d.axes)
        return spans

    def snap(self, point: Sequence[float]) -> Point:
        out: list[float] = []
        for d, a, b in self._spans():
            out += d.repair(list(point[a:b]))
        return tuple(out)

    def decode(self, point: Point) -> dict[str, Any]:
        return {d.name: d.value(point[a:b]) for d, a, b in self._spans()}

    def encode(self, values: Mapping[str, Any]) -> Point:
        coords: list[float] = []
        for d in self.decisions:
            c = d.coords(values[d.name])
            if c is None:
                raise ValueError(f"decision '{d.name}': {values[d.name]!r} is not one of its values")
            coords += c
        return self.snap(coords)

    def start(self) -> Point:
        return self.snap([x for d in self.decisions for x in d.start])

    def grid_size(self) -> int:
        return math.prod(len(axis.levels()) for axis in self.axes)

    def grid(self) -> list[Point]:
        seen: dict[Point, None] = {}
        for combo in itertools.product(*(axis.levels() for axis in self.axes)):
            seen.setdefault(self.snap(combo), None)
        return list(seen)

    def from_unit(self, unit: Sequence[float]) -> Point:
        return self.snap([axis.from_unit(u) for axis, u in zip(self.axes, unit)])

    def to_unit(self, point: Point) -> list[float]:
        return [axis.to_unit(x) for axis, x in zip(self.axes, point)]

    def moves(self, point: Point, i: int, step: float) -> list[Point]:
        """Neighbours of ``point`` along coordinate ``i``: every other choice, ±``step``, or — for a vector whose sum
        is fixed — ``step`` moved between ``i`` and each other position of that vector."""
        axis = self.axes[i]
        if axis.values is not None:
            raw = [_with(point, {i: float(k)}) for k in range(len(axis.values)) if k != int(point[i])]
        else:
            owner, a, b = next(span for span in self._spans() if span[1] <= i < span[2])
            fixed = owner.total is not None and owner.total[0] is not None and owner.total[0] == owner.total[1]
            if fixed:
                raw = [_with(point, {i: point[i] + sign * step, j: point[j] - sign * step})
                       for j in range(a, b) if j != i for sign in (1, -1)]
            else:
                raw = [_with(point, {i: point[i] + step}), _with(point, {i: point[i] - step})]
        return self._distinct(point, raw)

    def pair_moves(self, point: Point, i: int, steps: Sequence[float]) -> list[Point]:
        """``point`` with coordinate ``i`` a step up and each later numeric coordinate a step down, or the reverse:
        the shifts single moves cannot make along a constraint (an agent moved from one half hour to another)."""
        if self.axes[i].choice:
            return []
        return self._distinct(point, [_with(point, {i: point[i] + sign * steps[i], j: point[j] - sign * steps[j]})
                                      for j in range(i + 1, self.dims) if not self.axes[j].choice for sign in (1, -1)])

    def block_moves(self, point: Point, i: int, steps: Sequence[float]) -> list[Point]:
        """``point`` with a block of a vector's positions from ``i`` moved a step up or down together: 2, 4, 8…
        positions, and the run of equal values ``i`` starts. A monotone profile cannot move one position past its
        neighbour, and a smooth one improves by shifting a stretch, not a point."""
        owner, a, b = self._span_of(i)
        if not owner.vector:
            return []
        ends = set()
        width = 2
        while i + width <= b:
            ends.add(i + width)
            width *= 2
        if i == a or point[i - 1] != point[i]:
            run = i + 1
            while run < b and point[run] == point[i]:
                run += 1
            if run > i + 1:
                ends.add(run)
        return self._distinct(point, [_with(point, {j: point[j] + sign * steps[j] for j in range(i, end)})
                                      for end in sorted(ends) for sign in (1, -1)])

    def smoothing_moves(self, point: Point, i: int, steps: Sequence[float]) -> list[Point]:
        """``point`` with an inner position of a vector set to the middle of its two neighbours (onto its step from
        below and from above): the move that irons a lone spike or dip out of a near-monotone or smooth profile."""
        owner, a, b = self._span_of(i)
        if not owner.vector or not a < i < b - 1:
            return []
        middle, axis = (point[i - 1] + point[i + 1]) / 2, self.axes[i]
        if axis.step is None:
            return self._distinct(point, [_with(point, {i: middle})])
        below = axis.low + math.floor((middle - axis.low) / axis.step + 1e-9) * axis.step
        return self._distinct(point, [_with(point, {i: below}), _with(point, {i: below + axis.step})])

    def _span_of(self, i: int) -> tuple[Decision, int, int]:
        return next(span for span in self._spans() if span[1] <= i < span[2])

    def _distinct(self, point: Point, raw: Sequence[Sequence[float]]) -> list[Point]:
        """``raw`` snapped into the space, without repeats or ``point`` itself."""
        seen: dict[Point, None] = {}
        for candidate in raw:
            snapped = self.snap(candidate)
            if snapped != point:
                seen.setdefault(snapped, None)
        return list(seen)

    def shifted(self, point: Point, name: str, direction: int) -> Point | None:
        """``point`` with one decision moved a step up or down as a whole (``None`` when that changes nothing)."""
        changes = {}
        for d, a, b in self._spans():
            if d.name == name:
                for i in range(a, b):
                    changes[i] = point[i] + direction * (1.0 if d.axes[i - a].choice else d.axes[i - a].nudge())
        moved = self.snap(_with(point, changes))
        return None if moved == point else moved

    def at_edges(self, point: Point) -> dict[str, list[str]]:
        """Numeric decisions with a value on a bound of its range: the positions or keys there (empty for a scalar)."""
        out: dict[str, list[str]] = {}
        for d, a, b in self._spans():
            if d.kind == "choice":
                continue
            labels = list(d.keys) or [str(i) for i in range(len(d.axes))]
            hits = [label for label, axis, x in zip(labels, d.axes, point[a:b])
                    if x in (axis.snap(axis.low), axis.snap(axis.high))]
            if hits:
                out[d.name] = hits if d.vector else []
        return out

    def describe(self, point: Point) -> str:
        return runner.describe_inputs(self.decode(point))


def _with(point: Sequence[float], changes: Mapping[int, float]) -> list[float]:
    return [changes.get(i, x) for i, x in enumerate(point)]


def parse_decisions(contract: Any, decisions: Mapping[str, Any]) -> DecisionSpace:
    """Check every decision against the contract's inputs and build the space (see the module notes)."""
    if not isinstance(decisions, Mapping) or not decisions:
        raise ValueError("optimise needs decisions: {input: {low, high, step?} | [values] | {length|keys, low, high}}")
    return DecisionSpace(tuple(_decision(contract, name, spec) for name, spec in decisions.items()))


def _decision(contract: Any, name: str, spec: Any) -> Decision:
    input_spec = runner.input_spec(contract, name)
    if isinstance(spec, Sequence) and not isinstance(spec, (str, bytes)):
        spec = {"values": list(spec)}
    if not isinstance(spec, Mapping):
        raise ValueError(f"decision '{name}': give {{low, high, step?}}, a list of values, or {{values: [...]}}; "
                         f"got {spec!r}")
    unknown = sorted(set(spec) - _KEYS)
    if unknown:
        raise ValueError(f"decision '{name}': unknown key(s) {', '.join(unknown)} (use {', '.join(sorted(_KEYS))})")
    if "values" in spec or input_spec.type in ("bool", "enum"):
        return _choice(name, spec, input_spec)
    if input_spec.type in ("list", "map"):
        return _vector(name, spec, input_spec)
    if input_spec.type not in ("number", "int"):
        raise ValueError(f"decision '{name}': a {input_spec.type} input can only be decided from a list of values")
    _only(name, spec, {"low", "high", "step", "start"}, "a number or int input")
    low, high = runner.bounds(contract, name, spec)
    _within_declared(name, input_spec, low, high)
    step = _step(name, spec.get("step", 1 if input_spec.type == "int" else None))
    if input_spec.type == "int" and not float(step or 1).is_integer():
        raise ValueError(f"decision '{name}': an int input needs a whole-number step, got {step}")
    axis = Axis(low, high, step)
    start = _scalar_start(name, spec.get("start", input_spec.default), axis, "start" in spec)
    return Decision(name, "scalar", (axis,), integer=input_spec.type == "int", start=(start,))


def _choice(name: str, spec: Mapping[str, Any], input_spec: Any) -> Decision:
    _only(name, spec, {"values", "start"}, "a choice")
    if "values" in spec:
        values = spec["values"]
    elif input_spec.type == "bool":
        values = [False, True]
    else:
        values = list(input_spec.values or [])
    if not isinstance(values, Sequence) or isinstance(values, str) or len(values) < 2:
        raise ValueError(f"decision '{name}': choices need at least two values, got {values!r}")
    allowed = input_spec.values if input_spec.type == "enum" and input_spec.values else None
    stray = [v for v in values if allowed is not None and v not in allowed]
    if stray:
        raise ValueError(f"decision '{name}': {stray} not among the input's values {allowed}")
    start = spec.get("start", input_spec.default)
    if "start" in spec and start not in values:
        raise ValueError(f"decision '{name}': start {start!r} is not one of {list(values)}")
    index = list(values).index(start) if start in values else 0
    return Decision(name, "choice", (Axis(0.0, float(len(values) - 1), 1.0, tuple(values)),), start=(float(index),))


def _vector(name: str, spec: Mapping[str, Any], input_spec: Any) -> Decision:
    default = input_spec.default
    if input_spec.type == "map":
        _only(name, spec, _KEYS - {"values", "length"}, "a map input")
        keys = spec.get("keys") or (list(default) if isinstance(default, Mapping) else [])
        if not keys or not all(isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
            raise ValueError(f"decision '{name}': a map decision needs 'keys', a list of distinct names")
        size, labels = len(keys), [str(k) for k in keys]
    else:
        _only(name, spec, _KEYS - {"values", "keys"}, "a list input")
        size = spec.get("length") or (len(default) if isinstance(default, list) else 0)
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError(f"decision '{name}': a list decision needs 'length', the number of values")
        labels = [str(i) for i in range(size)]
    lows = _per_position(name, "low", spec.get("low", input_spec.min), labels)
    highs = _per_position(name, "high", spec.get("high", input_spec.max), labels)
    step = _step(name, spec.get("step"))
    axes = []
    for label, low, high in zip(labels, lows, highs):
        if not low < high:
            raise ValueError(f"decision '{name}' at {label}: low must be below high, got {low} and {high}")
        axes.append(Axis(low, high, step))
    integer = step is not None and float(step).is_integer() and all(float(x).is_integer() for x in lows + highs)
    monotone, total = _structure(name, spec, axes)
    kind = input_spec.type
    probe = Decision(name, kind, tuple(axes), tuple(labels) if kind == "map" else (), integer, monotone, total)
    start = _vector_start(probe, spec, default)
    return Decision(name, kind, tuple(axes), probe.keys, integer, monotone, total, start)


def _structure(name: str, spec: Mapping[str, Any], axes: Sequence[Axis]
               ) -> tuple[str | None, tuple[float | None, float | None] | None]:
    monotone = spec.get("monotone")
    if monotone not in (None, "increasing", "decreasing"):
        raise ValueError(f"decision '{name}': monotone must be 'increasing' or 'decreasing', got {monotone!r}")
    if monotone:
        sign = 1 if monotone == "increasing" else -1
        bounds_follow = all(sign * (b.low - a.low) >= 0 and sign * (b.high - a.high) >= 0
                            for a, b in zip(axes, axes[1:]))
        if not bounds_follow:
            raise ValueError(f"decision '{name}': per-position low and high must themselves be {monotone} "
                             "for a monotone decision")
    total = spec.get("sum")
    if total is None:
        return monotone, None
    if monotone:
        raise ValueError(f"decision '{name}': monotone and sum cannot be combined; keep one")
    low, high = (total, total) if is_number(total) else (
        (total.get("min"), total.get("max")) if isinstance(total, Mapping) else (None, None))
    if (low is None and high is None) or any(v is not None and not is_number(v) for v in (low, high)):
        raise ValueError(f"decision '{name}': sum must be a number or {{min, max}}, got {total!r}")
    floor_sum, ceiling_sum = math.fsum(a.low for a in axes), math.fsum(a.high for a in axes)
    if (low is not None and low > ceiling_sum) or (high is not None and high < floor_sum):
        raise ValueError(f"decision '{name}': the sum can only range from {floor_sum:g} to {ceiling_sum:g} "
                         f"within the bounds, so {total!r} cannot be met")
    return None, (None if low is None else float(low), None if high is None else float(high))


def _vector_start(probe: Decision, spec: Mapping[str, Any], default: Any) -> tuple[float, ...]:
    given = spec.get("start")
    coords = probe.coords(given) if given is not None else None
    if given is not None and coords is None:
        shape = f"keys {list(probe.keys)}" if probe.kind == "map" else f"{len(probe.axes)} numbers"
        raise ValueError(f"decision '{probe.name}': start must have {shape}, got {given!r}")
    if coords is None:
        coords = probe.coords(default)
    if coords is None or any(not a.low <= x <= a.high for a, x in zip(probe.axes, coords)):
        coords = tuple((a.low + a.high) / 2 for a in probe.axes)
    return tuple(probe.repair(list(coords)))


def _scalar_start(name: str, value: Any, axis: Axis, given: bool) -> float:
    inside = is_number(value) and axis.low <= float(value) <= axis.high
    if given and not inside:
        raise ValueError(f"decision '{name}': start {value!r} is outside {axis.low:g}..{axis.high:g}")
    return axis.snap(float(value) if inside else (axis.low + axis.high) / 2)


def _per_position(name: str, key: str, value: Any, labels: Sequence[str]) -> list[float]:
    if value is None:
        raise ValueError(f"decision '{name}': give '{key}' (one number, or one per position or key); the input "
                         f"declares no {'min' if key == 'low' else 'max'}")
    if isinstance(value, Mapping):
        missing = [label for label in labels if label not in value]
        if missing:
            raise ValueError(f"decision '{name}': {key} has no value for {', '.join(missing)}")
        value = [value[label] for label in labels]
    if isinstance(value, Sequence) and not isinstance(value, str):
        if len(value) != len(labels) or not all(is_number(v) for v in value):
            raise ValueError(f"decision '{name}': {key} must hold {len(labels)} numbers, got {value!r}")
        return [float(v) for v in value]
    if not is_number(value):
        raise ValueError(f"decision '{name}': {key} must be a number, got {value!r}")
    return [float(value)] * len(labels)


def _step(name: str, step: Any) -> float | None:
    if step is None:
        return None
    if not is_number(step) or step <= 0:
        raise ValueError(f"decision '{name}': step must be a positive number, got {step!r}")
    return float(step)


def _only(name: str, spec: Mapping[str, Any], allowed: set, what: str) -> None:
    extra = sorted(set(spec) - allowed)
    if extra:
        raise ValueError(f"decision '{name}': {', '.join(extra)} does not apply to {what} "
                         f"(it takes {', '.join(sorted(allowed))})")


def _within_declared(name: str, input_spec: Any, low: float, high: float) -> None:
    if input_spec.min is not None and low < input_spec.min or input_spec.max is not None and high > input_spec.max:
        raise ValueError(f"decision '{name}': {low:g}..{high:g} leaves the input's declared range "
                         f"{input_spec.min}..{input_spec.max}")
