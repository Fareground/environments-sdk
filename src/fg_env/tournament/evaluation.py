"""Rankings that hold up when skill is not transitive (rock beats scissors beats paper beats rock).

All three read the empirical meta-game: the margin matrix ``A[i][j]`` = (i's wins − i's losses) / games
between i and j, which is antisymmetric and between −1 and 1 (0 for pairs that never met).

* :func:`nash_average` (Balduzzi et al., 2018): the maximum-entropy Nash equilibrium of the zero-sum game
  "pick an entrant, earn the margin against the opponent's pick", and each entrant's payoff against it.
  Entrants in the equilibrium score 0; the rest score how badly the equilibrium mixture beats them.
  Adding a copy of an entrant changes nothing: the copies share its weight.
* :func:`alpha_rank` (Omidshafiei et al., 2019): the long-run share of a population that keeps adopting
  whichever entrant beats the current one, with strong but finite selection.
* :func:`schulze`: each game is a ballot ranking its seats; Schulze's method turns the pairwise
  preferences into an order that respects every clear majority (a Condorcet method).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from ..stdlib.linalg import eliminate

__all__ = ["margins", "nash_average", "alpha_rank", "schulze"]

Matrix = List[List[float]]

#: Numbers this close to zero are zero (pivots, reduced costs, equilibrium weights).
_EPSILON = 1e-9
#: The maximum-entropy search stops once every equilibrium condition holds to this tolerance.
_KKT_TOLERANCE = 1e-10
_NEWTON_ITERATIONS = 200
#: Share of the predicted decrease a Newton step must achieve to be accepted.
_ARMIJO = 1e-4
#: α-Rank population size and selection intensity (margins lie in [−1, 1], so m·α·Δ reaches 1,000: strong selection).
ALPHA_RANK_POPULATION, ALPHA_RANK_ALPHA = 50, 10.0


def margins(entrants: Sequence[str], head_to_head: Mapping[str, Mapping[str, Mapping[str, int]]]) -> Matrix:
    """The antisymmetric margin matrix from head-to-head records (``wins``, ``draws``, ``losses``)."""
    out = [[0.0] * len(entrants) for _ in entrants]
    for i, a in enumerate(entrants):
        for j, b in enumerate(entrants):
            if i != j:
                record = head_to_head[a][b]
                played = record["wins"] + record["draws"] + record["losses"]
                out[i][j] = (record["wins"] - record["losses"]) / played if played else 0.0
    return out


def _simplex_max(objective: Sequence[float], rows: Matrix, bounds: Sequence[float]) -> float:
    """max objective·x subject to rows·x ≤ bounds, x ≥ 0, for bounds ≥ 0 (the slack basis is feasible).
    Bland's rule, so degenerate problems terminate. The problems here are bounded."""
    m, n = len(rows), len(objective)
    table = [list(row) + [1.0 if k == i else 0.0 for k in range(m)] + [bounds[i]] for i, row in enumerate(rows)]
    costs = [-v for v in objective] + [0.0] * (m + 1)
    basis = [n + i for i in range(m)]
    while True:
        entering = next((j for j in range(n + m) if costs[j] < -_EPSILON), None)
        if entering is None:
            return costs[-1]
        candidates = [(table[i][-1] / table[i][entering], basis[i], i) for i in range(m) if table[i][entering] > _EPSILON]
        _, _, row = min(candidates)
        lead = table[row][entering]
        table[row] = [v / lead for v in table[row]]
        for i in range(m):
            factor = table[i][entering]
            if i != row and factor != 0.0:
                table[i] = [v - factor * p for v, p in zip(table[i], table[row])]
        factor = costs[entering]
        costs = [v - factor * p for v, p in zip(costs, table[row])]
        basis[row] = entering


def _support(payoff: Matrix) -> List[int]:
    """Strategies used by some equilibrium: j is in it when max p_j over {p ≥ 0, A·p ≤ 0, Σp ≤ 1} is above 0."""
    n = len(payoff)
    rows = [list(row) for row in payoff] + [[1.0] * n]
    bounds = [0.0] * n + [1.0]
    return [j for j in range(n) if _simplex_max([1.0 if k == j else 0.0 for k in range(n)], rows, bounds) > _EPSILON]


