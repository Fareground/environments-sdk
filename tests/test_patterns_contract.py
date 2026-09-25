"""Patterns in a contract (`pattern` mechanisms): checks that say what to fix, recording, uncertainty, and runs that
snapshot, clone and fork exactly."""
import json
import statistics

import pytest
from patterns_helpers import errors, series, world

import fg_env


def _messages(patterns, **options):
    return [(issue.path, issue.message, issue.fix) for issue in errors(world(patterns, **options))]


def test_an_unknown_kind_or_field_is_named_with_the_closest_choice():
    (path, message, fix), = _messages({"s": {"kind": "seasonl"}})
    assert (path == "mechanisms.s.mode" and "'seasonl' is not a mode of `pattern`" in message and fix
            == "did you mean 'seasonal'?")
    (path, message, fix), = _messages({"t": {"kind": "trend", "slop": 1}})
    assert path == "mechanisms.t.slop" and "`slop` is not a field of `pattern` mode `trend`" in message
    assert fix.startswith("did you mean 'slope'?")


def test_the_dynamics_names_point_to_the_pattern_kinds_that_replaced_them():
    (path, message, fix), = _messages({"p": {"kind": "priors"}})
    assert path == "mechanisms.p.mode" and fix == "use draw patterns: $pattern.<name> (guide('patterns'))"


def test_a_dynamics_mechanism_says_that_patterns_replaced_it():
    contract = world({}, mechanisms={"trends": {"kind": "dynamics", "mode": "drift", "rules": {}}})
    (issue,) = errors(contract)
    assert issue.path == "mechanisms.trends.mode" and "no longer a mechanism" in issue.message
    assert "trend" in issue.fix and "random_walk" in issue.fix


def test_a_parameter_that_reads_run_state_or_shared_randomness_is_refused_with_why():
    issues = _messages({"t": {"kind": "trend", "slope": "$world.x"}}, world={"x": 1})
    assert issues[0][0] == "mechanisms.t.slope" and "$world is not available in a pattern parameter" in issues[0][1]
    issues = _messages({"t": {"kind": "trend", "slope": "$normal(0, 1)"}})
    assert "draws from the shared stream" in issues[0][1] and "draw pattern" in issues[0][2]
    issues = _messages({"w": {"kind": "random_walk"}, "t": {"kind": "trend", "slope": "$pattern.w"}})
    assert "changes during a run, so a parameter cannot read it" in issues[0][1]


def test_parameters_may_read_draws_inputs_keys_and_rows():
    assert _messages({"e": {"kind": "draw", "dist": "normal", "mean": -1, "sd": 0.1, "keys": ["a"]},
                      "p": {"kind": "elasticity", "keys": ["a"], "elasticity": "$pattern.e($key)",
                            "reference": "$inputs.ref"}},
                     inputs={"ref": {"type": "number", "default": 10}}) == []


def test_reads_and_calls_must_match_what_the_pattern_takes():
    patterns = {"p": {"kind": "elasticity", "elasticity": -1}, "s": {"kind": "seasonal", "keys": ["a"], "profile": [1]}}
    issues = _messages(patterns,
                       outputs={"x": "$pattern.p", "y": "$pattern.s", "z": "$pattern.p(1, 2)", "u": "$pattern.q"})
    found = {path: message for path, message, _ in issues}
    assert found["outputs.x"] == "$pattern.p is read with (price)"
    assert found["outputs.y"] == "$pattern.s is read with (key)"
    assert found["outputs.z"] == "$pattern.p takes 1 argument(s): (price), got 2"
    assert found["outputs.u"] == "$pattern.q: no such pattern"


def test_structural_mistakes_are_reported_on_their_path():
    found = {path: message for path, message, _ in _messages({
        "x": {"kind": "cross_price", "reference": 1, "own": -1},
        "c": {"kind": "calendar", "effects": [{"on": "weekend", "effect": 1.2}]},
        "k": {"kind": "seasonal", "keys": "shop", "profile": [1]},
        "p": {"kind": "product", "of": ["k", "missing"]},
        "a": {"kind": "sum", "of": ["b"]}, "b": {"kind": "sum", "of": ["a"]},
        "r": {"kind": "elasticity", "elasticity": -1, "record": True},
        "u": {"kind": "trend", "uncertainty": {"slop": 0.1}},
    })}
    assert "needs `keys`" in found["mechanisms.x"]
    assert "calendar effects need clock.start" in found["mechanisms.c"]
    assert "'shop' is not a declared type" in found["mechanisms.k.keys"]
    assert "'k' is keyed but 'p' is not" in found["mechanisms.p.of[0]"]
    assert "'missing' is not a declared pattern" in found["mechanisms.p.of[1]"]
    assert "cycle" in found["mechanisms.a.of"] or "cycle" in found["mechanisms.b.of"]
    assert "no value of its own to record" in found["mechanisms.r.record"]
    assert "'slop' is not a parameter" in found["mechanisms.u.uncertainty.slop"]


