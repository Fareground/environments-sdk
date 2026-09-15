"""Independent analytical references for error-controlled integration."""
import math

import pytest

from fg_env.sdk.integration import integrate


@pytest.mark.parametrize("rate", [0.01, 1, 20, 200])
def test_stable_decay_cannot_silently_explode(rate):
    result = integrate(lambda y, t: [-rate*y[0]], [1.0], 0, 1)
    assert result[0] == pytest.approx(math.exp(-rate), abs=1e-9, rel=1e-5)


def test_oscillator_preserves_phase_and_energy_over_many_periods():
    end = 20 * math.pi + 0.3
    x, v = integrate(lambda y, t: [y[1], -y[0]], [1.0, 0.0], 0, end)
    assert x == pytest.approx(math.cos(end), abs=1e-5)
    assert v == pytest.approx(-math.sin(end), abs=1e-5)
    assert x*x + v*v == pytest.approx(1, abs=1e-5)


def test_time_dependent_forcing_uses_intermediate_times():
    assert integrate(lambda y, t: [math.sin(t)], [0.0], 0, math.pi)[0] == pytest.approx(2, abs=1e-7)


def test_invalid_equation_fails_instead_of_returning_a_trajectory():
    with pytest.raises((ArithmeticError, ValueError)):
        integrate(lambda y, t: [math.sqrt(-1)], [1.0], 0, 1)


def test_public_sdk_default_resolution_checks_numerical_accuracy():
    import fg_env

    result = fg_env.load({
        "name": "Rapid decay",
        "clock": {"rounds": 1}, "types": {"marker": {}},
        "physics": {"vars": {"x": {"start": 1, "rate": "-20*x"}}},
        "outputs": {"x": "$physics.x"},
    }).run()
    assert result.status == "completed"
    assert result.outputs["x"] == pytest.approx(math.exp(-20), abs=1e-10)


def test_independent_entity_defaults_also_control_numerical_accuracy():
    import fg_env

    result = fg_env.load({
        "name": "Rapid cellular decay", "clock": {"rounds": 1},
        "types": {"cell": {"props": {"x": 1.0}}},
        "entities": {"a": {"type": "cell"}},
        "physics": {"per": {"cell": {"vars": {"x": "-20*x"}}}},
        "outputs": {"x": "$entity(a).x"},
    }).run()
    assert result.status == "completed"
    assert result.outputs["x"] == pytest.approx(math.exp(-20), abs=1e-10)


def test_clock_reads_follow_intermediate_continuous_times():
    import fg_env

    result = fg_env.load({
        "name": "Linearly increasing force",
        "clock": {"mode": "continuous", "horizon": 1},
        "types": {"marker": {}},
        "physics": {"read": {"force": "$clock.time"},
                    "vars": {"impulse": {"start": 0, "rate": "force"}}},
        "outputs": {"impulse": "$physics.impulse"},
    }).run()
    assert result.status == "completed"
    assert result.time == 1
    assert result.outputs["impulse"] == pytest.approx(0.5, abs=1e-10)


@pytest.mark.parametrize("field,value", [("rtol", 0), ("rtol", float("inf")), ("atol", -1), ("atol", float("nan"))])
def test_invalid_accuracy_requests_are_rejected(field, value):
    import fg_env

    issues = fg_env.check({"name": "Bad tolerance", "types": {"marker": {}},
                           "physics": {field: value, "vars": {"x": {"rate": "0"}}}})
    assert any(i.severity == "error" and field in i.path for i in issues)
