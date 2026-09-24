"""Analytical transition laws, moments and structural recognition for SDEs."""
import math
import statistics

import pytest

import fg_env
from fg_env.physics.model import _CompiledExpr
from fg_env.physics.stochastic import affine, exact_transition


@pytest.mark.parametrize("rate", ["x*x", "sin(x)", "1/x", "x > 0", "sqrt(x)"])
def test_nonlinear_rates_are_not_misclassified_as_affine(rate):
    assert affine(rate, "x") is None


def test_affine_transition_has_exact_conditional_mean_and_variance():
    rate, noise = _CompiledExpr("-k*(x-target)"), _CompiledExpr("sigma")
    ns = {"k": 20, "target": 3, "sigma": 2}
    mean = 3 + (5-3)*math.exp(-20)
    sd = 2*math.sqrt((1-math.exp(-40))/40)
    assert exact_transition(rate, noise, "x", ns, 5, 1, 0) == pytest.approx(mean)
    assert exact_transition(rate, noise, "x", ns, 5, 1, 1) == pytest.approx(mean+sd)


def test_geometric_transition_includes_ito_correction():
    actual = exact_transition(_CompiledExpr("mu*x"), _CompiledExpr("sigma*x"), "x",
                              {"mu": 0.2, "sigma": 0.8}, 2, 1, -0.5)
    assert actual == pytest.approx(2*math.exp(0.2-0.8**2/2-0.8*0.5))


@pytest.mark.parametrize("substeps", [1, 4, 20])
def test_noisy_relaxation_has_correct_population_variance_at_any_resolution(substeps):
    contract = {
        "name": "Noisy relaxation", "clock": {"rounds": 1},
        "types": {"particle": {"props": {"x": 0.0}}},
        "population": [{"type": "particle", "count": 2000}],
        "physics": {"substeps": substeps, "per": {"particle": {"vars": {"x": {"rate": "-20*x", "noise": "1"}}}}},
        "outputs": {"variance": "$variance($map(particle, $it.x))", "mean": "$avg(particle, $it.x)"},
    }
    result = fg_env.load(contract, seed=42).run()
    assert result.status == "completed"
    assert result.outputs["variance"] == pytest.approx((1-math.exp(-40))/40, rel=0.1)
    assert abs(result.outputs["mean"]) < 0.012  # over three standard errors


def test_world_noise_uses_the_same_exact_law():
    contract = {"name": "Noisy world", "clock": {"rounds": 1}, "types": {"marker": {}},
                "physics": {"substeps": 1, "vars": {"x": {"start": 0, "rate": "-20*x", "noise": "1"}}},
                "outputs": {"x": "$physics.x"}}
    values = [fg_env.load(contract, seed=seed).run().outputs["x"] for seed in range(400)]
    assert statistics.variance(values) == pytest.approx(0.025, rel=0.2)
    assert abs(statistics.mean(values)) < 0.025


@pytest.mark.parametrize("seed", range(6))
def test_nonlinear_refinement_tracks_the_same_analytical_brownian_path(seed):
    # Roberts (2012), arXiv:1210.0933, example 2.1: X(t)=sinh(t+W(t)).
    import random

    from fg_env.physics.stochastic_integration import integrate_noise

    w = random.Random(seed).gauss(0, 1)
    expected = math.sinh(1+w)
    actual = integrate_noise(
        lambda y, t: ([0.5*y[0]+math.sqrt(1+y[0]**2)], [math.sqrt(1+y[0]**2)]),
        [0.0], 0, 1, {0: random.Random(seed)}, [(None, None)])
    assert abs(actual[0]-expected)/(1+abs(expected)) < 0.01


@pytest.mark.slow
def test_public_coupled_stochastic_dynamics_preserve_damped_difference_variance():
    # dX=-10(X-Y)dt+dW1; dY=10(X-Y)dt+dW2.
    # D=X-Y: dD=-20Ddt+sqrt(2)dW; Var(D(1))=(1-exp(-40))/20.
    contract = {"name": "Coupled noisy relaxation", "clock": {"rounds": 1},
                "types": {"marker": {}},
                "physics": {"vars": {"x": {"start": 0, "rate": "-10*(x-y)", "noise": "1"},
                                      "y": {"start": 0, "rate": "10*(x-y)", "noise": "1"}}},
                "outputs": {"difference": "$physics.x-$physics.y"}}
    values = []
    for seed in range(100):
        result = fg_env.load(contract, seed=seed).run()
        assert result.status == "completed", result.error
        values.append(result.outputs["difference"])
    assert statistics.variance(values) == pytest.approx(0.05, rel=0.3)


def test_time_varying_noise_starting_at_zero_converges():
    # Integral_0^1 t dW has variance integral_0^1 t²dt=1/3.
    import random

    from fg_env.physics.stochastic_integration import integrate_noise

    values = [integrate_noise(lambda y, t: ([0.0], [t]), [0.0], 0, 1,
                              {0: random.Random(seed)}, [(None, None)])[0] for seed in range(200)]
    assert statistics.variance(values) == pytest.approx(1/3, rel=0.2)
    assert abs(statistics.mean(values)) < 0.12


def test_unattainable_stochastic_precision_fails_instead_of_silently_accepting():
    import random

    from fg_env.physics.stochastic_integration import integrate_noise

    with pytest.raises(ArithmeticError, match="convergence|work limit"):
        integrate_noise(lambda y, t: ([0.5*y[0]+math.sqrt(1+y[0]**2)], [math.sqrt(1+y[0]**2)]),
                        [0.0], 0, 1, {0: random.Random(0)}, [(None, None)], rtol=1e-15, atol=1e-20)


def test_refinement_preserves_the_parent_brownian_increment():
    import random

    from fg_env.physics.stochastic_integration import integrate_noise

    expected = random.Random(19).gauss(0, 1)
    for tolerance in [0.1, 0.01, 0.0001]:
        actual = integrate_noise(lambda y, t: ([0.0], [1.0]), [0.0], 0, 1,
                                 {0: random.Random(19)}, [(None, None)], rtol=tolerance)
        assert actual[0] == pytest.approx(expected, abs=1e-14)
