"""Skill ratings from pairwise results: Elo fitted by maximum likelihood, and Glicko-2 by rating period.

A game between several seats counts as every pair of seats meeting once: the higher score beats the
lower, equal scores draw. Elo is the Bradley–Terry model on the Elo scale (a 400-point gap is 10:1
odds) with a draw worth half a win, fitted to all results at once, so the order games were played in
does not matter. Each entrant also carries one virtual draw against a 1500-rated anchor: an entrant
that won every game gets a high, finite rating with a wide interval instead of an infinite one, and
the prior's weight fades as real games accumulate. Intervals come from the curvature of the
likelihood at the fit (Wald). Glicko-2 (Glickman, 2012) rates a period of games at once and reports
how uncertain each rating still is (RD).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, List, Mapping, Sequence, Tuple

from ..analysis.stats import normal_quantile
from ..stdlib.linalg import eliminate

__all__ = ["ELO_START", "ELO_PRIOR_DRAWS", "EloRating", "Glicko", "Pairing", "pairwise", "elo_mle", "glicko2_period"]

#: The anchor's rating (and every entrant's before any game).
ELO_START = 1500.0
#: Virtual draws each entrant plays against the anchor.
ELO_PRIOR_DRAWS = 1.0
#: Elo points per unit of log-odds.
_ELO_SCALE = 400.0 / math.log(10.0)
#: Newton iterations and the step (in log-odds) below which the fit has converged.
_NEWTON_ITERATIONS, _NEWTON_TOLERANCE = 100, 1e-10
#: Glicko-2 starting rating, rating deviation and volatility, and the volatility constraint τ (Glickman, 2012).
GLICKO_START, GLICKO_RD, GLICKO_VOLATILITY, GLICKO_TAU = 1500.0, 350.0, 0.06, 0.5
#: Converts between the Glicko scale and the Glicko-2 internal scale.
_GLICKO_SCALE = 173.7178
#: Convergence tolerance of the volatility iteration.
_VOLATILITY_EPSILON = 1e-6

#: One pairwise result: (entrant, opponent, entrant's score: 1 win, 0.5 draw, 0 loss).
Pairing = Tuple[str, str, float]


def pairwise(scores: Sequence[Tuple[str, float]]) -> List[Pairing]:
    """Every pair of seats in one game as a result for the first of the pair (by score)."""
    out: List[Pairing] = []
    for i, (a, score_a) in enumerate(scores):
        for b, score_b in scores[i + 1:]:
            out.append((a, b, 1.0 if score_a > score_b else 0.5 if score_a == score_b else 0.0))
    return out


@dataclass(frozen=True)
class EloRating:
    """A maximum-likelihood Elo rating with its standard error and 95% interval."""

    rating: float
    se: float
    low: float
    high: float

    def to_dict(self) -> Dict[str, float]:
        return {"rating": self.rating, "se": self.se, "low": self.low, "high": self.high}


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _log_sigmoid(x: float) -> float:
    return -math.log1p(math.exp(-x)) if x >= 0 else x - math.log1p(math.exp(x))


def elo_mle(entrants: Sequence[str], games: Sequence[Pairing], prior_draws: float = ELO_PRIOR_DRAWS) -> Dict[str, EloRating]:
    """Elo ratings that make the observed results most likely (see the module notes for the model)."""
    if prior_draws <= 0:
        raise ValueError(f"prior_draws must be above 0 (it keeps ratings finite), got {prior_draws}")
    index = {name: i for i, name in enumerate(entrants)}
    totals: Dict[Tuple[int, int], List[float]] = {}  # (i, j) with i < j → [games, i's points]
    for a, b, score in games:
        i, j = index[a], index[b]
        key, points = ((i, j), score) if i < j else ((j, i), 1.0 - score)
        cell = totals.setdefault(key, [0.0, 0.0])
        cell[0] += 1.0
        cell[1] += points
    n = len(entrants)

    def log_posterior(theta: Sequence[float]) -> float:
        total = math.fsum(prior_draws * 0.5 * (_log_sigmoid(t) + _log_sigmoid(-t)) for t in theta)
        for (i, j), (count, points) in totals.items():
            d = theta[i] - theta[j]
            total += points * _log_sigmoid(d) + (count - points) * _log_sigmoid(-d)
        return total

    def derivatives(theta: Sequence[float]) -> Tuple[List[float], List[List[float]]]:
        """Gradient of the log posterior and its negated Hessian (positive definite)."""
        grad = [prior_draws * (0.5 - _sigmoid(t)) for t in theta]
        curvature = [[0.0] * n for _ in range(n)]
        for i, t in enumerate(theta):
            p = _sigmoid(t)
            curvature[i][i] = prior_draws * p * (1.0 - p)
        for (i, j), (count, points) in totals.items():
            p = _sigmoid(theta[i] - theta[j])
            grad[i] += points - count * p
            grad[j] -= points - count * p
            w = count * p * (1.0 - p)
            curvature[i][i] += w
            curvature[j][j] += w
            curvature[i][j] -= w
            curvature[j][i] -= w
        return grad, curvature

    theta = [0.0] * n
    for _ in range(_NEWTON_ITERATIONS):
        grad, curvature = derivatives(theta)
        solved = eliminate(curvature, [[g] for g in grad])
        if solved is None:
            break
        step = [row[0] for row in solved]
        current, fraction = log_posterior(theta), 1.0
        candidate = [t + s for t, s in zip(theta, step)]
        while log_posterior(candidate) < current and fraction > _NEWTON_TOLERANCE:
            fraction /= 2.0
            candidate = [t + fraction * s for t, s in zip(theta, step)]
        theta = candidate
        if max((abs(fraction * s) for s in step), default=0.0) < _NEWTON_TOLERANCE:
            break
    _, curvature = derivatives(theta)
    covariance = eliminate(curvature, [[1.0 if r == c else 0.0 for c in range(n)] for r in range(n)])
    z = normal_quantile(0.975)
    out = {}
    for name, i in index.items():
        rating = ELO_START + theta[i] * _ELO_SCALE
        se = math.sqrt(max(covariance[i][i], 0.0)) * _ELO_SCALE if covariance is not None else math.inf
        out[name] = EloRating(rating, se, rating - z * se, rating + z * se)
    return out


@dataclass(frozen=True)
class Glicko:
    """A Glicko-2 rating: ``rating`` ± about 2 × ``rd`` is a 95% interval; ``volatility`` is how erratic results are."""

    rating: float = GLICKO_START
    rd: float = GLICKO_RD
    volatility: float = GLICKO_VOLATILITY

    def to_dict(self) -> Dict[str, float]:
        return {"rating": self.rating, "rd": self.rd, "volatility": self.volatility}


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _new_volatility(sigma: float, phi: float, v: float, delta: float, tau: float) -> float:
    """Step 5 of Glickman's algorithm: the Illinois method on f(x) = 0 with x = ln σ²."""
    a = math.log(sigma * sigma)

    def f(x: float) -> float:
        ex = math.exp(x)
        return ex * (delta * delta - phi * phi - v - ex) / (2.0 * (phi * phi + v + ex) ** 2) - (x - a) / (tau * tau)

    low = a
    if delta * delta > phi * phi + v:
        high = math.log(delta * delta - phi * phi - v)
    else:
        step = 1
        while f(a - step * tau) < 0:
            step += 1
        high = a - step * tau
    f_low, f_high = f(low), f(high)
    while abs(high - low) > _VOLATILITY_EPSILON:
        middle = low + (low - high) * f_low / (f_high - f_low)
        f_middle = f(middle)
        if f_middle * f_high < 0:
            low, f_low = high, f_high
        else:
            f_low /= 2.0
        high, f_high = middle, f_middle
    return math.exp(low / 2.0)


