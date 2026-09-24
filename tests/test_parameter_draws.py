"""Parameter uncertainty in runs: each run draws its parameters from a calibration's plausible set, a list of points
or priors — the same draw for run i in every arm, cell and case — so forecast intervals include not knowing them."""
import pytest

import fg_env

LINEAR = {"name": "Linear", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
          "inputs": {"p": {"type": "number", "default": 1.0, "min": 0, "max": 3},
                     "k": {"type": "int", "default": 2, "min": 0, "max": 10},
                     "scale": {"type": "number", "default": 10, "min": 0, "max": 100}},
          "outputs": {"y": {"type": "number", "expr": "$inputs.scale * $inputs.p"},
                      "k": {"type": "number", "expr": "$inputs.k"}},
          "arms": {"base": {"description": "as written"},
                   "double": {"description": "twice the scale", "inputs": {"scale": 20}}}}

#: Each quarter's true p differs from 1.0 by these shares; a forecast that treats p as known misses all but one.
DEVIATIONS = [-0.12, -0.05, 0.0, 0.03, 0.08, -0.02, 0.1, -0.09, 0.04, 0.06]
QUARTERS = [{"name": f"q{i}", "actuals": {"y": 10 * (1 + d)}} for i, d in enumerate(DEVIATIONS)]


def test_every_arm_draws_the_same_parameters_for_run_i():
    result = fg_env.experiment(LINEAR, runs=6, uncertainty={"p": {"dist": "uniform", "low": 0.5, "high": 1.5}})
    base, double = result.arms["base"].runs, result.arms["double"].runs
    assert [r.inputs["p"] for r in base] == [r.inputs["p"] for r in double]
    assert len({r.inputs["p"] for r in base}) == 6 and all(0.5 <= r.inputs["p"] <= 1.5 for r in base)
    assert all(d.outputs["y"] == pytest.approx(2 * b.outputs["y"]) for b, d in zip(base, double))


def test_priors_clip_into_range_round_whole_numbers_and_pick_listed_values():
    swept = fg_env.analysis.sweep(LINEAR, {"scale": [10, 20]}, runs=20,
                         uncertainty={"p": {"dist": "normal", "mean": 1.0, "sd": 5, "min": 0.8, "max": 1.2},
                                      "k": {"dist": "triangular", "low": 0, "mode": 4, "high": 9}})
    first, second = (cell.runs for cell in swept.cells)
    assert all(0.8 <= r.inputs["p"] <= 1.2 for r in first) and all(isinstance(r.inputs["k"], int) for r in first)
    assert [r.inputs["p"] for r in first] == [r.inputs["p"] for r in second]  # every cell shares run i's draw
    picked = fg_env.experiment(LINEAR, arms=["base"], runs=20, uncertainty={"k": {"values": [1, 3]}})
    assert {r.inputs["k"] for r in picked.arms["base"].runs} == {1, 3}


def test_a_calibrations_plausible_points_are_drawn_whole():
    fitted = fg_env.analysis.calibrate(LINEAR, {"y": 12}, {"p": {"low": 0.5, "high": 2}}, runs=1, budget=30)
    assert fitted.params in fitted.plausible and fitted.to_dict()["plausible"] == fitted.plausible
    result = fg_env.experiment(LINEAR, arms=["base"], runs=5, uncertainty=fitted)
    assert {r.inputs["p"] for r in result.arms["base"].runs} <= {point["p"] for point in fitted.plausible}
    points = [{"p": 0.9, "k": 1}, {"p": 1.1, "k": 5}]
    drawn = fg_env.experiment(LINEAR, arms=["base"], runs=10, uncertainty=points).arms["base"].runs
    assert {(r.inputs["p"], r.inputs["k"]) for r in drawn} <= {(0.9, 1), (1.1, 5)}


def test_parameter_uncertainty_widens_intervals_until_they_hold_the_actual_values():
    known = fg_env.analysis.validate(LINEAR, QUARTERS, runs=40, levels=(0.8,), baselines=())
    drawn = fg_env.analysis.validate(LINEAR, QUARTERS, runs=40, levels=(0.8,), baselines=(),
                            uncertainty={"p": {"dist": "normal", "mean": 1.0, "sd": 0.1}})
    assert known.measures["y"]["overall"]["coverage"]["0.8"]["coverage"] == pytest.approx(0.1)
    assert any("intervals held 1 of 10" in warning for warning in known.warnings)
    assert drawn.measures["y"]["overall"]["coverage"]["0.8"]["coverage"] >= 0.8
    assert not any("intervals held" in warning for warning in drawn.warnings)


def test_a_drawn_input_also_set_by_the_case_is_refused_by_name():
    with pytest.raises(ValueError) as excinfo:
        fg_env.analysis.backtest(LINEAR, [{"name": "a", "inputs": {"p": 1}, "outcome": 10}], "y", runs=2,
                        uncertainty={"p": {"values": [1, 2]}})
    assert "p is set for these runs and also drawn from uncertainty" in str(excinfo.value)


@pytest.mark.parametrize("uncertainty, message", [
    ({"p": {"dist": "beta"}}, "dist must be one of normal, lognormal, uniform, triangular"),
    ({"p": {"dist": "normal", "mean": 1}}, "a normal prior needs a number for sd"),
    ({"missing": {"values": [1]}}, "'missing' is not a declared input"),
    ([{"p": "high"}], "drawn parameters must be number or int inputs"),
    ("p", "uncertainty must be a CalibrationResult, a list of points"),
])
def test_uncertainty_mistakes_say_what_to_pass(uncertainty, message):
    with pytest.raises(ValueError) as excinfo:
        fg_env.experiment(LINEAR, arms=["base"], runs=1, uncertainty=uncertainty)
    assert message in str(excinfo.value)


CENTRE = {"name": "Toy centre", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
          "inputs": {"p": {"type": "number", "default": 0.5, "min": 0, "max": 1},
                     "day": {"type": "number", "default": 0, "min": -1, "max": 1}},
          "outputs": {"sl": {"type": "number", "expr": "0.70 + 0.20 * $inputs.p + 0.03 * $inputs.day"},
                      "abandon": {"type": "number", "expr": "0.03 + 0.04 * $inputs.p + 0.01 * $inputs.day"}}}


def test_calibration_names_the_target_traded_away_when_the_others_count_for_more():
    cases = [{"name": f"day {i}", "inputs": {"day": day},
              "targets": {"sl": {"value": 0.85 + 0.03 * day, "scale": 0.85 + 0.03 * day},
                          "abandon": {"value": 0.05 + 0.01 * day, "scale": 0.05 + 0.01 * day}}}
             for i, day in enumerate([-1, -0.5, 0, 0.5, 1])]
    result = fg_env.analysis.calibrate(CENTRE, cases, {"p": {"low": 0, "high": 1}}, runs=1, budget=40, method="golden")
    [note] = [n for n in result.notes if "of the misfit" in n]
    assert note.startswith("sl carries") and "the search traded it away" in note
