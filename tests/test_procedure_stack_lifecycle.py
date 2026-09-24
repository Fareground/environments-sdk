"""Review windows reconcile participant departure without waiving living responses."""
import json

import pytest

import fg_env


def contract(*, unanswered="wait", custom_window=False, reviewers=("reviewer",)):
    c = {
        "name": "Procurement reviews", "clock": {"rounds": 3},
        "types": {"manager": {"agent": True}},
        "entities": {name: {"type": "manager"} for name in ("owner", *reviewers)},
        "world": {"spent": 0},
        "actions": {"leave": {"by": "manager", "do": [{"remove": "$actor"}]}},
        "stages": [
            {"name": "submit", "who": "$it.id == 'owner'", "actions": ["review_purchase"]},
            {"name": "departure", "who": "$it.id != 'owner'", "actions": ["leave"]},
        ],
        "mechanisms": {"review": {"kind": "flow", "mode": "procedure", "stack": {
            "who": "manager", "silence": "wait", "unanswered": unanswered,
            "kinds": {"purchase": {"resolve": ["$world.spent += 10"]}},
        }}},
    }
    if custom_window:
        c["stages"].append({"name": "window", "actions": []})
        c["mechanisms"]["review"]["stack"]["stage"] = "window"
    return c


def participant(*, leave_round=1, leave=("reviewer",), pass_round=None):
    def play(wake):
        if wake.stage == "submit" and wake.round == 1:
            assert wake.call("review_purchase", {}).ok
        if wake.stage == "departure" and wake.round == leave_round and wake.entity_id in leave:
            assert wake.call("leave", {}).ok
        if wake.stage in ("review_stack", "window") and wake.round == pass_round:
            assert wake.call("review_pass", {}).ok
        wake.end()
    return play


def resolutions(env):
    return [e for e in env.world.log if e.kind == "review_stack" and e.data.get("act") == "resolve"]


@pytest.mark.parametrize("unanswered", ["wait", "pass"])
@pytest.mark.parametrize("custom_window", [False, True])
def test_last_reviewer_departure_resolves_once_at_the_window_boundary(unanswered, custom_window):
    env = fg_env.load(contract(unanswered=unanswered, custom_window=custom_window))
    result = env.run(participant())
    assert result.status == "completed", result.error
    assert env.props["spent"] == 10
    assert env.props["review_stack"]["items"] == []
    assert len(resolutions(env)) == 1
    assert resolutions(env)[0].round == 1


def test_living_reviewers_still_block_until_they_answer():
    env = fg_env.load(contract(reviewers=("reviewer", "other")))
    play = participant(pass_round=2)
    env.run(play, rounds=1)
    assert env.props["spent"] == 0
    assert not resolutions(env)
    assert env.props["review_stack"]["items"][0]["responders"] == ["reviewer", "other"]
    env.run(play)
    assert env.props["spent"] == 10
    assert len(resolutions(env)) == 1
    assert resolutions(env)[0].round == 2


def test_pending_review_survives_snapshot_then_settles_on_later_departure():
    c = contract()
    env = fg_env.load(c, seed=5)
    play = participant(leave_round=2)
    env.run(play, rounds=1)
    assert env.props["spent"] == 0
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    assert env.run(play).to_dict() == restored.run(play).to_dict()
    assert restored.props["spent"] == 10
    assert len(resolutions(restored)) == 1
    assert resolutions(restored)[0].round == 2


def test_independent_reviews_keep_their_own_responders_and_share_costs():
    c = contract(reviewers=("reviewer", "other"))
    stack = c["mechanisms"]["review"]["stack"]
    stack["kinds"]["purchase"]["responders"] = "$it.id == 'reviewer'"
    c["mechanisms"]["second"] = json.loads(json.dumps(c["mechanisms"]["review"]))
    second = c["mechanisms"]["second"]["stack"]["kinds"]["purchase"]
    second.update(responders="$it.id == 'other'", resolve=["$world.spent += 20"])
    c["actions"]["submit_both"] = {"by": "manager", "do": [
        {"flow": name, "action": "push", "item": "purchase"} for name in ("review", "second")]}
    c["stages"][0]["actions"] = ["submit_both"]
    env = fg_env.load(c)

    def play(wake):
        if wake.stage == "submit" and wake.round == 1:
            assert wake.call("submit_both", {}).ok
        if wake.stage == "departure" and wake.entity_id == "reviewer" and wake.round == 1:
            assert wake.call("leave", {}).ok
        if wake.stage == "second_stack" and wake.round == 2:
            assert wake.call("second_pass", {}).ok
        wake.end()

    env.run(play, rounds=1)
    assert env.props["spent"] == 10
    assert env.props["review_stack"]["items"] == []
    assert len(env.props["second_stack"]["items"]) == 1
    env.run(play)
    assert env.props["spent"] == 30
    assert env.props["second_stack"]["items"] == []


def test_a_refused_resolution_fails_the_run_and_commits_nothing():
    c = contract()
    c["mechanisms"]["review"]["stack"]["kinds"]["purchase"]["resolve"] += [{"fail": "blocked"}]
    env = fg_env.load(c)
    result = env.run(participant())
    assert result.status == "failed" and "on_exit: blocked World logic cannot be refused" in result.error
    assert env.props["spent"] == 0
    assert len(env.props["review_stack"]["items"]) == 1
    assert not resolutions(env)
