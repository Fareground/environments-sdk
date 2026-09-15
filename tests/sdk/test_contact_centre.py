"""The contact-centre example: its history is what its truth arm records, the model estimated from that history
recovers the truth and forecasts the held-out weeks, and its plan, outage and callbacks behave as a centre would."""
import csv
import importlib.util
import json
import statistics
import sys
from pathlib import Path

import pytest

import fg_env

EXAMPLES = Path(__file__).parents[2] / "examples"
CONTRACT = EXAMPLES / "contracts" / "contact_centre.json"
FOLDER = CONTRACT.parent / "contact_centre"


def _example(name):
    sys.path.insert(0, str(EXAMPLES))
    try:
        spec = importlib.util.spec_from_file_location(name, EXAMPLES / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(EXAMPLES))


@pytest.fixture(scope="module")
def history():
    with open(FOLDER / "history.csv", newline="") as handle:
        return list(csv.DictReader(handle))


def _contract():
    return json.loads(CONTRACT.read_text())


def test_the_bundled_history_is_exactly_what_the_truth_arm_records(history):
    assert _example("contact_centre_history").history_rows() == history
    assert len(history) == 56 * 24
    assert {row["date"] for row in history if row["outage"] == "1"} == {"2026-07-29", "2026-08-12", "2026-09-08"}


def test_the_model_estimated_from_six_weeks_recovers_the_truth(history):
    generator = _example("contact_centre_history")
    shipped = _contract()["inputs"]
    fitted = fg_env.fit_patterns(CONTRACT).contract["inputs"]
    for name in ("calls_scale", "weekday_profile", "hours_profile"):
        assert fitted[name]["default"] == pytest.approx(shipped[name]["default"], rel=1e-9), name
    assert generator.average_handle_time(history) == pytest.approx(shipped["aht_sec"]["default"], abs=0.05)
    truth = _contract()["arms"]["truth"]["inputs"]
    assert shipped["aht_sec"]["default"] == pytest.approx(truth["aht_sec"], rel=0.02)
    assert shipped["patience_sec"]["default"] == pytest.approx(truth["patience_sec"], rel=0.15)
    true_uplifts = [uplift for day, (_, uplift) in generator.OUTAGES.items() if day < generator.TRAIN_END]
    assert shipped["outage_uplift_estimate"]["default"] == pytest.approx(statistics.fmean(true_uplifts), rel=0.25)
    for index in (0, 3, 12, 23):  # the fitted forecast of a Monday's half-hours against the true expected calls
        fitted_calls = fg_env.decompose(CONTRACT, "calls", rounds=[index + 1], inputs={"parameter_uncertainty": 0}).rows[0]
        true_calls = fg_env.decompose(fg_env.sdk.api.apply_arm(fg_env.parse(CONTRACT), "truth"), "calls", rounds=[index + 1],
                                      data_dir=FOLDER.parent).rows[0]
        assert fitted_calls["total"] == pytest.approx(true_calls["total"], rel=0.12), index


def test_the_kept_plan_is_the_one_the_arms_play_and_its_fresh_seed_check_is_recorded():
    plan = json.loads((FOLDER / "plan.json").read_text())
    arms = _contract()["arms"]
    for arm in ("recommended", "outage", "outage_with_callbacks", "callbacks"):
        assert arms[arm]["inputs"]["staffing"] == plan["best"]["staffing"], arm
    assert len(plan["best"]["staffing"]) == 24 and plan["holdout"]["seeds"] >= 40
    assert plan["constraints"] == ["centre_service_level >= 0.8 in 90% of runs",
                                   "mean of centre_intervals_below_target <= 1.5"]


def test_an_outage_cuts_service_and_callbacks_that_keep_agents_free_cut_abandonment_during_it():
    exp = fg_env.experiment(CONTRACT, arms=["recommended", "outage", "outage_with_callbacks"], runs=6, seed=5)
    mean = {arm: {key: statistics.fmean(r.outputs[key] for r in exp.arms[arm].runs)
                  for key in ("centre_service_level", "centre_abandon_rate", "centre_callbacks")}
            for arm in exp.arms}
    assert mean["recommended"]["centre_service_level"] >= 0.85
    assert mean["outage"]["centre_service_level"] < mean["recommended"]["centre_service_level"] - 0.3
    assert mean["outage_with_callbacks"]["centre_callbacks"] > 0
    assert mean["outage_with_callbacks"]["centre_abandon_rate"] < mean["outage"]["centre_abandon_rate"]


def test_the_owner_report_names_the_plan_the_monday_peak_and_the_outage():
    exp = fg_env.experiment(CONTRACT, arms=["recommended", "current", "outage"], runs=4, seed=7)
    written = fg_env.report(exp, contract=CONTRACT, control="recommended")
    text = written.markdown
    assert "Staff between" in text or "Staff " in text
    assert "day of the week (Monday) adds" in text
    assert "A network outage at 09:30 with the recommended plan: service level −" in text
    assert "round" not in text.lower()
