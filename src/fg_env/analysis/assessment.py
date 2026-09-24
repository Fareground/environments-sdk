"""A candidate decision judged on one set of seeds: its objectives, its constraints' verdicts, and its rank."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Sequence, Tuple

from ..runtime.measure import RunResult
from .constraints import Constraint, Standard, check
from .goals import Objective

__all__ = ["Assessment", "Key", "assess", "rank"]

#: A candidate's place: (tier, then lower is better within the tier, then a tie-break); see :func:`rank`.
Key = Tuple[int, float, float]


@dataclass(frozen=True)
class Assessment:
    """One candidate on one set of seeds: objective and constraint values with intervals and verdicts."""

    objectives: Tuple[Dict[str, Any], ...]
    constraints: Tuple[Dict[str, Any], ...]
    senses: Tuple[int, ...]
    runs: int
    failed: int

    @property
    def usable(self) -> bool:
        return all(o["value"] is not None for o in self.objectives) and all(
            c["value"] is not None for c in self.constraints)

    @property
    def feasible(self) -> bool:
        """Every constraint passes the standard it was checked against (a search's margin, or the confidence)."""
        return self.usable and all(c["passes"] for c in self.constraints)

    @property
    def verdict(self) -> str:
        """``feasible`` (every constraint confident), ``infeasible`` (one clearly missed) or ``borderline``."""
        if not self.usable or any(c["clearly_missed"] for c in self.constraints):
            return "infeasible"
        return "feasible" if all(c["confident"] for c in self.constraints) else "borderline"

    @property
    def violation(self) -> float:
        """How far the constraints are from passing, each shortfall on its own scale, added up."""
        return math.fsum(c["violation"] for c in self.constraints if c["violation"] is not None)

    def to_dict(self) -> Dict[str, Any]:
        return {"objectives": list(self.objectives), "constraints": list(self.constraints), "feasible": self.feasible,
                "verdict": self.verdict, "runs": self.runs, "failed": self.failed}


def assess(objectives: Sequence[Objective], constraints: Sequence[Constraint], runs: Sequence[RunResult],
           standard: Standard, rng: random.Random) -> Assessment:
    rows = []
    for objective in objectives:
        values = [v for v in objective.measure.per_run(runs) if v is not None]
        low, high = objective.stat.interval(values, rng)
        rows.append({"objective": objective.text, "value": objective.stat.of(values) if values else None, "low": low,
                     "high": high, "n": len(values)})
    checks = [check(c, runs, standard, rng) for c in constraints]
    return Assessment(tuple(rows), tuple(checks), tuple(o.sense for o in objectives), len(runs),
                      sum(1 for r in runs if r.status == "failed"))


def rank(a: Assessment) -> Key:
    """Order for one objective: candidates that pass, by the objective; then those that miss without clearly missing,
    then the clearly missed, each by how far they miss (the objective breaks ties); unusable ones last."""
    if not a.usable:
        return (3, 0.0, 0.0)
    objective = -a.senses[0] * a.objectives[0]["value"]
    if a.feasible:
        return (0, objective, 0.0)
    missed = any(c["clearly_missed"] for c in a.constraints)
    return (2 if missed else 1, a.violation, objective)
