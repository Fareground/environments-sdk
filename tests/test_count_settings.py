"""A stage's `passes` may be an expression over $inputs (as may an event's schedule): checked, resolved at load,
snapshot-safe."""
import json

import pytest

import fg_env
from fg_env.errors import RunError


def contract(passes="$inputs.passes", every="$inputs.every"):
    return {
        "fg_env": "1", "name": "counts",
        "clock": {"rounds": 6},
        "inputs": {"passes": {"type": "int", "default": 3}, "every": {"type": "int", "default": 2}},
        "world": {"turns": 0, "ticks": 0},
        "types": {"walker": {"agent": True}},
        "entities": {"w": {"type": "walker"}},
        "actions": {"step": {"by": "walker", "do": "$world.turns += 1", "terminal": True}},
        "policies": {"stepper": {"rules": [{"do": "step"}]}},
        "stages": [{"name": "walk", "passes": passes}],
        "events": [{"name": "tick", "phase": "end", "every": every, "do": "$world.ticks += 1"}],
        "outputs": {"turns": "$world.turns", "ticks": "$world.ticks"},
    }


def test_passes_and_every_read_their_inputs():
    result = fg_env.run(contract(), {"walker": "policy:stepper"}, seed=1)
    assert result.outputs == {"turns": 18, "ticks": 3}  # 3 passes × 6 rounds; rounds 1, 3, 5
    swept = fg_env.run(contract(), {"walker": "policy:stepper"}, inputs={"passes": 1, "every": 3}, seed=1)
    assert swept.outputs == {"turns": 6, "ticks": 2}  # rounds 1 and 4


def test_an_expression_count_matches_the_same_literal_count():
    by_expression = fg_env.run(contract(), {"walker": "policy:stepper"}, seed=4).to_dict()
    literal = fg_env.run(contract(passes=3, every=2), {"walker": "policy:stepper"}, seed=4).to_dict()
    assert by_expression["outputs"] == literal["outputs"] and by_expression["events"] == literal["events"]


def test_check_rejects_counts_that_read_more_than_inputs_or_are_below_one():
    bad = contract(passes="$world.turns + 1")
    issues = {i.path: i.message for i in fg_env.check(bad) if i.severity == "error"}
    assert "stages[0].passes" in issues and "world" in issues["stages[0].passes"]


def test_a_count_that_is_not_a_whole_number_fails_at_load_with_its_path():
    with pytest.raises(RunError) as error:
        fg_env.load(contract(passes="$inputs.passes / 2"), seed=1)
    assert error.value.path == "stages.walk.passes" and "1.5" in str(error.value)


def test_a_snapshot_resumes_a_run_with_expression_counts_exactly():
    straight = fg_env.load(contract(), seed=2, inputs={"passes": 2, "every": 4}).run(
        {"walker": "policy:stepper"}).to_dict()
    env = fg_env.load(contract(), seed=2, inputs={"passes": 2, "every": 4})
    env.run({"walker": "policy:stepper"}, rounds=3)
    resumed = fg_env.Env.restore(contract(), json.loads(json.dumps(env.snapshot())))
    assert resumed.run({"walker": "policy:stepper"}).to_dict() == straight


def test_a_create_count_that_is_not_a_whole_number_of_at_least_zero_is_refused():
    def spawn(count):
        return {"name": "Spawn", "clock": {"rounds": 1}, "types": {"p": {"agent": True}},
                "entities": {"a": {"type": "p"}}, "events": [{"do": [{"create": "p", "count": count}]}]}
    result = fg_env.load(spawn(-1), seed=1).run("idle")
    assert result.status == "failed" and "count must be a whole number" in result.error
    # a count that is not whole is refused by its shape before any run (audit 12 H2)
    assert any(i.path == "events[0].do[0].count" for i in fg_env.check(spawn(2.7), rounds=0))
