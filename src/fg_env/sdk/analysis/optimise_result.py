"""The result of :func:`fg_env.optimise`: the chosen decision, how sure it is, and how it was found."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import runner

__all__ = ["OptimisationResult"]

#: Searched decisions a report lists, best first.
_HISTORY_SHOWN = 8


def _num(value: Optional[float]) -> str:
    if value is None:
        return "—"
    value = value + 0.0  # -0.0 reads as 0
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:.4g}"


def _decision(values: Dict[str, Any]) -> str:
    """A decision in full (tables shorten long values; a summary never does)."""
    return ", ".join(f"{name}={json.dumps(value)}" for name, value in values.items())


def _span(row: Dict[str, Any], share: bool = False) -> str:
    text = (lambda v: "—" if v is None else f"{v:.0%}") if share else _num
    interval = f" [{text(row['low'])}, {text(row['high'])}]" if row.get("low") is not None else ""
    return f"{text(row['value'])}{interval}"


def _constraint_text(row: Dict[str, Any]) -> str:
    if row["value"] is None:
        return f"{row['constraint']}: no value"
    if "share_needed" in row:
        verdict = "met" if row["met"] else f"NOT met, short by {row['shortfall']:.0%} of runs"
        return f"{row['constraint']}: holds in {_span(row, share=True)} of runs — {verdict}"
    verdict = "met" if row["met"] else f"NOT met, short by {_num(row['shortfall'])}"
    return f"{row['constraint']}: {row.get('stat', 'mean')} {_span(row)} — {verdict}"


def _difference_text(diff: Optional[Dict[str, Any]]) -> str:
    if not diff:
        return "no paired runs to compare"
    interval = f" (95% CI {_num(diff['low'])} to {_num(diff['high'])})" if diff["low"] is not None else ""
    return f"{_num(diff['mean'])}{interval}"


@dataclass
class OptimisationResult:
    contract: str
    method: str
    decisions: List[str]
    objectives: List[str]
    constraints: List[str]
    runs: int
    seed: int
    #: Distinct decisions the search evaluated.
    evaluations: int
    #: Every run made: search, confirming the finalists, held-out seeds and sensitivity.
    total_runs: int
    #: The chosen decision (``None`` for a Pareto frontier); when nothing is feasible, the closest one.
    best: Optional[Dict[str, Any]] = None
    feasible: bool = False
    #: The chosen decision's objectives and constraints with 95% intervals, on the confirmation seeds (not the search's).
    estimates: Optional[Dict[str, Any]] = None
    runner_up: Optional[Dict[str, Any]] = None
    #: The chosen decision and the runner-up re-run on fresh seeds: values, the paired difference, seed-luck flags.
    holdout: Optional[Dict[str, Any]] = None
    #: The objective when each decision moves one step down or up from the best, paired on the search seeds.
    sensitivity: List[Dict[str, Any]] = field(default_factory=list)
    #: Pareto frontier rows (two or three objectives), first objective best first.
    frontier: List[Dict[str, Any]] = field(default_factory=list)
    #: Every evaluated decision, in the order the search tried them.
    history: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """The answer in plain words."""
        lines = [f"Optimising {self.contract}: {self.method}, {self.evaluations} decision(s) tried, "
                 f"{self.total_runs:,} run(s)."]
        if self.frontier or self.best is None:
            lines.append(f"Pareto frontier of {' vs '.join(self.objectives)}: {len(self.frontier)} decision(s) where "
                         "no objective can improve without another getting worse.")
            return "\n".join(lines + [f"note: {n}" for n in self.notes])
        assert self.estimates is not None
        head = "Best decision" if self.feasible else "No decision tried meets every constraint. Closest"
        lines.append(f"{head}: {_decision(self.best)}")
        for row in self.estimates["objectives"]:
            lines.append(f"  {row['objective']}: {_span(row)} over {row['n']} run(s)")
        lines += [f"  {_constraint_text(row)}" for row in self.estimates["constraints"]]
        lines += self._holdout_lines()
        return "\n".join(lines + [f"note: {n}" for n in self.notes])

    def _holdout_lines(self) -> List[str]:
        h = self.holdout
        if not h:
            return []
        fresh = h["best"]["objectives"][0]
        short = f"; {', '.join(h['short_within_noise'])} falls short there, within noise" \
            if h["short_within_noise"] else ""
        lines = [f"On {h['seeds']} fresh seed(s): {_measure(fresh['objective'])} {_span(fresh)}{short}"]
        if "runner_up" in h:
            diff = h["difference"]
            if h["still_wins"]:
                verdict = "it still ranks first"
            elif diff is not None and not diff["clear"] and h["best"]["feasible"] == h["runner_up"]["feasible"]:
                verdict = "the runner-up edges ahead, within noise"
            else:
                verdict = "the runner-up does better"
            lines.append(f"  against the runner-up ({_decision(h['runner_up']['decision'])}): {verdict}; "
                         f"{_measure(self.objectives[0])} difference {_difference_text(diff)} (positive favours the choice)")
        if h["seed_luck"]:
            lines.append("  SEED LUCK: " + "; ".join(h["reasons"]) + " — trust the fresh seeds and use more runs")
        return lines

    def table(self) -> str:
        """The frontier, or the best evaluated decisions, one row each."""
        rows = self.frontier or sorted(self.history, key=lambda h: (not h["feasible"], h["rank"]))[:_HISTORY_SHOWN]
        header = ["decision", *self.objectives, *(["constraints"] if self.constraints else []),
                  *(["fresh seeds"] if self.frontier and self.holdout is not None else [])]
        body = [[runner.describe_inputs(r["decision"]), *[_num(v) for v in _values(r)],
                 *(["met" if r["feasible"] else "not met"] if self.constraints else []),
                 *(["on frontier" if r.get("holdout", {}).get("on_frontier") else "dropped"]
                   if self.frontier and self.holdout is not None else [])] for r in rows]
        widths = [max(len(str(line[i])) for line in [header, *body]) for i in range(len(header))]
        return "\n".join("  ".join(str(cell).ljust(w) for cell, w in zip(line, widths)).rstrip()
                         for line in [header, *body])

    def report(self) -> str:
        """The summary, the sensitivity around the best decision and the table."""
        lines = [self.summary()]
        if self.sensitivity:
            lines.append("Around the best decision (paired on the search seeds):")
            for row in self.sensitivity:
                state = "" if row["feasible"] else ", constraints NOT met"
                lines.append(f"  {row['decision']} one step {row['direction']}: {_measure(self.objectives[0])} changes by "
                             f"{_difference_text(row['change'])}{state}")
        lines += ["", self.table()]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"contract": self.contract, "method": self.method, "decisions": self.decisions,
                "objectives": self.objectives, "constraints": self.constraints, "runs": self.runs, "seed": self.seed,
                "evaluations": self.evaluations, "total_runs": self.total_runs, "best": self.best,
                "feasible": self.feasible, "estimates": self.estimates, "runner_up": self.runner_up,
                "holdout": self.holdout, "sensitivity": self.sensitivity, "frontier": self.frontier,
                "history": self.history, "notes": self.notes}


def _measure(objective: str) -> str:
    """``maximise mean of margin`` → ``mean of margin``."""
    return objective.split(" ", 1)[1]


def _values(row: Dict[str, Any]) -> Sequence[Optional[float]]:
    objectives = row.get("objectives", [])
    return [o["value"] if isinstance(o, dict) else o for o in objectives]
