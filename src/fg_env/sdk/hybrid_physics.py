"""Symmetric splitting of equation and rigid-body flows at the native timestep."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .errors import RunError

if TYPE_CHECKING:
    from .world import SdkWorld


def advance_hybrid(world: "SdkWorld", dt: float) -> None:
    from .world_physics import advance_equations

    spec, model, rigid = world.contract.physics, world.physics, world.rigid
    assert spec is not None and model is not None and rigid is not None
    width = min(float(rigid.model.opt.timestep), spec.dt/model.substeps)
    if not math.isfinite(width) or width <= 0 or dt/width > 100_000:
        raise RunError("coupled rigid interval exceeds 100000 steps; reduce the clock tick", "physics.rigid")
    count = max(1, math.ceil(dt/width))
    h = dt/count
    beginning, native_beginning, clock_end = model.time, float(rigid.data.time), world.time
    try:
        for index in range(count):
            if world.continuous:
                world.time = clock_end - (dt-(index+0.5)*h)/spec.dt
            advance_equations(world, h/2, 1, ("hybrid", index, 0))
            rigid.step(world, h)
            if world.continuous:
                world.time = clock_end - (dt-(index+1)*h)/spec.dt
            advance_equations(world, h/2, 1, ("hybrid", index, 1))
        model.time = beginning+dt
        rigid.data.time = native_beginning+dt
    finally:
        world.time = clock_end
        world.touch()
