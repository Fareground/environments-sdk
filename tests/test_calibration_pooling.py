"""Calibrating a rate recorded day by day: relative per-case errors let the quiet days' noisy rates pull the parameter
away, weighting each day by its count or matching the days pooled recovers it, and a per-case fit that drifts from the
pooled level is flagged."""
import pytest

import fg_env

#: A day's abandonment is its load times (1 - patience) / 10, with a little run noise: the true patience is 0.5.
CENTRE = {"name": "Rate days", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
          "inputs": {"patience": {"type": "number", "default": 0.5, "min": 0, "max": 1},
                     "load": {"type": "number", "default": 1, "min": 0, "max": 10}},
          "outputs": {"rate": {"type": "number",
                               "expr": "$inputs.load * (1 - $inputs.patience) / 10 + $normal(0, 0.002)"}}}
#: (load, calls offered, recorded abandonment): the quiet days' few calls recorded rates well below their true 5%.
DAYS = [(1, 40, 0.025), (1, 60, 0.033), (2, 200, 0.09), (3, 500, 0.156), (4, 900, 0.198), (4, 1100, 0.203)]


def _fit(**options):
    cases = [{"name": f"day {i}", "inputs": {"load": load},
              "targets": {"rate": {"value": rate, **({"count": calls} if "count" in options else {}),
                                   **({"pool": True} if "pool" in options else {})}}}
             for i, (load, calls, rate) in enumerate(DAYS)]
    return fg_env.analysis.calibrate(CENTRE, cases, {"patience": {"low": 0, "high": 1}}, runs=4, budget=30,
                                     method="golden")


def test_relative_per_day_errors_let_the_quiet_days_pull_patience_up_and_the_drift_is_flagged():
    result = _fit()
    assert result.params["patience"] > 0.6
    (check,) = result.pooled
    assert check["disagrees"] and check["simulated"] < check["goal"]
    assert any("pooled over the 6 cases" in note and "'pool': true" in note for note in result.notes)


@pytest.mark.parametrize("options", [{"count": True}, {"count": True, "pool": True}])
def test_weighting_days_by_their_calls_or_pooling_them_recovers_the_true_patience(options):
    result = _fit(**options)
    assert result.params["patience"] == pytest.approx(0.5, abs=0.03)
    assert not any(check["disagrees"] for check in result.pooled)


def test_a_pooled_target_is_one_error_over_every_day():
    result = _fit(count=True, pool=True)
    pooled = [row for row in result.targets if row.get("case") == "pooled"]
    assert len(pooled) == 1 and pooled[0]["cases"] == 6
    assert pooled[0]["goal"] == pytest.approx(sum(c * r for _, c, r in DAYS) / sum(c for _, c, _ in DAYS))


def test_cases_draw_their_own_seeds_so_averaging_over_days_averages_their_noise_while_candidates_share_them():
    same_day = [{"name": f"copy {i}", "inputs": {"load": 3}, "targets": {"rate": 0.15}} for i in range(3)]
    result = fg_env.analysis.calibrate(CENTRE, same_day, {"patience": {"low": 0, "high": 1}}, runs=2, budget=6,
                                       method="golden")
    simulated = [row["simulated"] for row in result.targets]
    assert len(set(simulated)) == 3  # identical days, different seeds
    pooled = fg_env.analysis.calibrate(CENTRE, same_day, {"patience": {"low": 0, "high": 1}}, runs=2, budget=6,
                                       method="golden",
                              workers=2)
    assert pooled.to_dict() == result.to_dict()


@pytest.mark.parametrize("spec, message", [
    ({"value": 0.2, "count": 0}, "count must be a positive number"),
    ({"value": 3, "count": 10}, "must lie between 0 and 1"),
    ({"distribution": [0.1, 0.2], "pool": True}, "apply to a number target"),
    ({"value": 0.2, "pool": "yes"}, "pool must be true or false"),
])
def test_count_and_pool_mistakes_say_what_to_fix(spec, message):
    with pytest.raises(ValueError, match=message):
        fg_env.analysis.calibrate(CENTRE, [{"name": "a", "targets": {"rate": spec}}],
                                  {"patience": {"low": 0, "high": 1}}, runs=1)
