"""Linear algebra and correlated draws: hand-computed values, shape and singularity errors, budget, determinism."""
import random

import pytest

import fg_env
from fg_env.sdk.expr import ExprError, Scope, World, evaluate
from fg_env.sdk.stdlib.linalg import cholesky


class _World(World):
    def __init__(self, seed=7):
        self.rng = random.Random(seed)


def ev(source, seed=7, **roots):
    return evaluate(source, Scope(roots, _World(seed)))


def test_dot_of_two_lists_is_the_sum_of_products():
    assert ev("$dot([1, 2, 3], [4, 5, 6])") == 32  # 4 + 10 + 18
    assert ev("$dot([0.5, 2], [2, 0.25])") == pytest.approx(1.5)


def test_matmul_multiplies_matrices_and_treats_lists_as_columns_or_rows():
    assert ev("$matmul([[1, 2], [3, 4]], [[5, 6], [7, 8]])") == [[19, 22], [43, 50]]
    assert ev("$matmul([[1, 2], [3, 4]], [1, 1])") == [3, 7]  # matrix × column
    assert ev("$matmul([1, 1], [[1, 2], [3, 4]])") == [4, 6]  # row × matrix
    assert ev("$matmul([[1, 2, 3]], [[1], [2], [3]])") == [[14]]


def test_transpose_and_identity():
    assert ev("$transpose([[1, 2, 3], [4, 5, 6]])") == [[1, 4], [2, 5], [3, 6]]
    assert ev("$identity(3)") == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def test_inverse_of_a_two_by_two_matrix():
    # det = 4·6 − 7·2 = 10; inverse = [[6, −7], [−2, 4]] / 10
    inverse = ev("$inverse([[4, 7], [2, 6]])")
    assert inverse == [[pytest.approx(0.6), pytest.approx(-0.7)], [pytest.approx(-0.2), pytest.approx(0.4)]]
    product = ev("$matmul($m, $inverse($m))", m=[[2, 1, 1], [1, 3, 2], [1, 0, 0]])
    for r, row in enumerate(product):
        assert row == [pytest.approx(1.0 if r == c else 0.0, abs=1e-12) for c in range(3)]


def test_determinant_is_exact_for_whole_numbers_and_floating_otherwise():
    assert ev("$det([[1, 2], [3, 4]])") == -2 and isinstance(ev("$det([[1, 2], [3, 4]])"), int)
    assert ev("$det([[2, 0, 1], [1, 3, 2], [1, 1, 2]])") == 6  # 2·(6−2) − 0 + 1·(1−3)
    assert ev("$det([[0, 1], [1, 0]])") == -1  # needs a row swap
    assert ev("$det([[1, 2], [2, 4]])") == 0
    assert ev("$det([[0.5, 1], [2, 1]])") == pytest.approx(-1.5)
    assert ev("$det([[1.0, 2], [2, 4]])") == 0.0


def test_linsolve_finds_x_with_a_times_x_equal_to_b():
    # 2x + y = 3, x + 3y = 5  →  x = 0.8, y = 1.4
    assert ev("$linsolve([[2, 1], [1, 3]], [3, 5])") == [pytest.approx(0.8), pytest.approx(1.4)]
    assert ev("$linsolve([[2, 0], [0, 4]], [[2, 4], [8, 4]])") == [[pytest.approx(1.0), pytest.approx(2.0)],
                                                                [pytest.approx(2.0), pytest.approx(1.0)]]


