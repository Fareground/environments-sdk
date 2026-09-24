"""The analysis layer: statistics against hand-computed values, analyses on example and inline contracts.

Expected numbers in the statistics tests were computed by hand (or with an independent formula
noted beside them), never by calling the code under test.
"""
import argparse
import json
import math
from pathlib import Path

import pytest

import fg_env
from fg_env.analysis import (
    AnalysisError,
    backtest,
    behavior_checks,
    brier,
    brier_multiclass,
    calibrate,
    chain,
    compare,
    crps_ensemble,
    drivers,
    ece,
    highlights,
    interval_coverage,
    log_loss,
    log_loss_multiclass,
    murphy,
    narrative,
    precision,
    reliability,
    runner,
    score,
    sensitivity,
    skill_score,
    statistic,
    sweep,
    unit_search,
)
from fg_env.analysis.calibrate import evaluate_targets, parse_targets
from fg_env.analysis.cli import add_analysis_commands
from fg_env.analysis.compare import welch
from fg_env.analysis.facts import autocorrelation, excess_kurtosis, max_drawdown
from fg_env.analysis.highlights import move_score
from fg_env.analysis.stats import (
    correlation_ratio,
    estimate,
    fisher_interval,
    latin_hypercube,
    levels,
    pearson,
    quantile,
    ranks,
    spearman,
    t_quantile,
    wasserstein,
    wilson,
)
from fg_env.errors import InputError
from fg_env.runtime.measure import RunResult

EXAMPLES = Path(__file__).parents[1] / "examples" / "contracts"
EPIDEMIC = EXAMPLES / "town_epidemic.json"
EXCHANGE = EXAMPLES / "order_book_exchange.json"
LEMONADE = EXAMPLES / "lemonade_stand.json"

#: y = 2a + b²; c is declared but unused. Deterministic.
FORMULA = {
    "name": "Formula",
    "inputs": {"a": {"type": "number", "default": 1, "min": 0, "max": 2},
               "b": {"type": "number", "default": 2, "min": 0, "max": 3},
               "c": {"type": "number", "default": 1, "min": 0, "max": 1}},
    "clock": {"rounds": 1},
    "types": {"thing": {"props": {"v": 0}}},
    "outputs": {"y": {"expr": "2 * $inputs.a + $inputs.b ** 2", "type": "number"},
                "sum": {"expr": "$inputs.a + $inputs.b", "type": "number"},
                "gap": {"expr": "$inputs.a - $inputs.b", "type": "number"}},
}

#: A level that grows by `rate` (plus noise) every round for 10 rounds.
NOISY = {
    "name": "Noisy growth",
    "inputs": {"rate": {"type": "number", "default": 0.5, "min": 0, "max": 1}},
    "clock": {"rounds": 10},
    "types": {"thing": {"props": {"v": 0}}},
    "world": {"level": 0},
    "events": [{"do": ["$world.level += $inputs.rate + $normal(0, 0.1)"]}],
    "metrics": {"level": "$world.level"},
    "outputs": {"level": {"expr": "$world.level", "type": "number"},
                "high": {"expr": "$world.level > 5", "type": "bool"}},
}

#: Five planted defects: an unused input, an unreachable action, a stage that never runs, a constant
#: metric, and an output that cannot vary.
BROKEN = {
    "name": "Broken shop",
    "inputs": {"price": {"type": "number", "default": 5, "min": 1, "max": 10},
               "unused": {"type": "number", "default": 3, "min": 0, "max": 10}},
    "clock": {"rounds": 4},
    "types": {"shopper": {"agent": True, "props": {"cash": 20, "bought": 0}}},
    "entities": {"ann": {"type": "shopper"}, "bob": {"type": "shopper"}},
    "world": {"sales": 0},
    "actions": {
        "buy": {"by": "shopper", "when": ["$actor.cash >= $inputs.price"],
                "do": ["$actor.cash -= $inputs.price", "$actor.bought += 1", "$world.sales += 1"]},
        "steal": {"by": "shopper", "when": ["$actor.cash < 0"], "do": ["$actor.bought += 1"]},
        "wait": {"by": "shopper", "do": []},
    },
    "stages": [{"name": "shop", "actions": ["buy", "wait"]},
               {"name": "night", "when": "$round > 100", "actions": ["wait"]}],
    "metrics": {"sales": "$world.sales", "constant": "7"},
    "outputs": {"sales": {"expr": "$world.sales", "type": "int"}, "label": {"expr": "'shop'", "type": "text"}},
}


