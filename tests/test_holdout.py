"""Holding cases out of calibration and backtests: seeded splits, out-of-sample error, and the CLI.

The contract is deterministic (y = 2a + b²), so every forecast and error below is worked out by hand.
"""
import json

import pytest

from fg_env.__main__ import main
from fg_env.analysis import backtest, calibrate
from fg_env.analysis.holdout import case_names, splits

FORMULA = {
    "name": "Formula",
    "inputs": {"a": {"type": "number", "default": 1, "min": 0, "max": 2},
               "b": {"type": "number", "default": 2, "min": 0, "max": 3}},
    "clock": {"rounds": 1},
    "types": {"thing": {"props": {"v": 0}}},
    "outputs": {"y": {"expr": "2 * $inputs.a + $inputs.b ** 2", "type": "number"}},
}

#: a = 0.6 explains every case: y = 1.2 + b².
CASES = [{"name": f"b{b}", "inputs": {"b": b}, "targets": {"y": 1.2 + b * b}} for b in (0, 1, 2)]


# --- splits ------------------------------------------------------------------------------------------------------


def test_folds_partition_the_cases_and_the_same_seed_gives_the_same_folds():
    names = ["a", "b", "c", "d", "e"]
    folds = splits(names, folds=2, seed=7)
    tests = [set(split.test) for split in folds]
    assert (sorted(len(t) for t in tests) == [2, 3] and tests[0].isdisjoint(tests[1]) and tests[0] | tests[1]
            == set(range(5)))
    assert all(set(split.train) == set(range(5)) - set(split.test) for split in folds)
    assert splits(names, folds=2, seed=7) == folds
    assert [s.label for s in folds] == ["fold 1 of 2", "fold 2 of 2"]


def test_a_test_split_takes_a_share_or_named_cases():
    names = ["a", "b", "c", "d", "e"]
    shared = splits(names, test=0.4, seed=1)[0]
    assert len(shared.test) == 2 and len(shared.train) == 3
    named = splits(names, test=["c", 4])[0]
    assert named.test == (2, 4) and named.train == (0, 1, 3)
    assert splits(names) == []


@pytest.mark.parametrize("kwargs, message", [
    ({"test": 0.5, "folds": 2}, "not both"),
    ({"folds": 6}, "from 2 to the number of cases"),
    ({"test": 1.5}, "between 0 and 1"),
    ({"test": ["zed"]}, "not a case name"),
    ({"test": ["a", "b", "c", "d", "e"]}, "every case is held out"),
])
def test_split_mistakes_say_what_to_fix(kwargs, message):
    with pytest.raises(ValueError, match=message):
        splits(["a", "b", "c", "d", "e"], **kwargs)


def test_case_names_default_and_must_be_unique():
    assert case_names([{}, {"name": "x"}]) == ["case 1", "x"]
    with pytest.raises(ValueError, match="unique"):
        case_names([{"name": "x"}, {"name": "x"}])


# --- backtest ------------------------------------------------------------------------------------------------------

#: Forecast "y > 3" is 1 or 0 exactly. y: c1 = 4, c2 = 1, c3 = 6, c4 = 0; the model misses only c4.
BINARY = [{"name": "c1", "inputs": {"a": 2, "b": 0}, "outcome": True},
          {"name": "c2", "inputs": {"a": 0, "b": 1}, "outcome": False},
          {"name": "c3", "inputs": {"a": 1, "b": 2}, "outcome": True},
          {"name": "c4", "inputs": {"a": 0, "b": 0}, "outcome": True}]
FORECAST = {"c1": 1.0, "c2": 0.0, "c3": 1.0, "c4": 0.0}
OUTCOME = {case["name"]: 1.0 if case["outcome"] else 0.0 for case in BINARY}


def test_backtest_scores_held_out_cases_against_a_climatology_from_the_rest():
    result = backtest(FORMULA, BINARY, "y", runs=2, threshold=3, test=["c3", "c4"])
    h = result.holdout
    # Training outcomes c1 True, c2 False: base rate 0.5. Held out: c3 (1 vs 1) and c4 (0 vs 1).
    # Brier (0 + 1) / 2 = 0.5; climatology Brier ((0.5 − 1)² + (0.5 − 1)²) / 2 = 0.25; skill 1 − 0.5 / 0.25 = −1.
    assert h["method"] == "test" and h["splits"][0]["train"] == ["c1", "c2"]
    assert h["out_of_sample"] == {"n": 2, "brier": pytest.approx(0.5), "reference": pytest.approx(0.25),
                                  "skill": pytest.approx(-1.0)}
    assert result.scores["brier"] == pytest.approx(0.25)  # in sample, over all four cases, unchanged
    assert "Out of sample (held-out cases" in result.report()


