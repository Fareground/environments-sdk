"""Search methods over a decision space, each spending at most ``budget`` distinct decisions.

A method never ranks candidates itself beyond what it needs to move: it asks ``score(points, runs)`` for their
ranking keys (lower is better; the scorer runs them on the first ``runs`` shared seeds and raises
:class:`~.optimize.BudgetExhausted` once the budget is spent). The optimiser picks the winner from everything scored.

* ``grid`` — every decision on the steps (continuous ranges cut into levels): exhaustive, only for small spaces.
* ``random`` / ``lhs`` — the start plus uniform or Latin-hypercube samples: a broad look at large spaces, no refinement.
* ``local`` — coordinate descent from the start with step halving, then paired moves along constraints: for integers
  and vectors; finds a local optimum.
* ``race`` — successive halving: many sampled decisions on a few seeds, the better half kept and given twice the
  seeds, until the survivors have every seed: spends runs where they matter on noisy objectives.
* ``nelder_mead`` / ``cross_entropy`` — calibration's simplex and cross-entropy searches (:mod:`.optimize`), on a
  penalised objective: for a few continuous decisions.
"""
from __future__ import annotations

import math
import random
from typing import Callable, List, Sequence, Tuple

from . import optimize
from .decisions import DecisionSpace, Point
from .optimize import BudgetExhausted
from .stats import latin_hypercube

__all__ = ["METHODS", "grid", "sampled", "local", "race", "simplex", "cross_entropy"]

Key = Tuple[int, float]
Score = Callable[[Sequence[Point], int], List[Key]]
Loss = Callable[[Point], float]

METHODS = ("grid", "random", "lhs", "local", "race", "nelder_mead", "cross_entropy")
#: Decisions left when a race stops halving.
_RACE_SURVIVORS = 2
#: Fewest seeds a race judges a decision on in its first round.
_RACE_FIRST_RUNS = 2


def grid(space: DecisionSpace, score: Score, budget: int, runs: int) -> None:
    size = space.grid_size()
    if size > budget:
        raise ValueError(f"the grid holds {size:,} decisions but the budget is {budget}: raise budget, give coarser "
                         "steps, or use method 'lhs', 'local' or 'race'")
    score(space.grid(), runs)


def _samples(space: DecisionSpace, count: int, rng: random.Random, design: str) -> List[Point]:
    units = latin_hypercube(count, space.dims, rng) if design == "lhs" and count > 0 else \
        [[rng.random() for _ in range(space.dims)] for _ in range(count)]
    seen = {space.start(): None}
    for unit in units:
        seen.setdefault(space.from_unit(unit), None)
    return list(seen)


def sampled(space: DecisionSpace, score: Score, budget: int, runs: int, rng: random.Random, design: str) -> None:
    try:
        score(_samples(space, budget - 1, rng, design), runs)
    except BudgetExhausted:
        return


def _descend(space: DecisionSpace, score: Score, runs: int, current: Point, best: Key,
             moves_for: Callable[[Point, int], List[Point]]) -> Tuple[Point, Key, bool]:
    """One pass over the coordinates: the best of each coordinate's moves replaces the current decision if better."""
    improved = False
    for i in range(space.dims):
        moves = moves_for(current, i)
        if not moves:
            continue
        keys = score(moves, runs)
        j = min(range(len(moves)), key=keys.__getitem__)
        if keys[j] < best:
            current, best, improved = moves[j], keys[j], True
    return current, best, improved


def local(space: DecisionSpace, score: Score, budget: int, runs: int) -> None:
    """Coordinate descent: try each coordinate's moves, keep the best if it improves, halve the steps when a full
    pass improves nothing; at the smallest steps, try moving two coordinates in opposite directions before stopping
    (along a constraint, one coordinate can only give way if another takes up the slack)."""
    axes = space.axes
    current = space.start()
    try:
        best = score([current], runs)[0]
        steps = [axis.first_step() for axis in axes]
        while True:
            current, best, improved = _descend(space, score, runs, current, best,
                                               lambda p, i: space.moves(p, i, steps[i]))
            if improved:
                continue
            if any(step > axis.last_step() + 1e-12 for step, axis in zip(steps, axes)):
                steps = [axis.halve(step) if step > axis.last_step() + 1e-12 else step
                         for step, axis in zip(steps, axes)]
                continue
            current, best, improved = _descend(space, score, runs, current, best,
                                               lambda p, i: space.pair_moves(p, i, steps))
            if not improved:
                return
    except BudgetExhausted:
        return


def race(space: DecisionSpace, score: Score, budget: int, runs: int, rng: random.Random) -> None:
    """Successive halving over the start and ``budget - 1`` Latin-hypercube decisions."""
    alive = _samples(space, budget - 1, rng, "lhs")[:budget]
    halvings = max(0, math.ceil(math.log2(max(1, len(alive) / _RACE_SURVIVORS))))
    count = min(runs, max(_RACE_FIRST_RUNS, runs >> halvings))
    while True:
        keys = score(alive, count)
        order = sorted(range(len(alive)), key=keys.__getitem__)
        if len(alive) <= _RACE_SURVIVORS or count >= runs:
            if count < runs:
                score([alive[i] for i in order[:_RACE_SURVIVORS]], runs)
            return
        alive = [alive[i] for i in order[:max(_RACE_SURVIVORS, len(alive) // 2)]]
        count = min(runs, count * 2)


def _evaluator(space: DecisionSpace, loss: Loss, budget: int) -> optimize.Evaluator:
    return optimize.Evaluator(lambda unit: (loss(space.from_unit(unit)), None), key=space.from_unit, budget=budget)


def simplex(space: DecisionSpace, loss: Loss, budget: int) -> None:
    try:
        optimize.nelder_mead(_evaluator(space, loss, budget), space.dims, start=space.to_unit(space.start()))
    except BudgetExhausted:
        return


def cross_entropy(space: DecisionSpace, loss: Loss, budget: int, rng: random.Random) -> None:
    population, elite, generations = optimize.cross_entropy_sizes(space.dims, budget)
    try:
        optimize.cross_entropy(_evaluator(space, loss, budget), space.dims, rng, population, elite, generations)
    except BudgetExhausted:
        return
