"""Population patterns: values drawn once per run and key (priors, heterogeneity, segments) and Bass diffusion."""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..sampling.poisson import sample_poisson
from ..stdlib.linalg import cholesky
from .base import Number, PatternConfig, kind
from .signals import when

__all__ = ["DrawConfig", "SegmentsConfig", "DiffusionConfig"]

_NEEDS = {"normal": ("mean", "sd"), "lognormal": ("mu", "sigma"), "uniform": ("low", "high"), "beta": ("a", "b"),
          "gamma": ("shape", "scale"), "triangular": ("low", "mode", "high"), "choice": ("values",),
          "mvnormal": ("means", "cov"), "poisson": ("mean",)}


class DrawConfig(PatternConfig):
    kind: Literal["draw"] = "draw"
    dist: Literal["normal", "lognormal", "uniform", "beta", "gamma", "triangular", "choice", "mvnormal",
                  "poisson"] = Field(
        ..., description="The distribution. mvnormal draws correlated values (a list, or a map with `names`).")
    mean: Number | None = None
    sd: Number | None = None
    mu: Number | None = None
    sigma: Number | None = None
    low: Number | None = None
    high: Number | None = None
    mode: Number | None = None
    a: Number | None = None
    b: Number | None = None
    shape: Number | None = None
    scale: Number | None = None
    values: list[Any] | None = Field(None, description="choice: the values.")
    weights: list[Number] | None = Field(None, description="choice: relative weights.")
    means: list[Number] | str | None = Field(None, description="mvnormal: the means.")
    cov: list[list[Number]] | str | None = Field(None, description="mvnormal: the covariance matrix.")
    names: list[str] | None = Field(None,
                                    description="mvnormal: names for the values, giving a map "
                                                "($pattern.traits($it).elasticity).")
    log: bool = Field(False, description="mvnormal: exponentiate every value (correlated lognormals).")
    integer: bool = Field(False, description="A whole number (uniform draws whole numbers from low to high).")

    @model_validator(mode="after")
    def _shape(self) -> DrawConfig:
        for key in _NEEDS[self.dist]:
            if getattr(self, key) is None:
                raise ValueError(f"a {self.dist} draw needs `{key}`")
        literal = {k: getattr(self, k) for k in ("low", "high", "mode", "sd", "sigma", "a", "b", "shape", "scale")
                   if isinstance(getattr(self, k), (int, float))}
        for key, value in literal.items():
            if not math.isfinite(value):
                raise ValueError(f"{key} must be a finite number")
        if "low" in literal and "high" in literal and literal["low"] > literal["high"]:
            raise ValueError("low is more than high")
        if self.dist == "triangular" and "mode" in literal:
            if ("low" in literal and literal["mode"] < literal["low"]) or \
                    ("high" in literal and literal["mode"] > literal["high"]):
                raise ValueError("a triangular draw needs low ≤ mode ≤ high")
        if self.dist == "uniform" and self.integer and "low" in literal and "high" in literal:
            if math.ceil(literal["low"]) > math.floor(literal["high"]):
                raise ValueError("an integer uniform draw needs at least one whole number between low and high")
        for key in ("sd", "sigma"):
            if literal.get(key, 0) < 0:
                raise ValueError(f"{key} must be ≥ 0")
        for key in ("a", "b", "shape", "scale"):
            if key in literal and literal[key] <= 0:
                raise ValueError(f"{key} must be more than 0")
        if self.dist == "choice" and self.weights is not None and len(self.weights) != len(self.values or []):
            raise ValueError("weights: one per value")
        if self.names is not None and self.dist != "mvnormal":
            raise ValueError("`names` is for mvnormal draws")
        return self


def _draw_words(cfg: DrawConfig) -> str:
    who = " for each key" if cfg.keyed else ""
    params = ", ".join(f"{k} {getattr(cfg, k)}" for k in _NEEDS[cfg.dist] if k not in ("values", "cov"))
    return f"drawn once per run{who} from a {cfg.dist} distribution" + (f" ({params})" if params else "")