def fake_run(seed=0, outputs=None, inputs=None, series=None, metrics=None, events=None, status="completed",
             rounds=None, ended_by=None, arm=None):
    series = series or {}
    return RunResult(status=status, ended_by=ended_by, rounds=rounds if rounds is not None else
                     max([len(v) for v in series.values()] or [1]), seed=seed, arm=arm, inputs=inputs or {},
                     outputs=outputs or {}, metrics=metrics or {}, series=series, events=events or [])


# --- statistics -------------------------------------------------------------------------------------


def test_t_quantile_matches_published_tables():
    assert t_quantile(0.975, 4) == pytest.approx(2.776445, abs=1e-6)
    assert t_quantile(0.975, 1) == pytest.approx(12.706205, abs=1e-5)
    assert t_quantile(0.95, 10) == pytest.approx(1.812461, abs=1e-6)
    assert t_quantile(0.025, 4) == pytest.approx(-2.776445, abs=1e-6)
    assert t_quantile(0.975, 1e9) == pytest.approx(1.959964, abs=1e-6)
    with pytest.raises(ValueError):
        t_quantile(1.0, 3)


def test_estimate_uses_student_t():
    e = estimate([1, 2, 3, 4, 5])
    # sd = sqrt(10/4) = 1.5811388; half-width = 2.776445 × 1.5811388/√5 = 1.963243
    assert (e.mean, e.sd) == (3, pytest.approx(1.5811388))
    assert (e.low, e.high) == (pytest.approx(1.036757, abs=1e-5), pytest.approx(4.963243, abs=1e-5))
    single = estimate([7.0])
    assert single.mean == 7.0 and single.low is None and not single.excludes_zero


def test_wilson_interval():
    # p = 0.8, z = 1.959964: centre 0.716741, half-width 0.226575
    low, high = wilson(8, 10)
    assert low == pytest.approx(0.490166, abs=1e-5) and high == pytest.approx(0.943316, abs=1e-5)
    assert wilson(0, 5)[0] == pytest.approx(0.0, abs=1e-12) and wilson(0, 5)[1] > 0.4


def test_quantiles_ranks_and_correlations():
    assert quantile([1, 2, 3, 4], 0.25) == pytest.approx(1.75)
    assert ranks([10, 20, 20, 30]) == [1, 2.5, 2.5, 4]
    # dx = (−1.5, −.5, .5, 1.5), dy = (−3, −1, 0, 4): r = 11/√(5·26)
    assert pearson([1, 2, 3, 4], [2, 4, 5, 9]) == pytest.approx(0.964764, abs=1e-6)
    assert spearman([1, 2, 3, 4], [2, 4, 5, 9]) == pytest.approx(1.0)
    assert pearson([1, 1, 1], [1, 2, 3]) is None
    low, high = fisher_interval(0.5, 28)  # atanh(.5) ± 1.96 × 1.06/5, back through tanh
    assert low == pytest.approx(0.133001, abs=1e-5) and high == pytest.approx(0.746418, abs=1e-5)


def test_correlation_ratio_and_its_chance_correction():
    # groups {1,3} and {5,7}: between 16, total 20
    assert correlation_ratio([0, 0, 1, 1], [1, 3, 5, 7], bins=2) == pytest.approx(0.8)
    # ε² = (16 − (2−1)·(4/2)) / (20 + 2)
    assert correlation_ratio([0, 0, 1, 1], [1, 3, 5, 7], bins=2, adjusted=True) == pytest.approx(14 / 22)
    assert correlation_ratio([0, 1], [3, 3], bins=2) is None


def test_wasserstein_distance():
    assert wasserstein([0, 1], [1, 2]) == pytest.approx(1.0)
    assert wasserstein([0, 0, 3], [1]) == pytest.approx(4 / 3)  # mean |x − 1|


def test_designs():
    import random

    points = latin_hypercube(5, 2, random.Random(3))
    for d in range(2):
        assert sorted(int(p[d] * 5) for p in points) == [0, 1, 2, 3, 4]
    assert levels(1, 100, 3, log=True) == pytest.approx([1, 10, 100])
    assert levels(0, 1, 5) == pytest.approx([0, 0.25, 0.5, 0.75, 1])


