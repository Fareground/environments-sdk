"""Things that used to go wrong quietly: an untemplated `why`, a stage `until` that never holds, a tampered snapshot."""
import copy

import pytest

import fg_env
from fg_env.errors import SnapshotError

CAPPED = {
    "name": "Capped",
    "inputs": {"cap": {"type": "int", "default": 3}},
    "world": {"agreed": False},
    "types": {"p": {"agent": True, "props": {"x": {"type": "int", "default": 0, "min": 0, "max": 50}}}},
    "entities": {"a": {"type": "p"}},
    "actions": {"go": {"by": "p", "params": {"n": {"type": "int", "min": 0, "max": 100}},
                       "when": [{"expr": "$params.n <= $inputs.cap",
                                 "why": "At most {$inputs.cap} (you asked {$params.n})."}],
                       "do": "$actor.x += $params.n"},
                "agree": {"by": "p", "do": "$world.agreed = true"}},
    "stages": [{"name": "talk", "until": "$world.agreed", "passes": 3}],
    "clock": {"rounds": 2},
}


def test_a_requirements_why_is_a_template():
    told = []
    fg_env.run(CAPPED, lambda wake: told.append(wake.call("go", {"n": 5}).text), rounds=1)
    assert "At most 3 (you asked 5)" in told[0]
    broken = copy.deepcopy(CAPPED)
    broken["actions"]["go"]["when"][0]["why"] = "At most {$inputs.capp}."
    assert "actions.go.when[0].why" in [i.path for i in fg_env.check(broken) if i.severity == "error"]


def test_a_stage_whose_until_never_holds_is_reported():
    result = fg_env.run(CAPPED, lambda wake: wake.call("go", {"n": 1}), seed=1)
    finding = next(d for d in result.diagnostics if d["code"] == "stage_until_capped")
    assert finding["path"] == "stages.talk.until" and "all 2 time(s)" in finding["message"]
    agreed = fg_env.run(CAPPED, lambda wake: wake.call("agree"), seed=1)
    assert not any(d["code"] == "stage_until_capped" for d in agreed.diagnostics)


def test_a_stage_whose_until_gave_up_in_only_one_round_is_reported():
    contract = {"name": "Talks", "clock": {"rounds": 10}, "world": {"offers": 0},
                "types": {"p": {"agent": True}}, "entities": {"ann": {"type": "p"}},
                "stages": [{"name": "talk", "until": "$round < 10", "actions": ["offer"]}],
                "actions": {"offer": {"by": "p", "do": "$world.offers += 1"}}}
    result = fg_env.run(contract, seed=1)
    finding = next(d for d in result.diagnostics if d["code"] == "stage_until_capped")
    assert "1 of the 10 time(s)" in finding["message"]


@pytest.mark.parametrize("value, problem",
                         [("lots", "finite number"), (90, r"entities\.a\.props\.x: a's x cannot go above 50"),
                          (1.5, "whole number")])
def test_a_snapshot_holding_a_value_its_contract_does_not_allow_is_refused(value, problem):
    env = fg_env.load(CAPPED, seed=1)
    env.run(lambda wake: wake.call("go", {"n": 1}), rounds=1)
    snapshot = copy.deepcopy(env.snapshot())
    next(row for row in snapshot["entities"] if row["id"] == "a")["props"]["x"] = value
    with pytest.raises(SnapshotError, match=problem):
        fg_env.Env.restore(CAPPED, snapshot)


_CULL = [{"if": "$it.id == 'a'", "then": [{"remove": "$entity(b)"}]}, "$it.coins += 1"]


@pytest.mark.parametrize("event", [{"do": [{"each": "p", "do": _CULL}]}, {"each": "p", "do": _CULL}],
                         ids=["each effect", "event each"])
def test_an_each_loop_skips_an_entity_removed_earlier_in_the_same_loop(event):
    culling = {"name": "Cull", "clock": {"rounds": 1},
               "types": {"p": {"agent": True, "props": {"coins": 10}}},
               "entities": {"a": {"type": "p"}, "b": {"type": "p"}},
               "events": [event],
               "outputs": {"b": "$entity(b).coins", "a": "$entity(a).coins"}}
    result = fg_env.run(culling, "idle", seed=1)
    assert result.outputs == {"b": 10, "a": 11}
