"""Owner reports: a run, an experiment, a sweep or a validation told in plain words and the clock's own terms — the
decision and its outcome with ranges, what drives it, the risks, the assumptions and the fit to data."""
import contextlib
import io
import json

import pytest

import fg_env
from fg_env.__main__ import main

SHAPED = [8, 12, 15, 15, 12, 12, 8, 8]
CENTRE = {
    "name": "Small contact centre",
    "clock": {"rounds": 8, "unit": "minute", "step": 30, "start": "2026-09-14T08:00"},
    "inputs": {"staffing": {"type": "list", "default": [12] * 8},
               "surge": {"type": "number", "default": 0, "description": "ASSUMED: extra calls at an outage's peak."}},
    "types": {"clerk": {}},
    "patterns": {
        "hours": {"kind": "seasonal", "period": 240, "description": "time of day",
                  "profile": [0.6, 1.0, 1.4, 1.4, 1.2, 1.0, 0.8, 0.6]},
        "weekday": {"kind": "seasonal", "period": "week", "description": "day of the week",
                    "profile": [1.3, 1.1, 1.0, 1.0, 0.9, 0.6, 0.5]},
        "calls": {"kind": "product", "scale": 55, "of": ["hours", "weekday"]},
        "outage": {"kind": "lifecycle", "start": "2026-09-14T09:00", "before": 1, "peak": "1 + $inputs.surge",
                   "floor": 1, "half_life": 60}},
    "mechanisms": {"centre": {"kind": "operations", "mode": "queue",
                              "channels": {"calls": {"arrivals": "$pattern.calls * $pattern.outage",
                                                     "service": {"mean": 240}, "patience": {"mean": 180},
                                                     "threshold": 20, "target": 0.75}},
                              "servers": {"agents": {"staff": "$inputs.staffing[$interval]", "cost": 30}}}},
    "arms": {"lean": {"description": "8 agents all morning", "inputs": {"staffing": [8] * 8}},
             "shaped": {"description": "staffing shaped to the morning", "inputs": {"staffing": SHAPED}},
             "rich": {"description": "16 agents all morning", "inputs": {"staffing": [16] * 8}},
             "outage": {"description": "an outage at 09:00", "inputs": {"staffing": SHAPED, "surge": 2.0}}},
}


@pytest.fixture(scope="module")
def experiment():
    return fg_env.experiment(CENTRE, runs=6, seed=2)


def _section(written, title):
    return next(s for s in written.sections if s.title == title)


def test_an_owner_report_recommends_the_cheapest_staffing_that_meets_the_service_target(experiment):
    written = fg_env.report(experiment, contract=CENTRE)
    assert written.recommendation["option"] == "shaped"
    assert written.recommendation["objective"] == "min:centre_cost"
    text = written.markdown
    assert "Choose staffing shaped to the morning." in text
    assert "Staff 8 agents 08:00–08:30, 12 agents 08:30–09:00, 15 agents 09:00–10:00" in text
    assert "Expected: service level" in text and "80% range" in text and "staffing cost $" in text
    assert "**Staffing plan**" in text and "| 09:00–10:00 | 15 |" in text
    assert "round" not in text.lower()


def test_the_report_names_the_pattern_and_the_shock_behind_the_outcome(experiment):
    written = fg_env.report(experiment, contract=CENTRE)
    causes = _section(written, "What drives it").lines
    assert any("The busiest half-hour is" in line and "day of the week (Monday) adds" in line for line in causes)
    assert any(line.startswith("An outage at 09:00: service level −") for line in causes)
    assert any(line.startswith("With an outage at 09:00: service level") for line in _section(written, "Risks").lines)
    assumed = _section(written, "What the model assumes").lines
    assert any("give up after waiting 180 seconds on average" in line for line in assumed)
    assert any("ASSUMED" in line for line in assumed)


def test_an_explicit_rule_and_the_analyst_audience_add_the_method(experiment):
    written = fg_env.report(experiment, "analyst", contract=CENTRE, objective="max:centre_service_level",
                            require={"centre_cost": "<= 1500"})
    assert written.recommendation["option"] == "shaped"
    method = _section(written, "Method")
    assert any("common random numbers" in line for line in method.lines)
    assert method.tables[0].title == "Every output"
    with pytest.raises(ValueError, match="min:<output>"):
        fg_env.report(experiment, objective="cheapest:centre_cost")
    with pytest.raises(ValueError, match="'>= 0.8'"):
        fg_env.report(experiment, require={"centre_service_level": "at least 0.8"})


def test_a_single_run_says_it_has_no_range_and_works_without_the_contract(experiment):
    written = fg_env.report(experiment.arms["shaped"].runs[0])
    lines = _section(written, "What the model says").lines
    assert any("one run of the model" in line for line in lines)
    assert any(line.startswith("Staff 8 servers") or line.startswith("Staff 8 staff") for line in lines)
    assert "The contract was not given" in written.markdown


def test_a_validation_is_told_as_how_far_off_and_how_often_ranges_held():
    truth = fg_env.run(CENTRE, seed=11, arm="shaped")
    cases = [{"name": "Monday", "inputs": {"staffing": SHAPED},
              "actuals": {"centre_offered_by_interval": truth.outputs["centre_offered_by_interval"]}}]
    checked = fg_env.validate(CENTRE, cases, runs=6)
    written = fg_env.report(checked, contract=CENTRE)
    [line, cases_line] = _section(written, "How well it matched the data").lines
    assert line.startswith("Customers by half-hour: off by") and "80% ranges held" in line
    assert cases_line == "Checked on 1 case(s) × 6 run(s)."


def test_a_sweep_names_the_input_that_moves_the_outcome_most():
    contract = {**CENTRE, "inputs": {**CENTRE["inputs"], "agents": {"type": "int", "default": 12, "min": 6, "max": 18}}}
    contract["mechanisms"] = json.loads(json.dumps(CENTRE["mechanisms"]))
    contract["mechanisms"]["centre"]["servers"]["agents"]["staff"] = "$inputs.agents"
    swept = fg_env.sweep(contract, {"agents": [8, 12, 16]}, runs=3)
    causes = _section(fg_env.report(swept, contract=contract), "What drives it").lines
    assert any(line.startswith("Agents moves service level from") for line in causes)


def test_a_report_exports_markdown_and_json(experiment, tmp_path):
    written = fg_env.report(experiment, contract=CENTRE)
    written.save(tmp_path / "report.md")
    written.save(tmp_path / "report.json")
    assert (tmp_path / "report.md").read_text().startswith("# Small contact centre")
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["recommendation"]["option"] == "shaped"
    assert [s["title"] for s in data["sections"]] == ["Recommendation", "What drives it", "Risks",
                                                     "What the model assumes", "How well it matched the data"]


def test_the_command_line_reports_a_contract_across_its_arms_and_a_saved_run(tmp_path):
    path = tmp_path / "centre.json"
    path.write_text(json.dumps(CENTRE))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["report", str(path), "--runs", "3", "--arm", "lean", "--arm", "shaped", "--arm", "rich"]) == 0
    assert "## Recommendation" in out.getvalue() and "Staffing plan" in out.getvalue()
    fg_env.run(CENTRE, seed=1).save(tmp_path / "run.json")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["report", str(tmp_path / "run.json"), "--contract", str(path), "--json"]) == 0
    assert json.loads(out.getvalue())["kind"] == "run"
