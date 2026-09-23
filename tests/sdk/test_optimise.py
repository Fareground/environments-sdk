"""The optimiser: decisions with domains, objectives and constraints over runs, search methods that find known optima,
honest checks on fresh seeds, Pareto frontiers, and mistakes that say what to fix."""
import argparse
import json

import pytest

import fg_env
from fg_env.sdk.analysis.cli import add_analysis_commands
from fg_env.sdk.analysis.goals import dominates

#: profit peaks at x = 3, y = 7.3; run i draws the same noise for every decision (common random numbers).
HILL = {
    "name": "Hill",
    "inputs": {"x": {"type": "int", "default": 0, "min": 0, "max": 10},
               "y": {"type": "number", "default": 0, "min": 0, "max": 10},
               "v": {"type": "list", "default": [0, 0, 0]},
               "w": {"type": "map", "default": {}},
               "mode": {"type": "enum", "values": ["a", "b", "c"], "default": "a"}},
    "clock": {"rounds": 1},
    "types": {"t": {"props": {"q": 0}}},
    "outputs": {
        "profit": {"type": "number", "expr": "100 - ($inputs.x - 3) ** 2 - ($inputs.y - 7.3) ** 2 + $normal(0, 1)"},
        "vec": {"type": "number", "expr": "-($inputs.v[0] - 2) ** 2 - ($inputs.v[1] - 5) ** 2 - ($inputs.v[2] - 1) ** 2"
                                          " + {a: 0, b: 3, c: 1}[$inputs.mode]"},
        "even": {"type": "number", "expr": "-$sum($map($inputs.v, ($it - 5) ** 2))"},
        "bumpy": {"type": "number", "expr": "-(($inputs.v[0] - 1) ** 2 + ($inputs.v[1] - 5) ** 2 + ($inputs.v[2] - 2) ** 2)"},
        "cost": {"type": "number", "expr": "$inputs.x * 2 + $inputs.y"},
        "service": {"type": "number", "expr": "$inputs.x + $inputs.y"},
        "noisy_service": {"type": "number", "expr": "$inputs.x + $inputs.y + $normal(0, 0.5)"},
    },
}

#: f peaks at a = 6.2, b = 2.7 (both continuous); robust is best at a = b, where b may be uncertain.
BOWL = {
    "name": "Bowl",
    "inputs": {"a": {"type": "number", "default": 0, "min": 0, "max": 10},
               "b": {"type": "number", "default": 4, "min": 0, "max": 10}},
    "clock": {"rounds": 1},
    "types": {"t": {"props": {"q": 0}}},
    "outputs": {"f": {"type": "number", "expr": "-($inputs.a - 6.2) ** 2 - ($inputs.b - 2.7) ** 2 + $normal(0, 1)"},
                "robust": {"type": "number", "expr": "-($inputs.a - $inputs.b) ** 2"}},
}

#: y has the same true mean for every k, but each k draws its own noise: whichever k wins is luck.
NOISE = {"name": "Noise", "clock": {"rounds": 1}, "types": {"t": {"props": {"q": 0}}},
         "inputs": {"k": {"type": "int", "default": 0, "min": 0, "max": 19}},
         "outputs": {"luck": {"type": "number", "expr": "$last($map($range($inputs.k + 1), $normal(0, 1)))"},
                     "peaked": {"type": "number", "expr": "-($inputs.k - 7) ** 2 + $normal(0, 1)"}}}

#: A safe plan earns 10 every time; a risky one 20 in most runs and -10 in the rest (a higher mean, a worse tail).
RISK = {"name": "Risk", "clock": {"rounds": 1}, "types": {"t": {"props": {"q": 0}}},
        "inputs": {"plan": {"type": "enum", "values": ["safe", "risky"], "default": "safe"}},
        "outputs": {"gain": {"type": "number", "expr": "10 if $inputs.plan == safe else (20 if $chance(0.8) else -10)"}}}