@kind("draw", "population", "draw", DrawConfig,
      "A value drawn once per run — a prior on an uncertain quantity — or once per key: heterogeneous traits per "
      "entity, correlated with mvnormal. Parameters of other patterns may read it.",
      example={"kind": "draw", "dist": "normal", "mean": -1.4, "sd": 0.3, "max": -0.2, "keys": "sku"},
      random=True, words=_draw_words,
      params=("mean", "sd", "mu", "sigma", "low", "high", "mode", "a", "b", "shape", "scale", "values", "weights",
              "means", "cov"))
def _draw(ctx: Any) -> Any:
    cfg: DrawConfig = ctx.cfg
    rng = ctx.stream("draw")
    dist = cfg.dist
    if dist == "choice":
        values = ctx.param("values")
        weights = ctx.numbers("weights") if cfg.weights is not None else None
        if not values or (weights is not None and (any(w < 0 for w in weights) or sum(weights) <= 0)):
            raise ctx.fail("`values` must not be empty and `weights` must be ≥ 0, not all 0")
        return rng.choices(list(values), weights=weights, k=1)[0]
    if dist == "mvnormal":
        return _mvnormal(ctx, rng)
    value: float
    if dist == "normal":
        value = rng.gauss(ctx.number("mean"), ctx.number("sd", 0))
    elif dist == "lognormal":
        value = rng.lognormvariate(ctx.number("mu"), ctx.number("sigma", 0))
    elif dist == "uniform":
        low, high = ctx.number("low"), ctx.number("high")
        if low > high:
            raise ctx.fail(f"low ({low:g}) is more than high ({high:g})")
        if cfg.integer and math.ceil(low) > math.floor(high):
            raise ctx.fail(f"an integer uniform draw needs at least one whole number between low ({low:g}) and high "
                           f"({high:g})")
        value = rng.randint(math.ceil(low), math.floor(high)) if cfg.integer else rng.uniform(low, high)
    elif dist == "beta":
        value = rng.betavariate(ctx.number("a", 1e-12), ctx.number("b", 1e-12))
    elif dist == "gamma":
        value = rng.gammavariate(ctx.number("shape", 1e-12), ctx.number("scale", 1e-12))
    elif dist == "triangular":
        low, high, mode = ctx.number("low"), ctx.number("high"), ctx.number("mode")
        if not low <= mode <= high:
            raise ctx.fail(f"a triangular draw needs low ≤ mode ≤ high, got {low:g}, {mode:g}, {high:g}")
        value = rng.triangular(low, high, mode)
    else:
        try:
            value = sample_poisson(rng, ctx.number("mean", 0))
        except ValueError as exc:
            raise ctx.fail(str(exc)) from None
    return int(round(value)) if cfg.integer or dist == "poisson" else value


def _mvnormal(ctx: Any, rng: Any) -> Any:
    means = ctx.numbers("means")
    cov = ctx.param("cov")
    if (not isinstance(cov, list) or len(cov) != len(means)
        or not all(isinstance(r, list) and len(r) == len(means) for r in cov)):
        raise ctx.fail(f"`cov` must be a {len(means)}×{len(means)} matrix to match `means`")
    lower, reason = cholesky([[float(v) for v in row] for row in cov])
    if reason:
        raise ctx.fail(f"`cov` is not a covariance matrix: {reason}")
    z = [rng.gauss(0.0, 1.0) for _ in means]
    values = [mu + math.fsum(lower[i][k] * z[k] for k in range(i + 1)) for i, mu in enumerate(means)]
    if ctx.cfg.log:
        values = [math.exp(min(v, 700)) for v in values]
    names = ctx.cfg.names
    if names is None:
        return values
    if len(names) != len(values):
        raise ctx.fail(f"`names` has {len(names)} names for {len(values)} values")
    return dict(zip(names, values))


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    share: Number = Field(..., description="Relative share of keys in this segment.")
    values: dict[str, Any] = Field(default_factory=dict, description="What the segment gives: {elasticity: -2.2, …}.")


