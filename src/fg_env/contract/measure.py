"""Contract sections of measurement, ending, experiments, invariants and calibration."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .base import OUTPUT_TYPES, Effects, TypeName, _ExprShorthand, _Model

__all__ = ["MetricSpec", "OutputSpec", "EndSpec", "DefSpec", "BlockSpec", "ArmSpec", "INVARIANT_CHECKS", "END_CHECKS",
           "InvariantSpec", "CalibrationSpec"]
# ---------------------------------------------------------------------------
# Measurement, ending, experiment, invariants
# ---------------------------------------------------------------------------


class MetricSpec(_ExprShorthand):
    """A number tracked every round (a series). Shorthand: the expression. One that names a private property (directly
    or through a def or metric) is not shown to agents: `$metrics`/`$series` reads of it in what they are shown are
    refused."""

    expr: str
    description: str = ""
    unit: str = ""


class OutputSpec(_ExprShorthand):
    """A typed field of the run result. ``$metrics.x`` is a metric's final value, ``$series.x`` its history."""

    expr: str
    type: TypeName = Field("any", description="One of: " + ", ".join(OUTPUT_TYPES))
    description: str = ""
    format: str = Field("",
                        description="How result.summary() and the CLI show it: a template format (money, pct, pct1, "
                                    "int, 0-4 decimals …); the stored value stays exact. Unset: numbers to 4 decimals.")


class EndSpec(_Model):
    """A condition that ends the run early."""

    when: str
    name: str | None = None
    winner: str | None = Field(None, description="Expression naming the winner(s).")
    say: str | None = None
    check: str = Field("stage", description="When it is checked: stage (after the start events, after every stage and "
                                            "at the end of the round) | action (also the moment any action, sealed "
                                            "choice or effect block commits: a winning move ends the run at once).")


class DefSpec(_Model):
    """A named, reusable expression called like a built-in: ``$utility($actor, $params.offer)``.
    Shorthand: the expression text (no arguments)."""

    args: list[str] = Field(default_factory=list, description="Argument names; the body reads them as roots ($side).")
    expr: str
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _expand(cls, data: Any) -> Any:
        return data if isinstance(data, dict) else {"expr": data}


class BlockSpec(_Model):
    """A named, reusable effect list: ``{"block": "settle", "with": {"buyer": "$actor"}}``.
    The effects see only the arguments (plus $inputs, $world, $round …), never the caller's locals."""

    args: list[str] = Field(default_factory=list)
    do: Effects
    description: str = ""


class ArmSpec(_Model):
    """An experiment variant: input overrides and/or a contract patch."""

    description: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    patch: dict[str, Any] = Field(default_factory=dict,
                                  description="Deep-merged into the contract (objects merge, lists replace).")


INVARIANT_CHECKS = ("action", "round", "end")
END_CHECKS = ("stage", "action")


class InvariantSpec(_ExprShorthand):
    """Must always hold. Broken by an agent's action (with everything its commit sets off), that action is refused and
    undone and the agent told `why`; broken by anything else (events, physics, the build), the run fails."""

    expr: str
    why: str = Field("",
                     description="What the agent whose action broke it is told: a template, which may read no agent's "
                                 "private property.")
    check: str = Field("action",
                       description="When it is checked: action (after the build, every action and effect block — a "
                                   "round event's `each` once its last item ran, or before a `change` event or "
                                   "reaction an item sets off — and every round; `$all(<type>, <condition>)` over "
                                   "each member's own properties re-checks only the members that changed) | round "
                                   "(after the build and at the end of every round: much cheaper for sums over big "
                                   "crowds) | end (once, when the run finishes).")


class CalibrationSpec(_Model):
    """A quick pilot calibration run whenever the contract loads: inputs are fitted so short pilot sessions hit the
    targets, and the session runs with the fitted values (``env.inputs``, ``result.inputs``; the fit is in
    ``env.calibration``). Deterministic given the session's seed. It costs ``budget × runs`` pilot sessions plus
    ``holdout`` at every load that does not set a fitted input itself — setting one (or sweeping it) skips it.

    A pilot fit is only as steady as its pilots: a noisy target (a volatility over a few dozen bars) fitted with one
    short pilot per point can land anywhere in the range, even on its bounds (check ``env.calibration``). Longer
    pilots, more ``runs`` per point, a larger ``holdout`` and a range no wider than plausible make it reliable."""

    params: dict[str, dict[str, Any]] = Field(..., min_length=1,
                                              description="{input: {low?, high?, log?}}: number or int inputs to fit "
                                                          "(the range defaults to the input's min and max).")
    targets: dict[str, Any] = Field(..., min_length=1,
                                    description="{output or metric: target} as fg_env.analysis.calibrate takes "
                                                "them; a number (or a stat target's `value`) may be an expression "
                                                "over $inputs and $world, read from the world this session builds.")
    inputs: dict[str, Any] = Field(default_factory=dict,
                                   description="Inputs of the pilot sessions only, e.g. fewer bars; the session's own "
                                               "inputs apply underneath.")
    runs: int = Field(2, ge=1, le=20, description="Pilot sessions per evaluated point.")
    budget: int = Field(6, ge=2, le=50, description="Distinct points evaluated.")
    holdout: int = Field(1, ge=1, le=20, description="Pilot sessions on fresh seeds that validate the fit.")
    method: Literal["auto", "bisection", "golden", "nelder_mead", "cross_entropy"] = Field(
        "auto", description="Search method (see fg_env.analysis.calibrate).")
    workers: int = Field(1, ge=1, le=64, description="Pilot sessions run in this many processes at once.")
