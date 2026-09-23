"""Parameter uncertainty in runs: each run draws its parameters, so a forecast's range includes not knowing them.

A calibration leaves a set of points that fit as well as the best within the objective's noise; a forecast run only
at the best point pretends those parameters are known, and its intervals are too narrow. ``uncertainty=`` on
:func:`fg_env.experiment`, :func:`fg_env.analysis.sweep`, :func:`fg_env.analysis.backtest` and :func:`fg_env.analysis.validate` draws them per
run instead, from:

* a :class:`~fg_env.analysis.calibrate.CalibrationResult` — one of its plausible points, the parameters together;
* a list of points ``[{"price_level": 0.94}, …]`` — one of them;
* priors ``{"price_level": {"dist": "normal", "mean": 0.95, "sd": 0.02, "min": 0.8, "max": 1.2}}`` — each parameter on
  its own: ``normal`` (mean, sd), ``lognormal`` (median, sd of the log), ``uniform`` (low, high), ``triangular`` (low,
  mode, high) or ``{"values": [...]}``; ``min``/``max`` clip a draw into range.

Run *i* draws the same parameters in every arm, cell and case (common random numbers), from the analysis seed.
"""
from __future__ import annotations

import math
import random
from dataclasses import replace
from typing import Any, Dict, List, Mapping, Sequence

from ..seeds import SeedTree
from . import runner
from .stats import is_number

__all__ = ["parameter_draws", "with_draws", "DISTRIBUTIONS"]

#: Prior distributions and the fields each needs.
DISTRIBUTIONS = {"normal": ("mean", "sd"), "lognormal": ("median", "sd"), "uniform": ("low", "high"),
                 "triangular": ("low", "mode", "high")}


def parameter_draws(contract: Any, uncertainty: Any, count: int, seed: int) -> List[Dict[str, Any]]:
    """``count`` parameter draws (one per run) from a calibration, a list of points or priors."""
    rng = SeedTree(seed).rng("parameter-draws")
    report = getattr(uncertainty, "calibration", None)  # a loaded session: its load-time calibration report
    if isinstance(report, Mapping):
        uncertainty = report
    if isinstance(uncertainty, Mapping) and "plausible" in uncertainty and "params" in uncertainty:
        chosen = list(uncertainty["plausible"]) or [dict(uncertainty["params"])]
        return [_checked(contract, dict(rng.choice(chosen)), "a calibration's plausible point") for _ in range(count)]
    points = getattr(uncertainty, "plausible", None)
    if points is not None:
        chosen = list(points) or [dict(uncertainty.params)]
        return [_checked(contract, dict(rng.choice(chosen)), "a calibration's plausible point") for _ in range(count)]
    if isinstance(uncertainty, Sequence) and not isinstance(uncertainty, (str, bytes)):
        if not uncertainty or not all(isinstance(point, Mapping) for point in uncertainty):
            raise ValueError("uncertainty as a list must hold points {input: value}, at least one")
        return [_checked(contract, dict(rng.choice(list(uncertainty))), "a listed point") for _ in range(count)]
    if not isinstance(uncertainty, Mapping) or not uncertainty:
        raise ValueError("uncertainty must be a CalibrationResult, a list of points {input: value} or priors "
                         "{input: {dist, …}}")
    for name, spec in uncertainty.items():
        _prior_fields(name, spec)
    return [_checked(contract, {name: _draw(name, spec, rng) for name, spec in uncertainty.items()}, "a prior draw")
            for _ in range(count)]


def with_draws(jobs: Sequence[runner.Job], draws: Sequence[Mapping[str, Any]]) -> List[runner.Job]:
    """Every job with run *i*'s draw added to its inputs. An input the job already sets cannot also be drawn."""
    out = []
    for job in jobs:
        draw = draws[job.tags["run"]]
        clash = sorted(set(draw) & set(job.inputs))
        if clash:
            raise ValueError(f"{', '.join(clash)} is set for these runs and also drawn from uncertainty; "
                             "leave it to one of them")
        out.append(replace(job, inputs={**job.inputs, **draw}))
    return out


def _prior_fields(name: str, spec: Any) -> None:
    if not isinstance(spec, Mapping):
        raise ValueError(f"prior '{name}' must be {{dist, …}} or {{values: [...]}}, got {spec!r}")
    if "values" in spec:
        if not isinstance(spec["values"], Sequence) or isinstance(spec["values"], str) or not spec["values"]:
            raise ValueError(f"prior '{name}': values must be a non-empty list")
        return
    dist = spec.get("dist")
    if dist not in DISTRIBUTIONS:
        raise ValueError(f"prior '{name}': dist must be one of {', '.join(DISTRIBUTIONS)} (or give values), got {dist!r}")
    for key in DISTRIBUTIONS[dist]:
        if not is_number(spec.get(key)):
            raise ValueError(f"prior '{name}': a {dist} prior needs a number for {key}")
    for key in ("min", "max"):
        if key in spec and not is_number(spec[key]):
            raise ValueError(f"prior '{name}': {key} must be a number")


def _draw(name: str, spec: Mapping[str, Any], rng: random.Random) -> Any:
    if "values" in spec:
        return rng.choice(list(spec["values"]))
    dist = spec["dist"]
    if dist == "normal":
        value = rng.gauss(spec["mean"], spec["sd"])
    elif dist == "lognormal":
        if spec["median"] <= 0:
            raise ValueError(f"prior '{name}': a lognormal median must be positive")
        value = math.exp(rng.gauss(math.log(spec["median"]), spec["sd"]))
    elif dist == "uniform":
        value = rng.uniform(spec["low"], spec["high"])
    else:
        value = rng.triangular(spec["low"], spec["high"], spec["mode"])
    if "min" in spec:
        value = max(spec["min"], value)
    if "max" in spec:
        value = min(spec["max"], value)
    return value


def _checked(contract: Any, point: Dict[str, Any], what: str) -> Dict[str, Any]:
    """A draw with every name a declared number input, whole numbers rounded for ``int`` inputs."""
    out = {}
    for name, value in point.items():
        spec = runner.input_spec(contract, name)
        if spec.type not in ("number", "int") or not is_number(value):
            raise ValueError(f"{what} sets '{name}' to {value!r}; drawn parameters must be number or int inputs")
        out[name] = runner.coerce_input(contract, name, float(value))
    return out
