"""Per-entity continuous dynamics: every entity of a type integrates its own ODEs.

``physics.per.<type>`` compiles once into an :class:`EntityDynamicsStep`. Each step, every
matching entity builds one namespace — math functions and constants, world physics values,
the type's params, the entity's own number props and its reads — and advances its variables
(which are number props) with RK4 sub-steps plus an Euler–Maruyama noise term. Noise draws come
from a stream derived from the run seed, the type and the round, so adding dynamics to one type
never shifts any other random draw. New values are written through the journaled world API.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from ..entity import Entity
from ..physics import _CONSTS, _FUNCS, PhysicsExprError, _CompiledExpr
from .contract import EntityDynamics
from .errors import RunError
from .expr import ExprError, compile_expr, truthy
from .props import prop_type

if TYPE_CHECKING:
    from .world import SdkWorld

__all__ = ["EntityDynamicsStep", "MATH_NAMES", "compile_math"]

Bounds = Tuple[Optional[float], Optional[float]]

#: Names physics math always has — functions, constants and the time — which props cannot shadow.
MATH_NAMES: FrozenSet[str] = frozenset(_FUNCS) | frozenset(_CONSTS) | {"t"}


def compile_math(source: str, path: str) -> _CompiledExpr:
    try:
        return _CompiledExpr(source)
    except PhysicsExprError as exc:
        raise RunError(str(exc), path) from None


class EntityDynamicsStep:
    """One compiled ``physics.per.<type>`` entry."""

    def __init__(self, world: "SdkWorld", type_name: str, spec: EntityDynamics, params: Dict[str, float]):
        path = f"physics.per.{type_name}"
        self.type_name = type_name
        self.path = path
        self.params = params
        self.vars: List[str] = list(spec.vars)
        self.rates = [compile_math(var.rate, f"{path}.vars.{name}.rate") for name, var in spec.vars.items()]
        self.noise = [(index, compile_math(var.noise, f"{path}.vars.{name}.noise"))
                      for index, (name, var) in enumerate(spec.vars.items()) if var.noise is not None]
        self.where = compile_expr(spec.where) if spec.where else None
        self.reads = [(name, compile_expr(source)) for name, source in spec.read.items()]
        specs = world.contract.props_of(type_name)
        self.writes = [(prop, prop_type(specs[prop]), compile_math(source, f"{path}.write.{prop}"))
                       for prop, source in spec.write.items()]
        #: Number props the math may read by name (the variables are set from the state being integrated).
        self.inputs = [name for name, prop in specs.items()
                       if prop_type(prop) in ("number", "int") and name not in spec.vars and name not in MATH_NAMES]

    def step(self, world: "SdkWorld", shared: Dict[str, Any], dt: float, start: float, substeps: int) -> None:
        """Advance every matching living entity by ``dt`` from time ``start``."""
        members = world.entities_of(self.type_name)
        if not members or dt <= 0:
            return
        rng = world.seeds.rng("physics", "noise", self.type_name, world.round) if self.noise else None
        base = {**shared, **self.params}
        for entity in members:
            if self.where is not None and not truthy(self._eval(world, self.where, entity, f"{self.path}.where")):
                continue
            ns = dict(base)
            props = entity.properties
            for name in self.inputs:
                value: Any = props.get(name)
                if _finite(value):
                    ns[name] = float(value)
            for name, expr in self.reads:
                ns[name] = self._number(self._eval(world, expr, entity, f"{self.path}.read.{name}"), entity,
                                        f"{self.path}.read.{name}")
            state = [self._number(props.get(var), entity, f"{self.path}.vars.{var}") for var in self.vars]
            bounds = [_bounds(world, entity, var) for var in self.vars]
            try:
                state = self._integrate(ns, state, bounds, start, dt / substeps, substeps, rng)
                for var, value in zip(self.vars, state):
                    ns[var] = value
                    world.set_prop(entity, var, value)
                for prop, kind, formula in self.writes:
                    world.set_prop(entity, prop, _as_prop(formula.eval(ns), kind))
            except (ArithmeticError, ValueError) as exc:
                raise RunError(f"{entity.id}: the dynamics broke down numerically ({exc})", self.path) from None

    def _integrate(self, ns: Dict[str, Any], y: List[float], bounds: Sequence[Bounds], t: float, h: float,
                   substeps: int, rng: Any) -> List[float]:
        names, rates, n = self.vars, self.rates, len(self.vars)

        def slope(values: Sequence[float], time: float) -> List[float]:
            for k in range(n):
                ns[names[k]] = values[k]
            ns["t"] = time
            return [rate.eval(ns) for rate in rates]

        root = math.sqrt(h)
        for _ in range(substeps):
            k1 = slope(y, t)
            k2 = slope([y[k] + 0.5 * h * k1[k] for k in range(n)], t + 0.5 * h)
            k3 = slope([y[k] + 0.5 * h * k2[k] for k in range(n)], t + 0.5 * h)
            k4 = slope([y[k] + h * k3[k] for k in range(n)], t + h)
            nxt = [y[k] + (h / 6.0) * (k1[k] + 2 * k2[k] + 2 * k3[k] + k4[k]) for k in range(n)]
            if self.noise:
                for k in range(n):  # the noise term is evaluated at the sub-step's start
                    ns[names[k]] = y[k]
                ns["t"] = t
                for index, expr in self.noise:
                    nxt[index] += expr.eval(ns) * root * rng.gauss(0.0, 1.0)
            for k, (low, high) in enumerate(bounds):
                value = nxt[k]
                if not math.isfinite(value):
                    raise ArithmeticError(f"{names[k]} became non-finite")
                if low is not None and value < low:
                    value = low
                if high is not None and value > high:
                    value = high
                nxt[k] = value
            y = nxt
            t += h
        return y

    @staticmethod
    def _eval(world: "SdkWorld", expr: Any, entity: Entity, path: str) -> Any:
        try:
            return expr(world.scope(it=entity))
        except ExprError as exc:
            raise RunError(f"{entity.id}: {exc}", path) from None

    @staticmethod
    def _number(value: Any, entity: Entity, path: str) -> float:
        if not _finite(value):
            raise RunError(f"{entity.id}: must be a finite number, got {value!r}", path)
        return float(value)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _bounds(world: "SdkWorld", entity: Entity, prop: str) -> Bounds:
    spec = world.prop_spec(entity, prop)  # a subtype may narrow the bounds
    return spec.min, spec.max


def _as_prop(value: float, kind: str) -> Any:
    """A math result as the written prop's kind: comparisons give true/false to bool props."""
    if kind == "bool":
        return value != 0
    if kind == "int" and float(value).is_integer():
        return int(value)
    return value
