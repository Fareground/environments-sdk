"""Correlated business traits retain their covariance when units differ."""
import math

import pytest

from fg_env.stdlib.linalg import cholesky


@pytest.mark.parametrize('variances', [(.0004, 1e10), (1e10, .0004), (1e-200, 1e200), (1e200, 1e-200)])
@pytest.mark.parametrize('correlation', [0, .5, -.5, 1])
def test_covariance_factor_preserves_small_and_large_units(variances, correlation):
    a, b = variances
    cross = correlation * math.sqrt(a) * math.sqrt(b)
    cov = [[a, cross], [cross, b]]
    lower, reason = cholesky(cov)
    assert not reason, reason
    for i in range(2):
        for j in range(2):
            reconstructed = math.fsum(lower[i][k] * lower[j][k] for k in range(2))
            assert reconstructed == pytest.approx(cov[i][j], rel=1e-12, abs=0)


def test_tiny_asymmetric_covariance_is_not_accepted_as_symmetric():
    _, reason = cholesky([[1e-20, 5e-21], [0, 1e-20]])
    assert 'symmetric' in reason


def test_zero_variance_with_nonzero_covariance_is_rejected_regardless_of_units():
    _, reason = cholesky([[0, 1e-10], [1e-10, 1e10]])
    assert 'positive semi-definite' in reason


@pytest.mark.parametrize('seed', range(8))
def test_rank_deficient_covariance_with_mixed_units_remains_supported(seed):
    import random

    rng = random.Random(seed)
    factors = [[rng.uniform(-1, 1) * 10 ** (i * 40 - 100) for _ in range(2)] for i in range(6)]
    cov = [[math.fsum(a * b for a, b in zip(x, y)) for y in factors] for x in factors]
    lower, reason = cholesky(cov)
    assert not reason, reason
    for i in range(6):
        for j in range(6):
            value = math.fsum(lower[i][k] * lower[j][k] for k in range(6))
            # Error relative to the pair's standard-deviation product remains bounded.
            scale = math.sqrt(cov[i][i]) * math.sqrt(cov[j][j])
            assert abs(value - cov[i][j]) <= 1e-10 * scale


def test_small_positive_conditional_variance_is_not_rounded_to_zero():
    lower, reason = cholesky([[1, 1], [1, 1 + 1e-12]])
    assert not reason
    assert lower[1][1] > 0
    assert lower[1][1] ** 2 == pytest.approx((1 + 1e-12) - 1)
