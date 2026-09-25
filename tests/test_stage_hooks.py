"""Mechanism stage hooks fill in turn settings the author left unset."""
import pytest
from family_fixtures import Nothing, scratch_family

import fg_env
from fg_env.registry import mode

FAMILY = "test_stage_settings"


@pytest.fixture
def hooks():
    with scratch_family(FAMILY):
        mode(FAMILY, "settings", Nothing, "Hooks turn settings onto the author's stage.")(
            lambda name, config,
            contract: {"stage_hooks": {"play": {"until": "$world.done", "passes": 5, "who": "$it.active"}}})
        mode(FAMILY, "bad", Nothing, "Tries to set a field stages don't have.")(
            lambda name, config, contract: {"stage_hooks": {"play": {"colour": "blue"}}})
        yield


BASE = {"name": "Hooks", "clock": {"rounds": 1}, "world": {"done": False},
        "types": {"p": {"agent": True, "props": {"active": True}}}, "entities": {"p": {"type": "p"}},
        "stages": [{"name": "play", "turns": "sequential", "passes": 2}]}


def test_hooks_set_what_the_author_left_unset(hooks):
    stage = fg_env.parse({**BASE, "mechanisms": {"m": {"kind": FAMILY, "mode": "settings"}}}).stages[0]
    assert stage.until == "$world.done" and stage.who == "$it.active" and stage.passes == 2


def test_hooks_cannot_set_unknown_stage_fields(hooks):
    issues = fg_env.check({**BASE, "mechanisms": {"m": {"kind": FAMILY, "mode": "bad"}}})
    assert any("cannot set colour" in i.message for i in issues)


TALK_THEN_VOTE = {
    "name": "Talk then vote", "clock": {"rounds": 1},
    "types": {"m": {"agent": True, "props": {"said": 0}}}, "entities": {"m": {"type": "m", "count": 3}},
    "actions": {"speak": {"by": "m", "do": "$actor.said += 1"}},
    "stages": [{"name": "talk", "turns": "sequential"}, {"name": "vote", "turns": "simultaneous"}],
    "mechanisms": {"b": {"kind": "decision", "mode": "ballot", "who": "m", "options": ["yes", "no"], "stage": "vote"}},
    "outputs": {"res": "$world.b_result"},
}


def test_a_mechanism_attached_to_a_stage_owns_its_tools_there():
    """A stage that offers every action does not offer a mechanism's tools attached to another stage: a ballot for
    the vote stage is never cast while agents talk, so the votes cast in the vote stage are the ones counted."""
    offered = {}

    def member(wake):
        offered.setdefault(wake.stage, {t.name for t in wake.tools})
        early = wake.call("b_vote", {"choice": "yes"}) if wake.stage == "talk" else None
        assert early is None or not early.ok
        if wake.stage == "vote":
            assert wake.call("b_vote", {"choice": "no"}).ok
        wake.end()

    result = fg_env.run(TALK_THEN_VOTE, {"m": member}, seed=1)
    assert "b_vote" not in offered["talk"] and "b_vote" in offered["vote"]
    assert result.outputs["res"]["winner"] == "no" and result.outputs["res"]["counts"]["no"] == 3


def test_a_stage_that_lists_a_mechanisms_tool_offers_it():
    listed = {**TALK_THEN_VOTE, "stages": [{"name": "talk", "turns": "sequential", "actions": ["speak", "b_vote"]},
                                           {"name": "vote", "turns": "simultaneous"}]}
    assert "b_vote" in fg_env.parse(listed).stages[0].actions
