"""Calibration at load: a contract's ``calibration`` section fits inputs with short pilot sessions every time a
session loads, and the session then runs with the fitted values.

The pilot sessions play the session's own inputs (data tables included) under the section's pilot ``inputs``, on
seeds derived from the session's seed, so the fitted values — and so the whole session — are reproducible from the
seed. A load whose caller or arm sets any fitted input skips the calibration: that is how the pilot sessions
themselves, a sweep over a fitted input, and anyone who already knows the values avoid paying for it.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from ..contract import CalibrationSpec, Contract
from ..errors import RunError
from ..expr import ExprError, compile_expr, is_expr
from ..sampling.seeds import SeedTree

__all__ = ["calibrate_at_load"]


def calibrate_at_load(contract: Contract, supplied: Mapping[str, Any], resolved: Mapping[str, Any], seed: int,
                      arm: str | None, build: Callable[[Mapping[str, Any]], Any]) -> dict[str, Any] | None:
    """Fit the contract's ``calibration`` params for a session with these inputs and seed. Returns the report
    ({params, targets, fit, validation_fit, method, evaluations, pilot_sessions, seconds, notes}), or None when the
    contract declares no calibration or the caller already set a fitted input. ``build(inputs)`` builds a world to
    read expression targets from."""
    spec = contract.calibration
    if spec is None or any(name in supplied for name in spec.params):
        return None
    from ..analysis.calibrate import calibrate  # the analysis layer loads contracts itself

    started = time.perf_counter()
    targets = _targets(spec, build(resolved))
    pilot = {name: value for name, value in resolved.items() if name not in spec.params}
    pilot.update(spec.inputs)
    try:
        fit = calibrate(contract, targets, spec.params, runs=spec.runs, budget=spec.budget, holdout=spec.holdout,
                        method=spec.method, inputs=pilot, arm=arm, seed=SeedTree(seed).derive("calibration"),
                        workers=spec.workers)
    except ValueError as exc:
        raise RunError(str(exc), "calibration") from None
    return {"params": dict(fit.params), "plausible": [dict(point) for point in fit.plausible], "targets": targets,
            "fit": fit.fit, "validation_fit": fit.validation["fit"],
            "method": fit.method, "evaluations": fit.evaluations,
            "pilot_sessions": fit.evaluations * spec.runs + spec.holdout,
            "seconds": round(time.perf_counter() - started, 3), "notes": list(fit.notes)}


def _targets(spec: CalibrationSpec, env: Any) -> dict[str, Any]:
    """The targets with every expression value read from the session's built world."""
    scope = env.world.scope()

    def value(raw: Any, path: str) -> Any:
        if not (isinstance(raw, str) and is_expr(raw)):
            return raw
        try:
            return compile_expr(raw)(scope)
        except ExprError as exc:
            raise RunError(str(exc), path) from None

    out: dict[str, Any] = {}
    for name, target in spec.targets.items():
        path = f"calibration.targets.{name}"
        if isinstance(target, Mapping) and "value" in target:
            out[name] = {**target, "value": value(target["value"], f"{path}.value")}
        else:
            out[name] = value(target, path)
    return out
