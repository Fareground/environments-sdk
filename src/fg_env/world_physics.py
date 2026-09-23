"""Atomic evolution of shared world and entity quantities between decisions."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .physics import _CONSTS, _FUNCS, PhysicsExprError, PhysicsModel, PhysicsVariable, _CompiledExpr
from .entity_physics import EntityDynamicsStep
from .errors import RunError
from .expr import ExprError, compile_expr, is_expr
from .props import finite_number, shown_value

if TYPE_CHECKING:
    from .world import SdkWorld

__all__ = ["build_physics", "step_physics"]


def build_physics(world: "SdkWorld") -> None:
    spec = world.contract.physics
    if spec is None:
        return
    scope = world.scope()
    params: Dict[str, float] = {}
    for name, raw in spec.params.items():
        params[name] = _constant(world, raw, f"physics.params.{name}")
    for name in spec.read:
        if name in spec.vars or name in spec.params:
            raise RunError(f"'{name}' is both a read name and a variable or param; give the read its own name", f"physics.read.{name}")
        params.setdefault(name, 0.0)
    variables = []
    for name, var in spec.vars.items():
        start = compile_expr(var.start)(scope) if is_expr(var.start) else var.start
        variables.append(PhysicsVariable(name=name, value=_number(start, f"physics.vars.{name}.start"),
                                         rate=var.rate, noise=var.noise, min=var.min, max=var.max))
    try:
        world.physics = PhysicsModel(variables=variables, params=params, substeps=spec.substeps)
        world.physics_writes = [(target, _CompiledExpr(src)) for target, src in spec.write.items()]
    except PhysicsExprError as exc:
        raise RunError(str(exc), "physics") from None
    world.entity_dynamics = []
    for type_name, dynamics in spec.per.items():
        constants = {name: _constant(world, raw, f"physics.per.{type_name}.params.{name}")
                     for name, raw in dynamics.params.items()}
        world.entity_dynamics.append(EntityDynamicsStep(world, type_name, dynamics, constants))
    _refresh_reads(world)


def step_physics(world: "SdkWorld", elapsed: Optional[float] = None) -> List[Dict[str, Any]]:
    """Advance a complete physical interval atomically, including all writebacks."""
    spec, model = world.contract.physics, world.physics
    if spec is None or model is None:
        return []
    dt = spec.dt if elapsed is None else spec.dt * elapsed
    if dt <= 0:
        return []
    mark = world.journal.mark()
    before, params, start = dict(model.values), dict(model.params), model.time
    clock_time, rng_state = world.time, world.rng.getstate()
    try:
        changes = advance_equations(world, dt)
        world.touch()
        return changes
    except BaseException as exc:
        world.journal.rollback(mark)
        for name, value in before.items():
            model.variables[name].value = value
        model.params.clear()
        model.params.update(params)
        model.time, world.time = start, clock_time
        world.rng.setstate(rng_state)
        world.touch()
        if isinstance(exc, (ArithmeticError, ValueError)):
            raise RunError(f"dynamics broke down numerically ({exc})", "physics") from None
        raise


def advance_equations(world: "SdkWorld", dt: float) -> List[Dict[str, Any]]:
    """Advance the equation subsystem; the caller owns interval atomicity."""
    spec, model = world.contract.physics, world.physics
    assert spec is not None and model is not None
    _refresh_reads(world)
    if spec.vars or spec.read or any(step.reads for step in world.entity_dynamics):
        from .coupled_physics import integrate_coupled

        changes = integrate_coupled(world, dt)
    else:
        shared = {**_FUNCS, **_CONSTS, **model.params}
        for step in world.entity_dynamics:
            step.step(world, shared, dt, model.time, model.substeps)
        model.time += dt
        changes = []
    namespace = model._namespace(model.values, model.time)
    for target, expr in world.physics_writes:
        value = expr.eval(namespace)
        owner, _, prop = target.partition(".")
        if owner == "world":
            world.set_world(prop, value)
        else:
            for entity in world.entities_of(owner):
                world.set_prop(entity, prop, value)
    world.touch()
    return changes


def _refresh_reads(world: "SdkWorld") -> None:
    spec = world.contract.physics
    if spec is None or world.physics is None:
        return
    scope = world.scope()
    for name, src in spec.read.items():
        try:
            value = compile_expr(src)(scope)
        except ExprError as exc:
            raise RunError(str(exc), f"physics.read.{name}") from None
        world.physics.params[name] = _number(value, f"physics.read.{name}")


def _constant(world: "SdkWorld", raw: Any, where: str) -> float:
    try:
        value = compile_expr(raw)(world.scope()) if is_expr(raw) else raw
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    return _number(value, where)


def _number(value: Any, where: str) -> float:
    if not finite_number(value):
        raise RunError(f"must be a finite number that fits in a float, got {shown_value(value)}", where)
    return float(value)
