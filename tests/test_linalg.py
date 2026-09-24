"""Correlated draws and the matrix helpers behind them: hand-computed values, shape errors, determinism."""
import random

import pytest

import fg_env
from fg_env.expr import ExprError, Scope, World, evaluate
from fg_env.stdlib.linalg import cholesky, eliminate


class _World(World):
    def __init__(self, seed=7):
        self.rng = random.Random(seed)


def ev(source, seed=7, **roots):
    return evaluate(source, Scope(roots, _World(seed)))


@pytest.mark.parametrize("source, message", [
    ("$mvnormal([0, 0], [[1, 0.5], [0.4]])", "ragged: row 0 has 2 numbers but row 1 has 1"),
    ("$mvnormal([0, 0], [[1, true], [0, 1]])", "row 0, column 1 must be a number, got bool True"),
    ("$mvnormal([0, 0], [[1, 2, 3], [4, 5, 6]])", "must be square, got 2×3"),
    ("$mvnormal([0, 0], [[1, 0.5], [0.4, 1]])", "not symmetric"),
    ("$mvnormal([0, 0], [[1, 2], [2, 1]])", "not positive semi-definite"),
    ("$mvnormal([0, 0], [[-1, 0], [0, 1]])", "negative"),
    ("$mvnormal([0, 0, 0], [[1, 0], [0, 1]])", "must be 3×3"),
])
def test_bad_shapes_and_singular_matrices_say_what_to_fix(source, message):
    with pytest.raises(ExprError, match=message.replace("$", r"\$").replace("(", r"\(")):
        ev(source)


def test_elimination_solves_a_linear_system_and_reports_a_singular_one():
    # 2x + y = 3, x + 3y = 5  →  x = 0.8, y = 1.4
    assert eliminate([[2, 1], [1, 3]], [[3], [5]]) == [[pytest.approx(0.8)], [pytest.approx(1.4)]]
    assert eliminate([[1, 1], [1, 1]], [[1], [2]]) is None


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