def test_named_statistics():
    # deviations (−1.5, −.5, .5, 1.5): lag-1 covariance 1.25 over variance sum 5
    assert autocorrelation([1, 2, 3, 4]) == pytest.approx(0.25)
    assert statistic("autocorrelation:2", [1, 2, 3, 4, 5]) == pytest.approx(-0.1)
    assert excess_kurtosis([1, 2, 3, 4]) == pytest.approx(-1.36)  # 2.5625/1.25² − 3
    # log returns ln 1.1 and ln 0.9: sd = |difference|/√2
    assert statistic("volatility", [100, 110, 99]) == pytest.approx(0.141896, abs=1e-6)
    assert max_drawdown([100, 120, 90, 130]) == pytest.approx(0.25)
    assert statistic("trend", [1, 3, 5]) == pytest.approx(2.0)
    assert statistic("time_to_peak", [1, 5, 2]) == 2.0
    with pytest.raises(ValueError, match="unknown statistic"):
        statistic("vibes", [1, 2])
    with pytest.raises(ValueError, match="positive series"):
        statistic("volatility", [1, 0, 2])


# --- scoring ----------------------------------------------------------------------------------------


def test_brier_and_log_loss_binary():
    assert brier([0.9, 0.2, 0.6], [1, 0, 0]) == pytest.approx((0.01 + 0.04 + 0.36) / 3)
    assert log_loss([0.9, 0.2, 0.6], [True, False, False]) == pytest.approx(
        (-math.log(0.9) - math.log(0.8) - math.log(0.4)) / 3)
    with pytest.raises(ValueError):
        brier([0.5], [1, 0])
    with pytest.raises(ValueError):
        brier([1.2], [1])


def test_multiclass_scores():
    forecasts = [{"a": 0.7, "b": 0.2, "c": 0.1}, {"a": 0.2, "b": 0.5, "c": 0.3}]
    # (0.09 + 0.04 + 0.01) and (0.04 + 0.25 + 0.49)
    assert brier_multiclass(forecasts, ["a", "c"]) == pytest.approx(0.46)
    assert log_loss_multiclass(forecasts, ["a", "c"]) == pytest.approx((-math.log(0.7) - math.log(0.3)) / 2)


def test_crps_of_ensembles():
    # E|X − y| = 2/3; ½E|X − X′| = 8/18
    assert crps_ensemble([1, 2, 3], 2) == pytest.approx(2 / 3 - 8 / 18)
    assert crps_ensemble([1, 2, 3], 5) == pytest.approx(3 - 8 / 18)
    assert crps_ensemble([3], 1) == pytest.approx(2.0)


def test_interval_coverage():
    out = interval_coverage([(0, 2), (1, 3), (5, 6)], [1, 4, 5.5], nominal=0.9)
    assert out["coverage"] == pytest.approx(2 / 3) and out["mean_width"] == pytest.approx(5 / 3)
    assert out["gap"] == pytest.approx(2 / 3 - 0.9)


def test_reliability_ece_and_murphy_decomposition():
    probs, outcomes = [0.1, 0.1, 0.8, 0.8, 0.8], [0, 1, 1, 1, 0]
    bins = reliability(probs, outcomes)
    assert [(b.n, b.mean_forecast, b.observed) for b in bins] == [(2, pytest.approx(0.1), 0.5),
                                                                  (3, pytest.approx(0.8), pytest.approx(2 / 3))]
    assert ece(probs, outcomes) == pytest.approx(2 / 5 * 0.4 + 3 / 5 * (0.8 - 2 / 3))
    m = murphy(probs, outcomes)
    assert m["brier"] == pytest.approx(1.54 / 5)
    assert m["reliability"] == pytest.approx((2 * 0.16 + 3 * (0.8 - 2 / 3) ** 2) / 5)
    assert m["resolution"] == pytest.approx((2 * 0.1 ** 2 + 3 * (2 / 3 - 0.6) ** 2) / 5)
    assert m["uncertainty"] == pytest.approx(0.24) and m["residual"] == pytest.approx(0.0, abs=1e-12)
    result = score(probs, outcomes)
    assert result["kind"] == "binary" and result["skill"] == pytest.approx(1 - 0.308 / 0.24)
    assert skill_score(0.1, 0.2) == pytest.approx(0.5)


