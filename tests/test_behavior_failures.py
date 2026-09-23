"""Behavior checks retain actionable failures instead of retrying or masking them."""
import pytest

import fg_env
from fg_env.analysis import runner
from fg_env.errors import InputError
from fg_env.experiments import Job, run_jobs


def contract(expr="$round == 0 or $inputs.capacity == 10"):
    return {
        "name": "Staffed capacity", "clock": {"rounds": 1},
        "types": {"worker": {}},
        "inputs": {"capacity": {"type": "int", "default": 10, "min": 1, "max": 20}},
        "invariants": [{"expr": expr, "why": "capacity must match staffed capacity"}],
        "outputs": {"capacity": "$inputs.capacity"},
    }


@pytest.mark.parametrize("at_build", [False, True])
def test_all_failed_variants_keep_the_real_cause(at_build):
    c = contract("$inputs.capacity == 10") if at_build else contract()
    report = fg_env.analysis.behavior_checks(c, runs=2)
    finding = next(f for f in report.findings if f.code == "input_breaks_runs")
    assert finding.subject == "inputs.capacity"
    assert "capacity must match staffed capacity" in finding.message
    assert "invariants[0]" in finding.evidence["error"]
    assert finding.evidence["value"] == 5


def test_failed_baseline_participants_are_not_run_again_for_diagnostics():
    c = contract()
    c["types"]["worker"] = {"agent": True}
    c["entities"] = {"staff": {"type": "worker"}}
    c["actions"] = {"work": {"by": "worker", "do": []}}
    calls = []

    def broken(wake):
        calls.append(wake.entity_id)
        raise ValueError("participant adapter unavailable")

    report = fg_env.analysis.behavior_checks(c, runs=2, participants=broken)
    assert not report.ok
    assert "participant adapter unavailable" in report.report()
    assert calls == ["staff", "staff"]


@pytest.mark.parametrize("workers", [1, 2])
def test_job_specific_build_failures_do_not_discard_other_jobs(workers):
    results = run_jobs(contract("$inputs.capacity == 10"), [Job({"capacity": 5}, None, 3), Job({}, None, 4)], workers=workers)
    assert [r.status for r in results] == ["failed", "completed"]
    assert "capacity must match staffed capacity" in results[0].error
    assert results[1].outputs == {"capacity": 10}


def test_invalid_input_shape_still_fails_fast():
    with pytest.raises(InputError):
        run_jobs(contract(), [Job({"capacity": "wrong"}, None, 3)])


def test_other_analyses_still_reject_all_failed_ensembles():
    with pytest.raises(runner.AnalysisError, match="capacity must match staffed capacity"):
        runner.run_jobs(contract("false"), [Job({}, None, 3)])


def test_all_failed_builds_are_an_actionable_behavior_report():
    report = fg_env.analysis.behavior_checks(contract("false"), runs=2)
    assert not report.ok
    assert "invariants[0]" in report.report()
    assert "capacity must match staffed capacity" in report.report()
    assert report.tested_inputs == []
    assert report.untested_inputs == ["capacity"]


def test_mixed_variant_outcomes_preserve_failure_and_do_not_claim_no_effect():
    report = fg_env.analysis.behavior_checks(contract("$inputs.capacity >= 10"), runs=2)
    found = [f for f in report.findings if f.subject == "inputs.capacity"]
    assert len(found) == 1 and found[0].code == "input_breaks_runs"
    assert found[0].evidence["value"] == 5
    assert "capacity must match staffed capacity" in found[0].evidence["error"]


def test_bad_input_after_a_valid_but_failing_build_still_fails_fast():
    with pytest.raises(InputError):
        run_jobs(contract("false"), [Job({}, None, 3), Job({"capacity": "wrong"}, None, 4)])