class SegmentsConfig(PatternConfig):
    kind: Literal["segments"] = "segments"
    segments: dict[str, Segment] = Field(..., description="{segment: {share, values}}: each key falls in one segment, "
                                                          "drawn once per run; reads give {segment, …values}.")


@kind("segments", "population", "draw", SegmentsConfig,
      "Segments: each key (entity) falls in one segment by share, and reads the segment's values.",
      example={"kind": "segments", "keys": "customer", "segments": {
          "bargain": {"share": 0.6, "values": {"elasticity": -2.4}},
          "loyal": {"share": 0.4, "values": {"elasticity": -0.8}}}},
      random=True, params=("segments",),
      words=lambda cfg: "segments " + ", ".join(f"{name} ({seg.share})" for name, seg in cfg.segments.items()))
def _segments(ctx: Any) -> dict[str, Any]:
    segments = ctx.param("segments")
    names = list(segments)
    shares = [segments[name]["share"] for name in names]
    if any(isinstance(s, bool) or not isinstance(s, (int, float)) or s < 0 for s in shares) or sum(shares) <= 0:
        raise ctx.fail("every segment's `share` must be a number ≥ 0, not all 0")
    roll, total = ctx.uniform("segment") * sum(shares), 0.0
    chosen = names[-1]
    for name, share in zip(names, shares):
        total += share
        if roll < total:
            chosen = name
            break
    return {"segment": chosen, **segments[chosen]["values"]}


class DiffusionConfig(PatternConfig):
    kind: Literal["diffusion"] = "diffusion"
    p: Number = Field(..., description="Innovation: the share adopting on their own each unit.")
    q: Number = Field(..., description="Imitation: how strongly adopters draw in others (word of mouth).")
    market: Number = Field(1.0, description="Everyone who will eventually adopt.")
    start: float | str = Field(0.0, description="When adoption begins: clock units or an ISO date.")
    output: Literal["adopters", "new", "share", "hazard"] = Field(
        "adopters", description="adopters: total so far | new: adopting this round | share: of the market | hazard: "
                                "called with the adopted share, the chance a non-adopter adopts now (p + q·share).")


def _bass(p: float, q: float, tau: float) -> float:
    if tau <= 0:
        return 0.0
    decay = math.exp(-(p + q) * tau)
    return (1 - decay) / (1 + (q / p) * decay)


@kind("diffusion", "population", "signal", DiffusionConfig,
      "Bass diffusion: adoption through innovation and word of mouth — the S-shaped curve over time, or the adoption "
      "chance for a given share already adopted (driven by the run's own adopters).",
      example={"kind": "diffusion", "p": 0.03, "q": 0.38, "market": 5000, "start": "2025-03-01", "output": "new"},
      args=lambda cfg: ("share",) if cfg.output == "hazard" else (),
      words=lambda cfg: f"Bass diffusion (p {cfg.p}, q {cfg.q}) of a market of {cfg.market}, giving {cfg.output}",
      params=("p", "q", "market", "start"))
def _diffusion(ctx: Any, share: Any = None) -> float:
    p, q = ctx.number("p", 1e-12), ctx.number("q", 0)
    if ctx.cfg.output == "hazard":
        if isinstance(share, bool) or not isinstance(share, (int, float)):
            raise ctx.fail(f"the adopted share must be a number, got {share!r}")
        return min(1.0, p + q * max(0.0, min(1.0, float(share))))
    tau = ctx.t - when(ctx, ctx.param("start"), "start")
    market = ctx.number("market")
    if ctx.cfg.output == "share":
        return _bass(p, q, tau)
    if ctx.cfg.output == "adopters":
        return market * _bass(p, q, tau)
    step = ctx.every()
    return market * (_bass(p, q, tau + step) - _bass(p, q, tau))
