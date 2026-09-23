"""A contract's `calibration` section fits inputs with pilot sessions when a session loads: reproducible, skippable, checked."""
import json
import sys

import pytest

import fg_env
from fg_env import api


def gain_contract(**calibration):
    """A level that grows by `gain` a round, with noise; pilots of 5 rounds are fitted so the level reaches `goal`."""
    return {
        "fg_env": "1", "name": "Gain",
        "clock": {"rounds": "$inputs.rounds"},
        "inputs": {"gain": {"type": "number", "default": 1, "min": 0.5, "max": 8},
                   "rounds": {"type": "int", "default": 10},
                   "goal": {"type": "number", "default": 30}},
        "world": {"level": 0, "goal": "$inputs.goal"},
        "types": {"gauge": {"description": "Nothing acts: the world grows on its own."}},
        "events": [{"name": "grow", "phase": "end", "do": "$world.level = $world.level + $inputs.gain * $uniform(0.9, 1.1)"}],
        "outputs": {"level": "$world.level"},
        "arms": {"known_gain": {"inputs": {"gain": 2}}},
        "calibration": {"params": {"gain": {}}, "targets": {"level": "$world.goal"}, "inputs": {"rounds": 5},
                        "runs": 2, "budget": 8, **calibration},
    }


def test_a_session_runs_with_the_inputs_its_pilot_sessions_fitted():
    env = fg_env.load(gain_contract(), seed=1)
    report = env.calibration
    assert report["targets"] == {"level": 30}  # read from the world the session builds
    assert env.inputs["gain"] == report["params"]["gain"] == pytest.approx(6, rel=0.1)  # 5 pilot rounds reach 30
    assert env.inputs["rounds"] == 10  # pilot inputs apply to the pilots only
    assert report["pilot_sessions"] == report["evaluations"] * 2 + 1 and report["seconds"] >= 0
    result = env.run()
    assert result.inputs["gain"] == env.inputs["gain"] and result.outputs["level"] == pytest.approx(60, rel=0.15)


def test_the_fit_and_the_session_are_reproducible_from_the_seed():
    first, again = fg_env.load(gain_contract(), seed=4), fg_env.load(gain_contract(), seed=4)
    assert first.inputs["gain"] == again.inputs["gain"]
    assert first.run().to_dict() == again.run().to_dict()


def test_setting_a_fitted_input_in_the_call_or_an_arm_skips_the_calibration(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("calibrate should not run")

    monkeypatch.setattr(sys.modules["fg_env.analysis.calibrate"], "calibrate", refuse)  # the package's `calibrate` is the function
    by_caller = fg_env.load(gain_contract(), inputs={"gain": 3}, seed=1)
    by_arm = fg_env.load(gain_contract(), arm="known_gain", seed=1)
    assert (by_caller.calibration, by_caller.inputs["gain"]) == (None, 3)
    assert (by_arm.calibration, by_arm.inputs["gain"]) == (None, 2)
    assert fg_env.load(gain_contract(), seed=1, calibrate=False).inputs["gain"] == 1


def test_check_neither_calibrates_nor_complains_about_a_valid_section(monkeypatch):
    monkeypatch.setattr(api, "calibrate_at_load", lambda *_args, **_kwargs: pytest.fail("check calibrated"))
    assert [i for i in fg_env.check(gain_contract()) if i.severity == "error"] == []


def test_a_snapshot_resumes_with_the_fitted_inputs_without_calibrating_again():
    straight = fg_env.load(gain_contract(), seed=2).run().to_dict()
    env = fg_env.load(gain_contract(), seed=2)
    env.run(rounds=4)
    resumed = fg_env.Env.restore(gain_contract(), json.loads(json.dumps(env.snapshot())))
    assert resumed.inputs["gain"] == env.inputs["gain"] and resumed.run().to_dict() == straight
    assert env.clone().calibration == env.calibration


def test_check_names_what_a_calibration_section_gets_wrong():
    contract = gain_contract()
    contract["inputs"]["mood"] = {"type": "text", "default": "calm"}
    contract["inputs"]["drift"] = {"type": "number", "default": 0}
    contract["calibration"] = {"params": {"gian": {}, "mood": {}, "drift": {}, "gain": {"low": 5, "high": 2}},
                               "targets": {"levle": 30}, "inputs": {"gain": 1, "turns": 3}}
    issues = {i.path: i.message for i in fg_env.check(contract, rounds=0) if i.severity == "error"}
    assert issues["calibration.params.gian"] == "'gian' is not a declared input"
    assert "only number and int inputs" in issues["calibration.params.mood"]
    assert issues["calibration.params.drift"] == "has no range"
    assert issues["calibration.params.gain"] == "low 5 must be below high 2"
    assert issues["calibration.inputs.gain"] == "'gain' is fitted; a pilot input cannot also fix it"
    assert issues["calibration.inputs.turns"] == "'turns' is not a declared input"
    assert issues["calibration.targets.levle"] == "'levle' is not an output or metric"