def test_backtest_cross_validation_pools_every_fold():
    result = backtest(FORMULA, BINARY, "y", runs=2, threshold=3, folds=2, seed=5)
    value = reference = 0.0
    for split in result.holdout["splits"]:
        base = sum(OUTCOME[name] for name in split["train"]) / len(split["train"])
        value += sum((FORECAST[name] - OUTCOME[name]) ** 2 for name in split["test"])
        reference += sum((base - OUTCOME[name]) ** 2 for name in split["test"])
    pooled = result.holdout["out_of_sample"]
    assert pooled["n"] == 4 and pooled["brier"] == pytest.approx(value / 4) == pytest.approx(0.25)
    assert pooled["reference"] == pytest.approx(reference / 4)
    assert pooled["skill"] == pytest.approx(1 - value / reference)


# --- calibration --------------------------------------------------------------------------------------------------


def test_calibration_fits_one_param_to_several_cases():
    result = calibrate(FORMULA, CASES, {"a": {}}, runs=1, budget=40)
    assert result.params["a"] == pytest.approx(0.6, abs=0.01)
    assert result.cases == ["b0", "b1", "b2"] and result.holdout is None
    assert {d["case"] for d in result.validation["targets"]} == {"b0", "b1", "b2"}
    assert "Fitted to 3 case(s)" in result.report()


def test_a_test_split_returns_the_training_fit_and_its_error_on_the_held_out_case():
    odd = CASES[:2] + [{"name": "odd", "inputs": {"b": 1}, "targets": {"y": 3.0}}]
    result = calibrate(FORMULA, odd, {"a": {}}, runs=1, budget=40, test=["odd"])
    assert result.cases == ["b0", "b1"] and result.params["a"] == pytest.approx(0.6, abs=0.01)
    row = result.holdout["splits"][0]
    assert row["test"] == ["odd"] and row["in_sample"] < 0.01
    # At a = 0.6 the odd case simulates y = 2.2 against 3.0: error (2.2 − 3) / 3 = −0.2667, a fit of 0.2667.
    a = result.params["a"]
    assert row["out_of_sample"] == pytest.approx(abs(2 * a + 1 - 3.0) / 3.0, rel=1e-9)
    assert row["out_of_sample"] == pytest.approx(0.2667, abs=0.01)
    assert "Out of sample: fit" in result.report()


def test_cross_validation_reports_error_on_every_fold_and_returns_the_fit_to_all_cases():
    result = calibrate(FORMULA, CASES, {"a": {}}, runs=1, budget=40, folds=3)
    h = result.holdout
    assert h["method"] == "folds" and len(h["splits"]) == 3
    assert sorted(name for row in h["splits"] for name in row["test"]) == ["b0", "b1", "b2"]
    assert h["out_of_sample"] < 0.02 and h["out_of_sample_sd"] is not None
    assert result.cases == ["b0", "b1", "b2"]


@pytest.mark.parametrize("targets, kwargs, message", [
    ({"y": 2.0}, {"test": 0.5}, "pass targets as a list of cases"),
    ([{"name": "x", "inputs": {"a": 1}, "targets": {"y": 2}}], {}, "fixes a, which calibrate is fitting"),
    ([{"name": "x", "inputs": {"b": 1}}], {}, "needs 'targets'"),
    (CASES, {"test": ["nope"]}, "not a case name"),
    ([{"name": "x", "targets": {"nope": 1}}], {}, "case 'x'"),
])
def test_case_mistakes_say_what_to_fix(targets, kwargs, message):
    with pytest.raises(ValueError, match=message):
        calibrate(FORMULA, targets, {"a": {}}, runs=1, budget=4, **kwargs)


def test_cli_holds_out_cases_for_calibration_and_backtests(tmp_path, capsys):
    contract, cases, outcomes = tmp_path / "formula.json", tmp_path / "cases.json", tmp_path / "outcomes.json"
    contract.write_text(json.dumps(FORMULA))
    cases.write_text(json.dumps(CASES))
    outcomes.write_text(json.dumps(BINARY))
    assert main(["calibrate", str(contract), "--cases", str(cases), "--param", "a", "--runs", "1", "--budget", "20",
                 "--test", "b2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["holdout"]["splits"][0]["test"] == ["b2"]
    assert main(["backtest", str(contract), "--cases", str(outcomes), "--output", "y", "--threshold", "3", "--runs",
                 "1", "--folds", "2"]) == 0
    assert "2-fold cross-validation" in capsys.readouterr().out
    assert main(["calibrate", str(contract), "--cases", str(cases), "--target", "y=1", "--param", "a"]) == 1
    assert "not both" in capsys.readouterr().err