def _update(player: Glicko, results: Sequence[Tuple[Glicko, float]], tau: float) -> Glicko:
    mu, phi = (player.rating - GLICKO_START) / _GLICKO_SCALE, player.rd / _GLICKO_SCALE
    if not results:
        return replace(player, rd=math.sqrt(phi * phi + player.volatility ** 2) * _GLICKO_SCALE)
    inverse_v, gain = 0.0, 0.0
    for opponent, score in results:
        mu_j, phi_j = (opponent.rating - GLICKO_START) / _GLICKO_SCALE, opponent.rd / _GLICKO_SCALE
        g = _g(phi_j)
        expected = 1.0 / (1.0 + math.exp(-g * (mu - mu_j)))
        inverse_v += g * g * expected * (1.0 - expected)
        gain += g * (score - expected)
    v = 1.0 / inverse_v
    sigma = _new_volatility(player.volatility, phi, v, v * gain, tau)
    phi_star = math.sqrt(phi * phi + sigma * sigma)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_new = mu + phi_new * phi_new * gain
    return Glicko(mu_new * _GLICKO_SCALE + GLICKO_START, phi_new * _GLICKO_SCALE, sigma)


def glicko2_period(players: Mapping[str, Glicko], games: Sequence[Pairing], tau: float = GLICKO_TAU) -> Dict[str, Glicko]:
    """Every player's rating after one rating period of pairwise ``games`` (all rated from the ratings before it).
    A player without games keeps its rating while its RD grows."""
    results: Dict[str, List[Tuple[Glicko, float]]] = {name: [] for name in players}
    for a, b, score in games:
        results[a].append((players[b], score))
        results[b].append((players[a], 1.0 - score))
    return {name: _update(player, results[name], tau) for name, player in players.items()}
