"""Fitting a product cannot identify its two factors; extra measurements can resolve them."""
import copy

import pytest

import fg_env

MODEL = {"name": "Confounded yield", "types": {}, "clock": {"rounds": 1},
         "inputs": {"load": {"type": "number", "default": 100},
                    "efficiency": {"type": "number", "default": .5},
                    "availability": {"type": "number", "default": .8}},
         "outputs": {"yield": "$inputs.load * $inputs.efficiency * $inputs.availability"}}
PARAMS = {name: {"low": 0, "high": 1} for name in ("efficiency", "availability")}
CASES = [{"name": str(load), "inputs": {"load": load}, "targets": {"yield": .4 * load}}
         for load in (10, 20, 30)]


def test_confounded_product_has_rank_one_within_total_budget():
    fit = fg_env.analysis.calibrate(MODEL, CASES, PARAMS, runs=1, holdout=1, budget=50, seed=17)
    diagnostic = fit.identification
    assert diagnostic["status"] == "rank_deficient"
    assert diagnostic["rank"] == 1
    assert len(diagnostic["unresolved_directions"]) == 1
    assert fit.evaluations <= 50
    assert diagnostic["evaluations"] <= 4
    assert fit.fit < .01
    assert "not confidence intervals" in fit.report()
    assert fit.to_dict()["identification"] == diagnostic
    for efficiency, availability in ((.4, 1), (.5, .8), (.8, .5), (1, .4)):
        result = fg_env.run(MODEL, inputs={"efficiency": efficiency, "availability": availability})
        assert result.outputs["yield"] == pytest.approx(40)


def test_independent_observable_restores_local_rank_without_global_claim():
    model = copy.deepcopy(MODEL)
    model["outputs"]["measured_efficiency"] = "$inputs.efficiency"
    cases = copy.deepcopy(CASES)
    for case in cases:
        case["targets"]["measured_efficiency"] = .5
    fit = fg_env.analysis.calibrate(model, cases, PARAMS, runs=1, budget=60)
    assert fit.identification["status"] == "full_local_rank"
    assert fit.identification["rank"] == 2
    assert "does not prove global uniqueness" in fit.report()


def test_small_budget_reports_unknown_and_never_spends_unrequested_probes():
    fit = fg_env.analysis.calibrate(MODEL, CASES, PARAMS, runs=1, budget=3)
    assert fit.identification["status"] == "not_assessed"
    assert fit.identification["evaluations"] == 0
    assert fit.evaluations <= 3


def test_declared_observation_tolerance_keeps_joint_support_not_independent_marginals():
    fit = fg_env.analysis.calibrate(MODEL, CASES, PARAMS, runs=1, budget=50, fit_tolerance=.02)
    assert len(fit.plausible) > 1
    assert all(set(point) == set(PARAMS) for point in fit.plausible)
    assert all(abs(point["efficiency"] * point["availability"] / .4 - 1) <= fit.fit + .0200001
               for point in fit.plausible)
    assert fit.uncertainty["efficiency"]["fit_tolerance"] == .02


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), True, "0.1"])
def test_invalid_observation_tolerance_is_rejected(bad):
    with pytest.raises(ValueError, match="fit_tolerance"):
        fg_env.analysis.calibrate(MODEL, CASES, PARAMS, fit_tolerance=bad)


def test_held_out_case_targets_do_not_change_fitted_diagnostics():
    cases = copy.deepcopy(CASES)
    first = fg_env.analysis.calibrate(MODEL, cases, PARAMS, runs=1, budget=30, test=["30"])
    cases[-1]["targets"]["yield"] = 999
    second = fg_env.analysis.calibrate(MODEL, cases, PARAMS, runs=1, budget=30, test=["30"])
    assert first.params == second.params
    assert first.identification == second.identification
    assert first.holdout["out_of_sample"] != second.holdout["out_of_sample"]