def test_score_dispatches_by_forecast_shape():
    ensemble = score([[1, 2, 3], [4, 5, 6]], [2, 5])
    assert ensemble["kind"] == "ensemble"
    assert ensemble["crps"] == pytest.approx(2 / 3 - 8 / 18)
    categorical = score([{"x": 0.9, "y": 0.1}, {"x": 0.3, "y": 0.7}], ["x", "x"])
    assert categorical["kind"] == "categorical" and categorical["accuracy"] == 0.5
    interval = score([(0, 1), (0, 1)], [0.5, 2], kind="interval")
    assert interval["coverage"] == 0.5


# --- runner -----------------------------------------------------------------------------------------


def test_runner_seeds_and_results_match_experiment():
    exp = fg_env.experiment(LEMONADE, runs=3, seed=4)
    assert runner.run_seeds(4, 3) == exp.seeds
    jobs = [runner.Job({}, None, s) for s in exp.seeds]
    ours = runner.run_jobs(fg_env.parse(LEMONADE), jobs)
    assert [r.outputs for r in ours] == [r.outputs for r in exp.arms["baseline"].runs]
    assert runner.run_seeds(4, 2, start=1) == exp.seeds[1:3]


def test_runner_process_pool_matches_serial():
    jobs = [runner.Job({"rate": 0.3}, None, s) for s in runner.run_seeds(0, 4)]
    serial = runner.run_jobs(NOISY, jobs)
    with runner.worker_pool(2, None) as pool:
        pooled = runner.run_jobs(NOISY, jobs, workers=2, pool=pool)
    assert [r.outputs for r in serial] == [r.outputs for r in pooled]


def test_runner_fails_fast_on_shared_problems():
    with pytest.raises(ValueError, match="is not an entity id, a type"):
        runner.run_jobs(LEMONADE, [runner.Job({}, None, 1)], participants={"nobody": "random"})
    with pytest.raises(InputError):
        runner.run_jobs(NOISY, [runner.Job({"rate": 7}, None, 1)])
    with pytest.raises(ValueError, match="neither an output nor a metric"):
        runner.resolve_measure(fg_env.parse(NOISY), "nope")


def test_runner_reports_when_every_run_fails():
    failing = {**NOISY, "invariants": ["$round < 2"]}
    with pytest.raises(AnalysisError, match="all 2 run"):
        runner.run_jobs(failing, [runner.Job({}, None, s) for s in (1, 2)])


# --- sweep ------------------------------------------------------------------------------------------


def test_sweep_lockdown_trigger_monotone_peak_infected():
    result = sweep(EPIDEMIC, {"lockdown_trigger": [2, 6, 14]}, runs=4, outputs=["peak_infected"],
                   inputs={"residents": 30, "lift_below": 0}, rounds=30, workers=4)
    effect = result.main_effects()["peak_infected"]["lockdown_trigger"]
    assert result.trend("lockdown_trigger", "peak_infected") == "increasing"
    assert effect["high_minus_low"]["low"] > 0
    assert "Main effects" in result.report()
    assert len(result.cells) == 3 and all(len(c.runs) == 4 for c in result.cells)


def test_sweep_factorial_main_effects_are_exact_on_a_formula():
    result = sweep(FORMULA, {"a": [0, 1, 2], "b": {"low": 0, "high": 2, "steps": 3}}, runs=2, outputs=["y"])
    assert result.params["b"] == [0.0, 1.0, 2.0] and len(result.cells) == 9
    a = result.main_effects()["y"]["a"]
    # mean over b ∈ {0, 1, 2} of 2a + b² = 2a + 5/3
    assert [row["mean"] for row in a["levels"]] == pytest.approx([5 / 3, 2 + 5 / 3, 4 + 5 / 3])
    assert a["trend"] == "increasing" and a["high_minus_low"]["mean"] == pytest.approx(4.0)
    assert result.to_dict()["cells"][0]["summary"]["y"]["mean"] == 0


