"""Mechanism stage hooks fill in turn settings the author left unset."""
import pytest
from pydantic import BaseModel

import fg_env
from fg_env.sdk.registry import MECHANISMS, mechanism


class _Empty(BaseModel):
    pass


@pytest.fixture
def hooking_mechanism():
    kind = "test_stage_settings"

    @mechanism(kind, _Empty, "Hooks turn settings onto the author's stage.")
    def _expand(name, config, contract):
        return {"stage_hooks": {"play": {"until": "$world.done", "passes": 5, "who": "$it.active"}}}

    yield kind
    MECHANISMS.pop(kind, None)


BASE = {"name": "Hooks", "clock": {"rounds": 1}, "world": {"done": False},
        "types": {"p": {"agent": True, "props": {"active": True}}}, "entities": {"p": {"type": "p"}},
        "stages": [{"name": "play", "turns": "sequential", "passes": 2}]}


def test_hooks_set_what_the_author_left_unset(hooking_mechanism):
    stage = fg_env.parse({**BASE, "mechanisms": {"m": {"kind": hooking_mechanism}}}).stages[0]
    assert stage.until == "$world.done" and stage.who == "$it.active" and stage.passes == 2


def test_hooks_cannot_set_unknown_stage_fields():
    kind = "test_stage_bad_setting"

    @mechanism(kind, _Empty, "Tries to set a field stages don't have.")
    def _expand(name, config, contract):
        return {"stage_hooks": {"play": {"colour": "blue"}}}

    try:
        issues = fg_env.check({**BASE, "mechanisms": {"m": {"kind": kind}}})
        assert any("cannot set colour" in i.message for i in issues)
    finally:
        MECHANISMS.pop(kind, None)
