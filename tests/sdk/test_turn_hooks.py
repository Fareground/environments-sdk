"""Stage on_wake runs before each agent's turn (so the turn reflects it) and on_turn_end after it."""
import pytest

import fg_env
from fg_env.sdk.mechanisms import expand_mechanisms
from fg_env.sdk.registry import mode

from family_fixtures import Nothing, scratch_family


def _contract(turns):
    return {"name": "Hooks", "clock": {"rounds": 2},
            "types": {"player": {"agent": True, "props": {"wakes": 0, "ends": 0, "acted": -1}}},
            "entities": {"ann": {"type": "player"}, "bo": {"type": "player"}},
            "actions": {"peek": {"by": "player", "terminal": True, "do": ["$actor.acted = $actor.ends"],
                                 "outcome": "wakes {$actor.wakes} ends {$actor.ends}"}},
            "stages": [{"name": "play", "turns": turns, "on_wake": ["$actor.wakes += 1"], "on_turn_end": ["$actor.ends += 1"]}]}


def test_sequential_turns_see_on_wake_and_on_turn_end_follows_every_turn():
    env = fg_env.load(_contract("sequential"), seed=1)
    told = []

    def play(wake):
        told.append(wake.call("peek").text)

    result = env.run(play)
    assert result.status == "completed", result.error
    assert told == ["wakes 1 ends 0", "wakes 1 ends 0", "wakes 2 ends 1", "wakes 2 ends 1"]
    assert all(env.entity(p)["props"]["wakes"] == 2 and env.entity(p)["props"]["ends"] == 2 for p in ("ann", "bo"))


def test_simultaneous_on_turn_end_runs_after_choices_are_committed():
    env = fg_env.load(_contract("simultaneous"), seed=1)
    result = env.run(lambda wake: wake.call("peek"))
    assert result.status == "completed", result.error
    for p in ("ann", "bo"):
        props = env.entity(p)["props"]
        assert props["wakes"] == 2 and props["ends"] == 2 and props["acted"] == 1  # the action saw ends before the hook


@pytest.fixture
def upkeep():
    with scratch_family("test_upkeep"):
        mode("test_upkeep", "wake", Nothing, "Adds an upkeep on wake.")(
            lambda name, cfg, contract: {"stage_hooks": {"play": {"on_wake": ["$actor.wakes += 10"],
                                                                  "on_turn_end": ["$actor.ends += 10"]}}})
        yield


def test_mechanisms_can_append_to_turn_hooks(upkeep):
    data, issues = expand_mechanisms({**_contract("sequential"), "mechanisms": {"upkeep": {"kind": "test_upkeep", "mode": "wake"}}})
    assert issues == []
    stage = data["stages"][0]
    assert stage["on_wake"] == ["$actor.wakes += 1", "$actor.wakes += 10"]
    assert stage["on_turn_end"] == ["$actor.ends += 1", "$actor.ends += 10"]
