"""Independent procedures can share phase names without author-managed global stage names."""
import copy
import json

import pytest

import fg_env


def contract(order=("north", "south")):
    return {
        "name": "Independent reviews", "clock": {"rounds": 3},
        "types": {"manager": {"agent": True}}, "entities": {"manager": {"type": "manager"}},
        "world": {"north_checks": 0, "south_checks": 0},
        "actions": {f"check_{name}": {"by": "manager", "do": [f"$world.{name}_checks += 1"]}
                    for name in order},
        "mechanisms": {name: {"kind": "flow", "mode": "procedure", "phases": {
            "review": {"stages": [{"actions": [f"check_{name}"]}],
                       "next": [{"to": "done", "after": 1 if name == "north" else 2}]},
            "done": {},
        }} for name in order},
    }


def participant(wake):
    for name in ("north", "south"):
        if wake.stage == f"{name}_review":
            assert wake.call(f"check_{name}", {}).ok
    wake.end()


@pytest.mark.parametrize("order", [("north", "south"), ("south", "north")])
def test_repeated_phase_names_preserve_independent_timing_and_choices(order):
    c = contract(order)
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == "error"]
    env = fg_env.load(c)
    result = env.run(participant)
    assert result.status == "completed", result.error
    assert env.props["north_checks"] == 1
    assert env.props["south_checks"] == 2
    assert env.props["north_phase"] == env.props["south_phase"] == "done"


def test_adding_an_independent_workflow_does_not_rename_existing_stages():
    alone = fg_env.parse(contract(("north",)))
    combined = fg_env.parse(contract())
    assert alone.stages[0].name == combined.stages[0].name == "north_review"


def test_repeated_workflows_resume_with_their_own_phase_and_stage():
    c = contract()
    env = fg_env.load(c, seed=4)
    env.run(participant, rounds=1)
    assert env.props["north_phase"] == "done" and env.props["south_phase"] == "review"
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    assert env.run(participant).to_dict() == restored.run(participant).to_dict()
    assert restored.props["south_checks"] == 2


def test_explicit_stage_names_are_preserved_and_conflicts_still_diagnosed():
    c = contract()
    c["mechanisms"]["north"]["phases"]["review"]["stages"][0]["name"] = "custom"
    assert fg_env.parse(c).stages[0].name == "custom"
    c["mechanisms"]["south"]["phases"]["review"]["stages"][0]["name"] = "custom"
    errors = [i for i in fg_env.check(c, rounds=0) if i.severity == "error"]
    assert any(i.path == "mechanisms.south.phases.review.stages[0].name" for i in errors)


def test_multiple_unnamed_stages_are_scoped_to_their_procedure():
    c = contract()
    for cfg in c["mechanisms"].values():
        cfg["phases"]["review"]["stages"] *= 2
    names = [stage.name for stage in fg_env.parse(c).stages]
    assert names == ["north_review", "north_review_2", "south_review", "south_review_2"]


def test_global_stage_can_share_a_local_phase_name():
    c = copy.deepcopy(contract())
    c["stages"] = [{"name": "review", "actions": []}]
    assert [stage.name for stage in fg_env.parse(c).stages] == ["review", "north_review", "south_review"]
