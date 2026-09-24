"""The evaluation result: focal score, baseline score and their paired difference, broken down every useful way."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence

from ..analysis.stats import estimate
from ..tournament.result import _columns, _estimate

if TYPE_CHECKING:
    from ..runtime.measure import RunResult
    from .suite import Scenario

__all__ = ["COST_FIELDS", "EvaluationResult", "summarize"]

#: The per-agent counters summed into a side's cost.
COST_FIELDS = ("wakes", "calls", "invalid_calls", "rejected_actions", "timeouts", "undone_turns", "forfeits",
               "truncated", "llm_calls", "input_tokens", "output_tokens")


@dataclass
class EvaluationResult:
    """``scenarios``: one row per scenario and mode. ``modes``, ``tags``, ``splits`` (``in_sample`` / ``held_out``,
    when the suite holds scenarios out) and ``overall`` pool the run pairs they cover. Every pool has ``n`` scored
    pairs, ``focal``, ``baseline`` and ``difference`` (focal − baseline) estimates with 95% intervals, ``clear``
    (the interval excludes zero), ``unscored`` pairs and ``cost`` per side. ``pairs`` holds every run pair, and
    ``results`` every run: pair *i* is ``results[2i]`` (focal) and ``results[2i + 1]`` (baseline)."""

    focal: str
    runs: int
    seed: int
    scenarios: List[Dict[str, Any]]
    modes: Dict[str, Dict[str, Any]]
    tags: Dict[str, Dict[str, Any]]
    splits: Dict[str, Dict[str, Any]]
    overall: Dict[str, Any]
    pairs: List[Dict[str, Any]]
    notes: List[str] = field(default_factory=list)
    results: List["RunResult"] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Evaluation of {self.focal}: {len({row['scenario'] for row in self.scenarios})} scenario(s), "
                 f"{self.runs} run(s) each (seed {self.seed}); a score is the mean over the focal seats, "
                 "compared with the baseline in the same seats on the same seed"]
        head = ["focal [95% CI]", "baseline [95% CI]", "focal − baseline [95% CI]", ""]
        lines += _columns([["scenario", "mode", "seats"] + head] + [
            [row["scenario"] + (" (held out)" if row["held_out"] else ""), row["mode"],
             f"{row['focal_seats']}/{len(row['seats'])}"] + _cells(row) for row in self.scenarios])
        for title, pools in (("By mode", self.modes), ("By tag", self.tags), ("Held out vs in sample", self.splits)):
            if len(pools) > 1 or (pools and title == "By tag"):
                lines.append(f"{title}:")
                lines += _columns([[""] + head] + [[name] + _cells(pool) for name, pool in pools.items()])
        lines.append("Overall: " + "  ".join(cell for cell in (
            f"focal {_estimate(self.overall['focal'])}", f"baseline {_estimate(self.overall['baseline'])}",
            f"difference {_estimate(self.overall['difference'])}", _verdict(self.overall)) if cell))
        lines += _cost_lines(self.overall["cost"])
        lines += [f"note: {note}" for note in self.notes]
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {"focal": self.focal, "runs": self.runs, "seed": self.seed, "scenarios": self.scenarios,
                "modes": self.modes, "tags": self.tags, "splits": self.splits, "overall": self.overall,
                "pairs": self.pairs, "notes": self.notes,
                "results": [r.to_dict(events=bool(r.exposures)) for r in self.results]}


def summarize(cases: Sequence["Scenario"], pairs: Sequence[Dict[str, Any]], *, focal: str, runs: int,
              seed: int, results: Sequence["RunResult"] = ()) -> EvaluationResult:
    by_name = {case.name: case for case in cases}
    rows = []
    for case in cases:
        for mode in case.modes:
            chosen = [p for p in pairs if p["scenario"] == case.name and p["mode"] == mode]
            seat_scores: Dict[str, List[float]] = {seat: [] for seat in case.seats}
            for pair in chosen:
                for seat, value in (pair["seat_scores"] or {}).items():
                    seat_scores[seat].append(value)
            rows.append({"scenario": case.name, "mode": mode, "tags": list(case.tags), "held_out": case.held_out,
                         "seats": list(case.seats), "focal_seats": case.focal_seats(mode), **_pool(chosen),
                         "seat_scores": {seat: estimate(values).to_dict() for seat, values in seat_scores.items()}})
    modes = {mode: _pool([p for p in pairs if p["mode"] == mode])
             for mode in dict.fromkeys(m for case in cases for m in case.modes)}
    tags = {tag: _pool([p for p in pairs if tag in by_name[p["scenario"]].tags])
            for tag in dict.fromkeys(t for case in cases for t in case.tags)}
    held = [p for p in pairs if by_name[p["scenario"]].held_out]
    splits = {"in_sample": _pool([p for p in pairs if not by_name[p["scenario"]].held_out]), "held_out": _pool(held)} \
        if held else {}
    unscored = [p for p in pairs if p["difference"] is None]
    notes = [f"{len(unscored)} of {len(pairs)} run pair(s) were left out (first: {unscored[0]['note']})"] if unscored else []
    return EvaluationResult(focal, runs, seed, rows, modes, tags, splits, _pool(pairs), list(pairs), notes, list(results))


def _pool(pairs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    scored = [p for p in pairs if p["difference"] is not None]
    difference = estimate([p["difference"] for p in scored])
    return {"n": len(scored), "focal": estimate([p["focal"] for p in scored]).to_dict(),
            "baseline": estimate([p["baseline"] for p in scored]).to_dict(), "difference": difference.to_dict(),
            "clear": difference.excludes_zero, "unscored": len(pairs) - len(scored),
            "cost": {side: _cost([p["cost"][side] for p in pairs]) for side in ("focal", "baseline")}}


def _cost(bills: Sequence[Mapping[str, int]]) -> Dict[str, Any]:
    total: Dict[str, Any] = {key: sum(bill[key] for bill in bills) for key in COST_FIELDS}
    total["invalid_rate"] = round(total["invalid_calls"] / total["calls"], 3) if total["calls"] else 0.0
    return total


def _cells(pool: Mapping[str, Any]) -> List[str]:
    return [_estimate(pool["focal"]), _estimate(pool["baseline"]), _estimate(pool["difference"]), _verdict(pool)]


def _verdict(pool: Mapping[str, Any]) -> str:
    if pool["difference"].get("low") is None:
        return ""
    return "clear" if pool["clear"] else "within noise"


def _cost_lines(cost: Mapping[str, Mapping[str, Any]]) -> List[str]:
    table = [["cost (focal seats)", "turns", "tool calls", "invalid", "timeouts", "LLM calls", "tokens in", "tokens out"]]
    for side in ("focal", "baseline"):
        c: Optional[Mapping[str, Any]] = cost.get(side)
        if c is None:
            continue
        table.append([side, str(c["wakes"]), str(c["calls"]), f"{c['invalid_calls']} ({c['invalid_rate']:.0%})",
                      str(c["timeouts"]), str(c["llm_calls"]), f"{c['input_tokens']:,}", f"{c['output_tokens']:,}"])
    return _columns(table)
