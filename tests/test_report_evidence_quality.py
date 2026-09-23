"""Recommendations need complete decision evidence, not a favorable surviving subset."""
import copy

import fg_env

CASE = {
    "name": "Alternative with failed outcomes",
    "clock": {"rounds": 1},
    "types": {"marker": {}},
    "inputs": {"fragile": {"type": "bool", "default": False}},
    "world": {"profit": 10},
    "events": [{"do": ["$world.profit = 1000 if $inputs.fragile else 10",
                        {"if": "$inputs.fragile and $random() < 0.5", "then": ["$world.profit = 1/0"]}]}],
    "outputs": {"profit": "$world.profit"},
    "arms": {"safe": {"inputs": {"fragile": False}}, "fragile": {"inputs": {"fragile": True}}},
}


def test_failed_runs_cannot_improve_an_options_recommendation():
    experiment = fg_env.experiment(CASE, runs=8, seed=19)
    statuses = [r.status for r in experiment.arms["fragile"].runs]
    assert "failed" in statuses and "completed" in statuses
    report = fg_env.analysis.report(experiment, contract=CASE, objective="max:profit")
    assert report.recommendation["option"] == "safe"
    assert report.recommendation["confidence"]["runner_up"] is None
    decision = "\n".join(report.sections[0].lines)
    assert "Fragile is excluded from recommendations" in decision and "run(s) failed" in decision
    assert "descriptive only" in decision


def test_an_unfinished_run_is_described_without_a_recommendation():
    contract = {**CASE, "clock": {"rounds": 3}}
    result = fg_env.load(contract).run(rounds=1)
    assert result.status == "running"
    report = fg_env.analysis.report(result, contract=contract, objective="max:profit")
    assert report.recommendation is None
    assert "unfinished" in "\n".join(report.sections[0].lines)


def test_missing_decision_measurements_cannot_be_silently_dropped():
    contract = copy.deepcopy(CASE)
    contract["events"][0]["do"] = ["$world.profit = 1000 if $inputs.fragile else 10"]
    contract["outputs"]["profit"] = "null if $inputs.fragile and $random() < 0.5 else $world.profit"
    experiment = fg_env.experiment(contract, runs=8, seed=19)
    assert all(r.status == "completed" for r in experiment.arms["fragile"].runs)
    values = [r.outputs["profit"] for r in experiment.arms["fragile"].runs]
    assert None in values and 1000 in values
    report = fg_env.analysis.report(experiment, contract=contract, objective="max:profit")
    assert report.recommendation["option"] == "safe"
    assert "missing finite values for profit" in "\n".join(report.sections[0].lines)


def test_an_intentionally_ended_run_remains_eligible():
    contract = copy.deepcopy(CASE)
    contract["clock"]["rounds"] = 3
    contract["events"][0]["do"] = [{"end": "settled"}]
    result = fg_env.run(contract)
    assert result.status == "ended"
    assert fg_env.analysis.report(result, contract=contract, objective="max:profit").recommendation["option"] == "run"


def test_explicit_experiment_window_is_complete_decision_evidence():
    contract = {**CASE, "clock": {"rounds": 3}}
    result = fg_env.experiment(contract, arms=["safe"], runs=2, rounds=1)
    assert result.rounds == result.to_dict()["rounds"] == 1
    assert all(r.status == "running" for r in result.arms["safe"].runs)
    assert fg_env.analysis.report(result, objective="max:profit").recommendation["option"] == "safe"


def test_budget_ended_run_is_incomplete_decision_evidence():
    result = fg_env.run({**CASE, "clock": {"rounds": 3}}, budget={"calls": 1})
    # A run with no actors will not spend calls; construct the observed budget-stop result.
    from dataclasses import replace
    result = replace(result, status="ended", ended_by="budget", budget={"exhausted": "calls"})
    assert fg_env.analysis.report(result, objective="max:profit").recommendation is None


def test_explicit_sweep_window_is_eligible_but_short_run_is_not():
    from dataclasses import replace
    contract = {**CASE, "clock": {"rounds": 3}}
    result = fg_env.analysis.sweep(contract, {"fragile": [False]}, runs=2, rounds=2)
    assert result.rounds == result.to_dict()["rounds"] == 2
    assert fg_env.analysis.report(result, objective="max:profit").recommendation is not None
    result.cells[0].runs[0] = replace(result.cells[0].runs[0], rounds=1)
    assert fg_env.analysis.report(result, objective="max:profit").recommendation is None