def test_sweep_latin_hypercube_is_deterministic_and_in_range():
    first = sweep(FORMULA, {"a": {}, "b": {}}, runs=1, design="lhs", samples=8, outputs=["y"], seed=5)
    second = sweep(FORMULA, {"a": {}, "b": {}}, runs=1, design="lhs", samples=8, outputs=["y"], seed=5)
    assert first.params == second.params
    assert all(0 <= a <= 2 for a in first.params["a"]) and all(0 <= b <= 3 for b in first.params["b"])
    assert first.main_effects()["y"]["b"]["spearman"] > 0.5


def test_sweep_rejects_bad_requests():
    with pytest.raises(ValueError, match="not a declared input"):
        sweep(FORMULA, {"zeta": [1]}, runs=1)
    with pytest.raises(ValueError, match="both fixed and swept"):
        sweep(FORMULA, {"a": [1]}, inputs={"a": 2}, runs=1)
    with pytest.raises(ValueError, match="is a metric"):
        sweep(NOISY, {"rate": [0.1]}, runs=1, outputs=["metrics.level"])


# --- sensitivity ------------------------------------------------------------------------------------


def test_sensitivity_one_at_a_time_elasticities():
    result = sensitivity(FORMULA, ["a", "b", "c"], "y", runs=2)
    by = {row["input"]: row for row in result.ranking}
    # at a=1, b=2 (y=6): elasticity of a = 2·1/6, of b = 2b·b/6; c is unused
    assert by["a"]["value"] == pytest.approx(1 / 3)
    assert by["b"]["value"] == pytest.approx(4 / 3)
    assert by["c"]["value"] == pytest.approx(0.0)
    assert [row["input"] for row in result.ranking] == ["b", "a", "c"]


def test_sensitivity_morris_screening():
    result = sensitivity(FORMULA, ["a", "b", "c"], "y", method="morris", runs=1, trajectories=5, seed=2)
    by = {row["input"]: row for row in result.ranking}
    assert by["a"]["value"] == pytest.approx(4.0)  # slope 2 over a range of 2
    assert by["a"]["sigma"] == pytest.approx(0.0, abs=1e-9)
    assert by["c"]["value"] == 0.0
    assert [row["input"] for row in result.ranking] == ["b", "a", "c"]


def test_sensitivity_sobol_lite_ranks_and_zeroes_unused_inputs():
    result = sensitivity(FORMULA, {"a": None, "b": None, "c": None}, "y", method="sobol", runs=1, samples=25, seed=1)
    by = {row["input"]: row for row in result.ranking}
    assert result.ranking[0]["input"] == "b"
    assert by["c"]["value"] < 0.15
    assert "first-order share" in result.report()


# --- calibration ------------------------------------------------------------------------------------


def test_calibration_recovers_a_known_input_on_held_out_seeds():
    truth = 0.35
    synthetic = runner.run_jobs(NOISY, [runner.Job({"rate": truth}, None, s) for s in runner.run_seeds(999, 8)])
    goal = sum(r.outputs["level"] for r in synthetic) / len(synthetic)
    result = calibrate(NOISY, {"level": goal}, {"rate": {}}, runs=6, budget=14)
    assert result.method == "bisection"
    assert result.params["rate"] == pytest.approx(truth, abs=0.03)
    assert result.validation["runs"] == 6 and result.validation["fit"] < 0.05
    u = result.uncertainty["rate"]
    assert u["low"] <= result.params["rate"] <= u["high"]
    assert "held-out" in result.report()


def test_calibration_of_several_params_with_nelder_mead_and_cross_entropy():
    targets = {"sum": 1.0, "gap": 0.2}  # a = 0.6, b = 0.4
    params = {"a": {"low": 0, "high": 1}, "b": {"low": 0, "high": 1}}
    simplex = calibrate(FORMULA, targets, params, runs=1, budget=120)
    assert simplex.method == "nelder_mead"
    assert simplex.params["a"] == pytest.approx(0.6, abs=0.02) and simplex.params["b"] == pytest.approx(0.4, abs=0.02)
    ce = calibrate(FORMULA, targets, params, runs=1, budget=120, method="cross_entropy", seed=3)
    assert ce.fit < 0.2


def test_calibration_to_a_stylized_fact():
    target = {"level": {"stat": "trend", "of": "level", "value": 0.35}}
    result = calibrate(NOISY, target, {"rate": {}}, runs=3, budget=16)
    assert result.method == "golden"
    assert result.params["rate"] == pytest.approx(0.35, abs=0.05)