@pytest.mark.parametrize("source, message", [
    ("$dot([1, 2], [1])", "same length"),
    ("$dot([[1]], [[1]])", "use $matmul"),
    ("$matmul([1, 2], [3, 4])", "use $dot"),
    ("$matmul([[1, 2]], [[1, 2]])", "cannot multiply 1×2 by 1×2"),
    ("$matmul([[1, 2], [3]], [1, 1])", "ragged: row 0 has 2 numbers but row 1 has 1"),
    ("$transpose([1, 2])", "list of rows"),
    ("$inverse([[1, 2], [2, 4]])", "singular"),
    ("$inverse([[1, 2, 3], [4, 5, 6]])", "must be square, got 2×3"),
    ("$linsolve([[1, 1], [1, 1]], [1, 2])", "singular"),
    ("$linsolve([[1, 0], [0, 1]], [1, 2, 3])", "b must have 2 numbers"),
    ("$det([[1, true], [0, 1]])", "row 0, column 1 must be a number, got bool True"),
    ("$identity(0)", "at least 1"),
    ("$identity(1001)", "the limit is 1,000,000"),
    ("$mvnormal([0, 0], [[1, 0.5], [0.4, 1]])", "not symmetric"),
    ("$mvnormal([0, 0], [[1, 2], [2, 1]])", "not positive semi-definite"),
    ("$mvnormal([0, 0], [[-1, 0], [0, 1]])", "negative"),
    ("$mvnormal([0, 0, 0], [[1, 0], [0, 1]])", "must be 3×3"),
])
def test_bad_shapes_and_singular_matrices_say_what_to_fix(source, message):
    with pytest.raises(ExprError, match=message.replace("$", r"\$").replace("(", r"\(")):
        ev(source)


def test_elimination_is_charged_against_the_work_budget():
    with pytest.raises(ExprError, match="work budget"):
        ev("$inverse($identity(150))")  # 150³ elimination steps is past the budget


def test_cholesky_factor_reproduces_the_covariance():
    lower, reason = cholesky([[4, 2], [2, 5]])
    assert reason == "" and lower == [[2.0, 0.0], [1.0, 2.0]]  # [[2,0],[1,2]] × [[2,1],[0,2]] = [[4,2],[2,5]]
    lower, reason = cholesky([[1, 1], [1, 1]])  # perfectly correlated: second column of L is zero
    assert reason == "" and lower == [[1.0, 0.0], [1.0, 0.0]]


def test_mvnormal_is_mean_plus_cholesky_times_standard_normals_from_the_world_rng():
    z = random.Random(11)
    z0, z1 = z.gauss(0.0, 1.0), z.gauss(0.0, 1.0)
    draw = ev("$mvnormal([10, -1], [[4, 2], [2, 5]])", seed=11)
    assert draw == [pytest.approx(10 + 2 * z0), pytest.approx(-1 + z0 + 2 * z1)]
    assert ev("$mvnormal([10, -1], [[4, 2], [2, 5]])", seed=11) == draw
    same = ev("$mvnormal([0, 5], [[1, 1], [1, 1]])", seed=3)
    assert same[1] - 5 == pytest.approx(same[0])


def test_mvnormal_gives_correlated_traits_in_a_population():
    contract = {
        "name": "Correlated traits",
        "clock": {"rounds": 1},
        "types": {"person": {"agent": True, "props": {
            "z": {"type": "list", "default": []},
            "height": 0.0, "weight": 0.0}}},
        "population": [{"type": "person", "count": 2000, "props": {
            "z": "$mvnormal([170, 70], [[100, 64], [64, 64]])",
            "height": "$it.z[0]", "weight": "$it.z[1]"}}],
        "actions": {"wait": {"by": "person", "do": []}},
        "stages": [{"name": "noop", "actions": []}],
        "outputs": {
            "corr": {"expr": "$corr($map(person, $it.height), $map(person, $it.weight))", "type": "number"},
            "mean_height": {"expr": "$avg(person, $it.height)", "type": "number"},
            "sd_weight": {"expr": "$sqrt($variance(person, $it.weight))", "type": "number"},
        },
    }
    first = fg_env.run(contract, seed=5)
    assert first.ok, first.summary()
    # correlation 64 / √(100·64) = 0.8; the standard error of r at n = 2000 is about (1 − 0.64)/√2000 ≈ 0.008
    assert first.outputs["corr"] == pytest.approx(0.8, abs=0.04)
    assert first.outputs["mean_height"] == pytest.approx(170, abs=1.0)  # standard error 10/√2000 ≈ 0.22
    assert first.outputs["sd_weight"] == pytest.approx(8, abs=0.5)
    assert fg_env.run(contract, seed=5).outputs == first.outputs