@pytest.mark.parametrize("method, tolerance", [("grid", 0), ("local", 0), ("race", 1), ("lhs", 1), ("random", 1)])
def test_every_search_method_finds_the_known_peak_of_a_noisy_hill(method, tolerance):
    result = fg_env.analysis.optimise(HILL, {"x": {}, "y": {"step": 1}}, "maximise profit", runs=4, budget=121, method=method)
    assert result.method == method and result.feasible
    assert abs(result.best["x"] - 3) <= tolerance and abs(result.best["y"] - 7) <= tolerance


@pytest.mark.parametrize("method", ["nelder_mead", "cross_entropy", "local"])
def test_continuous_decisions_converge_to_the_known_optimum(method):
    result = fg_env.analysis.optimise(BOWL, {"a": {}, "b": {}}, "maximise f", runs=4, budget=150, method=method)
    assert result.best["a"] == pytest.approx(6.2, abs=0.2) and result.best["b"] == pytest.approx(2.7, abs=0.2)


def test_auto_picks_a_grid_when_it_fits_the_budget_a_simplex_for_continuous_decisions_and_local_search_otherwise():
    assert fg_env.analysis.optimise(HILL, {"x": {}}, "maximise profit", runs=2).method == "grid"
    assert fg_env.analysis.optimise(BOWL, {"a": {}, "b": {}}, "maximise f", runs=2, budget=30).method == "nelder_mead"
    stepped = fg_env.analysis.optimise(HILL, {"x": {}, "y": {"step": 0.1}}, "maximise profit", runs=2, budget=60)
    assert stepped.method == "local" and stepped.best == {"x": 3, "y": 7.3}


def test_a_vector_and_a_choice_are_searched_together_to_their_known_best():
    result = fg_env.analysis.optimise(HILL, {"v": {"length": 3, "low": 0, "high": 8, "step": 1}, "mode": {}}, "maximise vec",
                             runs=1, budget=200)
    assert result.method == "local" and result.best == {"v": [2, 5, 1], "mode": "b"}
    assert all(isinstance(x, int) for x in result.best["v"])


def test_a_vector_with_a_fixed_sum_or_a_monotone_order_only_ever_tries_decisions_with_that_structure():
    fixed = fg_env.analysis.optimise(HILL, {"v": {"length": 3, "low": 0, "high": 9, "step": 1, "sum": 9}}, "maximise even",
                            runs=1, budget=100)
    assert fixed.best == {"v": [3, 3, 3]} and all(sum(h["decision"]["v"]) == 9 for h in fixed.history)
    falling = fg_env.analysis.optimise(HILL, {"v": {"length": 3, "low": 0, "high": 9, "step": 1, "monotone": "decreasing"}},
                              "maximise bumpy", runs=1, budget=1000, method="grid")
    assert falling.best == {"v": [3, 3, 2]}
    assert all(v[0] >= v[1] >= v[2] for v in (h["decision"]["v"] for h in falling.history))


def test_a_vector_takes_bounds_per_position_and_a_local_search_starts_where_it_is_told():
    result = fg_env.analysis.optimise(HILL, {"v": {"length": 3, "low": [0, 4, 0], "high": [1, 9, 9], "step": 1,
                                          "start": [1, 4, 0]}}, "maximise vec", runs=1, budget=200, method="local")
    assert result.history[0]["decision"] == {"v": [1, 4, 0]}
    assert result.best == {"v": [1, 5, 1]}


def test_the_cheapest_decision_that_meets_a_constraint_is_chosen_and_its_neighbours_show_why():
    result = fg_env.analysis.optimise(HILL, {"x": {}, "y": {"step": 1}}, "minimise cost", ["service >= 9"], runs=2)
    assert result.best == {"x": 0, "y": 9} and result.feasible
    (check,) = result.estimates["constraints"]
    assert check["met"] and check["value"] == 9
    by_move = {(row["decision"], row["direction"]): row for row in result.sensitivity}
    assert not by_move[("y", "down")]["feasible"] and by_move[("x", "up")]["change"]["mean"] == 2