def test_a_recorded_pattern_is_a_series_output_of_its_own_name():
    contract = world({"t": {"kind": "trend", "slope": 1, "start": 0, "record": True},
                      "k": {"kind": "draw", "keys": ["a", "b"], "dist": "uniform", "low": 1, "high": 1,
                            "record": True}},
                     outputs={"last": "$outputs.t", "total": "$sum($series.t, $it)"}, rounds=3)
    result = fg_env.run(contract, "idle", seed=1)
    assert result.series["t"] == [0, 1, 2] and result.outputs["last"] == 2 and result.outputs["total"] == 3
    assert result.series["k"][0] == {"a": 1, "b": 1}


def test_uncertainty_draws_each_parameter_once_per_run_around_its_value():
    patterns = {"p": {"kind": "elasticity", "elasticity": -1.5, "reference": 1, "uncertainty": {"elasticity": 0.2}}}
    draws = [fg_env.run(world(patterns, metrics={"e": "$log($pattern.p(2)) / "
                                                      "$log(2)"}, rounds=2), "idle", seed=s).series["e"]
             for s in range(400)]
    assert all(a == b for a, b in draws)
    values = [a for a, _ in draws]
    assert (statistics.fmean(values) == pytest.approx(-1.5, abs=0.03) and statistics.pstdev(values)
            == pytest.approx(0.2, rel=0.12))
    exact = {"p": {**patterns["p"], "uncertainty": {"elasticity": 0}}}
    assert series(exact, {"e": "$pattern.p(2)"}, rounds=1)["e"][0] == pytest.approx(2 ** -1.5)


def _living_world():
    return world({"w": {"kind": "random_walk", "sd": 1, "keys": "shop"},
                  "a": {"kind": "carryover", "keys": "shop", "input": "$it.spend", "retain": 0.5},
                  "s": {"kind": "shocks", "chance": 0.3, "size": 2, "half_life": 1}},
                 types={"shop": {"props": {"spend": 0.0}}},
                 entities={"s1": {"type": "shop"}, "s2": {"type": "shop"}},
                 events=[{"phase": "end", "each": "shop", "do": ["$it.spend = $abs($pattern.w($it)) + $pattern.s"]}],
                 metrics={"a": "$pattern_values('a')", "w": "$pattern_values('w')", "s": "$pattern.s"}, rounds=8)


def test_a_run_with_patterns_resumes_exactly_from_a_json_snapshot_and_a_clone():
    contract = _living_world()
    straight = fg_env.load(contract, seed=4).run("idle").to_dict()
    env = fg_env.load(contract, seed=4)
    env.run("idle", rounds=3)
    restored = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
    assert restored.run("idle").to_dict() == straight
    assert env.clone().run("idle").to_dict() == straight


def test_a_fork_with_new_inputs_reads_patterns_with_them_and_keeps_memory_state():
    contract = world({"t": {"kind": "trend", "slope": "$inputs.slope", "start": 0},
                      "a": {"kind": "carryover", "input": "$world.x", "retain": 1}},
                     inputs={"slope": {"type": "number", "default": 1}}, world={"x": 1.0},
                     metrics={"t": "$pattern.t", "a": "$pattern.a"}, rounds=4)
    env = fg_env.load(contract, seed=1)
    env.run("idle", rounds=2)
    forked = env.fork(inputs={"slope": 10})
    result = forked.run("idle")
    assert result.series["t"] == [0, 1, 20, 30]
    assert result.series["a"] == [1, 2, 3, 4]


def test_a_measure_reading_a_pattern_is_never_reported_as_stuck():
    contract = world({"t": {"kind": "trend", "slope": 1}}, world={"base": 3},
                     metrics={"m": "$pattern.t * $world.base"}, rounds=4)
    result = fg_env.run(contract, "idle", seed=1)
    assert not [d for d in result.diagnostics if d["path"] == "metrics.m"]


def test_the_guide_teaches_every_kind_and_each_kind_is_a_mode_of_the_pattern_family():
    page = fg_env.guide("patterns")
    assert page.startswith("## Patterns: `\"mechanisms\": {name: {\"kind\": \"pattern\", \"mode\": <kind>, …}}`")
    for name in ("trend", "seasonal", "calendar", "random_walk", "volatility", "elasticity", "cross_price", "counts",
                 "censored", "carryover", "promotion", "draw", "diffusion", "product"):
        assert f"#### `{name}`" in page
    assert "`patterns`" in fg_env.guide()
    trend = fg_env.guide("pattern.trend")
    assert trend.startswith("### `pattern.trend`") and "- `slope`" in trend and "- `kind`" not in trend
    assert "patterns" not in fg_env.schema()["properties"]
