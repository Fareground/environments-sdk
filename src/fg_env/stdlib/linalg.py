"""Linear algebra on nested lists (a matrix is a list of equal-length rows) and correlated normal draws.

Work is charged before it is done: reading a matrix costs one step per element, a product costs
n·m·p, and elimination costs n³, so a huge matrix meets the work budget instead of stalling a run.
Whole-number determinants are exact (fraction-free elimination); everything else is floating point.
"""
from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence, Tuple

from ..expr import MAX_INT_BITS, MAX_LIST_LEN, Call, _describe, charge, function
from ._args import check_len, fail, int_arg, list_arg

Matrix = List[List[Any]]

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


def _vector(call: Call, index: int, what: str) -> List[Any]:
    values = list_arg(call, index, f"{what} (a list of numbers)")
    if not values:
        raise fail(call, f"{what} is empty")
    return [_number(call, v, f"{what}: item {i}") for i, v in enumerate(values)]


def _is_matrix(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and isinstance(value[0], (list, tuple))


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


def _scale(matrix: Matrix) -> float:
    return max((abs(v) for row in matrix for v in row), default=0.0)


@function("dot(xs, ys)", "Inner product of two equal-length lists of numbers: Σ xs[i] × ys[i] (use $matmul for matrices).",
          min_args=2, max_args=2)
def _dot(call: Call) -> Any:
    if _is_matrix(call.arg(0)) or _is_matrix(call.arg(1)):
        raise fail(call, "takes two lists of numbers; use $matmul to multiply matrices")
    xs, ys = _vector(call, 0, "xs"), _vector(call, 1, "ys")
    if len(xs) != len(ys):
        raise fail(call, f"xs and ys must be the same length, got {len(xs)} and {len(ys)}")
    if all(isinstance(v, int) for v in xs + ys):
        return sum(x * y for x, y in zip(xs, ys))
    return math.fsum(x * y for x, y in zip(xs, ys))


def _product(a: Matrix, b: Matrix) -> Matrix:
    columns = list(zip(*b))
    exact = all(isinstance(v, int) for row in a for v in row) and all(isinstance(v, int) for row in b for v in row)
    total = sum if exact else math.fsum
    return [[total(x * y for x, y in zip(row, column)) for column in columns] for row in a]


@function("matmul(a, b)", "Matrix product a × b. A plain list is a column on the right (matrix × list → list) "
          "or a row on the left (list × matrix → list).", min_args=2, max_args=2)
def _matmul(call: Call) -> Any:
    left_matrix, right_matrix = _is_matrix(call.arg(0)), _is_matrix(call.arg(1))
    if not left_matrix and not right_matrix:
        raise fail(call, "needs at least one matrix; use $dot for two lists")
    a = _matrix(call, 0, "a") if left_matrix else [_vector(call, 0, "a")]
    b = _matrix(call, 1, "b") if right_matrix else [[v] for v in _vector(call, 1, "b")]
    if len(a[0]) != len(b):
        left = _shape(a) if left_matrix else f"a list of {len(a[0])}"
        right = _shape(b) if right_matrix else f"a list of {len(b)}"
        raise fail(call, f"cannot multiply {left} by {right}: the columns of a ({len(a[0])}) "
                         f"must equal the rows of b ({len(b)})")
    check_len(call, len(a) * len(b[0]), "product")
    charge(len(a) * len(b) * len(b[0]), call.source)
    product = _product(a, b)
    if not left_matrix:
        return product[0]
    if not right_matrix:
        return [row[0] for row in product]
    return product


@function("transpose(matrix)", "The matrix with rows and columns swapped: an n×m matrix becomes m×n.",
          min_args=1, max_args=1)
def _transpose(call: Call) -> Matrix:
    matrix = _matrix(call, 0, "the matrix")
    return [list(column) for column in zip(*matrix)]


@function("identity(n)", "The n×n identity matrix (1 on the diagonal, 0 elsewhere).", min_args=1, max_args=1)
def _identity(call: Call) -> Matrix:
    n = int_arg(call, 0, low=1, high=MAX_LIST_LEN, what="the size n")
    check_len(call, n * n, "identity matrix")
    return [[1 if r == c else 0 for c in range(n)] for r in range(n)]


def eliminate(matrix: Sequence[Sequence[float]], extra: Sequence[Sequence[float]]) -> Optional[List[List[float]]]:
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


def _eliminate(call: Call, matrix: Matrix, extra: Matrix, what: str) -> List[List[float]]:
    charge(len(matrix) ** 2 * (len(matrix) + len(extra[0])), call.source)
    solved = eliminate(matrix, extra)
    if solved is None:
        raise fail(call, f"{what} is singular (its rows are linearly dependent), so there is no unique answer")
    return solved


@function("inverse(matrix)", "The inverse of a square matrix (error when it is singular).", min_args=1, max_args=1)
def _inverse(call: Call) -> Matrix:
    matrix = _square(call, 0, "the matrix")
    n = len(matrix)
    return _eliminate(call, matrix, [[1.0 if r == c else 0.0 for c in range(n)] for r in range(n)], "the matrix")


@function("linsolve(a, b)", "x such that a × x = b, for a square matrix a and a list b (or a matrix b, solved column by column).",
          min_args=2, max_args=2)
def _linsolve(call: Call) -> Any:
    a = _square(call, 0, "a")
    columns = _is_matrix(call.arg(1))
    b = _matrix(call, 1, "b") if columns else [[v] for v in _vector(call, 1, "b")]
    if len(b) != len(a):
        raise fail(call, f"b must have {len(a)} {'rows' if columns else 'numbers'} to match a ({_shape(a)}), got {len(b)}")
    solved = _eliminate(call, a, b, "a")
    return solved if columns else [row[0] for row in solved]


def _exact_det(call: Call, matrix: Matrix) -> int:
    """Bareiss fraction-free elimination: every intermediate value is a minor, so it stays a whole number."""
    rows = [list(row) for row in matrix]
    n, sign, previous = len(rows), 1, 1
    for k in range(n - 1):
        if rows[k][k] == 0:
            swap = next((r for r in range(k + 1, n) if rows[r][k] != 0), None)
            if swap is None:
                return 0
            rows[k], rows[swap], sign = rows[swap], rows[k], -sign
        for i in range(k + 1, n):
            for j in range(k + 1, n):
                value = (rows[i][j] * rows[k][k] - rows[i][k] * rows[k][j]) // previous
                if value.bit_length() > MAX_INT_BITS:
                    raise fail(call, f"the determinant is past the limit of {MAX_INT_BITS:,} bits")
                rows[i][j] = value
        previous = rows[k][k]
    return sign * rows[n - 1][n - 1]


@function("det(matrix)", "Determinant of a square matrix (exact for whole numbers; 0 when singular).",
          min_args=1, max_args=1)
def _det(call: Call) -> Any:
    matrix = _square(call, 0, "the matrix")
    n = len(matrix)
    charge(n ** 3, call.source)
    if all(isinstance(v, int) for row in matrix for v in row):
        return _exact_det(call, matrix)
    tolerance = SINGULAR_TOLERANCE * n * _scale(matrix)
    rows = [[float(v) for v in row] for row in matrix]
    det = 1.0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) <= tolerance:
            return 0.0
        if pivot != col:
            rows[col], rows[pivot] = rows[pivot], rows[col]
            det = -det
        lead = rows[col][col]
        det *= lead
        for r in range(col + 1, n):
            factor = rows[r][col] / lead
            if factor != 0.0:
                rows[r] = [v - factor * p for v, p in zip(rows[r], rows[col])]
    if not math.isfinite(det):
        raise fail(call, "the determinant is too large to represent")
    return det


def cholesky(cov: Matrix) -> Tuple[List[List[float]], str]:
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
          "cov must be symmetric positive semi-definite). In population props, draw once and read parts with $it: "
          "{\"z\": \"$mvnormal([0, 0], [[1, 0.6], [0.6, 1]])\", \"a\": \"$it.z[0]\"}.", min_args=2, max_args=2)
def _mvnormal(call: Call) -> List[float]:
    means = _vector(call, 0, "the means")
    cov = _square(call, 1, "the covariance matrix")
    if len(cov) != len(means):
        raise fail(call, f"the covariance matrix must be {len(means)}×{len(means)} to match the means, got {_shape(cov)}")
    charge(len(cov) ** 3, call.source)
    lower, reason = cholesky(cov)
    if reason:
        raise fail(call, f"the covariance matrix is invalid: {reason}")
    rng = call.rng
    z = [rng.gauss(0.0, 1.0) for _ in means]
    return [mu + math.fsum(lower[i][k] * z[k] for k in range(i + 1)) for i, mu in enumerate(means)]