def test_an_unmeetable_constraint_reports_the_closest_decision_and_how_far_it_misses():
    result = fg_env.analysis.optimise(HILL, {"x": {}, "y": {"step": 1}}, "minimise cost", ["service >= 50"], runs=2)
    assert not result.feasible and result.best == {"x": 10, "y": 10}
    assert result.estimates["constraints"][0]["shortfall"] == 30
    assert "No decision tried meets every constraint" in result.summary() and "short by 30" in result.summary()


def test_a_share_of_runs_constraint_is_the_share_of_runs_where_it_holds_with_a_wilson_interval():
    result = fg_env.analysis.optimise(HILL, {"x": {"low": 0, "high": 2}}, "minimise cost",
                             ["noisy_service >= 1 in 90% of runs"], inputs={"y": 0}, runs=40, holdout_seeds=0)
    (check,) = result.estimates["constraints"]
    assert result.best == {"x": 2} and check["share_needed"] == 0.9
    assert 0.9 <= check["value"] <= 1 and check["low"] < check["value"] <= check["high"]


def test_a_risk_averse_percentile_objective_prefers_the_safe_plan_the_mean_passes_over():
    assert fg_env.analysis.optimise(RISK, {"plan": {}}, "maximise gain", runs=40).best == {"plan": "risky"}
    assert fg_env.analysis.optimise(RISK, {"plan": {}}, "maximise p10 of gain", runs=40).best == {"plan": "safe"}


def test_an_objective_can_be_an_expression_over_outputs_and_inputs():
    result = fg_env.analysis.optimise(HILL, {"x": {}}, "maximise $outputs.profit - 10 * $inputs.x", inputs={"y": 7}, runs=2)
    assert result.best == {"x": 0} and result.objectives == ["maximise mean of $outputs.profit - 10 * $inputs.x"]


def test_fresh_seeds_confirm_a_real_winner_and_call_a_lucky_one_within_noise():
    real = fg_env.analysis.optimise(NOISE, {"k": {}}, "maximise peaked", runs=3, holdout_seeds=30)
    assert real.best == {"k": 7} and real.holdout["still_wins"] and real.holdout["difference"]["clear"]
    luck = fg_env.analysis.optimise(NOISE, {"k": {}}, "maximise luck", runs=3, holdout_seeds=30)
    assert not luck.holdout["difference"]["clear"]
    assert luck.holdout["seeds"] == 30 and len(luck.holdout["best"]["objectives"]) == 1


def test_the_same_seed_reproduces_the_whole_search_with_or_without_worker_processes():
    kwargs = dict(runs=3, budget=40, seed=11, method="local")
    alone = fg_env.analysis.optimise(HILL, {"x": {}, "y": {"step": 0.5}}, "maximise profit", ["service >= 8"], **kwargs)
    pooled = fg_env.analysis.optimise(HILL, {"x": {}, "y": {"step": 0.5}}, "maximise profit", ["service >= 8"], workers=2,
                             **kwargs)
    assert alone.to_dict() == pooled.to_dict()
    other = fg_env.analysis.optimise(HILL, {"x": {}, "y": {"step": 0.5}}, "maximise profit", ["service >= 8"],
                            **{**kwargs, "seed": 12})
    assert other.estimates != alone.estimates


def test_uncertain_parameters_make_the_decision_robust_to_not_knowing_them():
    known = fg_env.analysis.optimise(BOWL, {"a": {"low": 0, "high": 5, "step": 0.5}}, "maximise p10 of robust", runs=20)
    assert known.best == {"a": 4.0}
    robust = fg_env.analysis.optimise(BOWL, {"a": {"low": 0, "high": 5, "step": 0.5}}, "maximise p10 of robust", runs=20,
                             uncertainty=[{"b": 1.0}, {"b": 4.0}])
    assert robust.best == {"a": 2.5}
    with pytest.raises(ValueError, match="also drawn from uncertainty"):
        fg_env.analysis.optimise(BOWL, {"a": {"step": 1}}, "maximise f", inputs={"b": 2}, uncertainty=[{"b": 1.0}], runs=2)


