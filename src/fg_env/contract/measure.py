"""Contract sections of measurement, ending, reuse, experiments and invariants."""
from __future__ import annotations

from typing import Any

from pydantic import Field, StrictBool, model_validator

from .base import OUTPUT_TYPES, Effects, TypeName, _ExprShorthand, _Model

__all__ = ["OutputSpec", "EndSpec", "DefSpec", "ArmSpec", "INVARIANT_CHECKS", "END_CHECKS",
           "InvariantSpec"]
# ---------------------------------------------------------------------------
# Measurement, ending, experiment, invariants
# ---------------------------------------------------------------------------


class OutputSpec(_ExprShorthand):
    """A typed field of the run result, worked out when the run ends. With ``series`` it is also sampled every round:
    ``$outputs.x`` reads its latest sample, ``$series.x`` every sample so far (``result.metrics`` and
    ``result.series``). One worked out from a private property (directly or through a def or another output) is not
    shown to agents: `$outputs`/`$series` reads of it in what they are shown are refused."""

    expr: str
    type: TypeName = Field("any", description="One of: " + ", ".join(OUTPUT_TYPES))
    description: str = ""
    unit: str = ""
    format: str = Field("",
                        description="How result.summary() and the CLI show it: a template format (money, pct, pct1, "
                                    "int, 0-4 decimals …); the stored value stays exact. Unset: numbers to 4 decimals.")
    series: StrictBool | str = Field(False,
                                     description="Also sample it every round: true samples `expr` (the result is the "
                                                 "last sample); an expression samples that instead, when the "
                                                 "per-round figure differs from the final one (sales each round, "
                                                 "total sales at the end).")

    @property
    def sampled(self) -> str | None:
        """The expression sampled every round, or None for an output worked out only at the end."""
        if isinstance(self.series, str):
            return self.series
        return self.expr if self.series else None


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
    """A named, reusable piece of the rules. With ``expr`` it is an expression called like a built-in:
    ``$utility($actor, $params.offer)`` (shorthand: the expression text, no arguments). With ``do`` it is an effect
    list run by the ``call`` effect: ``{"call": "settle", "with": {"buyer": "$actor"}}``; its effects see only the
    arguments (plus $inputs, $world, $round …), never the caller's locals."""

    args: list[str] = Field(default_factory=list, description="Argument names; the body reads them as roots ($side).")
    expr: str | None = Field(None, description="The expression it gives.")
    do: Effects | None = Field(None, description="The effects it runs, when called with {\"call\": name}.")
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _expand(cls, data: Any) -> Any:
        return data if isinstance(data, dict) else {"expr": data}

    @model_validator(mode="after")
    def _one_body(self) -> DefSpec:
        if (self.expr is None) == (self.do is None):
            raise ValueError("give `expr` (an expression, called as $name(...)) or `do` (effects, run with "
                             "{\"call\": name}), not both")
        return self


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
                       description="When it is checked: action (after the build, every action and effect block — an "
                                   "`each` event once its last item ran, or before a trigger or reaction an item sets "
                                   "off — and every round; `$all(<type>, <condition>)` over each member's own "
                                   "properties re-checks only the members that changed) | round (after the build and "
                                   "at the end of every round: much cheaper for sums over big crowds) | end (once, "
                                   "when the run finishes).")
