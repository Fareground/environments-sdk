"""Inputs are fitted before a session loads (`fg_env.analysis.calibrate`), not by a contract section at every load:
an earlier contract's `calibration` section is refused with how to fit instead."""
import pytest

import fg_env


def gain_contract():
    """A level that grows by `gain` a round, with noise."""
    return {
        "name": "Gain",
        "clock": {"rounds": "$inputs.rounds"},
        "inputs": {"gain": {"type": "number", "default": 1, "min": 0.5, "max": 8},
                   "rounds": {"type": "int", "default": 10}},
        "world": {"level": 0},
        "types": {"gauge": {"description": "Nothing acts: the world grows on its own."}},
        "events": [{"name": "grow", "phase": "end",
                    "do": "$world.level = $world.level + $inputs.gain * $uniform(0.9, 1.1)"}],
        "outputs": {"level": "$world.level"},
    }


def test_a_calibration_section_is_refused_with_how_to_fit_before_loading():
    old = {**gain_contract(), "calibration": {"params": {"gain": {}}, "targets": {"level": 30}}}
    [issue] = [i for i in fg_env.check(old) if i.severity == "error"]
    assert issue.path == "calibration" and "fg_env.analysis.calibrate" in issue.fix
    with pytest.raises(fg_env.ContractError, match="no longer part of the contract"):
        fg_env.load(old)


def test_inputs_fitted_before_load_run_the_session_and_calibrate_is_still_accepted():
    fit = fg_env.analysis.calibrate(gain_contract(), {"level": 30}, {"gain": {}}, inputs={"rounds": 5}, runs=2,
                                    budget=8, seed=1)
    env = fg_env.load(gain_contract(), inputs=fit.params, seed=1, calibrate=False)
    assert env.inputs["gain"] == fit.params["gain"] == pytest.approx(6, rel=0.1)