def test_two_objectives_trace_a_pareto_frontier_of_undominated_decisions():
    result = fg_env.analysis.optimise(HILL, {"x": {}, "y": {"step": 1}}, ["minimise cost", "maximise service"], runs=2,
                             budget=121)
    assert result.method == "grid"
    decisions = [row["decision"] for row in result.frontier]
    assert result.best is None and len(decisions) == 21
    assert {"x": 1, "y": 5} not in decisions and {"x": 0, "y": 4} in decisions and {"x": 4, "y": 10} in decisions
    signed = [[-row["objectives"][0]["value"], row["objectives"][1]["value"]] for row in result.frontier]
    assert not any(dominates(a, b) for a in signed for b in signed)
    assert all(row["holdout"]["on_frontier"] for row in result.frontier)
    assert len(result.table().splitlines()) == 22 and "Pareto frontier" in result.summary()


@pytest.mark.parametrize("decisions, objective, constraints, options, message", [
    ({"nope": {}}, "maximise profit", [], {}, "not a declared input"),
    ({"x": {"lo": 1}}, "maximise profit", [], {}, "unknown key"),
    ({"x": {"low": -5, "high": 5}}, "maximise profit", [], {}, "leaves the input's declared range"),
    ({"mode": ["a", "z"]}, "maximise vec", [], {}, "not among the input's values"),
    ({"w": {"low": 0, "high": 5}}, "maximise vec", [], {}, "needs 'keys'"),
    ({"v": {"length": 3, "low": 0, "high": 2, "sum": 9}}, "maximise vec", [], {}, "cannot be met"),
    ({"v": {"length": 3, "low": 0, "high": 9, "sum": 9, "monotone": "increasing"}}, "maximise vec", [], {},
     "cannot be combined"),
    ({"x": {}}, "profit", [], {}, "start with maximise or minimise"),
    ({"x": {}}, "maximise nope", [], {}, "neither an output nor a metric"),
    ({"x": {}}, "maximise profit", ["service >= lots"], {}, "right side must be a number"),
    ({"x": {}}, "maximise profit", ["service"], {}, "write it as"),
    ({"x": {}, "y": {"step": 0.01}}, "maximise profit", [], {"method": "grid"}, "the grid holds"),
    ({"x": {}}, ["maximise profit", "minimise cost"], [], {"method": "local"}, "follows one objective"),
    ({"x": {}}, "maximise profit", [], {"inputs": {"x": 2}}, "fixed in inputs and also a decision"),
    ({"x": {}}, "maximise profit", [], {"method": "annealing"}, "method must be"),
])
def test_mistakes_say_what_to_fix(decisions, objective, constraints, options, message):
    with pytest.raises(ValueError, match=message):
        fg_env.analysis.optimise(HILL, decisions, objective, constraints, runs=1, **options)


def _cli(argv):
    parser = argparse.ArgumentParser()
    add_analysis_commands(parser.add_subparsers(dest="cmd", required=True))
    args = parser.parse_args(argv)
    return args.func(args)


def test_the_command_line_prints_the_answer_or_json_and_one_line_errors(tmp_path, capsys):
    path = tmp_path / "hill.json"
    path.write_text(json.dumps(HILL))
    common = [str(path), "--decision", "x=0:10:1", "--decision", "y=0:10:1", "--objective", "minimise cost",
              "--constraint", "service >= 9", "--runs", "2"]
    assert _cli(["optimise", *common, "--confidence", "0.95", "--json"]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["best"] == {"x": 0, "y": 9} and answer["verdict"] == "feasible" and answer["confidence"] == 0.95
    assert _cli(["optimise", *common]) == 0
    assert 'Best decision: x=0, y=9' in capsys.readouterr().out
    assert _cli(["optimise", str(path), "--objective", "minimise cost"]) == 1
    assert "give at least one --decision" in capsys.readouterr().err


def test_the_guide_has_an_optimise_part_on_the_core_guides_map():
    part = fg_env.guide("optimise")
    assert "fg_env.analysis.optimise(" in part and "in 90% of runs" in part and "Pareto" in part
    core = fg_env.guide()
    assert "`optimise`" in core and len(core) / 4 < 6000
