"""Trustworthy optimisation: constraints judged with a stated confidence (feasible, borderline or infeasible), per-key
constraints over list and map outputs with the keys that bind, structured moves for vectors whose positions must move
together, and a Pareto frontier that is refined rather than only sampled."""
import random
from statistics import fmean

import pytest

import fg_env
from fg_env.sdk.analysis.constraints import Standard, check, parse_constraints
from fg_env.sdk.analysis.decisions import parse_decisions

#: Four slots needing 4, 8, 6 and 2 staff for a service level of 0.9; each slot's level is noisy.
SLOTS = {
    "name": "Slots",
    "inputs": {"staff": {"type": "list", "default": [5, 5, 5, 5]},
               "m": {"type": "number", "default": 0, "min": -10, "max": 10},
               "v": {"type": "list", "default": [1, 1, 1]}},
    "clock": {"rounds": 1},
    "types": {"t": {"props": {"q": 0}}},
    "outputs": {
        "sl_by_slot": {"type": "list", "expr": "$map($range(4), $min(1, 0.9 * $inputs.staff[$it] / [4, 8, 6, 2][$it]"
                                               " + $normal(0, 0.02)))"},
        "cost": {"type": "number", "expr": "$sum($inputs.staff)"},
        "x": {"type": "number", "expr": "$inputs.m + $normal(0, 1)"},
        "together": {"type": "number",
                     "expr": "$sum($inputs.v) - 10 * ($max($inputs.v) - $min($inputs.v))"},
        "service": {"type": "number", "expr": "$sum($inputs.v)"},
        "spend": {"type": "number", "expr": "2 * $inputs.v[0] + $inputs.v[1] + 3 * $inputs.v[2]"},
    },
}
STAFF = {"staff": {"length": 4, "low": 1, "high": 12, "step": 1, "start": [10, 10, 10, 10]}}


def _runs(inputs, count=20):
    return fg_env.experiment(SLOTS, runs=count, seed=3, inputs=inputs, arms=[None]).arms["baseline"].runs


def _noise():
    """The mean of x's noise over the runs every m shares (common random numbers), so a test can place the mean."""
    return fmean(r.outputs["x"] for r in _runs({"m": 0}))


@pytest.mark.parametrize("above, verdict", [(3, "feasible"), (0.05, "borderline"), (-3, "infeasible")])
def test_a_constraint_is_confident_borderline_or_clearly_missed_by_its_one_sided_bounds(above, verdict):
    (constraint,) = parse_constraints(fg_env.parse(SLOTS), "x >= 0 with 90% confidence")
    row = check(constraint, _runs({"m": above - _noise()}), Standard(0.6), random.Random(0))
    assert row["verdict"] == verdict and row["confidence"] == 0.9


def test_a_single_run_cannot_settle_a_constraint_either_way():
    (constraint,) = parse_constraints(fg_env.parse(SLOTS), "x >= 0")
    for m in (-5, 5):
        row = check(constraint, _runs({"m": m}, count=1), Standard(0.9), random.Random(0))
        assert row["verdict"] == "borderline" and row["passes"] == (m > 0)


def test_a_search_margin_asks_more_than_the_confidence_of_the_same_runs():
    (constraint,) = parse_constraints(fg_env.parse(SLOTS), "x >= 0")
    runs, rng = _runs({"m": 0.4 - _noise()}), random.Random(0)
    assert check(constraint, runs, Standard(0.9), rng)["passes"]
    assert not check(constraint, runs, Standard(0.9, margin=2.4), rng)["passes"]


def test_each_key_must_hold_with_confidence_and_the_tightest_keys_are_named_as_binding():
    result = fg_env.optimise(SLOTS, STAFF, "minimise cost", ["each sl_by_slot >= 0.9"], runs=12, budget=120,
                             holdout_seeds=20)
    assert result.verdict == "feasible" and result.holdout["verdict"] == "feasible"
    staff = result.best["staff"]
    assert all(staff[i] >= need for i, need in enumerate([4, 8, 6, 2]))
    (row,) = result.estimates["constraints"]
    assert row["keys_total"] == row["keys_needed"] == row["keys_holding"] == 4
    assert [key["key"] for key in row["keys"]] == ["0", "1", "2", "3"] and row["binding"]
    assert "4 of 4 keys hold, 4 needed — met with 90% confidence" in result.summary()


