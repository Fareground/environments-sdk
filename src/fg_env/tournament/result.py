"""The tournament result: standings, non-transitive rankings, head-to-head, returns by seat, costs, every game."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..runtime.measure import RunResult

__all__ = ["TournamentResult"]


@dataclass
class TournamentResult:
    """``standings`` is best first by ``rating``. ``evaluation`` holds the margin matrix, the Nash average, α-Rank
    and the Schulze vote; ``returns[entrant][seat]`` the score in each seat; ``games`` one record per game or bye."""

    contract: str
    pairing: str
    rating: str
    score: str
    seats: list[str]
    entrants: list[str]
    games_per_seating: int
    standings: list[dict[str, Any]]
    head_to_head: dict[str, dict[str, dict[str, int]]]
    returns: dict[str, dict[str, dict[str, Any]]]
    seat_points: dict[str, dict[str, Any]]
    evaluation: dict[str, Any]
    games: list[dict[str, Any]]
    runs: list[RunResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def standing(self, entrant: str) -> dict[str, Any]:
        for row in self.standings:
            if row["entrant"] == entrant:
                return row
        raise KeyError(f"no entrant '{entrant}' (entrants: {', '.join(self.entrants)})")

    def summary(self) -> str:
        played = sum(1 for g in self.games if "seating" in g)
        lines = [f"Tournament on {self.contract}: {len(self.entrants)} entrants, {self.pairing}, seats "
                 f"{', '.join(self.seats)}; {played} game(s), {self.games_per_seating} per seating; "
                 f"scored by {self.score}; ranked by {'Elo' if self.rating == 'elo' else 'Glicko-2'}"]
        header = ["rank", "entrant", "Elo [95% CI]", "Glicko-2 ± 2RD", "W-D-L", "points", "score [95% CI]"]
        lines += _columns([header] + [
            [str(row["rank"]), row["entrant"],
             f"{row['elo']['rating']:.0f} [{row['elo']['low']:.0f}, {row['elo']['high']:.0f}]",
             f"{row['glicko2']['rating']:.0f} ± {2 * row['glicko2']['rd']:.0f}",
             f"{row['wins']}-{row['draws']}-{row['losses']}", f"{row['points']:g}", _estimate(row["score"])]
            for row in self.standings])
        nash, mass = self.evaluation["nash_average"], self.evaluation["alpha_rank"]
        lines.append("Nash average (equilibrium weight, payoff against it): " + "; ".join(
            f"{name} {nash['equilibrium'][name]:.2f}, {nash['rating'][name]:+.2f}" for name in self.entrants))
        lines.append("α-Rank mass: " + "; ".join(f"{name} {mass[name]:.2f}" for name in self.entrants))
        lines.append("Schulze vote: "
                     + ", ".join(f"{row['rank']}. {row['entrant']}" for row in self.evaluation["votes"]))
        lines.append("Head to head (row's wins-draws-losses against each column):")
        lines += _columns([[""] + self.entrants] + [
            [a]
            + ["—" if a == b else "{wins}-{draws}-{losses}".format(**self.head_to_head[a][b]) for b in self.entrants]
            for a in self.entrants])
        lines.append("Mean score by seat:")
        lines += _columns([[""] + self.seats] + [[name] + [_mean(self.returns[name][seat]) for seat in self.seats]
                                                 for name in self.entrants])
        lines.append("Points per game by seat (win 1, draw ½): " + "; ".join(
            f"{seat} {_estimate(stats)}" for seat, stats in self.seat_points.items()))
        lines += self._cost_lines()
        lines += [f"note: {n}" for n in self.notes]
        return "\n".join(lines)

    def _cost_lines(self) -> list[str]:
        costs = [row["cost"] for row in self.standings]
        if not any(c["calls"] or c["llm_calls"] or c["timeouts"] or c["undone_turns"] for c in costs):
            return []
        tokens = any(c["input_tokens"] or c["output_tokens"] for c in costs)
        timing = any(c["timeouts"] or c["undone_turns"] for c in costs)
        header = ["entrant", "turns", "tool calls", "invalid"] + (["timeouts", "undone turns"] if timing else []) \
            + (["LLM calls", "tokens in", "tokens out"] if tokens else [])
        table = [header]
        for row in self.standings:
            c = row["cost"]
            cells = [row["entrant"], str(c["wakes"]), str(c["calls"]),
                     f"{c['invalid_calls']} ({c['invalid_rate']:.0%})"]
            if timing:
                cells += [str(c["timeouts"]), str(c["undone_turns"])]
            if tokens:
                cells += [str(c["llm_calls"]), f"{c['input_tokens']:,}", f"{c['output_tokens']:,}"]
            table.append(cells)
        return ["Cost per entrant (all its games):"] + _columns(table)

    def to_dict(self) -> dict[str, Any]:
        return {"contract": self.contract, "pairing": self.pairing, "rating": self.rating, "score": self.score,
                "seats": self.seats, "entrants": self.entrants, "games_per_seating": self.games_per_seating,
                "standings": self.standings, "evaluation": self.evaluation, "head_to_head": self.head_to_head,
                "returns": self.returns, "seat_points": self.seat_points, "games": self.games, "notes": self.notes,
                "runs": [r.to_dict(events=bool(r.exposures)) for r in self.runs]}


def _estimate(stats: Mapping[str, Any]) -> str:
    mean: float | None = stats.get("mean")
    if mean is None:
        return "—"
    if stats.get("low") is None:
        return f"{mean:.3g} (n={stats['n']})"
    return f"{mean:.3g} [{stats['low']:.3g}, {stats['high']:.3g}] (n={stats['n']})"


def _mean(stats: Mapping[str, Any]) -> str:
    return "—" if stats.get("mean") is None else f"{stats['mean']:.3g} (n={stats['n']})"


def _columns(rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return ["  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]