def test_target_errors_by_hand():
    contract = fg_env.parse(NOISY)
    value = parse_targets(contract, {"level": 10})
    fit, details = evaluate_targets(value, [fake_run(outputs={"level": v}) for v in (9, 11, 13)])
    assert details[0]["error"] == pytest.approx(0.1) and fit == pytest.approx(0.1)  # (11 − 10)/10
    path = parse_targets(contract, {"series.level": [1, 2, 3]})
    fit, details = evaluate_targets(path,
                                    [fake_run(series={"level": [1, 2, 4]}), fake_run(series={"level": [1, 2, 2]})])
    assert details[0]["rmse"] == pytest.approx(0.0) and fit == pytest.approx(0.0)
    spread = parse_targets(contract, {"level": {"distribution": [0, 1]}})
    fit, _ = evaluate_targets(spread, [fake_run(outputs={"level": v}) for v in (1, 2)])
    assert fit == pytest.approx(1 / math.sqrt(0.5))  # distance 1 over sd([0, 1])
    with pytest.raises(ValueError, match="unknown statistic"):
        parse_targets(contract, {"level": {"stat": "vibes", "value": 1}})


def test_search_algorithms_find_known_optima():
    def evaluator(fn, budget=60):
        return unit_search.Evaluator(lambda p: (fn(p), None), key=lambda p: tuple(round(x, 9) for x in p),
                                     budget=budget)

    e = evaluator(lambda p: abs(p[0] - 0.3))

    def signed(u):
        e((u,))  # record and budget the point, as calibrate does
        return u - 0.3

    assert unit_search.bisection(signed, e, 40)
    assert e.best[0][0] == pytest.approx(0.3, abs=1e-3)
    flat = evaluator(lambda p: 1.0)
    assert not unit_search.bisection(lambda u: flat((u,)), flat, 40)  # no sign change: nothing to bisect
    e = evaluator(lambda p: (p[0] - 0.7) ** 2)
    unit_search.golden_section(e, 40)
    assert e.best[0][0] == pytest.approx(0.7, abs=1e-3)
    e = evaluator(lambda p: (p[0] - 0.2) ** 2 + (p[1] - 0.8) ** 2, budget=200)
    unit_search.nelder_mead(e, 2)
    assert e.best[0] == pytest.approx((0.2, 0.8), abs=1e-3)
    e = evaluator(lambda p: 1.0, budget=3)
    unit_search.nelder_mead(e, 2)
    assert len(e.history) == 3  # the budget is a hard cap


# --- backtest and precision -------------------------------------------------------------------------


def test_backtest_binary_and_ensemble():
    cases = [{"inputs": {"rate": 0.2}, "outcome": False, "name": "slow"},
             {"inputs": {"rate": 0.8}, "outcome": True, "name": "fast"}]
    binary = backtest(NOISY, cases, "high", runs=6)
    assert binary.kind == "binary" and binary.scores["brier"] == pytest.approx(0.0)
    assert [c["forecast"] for c in binary.cases] == [0.0, 1.0]
    numbers = backtest(NOISY, [{"inputs": {"rate": 0.2}, "outcome": 2.0}, {"inputs": {"rate": 0.8}, "outcome": 8.0}],
                       "level", runs=6)
    assert numbers.kind == "ensemble" and numbers.scores["crps"] < 0.3
    thresholded = backtest(NOISY,
                           [{"inputs": {"rate": 0.2}, "outcome": 2.0}, {"inputs": {"rate": 0.8}, "outcome": 8.0}],
                           "level", runs=6, threshold=5)
    assert thresholded.kind == "binary" and thresholded.scores["brier"] == pytest.approx(0.0)
    assert "Brier" in binary.report()


def test_precision_stops_when_the_target_is_met():
    met = precision(NOISY, "level", target_se=0.2, batch=4)
    assert met.converged and met.runs == 8  # two batches is the floor
    missed = precision(NOISY, "level", target_se=0.001, batch=4, max_runs=12)
    assert not missed.converged and [t["runs"] for t in missed.trace] == [4, 8, 12]
    assert missed.runs_needed > 12
    with pytest.raises(ValueError, match="exactly one"):
        precision(NOISY, "level")


