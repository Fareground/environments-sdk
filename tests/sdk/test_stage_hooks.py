"""Mechanism stage hooks fill in turn settings the author left unset."""
import pytest

import fg_env
from fg_env.sdk.registry import mode

from family_fixtures import Nothing, scratch_family

FAMILY = "test_stage_settings"


@pytest.fixture
def hooks():
    with scratch_family(FAMILY):
        mode(FAMILY, "settings", Nothing, "Hooks turn settings onto the author's stage.")(
            lambda name, config, contract: {"stage_hooks": {"play": {"until": "$world.done", "passes": 5, "who": "$it.active"}}})
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
