"""Joint RK stages for physics that reads other evolving state.

Trial states are visible only while evaluating derivatives under the world's lock.
They are restored before committing through the normal journaled write API.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .model import _CONSTS, _FUNCS
from .entities import _as_prop, _bounds
from ..errors import RunError
from ..expr import compile_expr, truthy
from .integration import integrate
from .stochastic import exact_transition
from .stochastic_integration import integrate_noise

if TYPE_CHECKING:
    from ..world.live import SdkWorld

#: Roots an entity's read may use and still be the same at every RK stage of an interval (nothing there evolves).
_FIXED_ROOTS = frozenset({"world", "inputs", "round", "stage", "arm"})


def integrate_coupled(world: "SdkWorld", dt: float) -> List[Dict[str, Any]]:
    from .world import _number, _refresh_reads

    model = world.physics
    assert model is not None
    start = model.time
    clock_time = world.time
    spec = world.contract.physics
    assert spec is not None
    original_params = dict(model.params)
    world_names = list(model._compiled)
    state_names = list(world_names)
    noise_read_names = {i: set(spec.read) for i in range(len(world_names))}
    entities = []
    y = [model.variables[name].value for name in world_names]
    bounds = [(model.variables[name].min, model.variables[name].max) for name in world_names]
    rates = [model._compiled[name] for name in world_names]
    noises = []
    #: Each entity's reads that nothing evolving can change, worked out once per interval instead of per RK stage.
    fixed_reads: Dict[Any, Dict[str, float]] = {}
    for index, name in enumerate(world_names):
        if name in model._noise:
            noises.append((index, model._noise[name], world.seeds.rng("physics", "noise", "world", name, world.round)))
    for step in world.entity_dynamics:
        for entity in world.entities_of(step.type_name):
            if step.where is not None and not truthy(step._eval(world, step.where, entity, f"{step.path}.where")):
                continue
            offset = len(y)
            entities.append((step, entity, offset))
            fixed_reads[(id(step), entity.id)] = {
                name: step._number(step._eval(world, expr, entity, f"{step.path}.read.{name}"), entity,
                                   f"{step.path}.read.{name}")
                for name, expr in step.reads if not expr.functions and expr.roots <= _FIXED_ROOTS}
            state_names.extend(step.vars)
            for index in range(len(step.vars)):
                noise_read_names[offset+index] = {name for name, _ in step.reads}
            y.extend(step._number(entity.properties.get(name), entity, f"{step.path}.vars.{name}") for name in step.vars)
            bounds.extend(_bounds(world, entity, name) for name in step.vars)
            rates.extend(step.rates)
            for index, expr in step.noise:
                noises.append((offset + index, expr, world.seeds.rng(
                    "physics", "noise", step.type_name, entity.id, step.vars[index], world.round)))
    before = list(y)
    # Static world reads (for example a count of occupied beds) do not change
    # during an interval with no entity dynamics. Avoid rescanning populations
    # at every RK stage. Definitions are conservatively treated as dynamic.
    dynamic_reads = []
    for name, source in spec.read.items():
        expr = compile_expr(source)
        if entities or expr.roots & {"physics", "clock"} or (expr.roots | expr.functions) & set(world.contract.defs):
            dynamic_reads.append((name, expr))

    def publish(values: List[float], time: float) -> None:
        for index, name in enumerate(world_names):
            model.variables[name].value = values[index]
        for step, entity, offset in entities:
            for index, name in enumerate(step.vars):
                entity.properties[name] = values[offset + index]
        model.time = time
        if world.continuous and spec.dt > 0:
            world.time = clock_time - (start + dt - time) / spec.dt
        world.touch()

    entity_spaces: Dict[Any, Dict[str, Any]] = {}
    last_values: Optional[List[float]] = None
    last_time: Optional[float] = None
    last_spaces: List[Dict[str, Any]] = []

    def namespaces(values: List[float], time: float) -> List[Dict[str, Any]]:
        nonlocal last_values, last_time, last_spaces
        if values is last_values and time == last_time:
            return last_spaces
        publish(values, time)
        if dynamic_reads:
            scope = world.scope()
            for name, expr in dynamic_reads:
                model.params[name] = _number(expr(scope), f"physics.read.{name}")
        shared = {**_FUNCS, **_CONSTS, **model.params, **model.values, "t": time}
        spaces = [shared] * len(world_names)
        for step, entity, _ in entities:
            ns = {**shared, **step.params}
            for name in step.inputs + step.vars:
                ns[name] = step._number(entity.properties.get(name), entity, f"{step.path}.vars.{name}")
            fixed = fixed_reads[(id(step), entity.id)]
            for name, expr in step.reads:
                ns[name] = fixed[name] if name in fixed else step._number(
                    step._eval(world, expr, entity, f"{step.path}.read.{name}"), entity, f"{step.path}.read.{name}")
            entity_spaces[(id(step), entity.id)] = ns
            spaces.extend([ns] * len(step.vars))
        last_values, last_time, last_spaces = values, time, spaces
        return spaces

    def slope(values: List[float], time: float) -> List[float]:
        return [expr.eval(ns) for expr, ns in zip(rates, namespaces(values, time))]

    def coefficients(values: List[float], time: float) -> Any:
        spaces = namespaces(values, time)
        f = [expr.eval(ns) for expr, ns in zip(rates, spaces)]
        g = [0.0]*len(values)
        for index, expr, _ in noises:
            g[index] = expr.eval(spaces[index])
        return f, g

    def noise_derivatives(values: List[float], time: float) -> List[float]:
        spaces = namespaces(values, time)
        result = [0.0]*len(values)
        for index, expr, _ in noises:
            base = expr.eval(spaces[index])
            if base == 0:
                continue
            epsilon = 1e-5*max(1.0, abs(values[index]))
            if expr._names & noise_read_names[index]:
                shifted = list(values)
                shifted[index] += epsilon
                _, diffusion = coefficients(shifted, time)
                result[index] = (diffusion[index]-base)/epsilon
            else:
                ns = dict(spaces[index])
                ns[state_names[index]] = values[index]+epsilon
                result[index] = (expr.eval(ns)-base)/epsilon
        return result

    h = dt / model.substeps
    try:
        for substep in range(model.substeps):
            t = start + substep*h
            exact = False
            if noises and not entities and not spec.read:
                independent = all(not (r._names & (set(world_names)-{world_names[j]} | {"t"}))
                                  for j, r in enumerate(rates))
                spaces = namespaces(y, t)
                exact = independent and all(
                    not (expr._names & (set(world_names)-{world_names[index]} | {"t"}))
                    and exact_transition(rates[index], expr, world_names[index], spaces[index], y[index], h, 0) is not None
                    for index, expr, _ in noises)
            if noises and not exact:
                nxt = integrate_noise(coefficients, y, t, h, {index: rng for index, _, rng in noises},
                                      bounds, spec.noise_rtol, spec.atol, noise_derivatives)
            else:
                nxt = integrate(slope, y, t, h, substeps=1, rtol=spec.rtol, atol=spec.atol)
                if exact:
                    spaces = namespaces(y, t)
                    for index, expr, rng in noises:
                        value = exact_transition(rates[index], expr, world_names[index], spaces[index], y[index], h, rng.gauss(0, 1))
                        assert value is not None
                        nxt[index] = value
            for index, (low, high) in enumerate(bounds):
                if not math.isfinite(nxt[index]):
                    raise ArithmeticError(f"variable {index} became non-finite")
                if low is not None:
                    nxt[index] = max(low, nxt[index])
                if high is not None:
                    nxt[index] = min(high, nxt[index])
            y = nxt
    except (ArithmeticError, ValueError) as exc:
        raise RunError(f"coupled dynamics broke down numerically ({exc})", "physics") from None
    finally:
        publish(before, start)
        last_values = None
        world.time = clock_time
        world.touch()
        model.params.clear()
        model.params.update(original_params)

    # Commit only after every equation has succeeded. No trial value leaks into
    # the journal, snapshots or derived-expression caches.
    for index, name in enumerate(world_names):
        model.variables[name].value = y[index]
    for step, entity, offset in entities:
        for index, name in enumerate(step.vars):
            world.set_prop(entity, name, y[offset + index])
    model.time = start + dt
    world.touch()
    _refresh_reads(world)
    namespaces(y, model.time)
    for step, entity, _ in entities:
        ns = entity_spaces[(id(step), entity.id)]
        for prop, kind, formula in step.writes:
            world.set_prop(entity, prop, _as_prop(formula.eval(ns), kind))
    return []
