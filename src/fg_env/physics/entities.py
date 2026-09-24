"""Per-entity continuous dynamics: every entity of a type integrates its own ODEs.

``physics.per.<type>`` compiles once into an :class:`EntityDynamicsStep`. Each step, every matching entity builds one
namespace — math functions and constants, world physics values, the type's params, the entity's own number props and its
reads — and advances its variables (which are number props) with RK4 sub-steps plus an Euler–Maruyama noise term. Noise
draws come from streams derived from the run seed, type, entity, variable and round, so unrelated entities or variables
never shift an existing variable's random draws. New values are written through the journaled world API.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..contract import EntityDynamics
from ..errors import RunError
from ..expr import ExprError, compile_expr, truthy
from ..world.entity import Entity
from ..world.props import prop_type
from .integration import integrate
from .model import _CONSTS, _FUNCS, PhysicsExprError, _CompiledExpr
from .stochastic import exact_transition
from .stochastic_integration import integrate_noise

if TYPE_CHECKING:
    from ..world.live import SdkWorld

__all__ = ["EntityDynamicsStep", "MATH_NAMES", "compile_math"]

Bounds = tuple[float | None, float | None]

#: Names physics math always has — functions, constants and the time — which props cannot shadow.
MATH_NAMES: frozenset[str] = frozenset(_FUNCS) | frozenset(_CONSTS) | {"t"}


def compile_math(source: str, path: str) -> _CompiledExpr:
    try:
        return _CompiledExpr(source)
    except PhysicsExprError as exc:
        raise RunError(str(exc), path) from None


class EntityDynamicsStep:
    """One compiled ``physics.per.<type>`` entry."""

    def __init__(self, world: SdkWorld, type_name: str, spec: EntityDynamics, params: dict[str, float]):
        path = f"physics.per.{type_name}"
        self.type_name = type_name
        self.path = path
        self.params = params
        self.vars: list[str] = list(spec.vars)
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

    def step(self, world: SdkWorld, shared: dict[str, Any], dt: float, start: float, substeps: int) -> None:
        """Advance every matching living entity by ``dt`` from time ``start``."""
        members = world.entities_of(self.type_name)
        if not members or dt <= 0:
            return
        base = {**shared, **self.params}
        for entity in members:
            if self.where is not None and not truthy(self._eval(world, self.where, entity, f"{self.path}.where")):
                continue
            rng = {index: world.seeds.rng("physics", "noise", self.type_name, entity.id,
                                           self.vars[index], world.round)
                   for index, _ in self.noise}
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
                spec = world.contract.physics
                assert spec is not None
                state = self._integrate(ns, state, bounds, start, dt / substeps, substeps, rng, spec.rtol, spec.atol,
                                        spec.noise_rtol)
                for var, value in zip(self.vars, state):
                    ns[var] = value
                    world.set_prop(entity, var, value)
                for prop, kind, formula in self.writes:
                    world.set_prop(entity, prop, _as_prop(formula.eval(ns), kind))
            except (ArithmeticError, ValueError) as exc:
                raise RunError(f"{entity.id}: the dynamics broke down numerically ({exc})", self.path) from None

    def _integrate(self, ns: dict[str, Any], y: list[float], bounds: Sequence[Bounds], t: float, h: float,
                   substeps: int, rng: Any, rtol: float, atol: float, noise_rtol: float) -> list[float]:
        names, rates, n = self.vars, self.rates, len(self.vars)

        def slope(values: Sequence[float], time: float) -> list[float]:
            for k in range(n):
                ns[names[k]] = values[k]
            ns["t"] = time
            return [rate.eval(ns) for rate in rates]

        independent = not self.reads and all(
            not (expr._names & (set(names) - {names[index]} | {"t"}))
            for index, expr in enumerate(rates))
        for _ in range(substeps):
            for k in range(n):
                ns[names[k]] = y[k]
            ns["t"] = t
            exact = independent and bool(self.noise) and all(
                not (expr._names & (set(names)-{names[index]} | {"t"}))
                and exact_transition(rates[index], expr, names[index], ns, y[index], h, 0) is not None
                for index, expr in self.noise)
            if exact:
                nxt = list(y) if len(self.noise) == n else integrate(slope, y, t, h, substeps=1, rtol=rtol, atol=atol)
                for k in range(n):
                    ns[names[k]] = y[k]
                ns["t"] = t
                for index, expr in self.noise:
                    value = exact_transition(rates[index], expr, names[index], ns, y[index], h, rng[index].gauss(0, 1))
                    assert value is not None
                    nxt[index] = value
            elif self.noise:
                def coefficients(values: list[float], time: float) -> tuple[list[float], list[float]]:
                    drift = slope(values, time)
                    diffusion = [0.0] * n
                    for index, expr in self.noise:
                        diffusion[index] = expr.eval(ns)
                    return drift, diffusion

                nxt = integrate_noise(coefficients, y, t, h, rng, bounds, noise_rtol, atol)
            else:
                nxt = integrate(slope, y, t, h, substeps=1, rtol=rtol, atol=atol)
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
    def _eval(world: SdkWorld, expr: Any, entity: Entity, path: str) -> Any:
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


def _bounds(world: SdkWorld, entity: Entity, prop: str) -> Bounds:
    spec = world.prop_spec(entity, prop)  # a subtype may narrow the bounds
    return spec.min, spec.max


def _as_prop(value: float, kind: str) -> Any:
    """A math result as the written prop's kind: comparisons give true/false to bool props."""
    if kind == "bool":
        return value != 0
    if kind == "int" and float(value).is_integer():
        return int(value)
    return value