def test_at_most_some_keys_may_miss_so_the_plan_is_cheaper_than_one_where_each_holds():
    each = fg_env.optimise(SLOTS, STAFF, "minimise cost", ["each sl_by_slot >= 0.9"], runs=12, budget=120)
    some = fg_env.optimise(SLOTS, STAFF, "minimise cost", ["at most 1 of sl_by_slot < 0.9"], runs=12, budget=120)
    assert some.verdict == "feasible" and some.estimates["objectives"][0]["value"] < each.estimates["objectives"][0]["value"]
    (row,) = some.estimates["constraints"]
    assert row["keys_needed"] == 3 and row["value"] <= 1


@pytest.mark.parametrize("constraint, message", [
    ("each cost >= 1", "needs a list or map"),
    ("at most 9 of sl_by_slot < 0.9", "cannot be met"),
    ("x >= 0 with 100% confidence", "above 50% and below 100%"),
    ("x >= 0 with 40% confidence", "above 50% and below 100%"),
])
def test_per_key_and_confidence_mistakes_say_what_to_fix(constraint, message):
    with pytest.raises(ValueError, match=message):
        fg_env.optimise(SLOTS, STAFF, "minimise cost", [constraint], runs=2, budget=4)


def test_positions_that_only_improve_together_are_moved_as_a_block():
    result = fg_env.optimise(SLOTS, {"v": {"length": 3, "low": 0, "high": 5, "step": 1, "start": [1, 1, 1]}},
                             "maximise together", runs=1, budget=60, method="local")
    assert result.best == {"v": [5, 5, 5]}


def test_block_and_smoothing_moves_shift_a_run_of_positions_and_iron_out_a_spike():
    space = parse_decisions(fg_env.parse(SLOTS), {"v": {"length": 3, "low": 0, "high": 9, "step": 1}})
    steps = [1.0, 1.0, 1.0]
    assert set(space.block_moves((2.0, 2.0, 2.0), 0, steps)) == {(3.0, 3.0, 2.0), (1.0, 1.0, 2.0), (3.0, 3.0, 3.0),
                                                                  (1.0, 1.0, 1.0)}
    assert set(space.smoothing_moves((1.0, 6.0, 2.0), 1, steps)) == {(1.0, 1.0, 2.0), (1.0, 2.0, 2.0)}
    assert space.smoothing_moves((1.0, 6.0, 2.0), 0, steps) == []


def _cheapest(service):
    """The least spend for a service level: fill v[1] (1 per unit), then v[0] (2), then v[2] (3), up to 6 each."""
    spend = 0
    for price in (1, 2, 3):
        units = min(6, service)
        spend, service = spend + price * units, service - units
    return spend


def test_a_refined_frontier_finds_more_truly_optimal_decisions_than_sampling_on_the_same_budget():
    decisions = {"v": {"length": 3, "low": 0, "high": 6, "step": 1}}
    objectives = ["minimise spend", "maximise service"]
    refined = fg_env.optimise(SLOTS, decisions, objectives, runs=1, budget=120, holdout_seeds=0)
    sampled = fg_env.optimise(SLOTS, decisions, objectives, runs=1, budget=120, holdout_seeds=0, method="lhs")

    def optimal(result):
        return sum(1 for row in result.frontier
                   if row["objectives"][0]["value"] == _cheapest(row["objectives"][1]["value"]))

    assert refined.method == "frontier" and optimal(refined) > optimal(sampled)
    with pytest.raises(ValueError, match="traces a Pareto frontier"):
        fg_env.optimise(SLOTS, decisions, "minimise spend", runs=1, method="frontier")
