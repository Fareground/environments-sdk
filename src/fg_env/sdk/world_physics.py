"""World physics: world-level variables and per-entity dynamics, built from the contract and
stepped by the clock. World variables step first (RK4, optional noise) and write back; then every
``per`` type's entities step, reading the world variables' new values."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..physics import _CONSTS, _FUNCS, PhysicsExprError, PhysicsModel, PhysicsVariable, _CompiledExpr
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
    """Advance physics one round, or by ``elapsed`` clock time on a continuous clock
    (rates are then per clock unit)."""
    spec = world.contract.physics
    model = world.physics
    if spec is None or model is None:
        return []
    _refresh_reads(world)
    dt = spec.dt if elapsed is None else spec.dt * elapsed
    start = model.time
    noisy = any(var.noise is not None for var in spec.vars.values())
    changes = model.integrate(dt, rng=world.seeds.rng("physics", "noise", world.round) if noisy else None)
    world.touch()
    errors = [c for c in changes if c.get("type") == "physics_error"]
    if errors:
        raise RunError(errors[0]["narrative"], "physics")
    values = {**model.params, **model.values}
    namespace = model._namespace(values, model.time)
    for target, expr in world.physics_writes:
        value = expr.eval(namespace)
        owner, _, prop = target.partition(".")
        if owner == "world":
            world.set_world(prop, value)
        else:
            for entity in world.entities_of(owner):
                world.set_prop(entity, prop, value)
    if world.entity_dynamics:
        shared = {**_FUNCS, **_CONSTS, **values}
        for step in world.entity_dynamics:
            step.step(world, shared, dt, start, model.substeps)
    if dt > 0:
        model.time = start + dt  # also when only per-entity variables are integrated
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