# --- behavior checks --------------------------------------------------------------------------------


def test_behavior_checks_flag_a_broken_contract():
    report = behavior_checks(BROKEN, runs=3)
    found = set(report.codes())
    assert {("input_has_no_effect", "inputs.unused"), ("action_never_taken", "actions.steal"),
            ("stage_never_acted", "stages.night"), ("metric_constant", "metrics.constant"),
            ("output_never_varies", "outputs.label")} <= found
    assert ("input_has_no_effect", "inputs.price") not in found
    assert report.ok and "unused" in report.report()


def test_behavior_checks_report_failing_runs_and_pass_a_clean_example():
    failing = behavior_checks({**NOISY, "invariants": ["$round < 2"]}, runs=2)
    assert not failing.ok and failing.findings[0].code == "runs_fail"
    clean = behavior_checks(LEMONADE, runs=3)
    assert clean.ok


# --- highlights and narrative -----------------------------------------------------------------------


def test_move_score_by_hand():
    # others (1, 2, 1, 1, 2): median 1, MAD 0 → sd 0.547723; (3 − 1)/0.547723
    assert move_score(3, [1, 2, 1, 3, 1, 2]) == pytest.approx(3.651484, abs=1e-6)
    # every other move is 0: a one-in-four event, z = Φ⁻¹(1 − 0.25/2)
    assert move_score(2, [0, 0, 5, 0]) == pytest.approx(1.150349, abs=1e-6)


def test_highlights_on_a_synthetic_series():
    run = fake_run(series={"x": [10, 10, 10, 30, 30, 30, 12, 12]},
                   events=[{"round": 6, "kind": "news", "text": "Storm hits.", "data": {"event": "storm"}}], rounds=8)
    found = highlights(run, top=10)
    kinds = {(h.kind, h.round) for h in found}
    assert ("reversal", 4) in kinds and ("event", 6) in kinds
    assert ("peak", 4) not in kinds  # the peak is the same moment as the reversal, told once
    reversal = next(h for h in found if h.kind == "reversal")
    assert "gave back 90%" in reversal.text
    assert found == highlights(run, top=10)


def test_highlights_on_the_order_book_exchange():
    run = fg_env.run(EXCHANGE, seed=1)
    top = highlights(run, top=5)
    assert len(top) == 5
    assert (top[0].kind, top[0].subject, top[0].round) == ("move", "last_price", 9)
    texts = " ".join(h.text for h in top)
    assert "NEWS" in texts and "CIRCUIT BREAKER" in texts
    assert all(1 <= h.round <= run.rounds for h in top)
    assert [h.to_dict() for h in top] == [h.to_dict() for h in highlights(run, top=5)]
    story = narrative(run)
    assert story.startswith("Completed after 15 rounds") and "Round 9" in story and "Results:" in story


# --- drivers ----------------------------------------------------------------------------------------


def test_drivers_find_the_separating_input_and_compute_lift_by_hand():
    runs = [fake_run(seed=i, outputs={"won": i >= 10}, inputs={"x": i, "v": 1 if 8 <= i <= 17 else 0, "noise": i % 2})
            for i in range(20)]
    found = drivers(runs, "won", include=["inputs"], permutations=300)
    assert found.drivers[0].feature == "inputs.x" and found.drivers[0].lift == pytest.approx(1.0)
    assert found.drivers[0].p_value == pytest.approx(1 / 301)
    everything = drivers(runs, "won", include=["inputs"], permutations=50, alpha=0.999)
    v = next(d for d in everything.drivers if d.feature == "inputs.v")
    assert v.lift == pytest.approx(8 / 10 - 2 / 10)  # outcome in 8 of runs 8-17, 2 of the other 10
    assert all(d.feature != "inputs.noise" for d in found.drivers)


def test_drivers_leave_out_restated_outcomes_and_constant_outcomes():
    runs = [fake_run(seed=i, outputs={"deaths": i % 3}, metrics={"dead": i % 3}, inputs={"x": i}) for i in range(9)]
    result = drivers(runs, "deaths", threshold=0, permutations=20)
    assert any("metrics.dead" in note for note in result.notes)
    constant = drivers([fake_run(seed=i, outputs={"ok": True}) for i in range(5)], "ok")
    assert constant.drivers == [] and "same in every run" in constant.notes[0]


