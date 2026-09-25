"""Correlated normal draws ($mvnormal), and the matrix helpers behind them and the tournament ratings: Cholesky
factors of a covariance matrix and Gauss–Jordan elimination (a matrix is a list of equal-length rows).

Work is charged before it is done: reading a matrix costs one step per element and a factorisation n³, so a huge
matrix meets the work budget instead of stalling a run.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from ..expr import Call, _describe, charge, function
from ._args import check_len, fail, list_arg

Matrix = list[list[Any]]

#: A pivot smaller than this share of the matrix's largest entry (times its size) counts as zero.
SINGULAR_TOLERANCE = 1e-12
#: Covariance entries may differ from their mirror by this share of their size and still count as symmetric.
SYMMETRY_TOLERANCE = 1e-9
#: A negative Cholesky remainder within this share of its own variance is treated as roundoff.
PSD_TOLERANCE = 1e-10


def _number(call: Call, value: Any, where: str) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            (isinstance(value, float) and not math.isfinite(value)):
        raise fail(call, f"{where} must be a number, got {_describe(value)}")
    return value


def _vector(call: Call, index: int, what: str) -> list[Any]:
    values = list_arg(call, index, f"{what} (a list of numbers)")
    if not values:
        raise fail(call, f"{what} is empty")
    return [_number(call, v, f"{what}: item {i}") for i, v in enumerate(values)]


def _matrix(call: Call, index: int, what: str) -> Matrix:
    rows = list_arg(call, index, f"{what} (a list of rows)")
    if not rows:
        raise fail(call, f"{what} has no rows")
    if not all(isinstance(row, (list, tuple)) for row in rows):
        raise fail(call, f"{what} must be a list of rows (lists of numbers); write [[1, 2], [3, 4]]")
    width = len(rows[0])
    if width == 0:
        raise fail(call, f"{what} has empty rows")
    check_len(call, len(rows) * width, f"{what}")
    out: Matrix = []
    for r, row in enumerate(rows):
        if len(row) != width:
            raise fail(call, f"{what} is ragged: row 0 has {width} numbers but row {r} has {len(row)}")
        out.append([_number(call, v, f"{what}: row {r}, column {c}") for c, v in enumerate(row)])
    return out


def _square(call: Call, index: int, what: str) -> Matrix:
    matrix = _matrix(call, index, what)
    if len(matrix) != len(matrix[0]):
        raise fail(call, f"{what} must be square, got {_shape(matrix)}")
    return matrix


def _shape(matrix: Matrix) -> str:
    return f"{len(matrix)}×{len(matrix[0])}"


def eliminate(matrix: Sequence[Sequence[float]], extra: Sequence[Sequence[float]]) -> list[list[float]] | None:
    """Solve ``matrix`` × X = ``extra`` (square ``matrix``, ``extra`` with one row per row of it) by Gauss–Jordan
    elimination with partial pivoting. ``None`` when ``matrix`` is singular. No budget: callers charge the work."""
    n = len(matrix)
    tolerance = SINGULAR_TOLERANCE * n * max((abs(v) for row in matrix for v in row), default=0.0)
    rows = [[float(v) for v in matrix[r]] + [float(v) for v in extra[r]] for r in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) <= tolerance:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        lead = rows[col][col]
        rows[col] = [v / lead for v in rows[col]]
        for r in range(n):
            factor = rows[r][col]
            if r != col and factor != 0.0:
                rows[r] = [v - factor * p for v, p in zip(rows[r], rows[col])]
    return [row[n:] for row in rows]


def cholesky(cov: Matrix) -> tuple[list[list[float]], str]:
    """Lower-triangular L with L × Lᵀ = cov for a symmetric positive semi-definite matrix.

    Returns ``(L, "")``, or ``([], reason)`` when ``cov`` is not a valid covariance matrix. A zero
    eigenvalue (perfectly correlated or constant components) is allowed: its column of L is zero."""
    n = len(cov)
    for i in range(n):
        if cov[i][i] < 0:
            return [], f"variance {i} (the diagonal) is negative: {cov[i][i]}"
        for j in range(i):
            a, b = cov[i][j], cov[j][i]
            if abs(a - b) > SYMMETRY_TOLERANCE * max(abs(a), abs(b)):
                return [], f"it is not symmetric: row {i}, column {j} is {a} but row {j}, column {i} is {b}"
    deviations = [math.sqrt(cov[i][i]) for i in range(n)]
    lower = [[0.0] * n for _ in range(n)]
    for j in range(n):
        remainder = cov[j][j] - math.fsum(lower[j][k] ** 2 for k in range(j))
        if remainder < -PSD_TOLERANCE * cov[j][j]:
            return [], "it is not positive semi-definite (the correlations are impossible together)"
        # A small positive conditional variance is real variation, not roundoff.
        root = math.sqrt(max(0.0, remainder))
        lower[j][j] = root
        for i in range(j + 1, n):
            rest = cov[i][j] - math.fsum(lower[i][k] * lower[j][k] for k in range(j))
            if root > 0.0:
                lower[i][j] = rest / root
            elif abs(rest) > math.sqrt(PSD_TOLERANCE) * deviations[i] * deviations[j]:
                return [], "it is not positive semi-definite (the correlations are impossible together)"
    return lower, ""


@function("mvnormal(means, cov)", "A list of normal numbers with these means and covariance matrix (correlated draws; "
          "cov must be symmetric positive semi-definite). In entity props, draw once and read parts with $it: "
          "{\"z\": \"$mvnormal([0, 0], [[1, 0.6], [0.6, 1]])\", \"a\": \"$it.z[0]\"}.", min_args=2, max_args=2)
def _mvnormal(call: Call) -> list[float]:
    means = _vector(call, 0, "the means")
    cov = _square(call, 1, "the covariance matrix")
    if len(cov) != len(means):
        raise fail(call,
                   f"the covariance matrix must be {len(means)}×{len(means)} to match the means, got {_shape(cov)}")
    charge(len(cov) ** 3, call.source)
    lower, reason = cholesky(cov)
    if reason:
        raise fail(call, f"the covariance matrix is invalid: {reason}")
    rng = call.rng
    z = [rng.gauss(0.0, 1.0) for _ in means]
    return [mu + math.fsum(lower[i][k] * z[k] for k in range(i + 1)) for i, mu in enumerate(means)]