def _max_entropy(payoff: Matrix, support: Sequence[int]) -> List[float]:
    """The maximum-entropy p on ``support`` with A·p = 0 on the support and A·p ≤ 0 elsewhere.

    Solved in the dual: p_i ∝ exp((A·ν)_i) over the support, minimizing log Σ exp(A·ν) with ν free on the
    support and ν ≥ 0 off it. The equilibrium weights on the support are all positive, so the dual optimum is
    finite and a damped, bound-respecting Newton method reaches it quickly."""
    n = len(payoff)
    inside = set(support)
    nu = [0.0] * n

    def weights(values: Sequence[float]) -> Tuple[List[float], float]:
        exponents = {i: math.fsum(payoff[i][k] * values[k] for k in range(n)) for i in support}
        top = max(exponents.values())
        total = math.fsum(math.exp(e - top) for e in exponents.values())
        p = [math.exp(exponents[i] - top) / total if i in inside else 0.0 for i in range(n)]
        return p, top + math.log(total)

    for _ in range(_NEWTON_ITERATIONS):
        p, value = weights(nu)
        grad = [math.fsum(p[i] * payoff[i][k] for i in support) for k in range(n)]
        fixed = {k for k in range(n) if k not in inside and nu[k] <= 0.0 and grad[k] >= 0.0}
        residual = max((abs(grad[k]) for k in range(n) if k not in fixed), default=0.0)
        if residual < _KKT_TOLERANCE:
            break
        free = [k for k in range(n) if k not in fixed]
        hessian = [[math.fsum(p[i] * payoff[i][k] * payoff[i][l] for i in support) - grad[k] * grad[l] for l in free]
                   for k in free]
        ridge = _EPSILON * (1.0 + max(abs(hessian[r][r]) for r in range(len(free))))
        for r in range(len(free)):
            hessian[r][r] += ridge
        solved = eliminate(hessian, [[-grad[k]] for k in free])
        direction = [0.0] * n
        for r, k in enumerate(free):
            direction[k] = solved[r][0] if solved is not None else -grad[k]
        fraction = 1.0
        while True:  # backtrack until the projected step decreases the dual enough (Armijo)
            candidate = [v + fraction * d if k in inside else max(0.0, v + fraction * d)
                         for k, (v, d) in enumerate(zip(nu, direction))]
            predicted = math.fsum(g * (c - v) for g, c, v in zip(grad, candidate, nu))
            if weights(candidate)[1] <= value + _ARMIJO * predicted or fraction < _KKT_TOLERANCE:
                break
            fraction /= 2.0
        nu = candidate
    return weights(nu)[0]


def nash_average(entrants: Sequence[str], payoff: Matrix) -> Dict[str, Any]:
    """The maximum-entropy Nash equilibrium of the margin game and every entrant's payoff against it."""
    support = _support(payoff)
    p = _max_entropy(payoff, support)
    ratings = [math.fsum(payoff[j][i] * p[i] for i in range(len(p))) for j in range(len(p))]
    return {"equilibrium": {name: p[i] for i, name in enumerate(entrants)},
            "rating": {name: ratings[i] for i, name in enumerate(entrants)},
            "exploitability": max(0.0, max(ratings))}


def _fixation(delta: float, alpha: float, population: int) -> float:
    """Probability that one mutant with fitness advantage ``delta`` takes over a population of ``population``."""
    if delta == 0.0:
        return 1.0 / population
    if delta > 0.0:
        return -math.expm1(-alpha * delta) / -math.expm1(-population * alpha * delta)
    return math.exp((population - 1) * alpha * delta) * math.expm1(alpha * delta) / math.expm1(population * alpha * delta)


def alpha_rank(entrants: Sequence[str], payoff: Matrix, alpha: float = ALPHA_RANK_ALPHA,
               population: int = ALPHA_RANK_POPULATION) -> Dict[str, float]:
    """Stationary mass of each entrant in the single-population α-Rank Markov chain (sums to 1)."""
    k = len(entrants)
    if k == 1:
        return {entrants[0]: 1.0}
    chain = [[0.0] * k for _ in range(k)]
    for s in range(k):
        for r in range(k):
            if r != s:
                chain[s][r] = _fixation(payoff[r][s] - payoff[s][r], alpha, population) / (k - 1)
        chain[s][s] = 1.0 - math.fsum(chain[s])
    # π (C − I) = 0 with Σπ = 1: transpose, and replace the last equation by the normalization.
    system = [[chain[r][c] - (1.0 if r == c else 0.0) for r in range(k)] for c in range(k)]
    system[-1] = [1.0] * k
    solved = eliminate(system, [[0.0] for _ in range(k - 1)] + [[1.0]])
    mass = [max(0.0, row[0]) for row in solved] if solved is not None else [1.0 / k] * k
    total = math.fsum(mass)
    return {name: mass[i] / total for i, name in enumerate(entrants)}


def schulze(entrants: Sequence[str], preferred: Mapping[str, Mapping[str, int]]) -> List[Dict[str, Any]]:
    """Schulze order from ``preferred[a][b]`` = ballots ranking a above b. Entrants that no path separates share a rank."""
    names = list(entrants)
    strength = {a: {b: (preferred[a][b] if preferred[a][b] > preferred[b][a] else 0) for b in names if b != a}
                for a in names}
    for via in names:
        for a in names:
            if a == via:
                continue
            for b in names:
                if b in (a, via):
                    continue
                strength[a][b] = max(strength[a][b], min(strength[a][via], strength[via][b]))
    beats = {a: sum(1 for b in names if b != a and strength[a][b] > strength[b][a]) for a in names}
    order = sorted(names, key=lambda a: (-beats[a], names.index(a)))
    rows: List[Dict[str, Any]] = []
    for position, name in enumerate(order):
        tied = position > 0 and beats[name] == beats[order[position - 1]] and \
            strength[name][order[position - 1]] == strength[order[position - 1]][name]
        rank = rows[-1]["rank"] if tied else position + 1
        rows.append({"rank": rank, "entrant": name, "beats": beats[name]})
    return rows