# --- compare and chain ------------------------------------------------------------------------------


def test_welch_interval_by_hand():
    w = welch([1, 2, 3], [4, 6, 8])
    # variances of the means 1/3 and 4/3; df = (5/3)² / ((1/3)²/2 + (4/3)²/2)
    assert w["difference"] == pytest.approx(4.0) and w["se"] == pytest.approx(math.sqrt(5 / 3))
    assert w["df"] == pytest.approx(2.941176, abs=1e-6)


def test_compare_paired_and_single_runs():
    a = [fake_run(seed=s, outputs={"y": y, "tag": "x"}) for s, y in zip((1, 2, 3), (1.0, 2.0, 3.0))]
    b = [fake_run(seed=s, outputs={"y": y, "tag": "x"}) for s, y in zip((1, 2, 3), (2.0, 3.5, 4.0))]
    paired = compare(a, b)
    row = paired.outputs["y"]
    assert paired.paired and row["difference"] == pytest.approx(7 / 6)
    diffs = estimate([1.0, 1.5, 1.0])
    assert (row["low"], row["high"]) == (pytest.approx(diffs.low), pytest.approx(diffs.high))
    assert paired.outputs["tag"] == {"counts": {"a": {"x": 3}, "b": {"x": 3}}}
    single = compare(a[0], b[0], labels=("before", "after"))
    assert single.outputs["y"]["relative"] == pytest.approx(1.0) and "after − before" in single.report()


def test_chain_propagates_first_stage_uncertainty():
    result = chain(NOISY, NOISY, {"rate": "high"}, runs=6, first_inputs={"rate": 0.5})
    assert set(result.scenarios) == {"low", "point", "high"}
    rate = result.bindings["rate"]
    assert rate["low"] <= rate["point"] <= rate["high"]
    assert result.envelope["level"]["low"] <= result.scenarios["point"]["summary"]["level"]["mean"] <= \
        result.envelope["level"]["high"]
    with pytest.raises(ValueError, match="neither an output nor a metric"):
        chain(NOISY, NOISY, {"rate": "nope"}, runs=2)


# --- command line -----------------------------------------------------------------------------------


def _cli(argv):
    parser = argparse.ArgumentParser(prog="fg-env")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add_analysis_commands(sub)
    args = parser.parse_args(argv)
    return args.func(args)


def test_cli_commands(tmp_path, capsys):
    formula, broken, noisy = tmp_path / "formula.json", tmp_path / "broken.json", tmp_path / "noisy.json"
    formula.write_text(json.dumps(FORMULA))
    broken.write_text(json.dumps(BROKEN))
    noisy.write_text(json.dumps(NOISY))
    assert _cli(["sweep", str(formula), "--param", "a=0,1,2", "--param", "b=0:2:3", "--runs", "1", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["cells"]) == 9
    assert _cli(["sensitivity", str(formula), "--output", "y", "--vary", "a", "--vary", "b=0:3", "--runs", "1"]) == 0
    assert "elasticity" in capsys.readouterr().out
    assert _cli(["calibrate", str(formula), "--target", "y=4", "--param", "a", "--input", "b=0", "--runs", "1"]) == 0
    assert "Best inputs: a=2" in capsys.readouterr().out
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{"inputs": {"rate": 0.2}, "outcome": False},
                                 {"inputs": {"rate": 0.8}, "outcome": True}]))
    assert _cli(["backtest", str(noisy), "--cases", str(cases), "--output", "high", "--runs", "3"]) == 0
    assert "Brier" in capsys.readouterr().out
    assert _cli(["playtest", str(broken), "--runs", "2"]) == 0
    assert "inputs.unused" in capsys.readouterr().out
    assert _cli(["highlights", str(LEMONADE), "--seed", "1", "--narrative"]) == 0
    assert "Results:" in capsys.readouterr().out
    assert _cli(["sweep", str(formula), "--param", "a"]) == 1
    assert "expects NAME=VALUE" in capsys.readouterr().err
    assert _cli(["calibrate", str(formula), "--target", "nope=1", "--param", "a"]) == 1
    assert "neither an output nor a metric" in capsys.readouterr().err
