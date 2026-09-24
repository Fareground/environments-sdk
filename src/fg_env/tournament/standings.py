"""Standings: results, ratings, returns, costs and head-to-head records accumulated round by round."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..analysis.stats import estimate
from ..runtime.facts import Stats
from .evaluation import alpha_rank, margins, nash_average, schulze
from .ratings import EloRating, Glicko, Pairing, elo_mle, glicko2_period, pairwise

__all__ = ["RATINGS", "POINTS", "outcomes", "Ledger"]

RATINGS = ("elo", "glicko2")
#: Standings points for each outcome; a bye counts as a win.
POINTS = {"win": 1.0, "draw": 0.5, "loss": 0.0}
#: The counters of :class:`Stats` summed into each entrant's cost.
COST_FIELDS = tuple(Stats.__dataclass_fields__)


def outcomes(scores: Mapping[str, float]) -> dict[str, str]:
    """Each seat's outcome: the only top score wins, a shared top score draws (so does everyone equal), the rest lose.
    """
    top = max(scores.values())
    leaders = sum(1 for value in scores.values() if value == top)
    return {seat: ("win" if leaders == 1 else "draw") if value == top else "loss" for seat, value in scores.items()}


class Ledger:
    """Running totals of a tournament. Ratings are refitted, and Glicko-2 moves one period, after each round of play."""

    def __init__(self, entrants: Sequence[str], seats: Sequence[str]):
        self.entrants = list(entrants)
        self.seats = list(seats)
        self.pairs: list[Pairing] = []
        self.elo: dict[str, EloRating] = elo_mle(self.entrants, [])
        self.glicko: dict[str, Glicko] = {name: Glicko() for name in entrants}
        self.counts: dict[str, dict[str, int]] = {
            name: {"played": 0, "wins": 0, "draws": 0, "losses": 0, "byes": 0, "unscored": 0, "failed": 0}
            for name in entrants}
        self.scores: dict[str, list[float]] = {name: [] for name in entrants}
        self.seat_scores: dict[str, dict[str, list[float]]] = {name: {seat: [] for seat in seats} for name in entrants}
        self.seat_points: dict[str, list[float]] = {seat: [] for seat in seats}
        self.head_to_head: dict[str, dict[str, dict[str, int]]] = {
            a: {b: {"wins": 0, "draws": 0, "losses": 0} for b in entrants if b != a} for a in entrants}
        self.cost: dict[str, dict[str, int]] = {name: {key: 0 for key in COST_FIELDS} for name in entrants}
        self.met: dict[frozenset[str], int] = {}

    @property
    def byes(self) -> dict[str, int]:
        return {name: counts["byes"] for name, counts in self.counts.items()}

    def points(self, name: str) -> float:
        c = self.counts[name]
        return c["wins"] * POINTS["win"] + c["draws"] * POINTS["draw"] + c["byes"] * POINTS["win"]

    def bye(self, name: str) -> None:
        self.counts[name]["byes"] += 1

    def record_round(self, games: Sequence[Mapping[str, Any]]) -> None:
        """Add one round of game records (``seating``, ``status``, ``scores``, ``outcomes``, ``stats`` per seat)."""
        period: list[Pairing] = []
        for game in games:
            seating: Mapping[str, str] = game["seating"]
            table = [seating[seat] for seat in self.seats]
            for i, a in enumerate(table):
                for b in table[i + 1:]:
                    self.met[frozenset((a, b))] = self.met.get(frozenset((a, b)), 0) + 1
            for seat, stats in game["stats"].items():
                bill = self.cost[seating[seat]]
                for key in COST_FIELDS:
                    bill[key] += stats.get(key, 0)
            if game["scores"] is None:
                for name in table:
                    self.counts[name]["failed" if game["status"] == "failed" else "unscored"] += 1
                continue
            period += self._scored(seating, game["scores"], game["outcomes"])
        self.pairs += period
        self.elo = elo_mle(self.entrants, self.pairs)
        self.glicko = glicko2_period(self.glicko, period)

    def _scored(self, seating: Mapping[str, str], scores: Mapping[str, float],
                results: Mapping[str, str]) -> list[Pairing]:
        by_entrant: list[tuple[str, float]] = [(seating[seat], scores[seat]) for seat in self.seats]
        for seat, (name, value) in zip(self.seats, by_entrant):
            outcome = results[seat]
            self.counts[name]["played"] += 1
            self.counts[name]["losses" if outcome == "loss" else outcome + "s"] += 1
            self.scores[name].append(value)
            self.seat_scores[name][seat].append(value)
            self.seat_points[seat].append(POINTS[outcome])
        pairs = pairwise(by_entrant)
        for a, b, result in pairs:
            label = "wins" if result == 1.0 else "draws" if result == 0.5 else "losses"
            self.head_to_head[a][b][label] += 1
            self.head_to_head[b][a][{"wins": "losses", "draws": "draws", "losses": "wins"}[label]] += 1
        return pairs

    def rating(self, name: str, kind: str) -> float:
        return self.elo[name].rating if kind == "elo" else self.glicko[name].rating

    def ranked(self) -> list[str]:
        """Entrants by points, then Elo, then the order they were given (the Swiss pairing order)."""
        order = {name: i for i, name in enumerate(self.entrants)}
        return sorted(self.entrants, key=lambda n: (-self.points(n), -self.elo[n].rating, order[n]))

    def standings(self, kind: str) -> list[dict[str, Any]]:
        order = {name: i for i, name in enumerate(self.entrants)}
        names = sorted(self.entrants, key=lambda n: (-self.rating(n, kind), -self.points(n), order[n]))
        rows = []
        for rank, name in enumerate(names, 1):
            rows.append({"rank": rank, "entrant": name, "rating": self.rating(name, kind),
                         "elo": self.elo[name].to_dict(),
                         "glicko2": self.glicko[name].to_dict(), "points": self.points(name), **self.counts[name],
                         "score": estimate(self.scores[name]).to_dict(), "cost": self._bill(name)})
        return rows

    def _bill(self, name: str) -> dict[str, Any]:
        bill: dict[str, Any] = dict(self.cost[name])
        bill["invalid_rate"] = bill["invalid_calls"] / bill["calls"] if bill["calls"] else 0.0
        return bill

    def returns(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Every entrant's score in every seat (mean with a 95% interval)."""
        return {name: {seat: estimate(values).to_dict() for seat, values in seats.items()}
                for name, seats in self.seat_scores.items()}

    def evaluation(self) -> dict[str, Any]:
        payoff = margins(self.entrants, self.head_to_head)
        preferred = {a: {b: self.head_to_head[a][b]["wins"] for b in self.entrants if b != a} for a in self.entrants}
        return {"margins": {a: {b: payoff[i][j] for j, b in enumerate(self.entrants)}
                            for i, a in enumerate(self.entrants)},
                "nash_average": nash_average(self.entrants, payoff), "alpha_rank": alpha_rank(self.entrants, payoff),
                "votes": schulze(self.entrants, preferred)}
