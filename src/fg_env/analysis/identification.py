"""Budgeted local sensitivity diagnostics; never a claim of global or empirical identification."""
from __future__ import annotations

import math
from typing import Any

from .unit_search import Evaluator


def probe_budget(problem: Any, budget: int) -> int:
    """Leave at least half the budget for fitting; unsupported targets are explicit, not approximated."""
    count = len(problem.names)
    supported = all(goal.kind == "value" and not goal.pool for goal in problem.goals)
    continuous = all(problem.contract.inputs[name].type == "number" for name in problem.names)
    return 2 * count if supported and continuous and budget >= 4 * count + 2 else 0


def identify(problem: Any, evaluator: Evaluator, reserved: int) -> dict[str, Any]:
    base = {"status": "not_assessed", "method": "local_finite_difference_rank",
            "parameters": list(problem.names), "evaluations": 0,
            "scope": "local response on training cases and common simulation seeds; not global identification"}
    if not reserved or evaluator.best is None:
        return {**base, "message": "Identification was not established: the budget is too small or targets/parameters "
                "are not supported by continuous scalar sensitivity probes. "
                "Increase budget or add independent observables."}
    point = evaluator.best[0]
    base["at_inputs"] = problem.to_inputs(point)
    before = len(evaluator.history)
    columns = []
    for index in range(len(point)):
        low, high = list(point), list(point)
        low[index], high[index] = max(0.0, point[index] - .001), min(1.0, point[index] + .001)
        responses = []
        for probe in (low, high):
            _, details, _ = evaluator.detail(probe)
            responses.append([row.get("error") for row in details])
        if any(value is None or not math.isfinite(value) for row in responses for value in row):
            return {**base, "evaluations": len(evaluator.history) - before,
                    "message": "Sensitivity probes did not produce every target; identification remains unknown."}
        columns.append([(b - a) / (high[index] - low[index]) for a, b in zip(*responses)])
    matrix = [list(row) for row in zip(*columns)]
    rank, null = _rank_and_null(matrix, len(point))
    deficient = rank < len(point)
    return {**base, "status": "rank_deficient" if deficient else "full_local_rank", "rank": rank,
            "evaluations": len(evaluator.history) - before, "step_in_unit_range": .001,
            "relative_rank_tolerance": 1e-7, "normalized_sensitivity": matrix,
            "unresolved_directions": [dict(zip(problem.names, direction)) for direction in null],
            "message": ("Training observables do not locally distinguish all parameter directions. "
                        "Measure additional independent outcomes or fix externally measured parameters. "
                        if deficient else
                        "Training observables distinguish local parameter directions at the probed point. ")
            + "This numerical diagnostic does not prove global uniqueness, account for measurement error, "
              "or turn sampled search support into confidence intervals."}


def _rank_and_null(matrix: list[list[float]], width: int) -> tuple[int, list[list[float]]]:
    """Pivoted row reduction in normalized parameter coordinates, with an explicit relative threshold."""
    rows = [row[:] for row in matrix]
    threshold = max((abs(value) for row in rows for value in row), default=0.0) * 1e-7
    threshold = max(threshold, 1e-12)
    pivots: list[int] = []
    for column in range(width):
        start = len(pivots)
        if start >= len(rows):
            break
        pivot = max(range(start, len(rows)), key=lambda i: abs(rows[i][column]))
        if abs(rows[pivot][column]) <= threshold:
            continue
        rows[start], rows[pivot] = rows[pivot], rows[start]
        divisor = rows[start][column]
        rows[start] = [value / divisor for value in rows[start]]
        for i in range(len(rows)):
            if i != start:
                factor = rows[i][column]
                rows[i] = [a - factor * b for a, b in zip(rows[i], rows[start])]
        pivots.append(column)
    null = []
    for free in sorted(set(range(width)) - set(pivots)):
        direction = [0.0] * width
        direction[free] = 1.0
        for row, pivot in zip(rows, pivots):
            direction[pivot] = -row[free]
        null.append(direction)
    return len(pivots), null
