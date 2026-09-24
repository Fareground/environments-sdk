"""A stage's `turn` events run after each agent's turn (in a simultaneous stage, once its choices have committed)."""
import pytest
from family_fixtures import Nothing, scratch_family

import fg_env
from fg_env.mechanisms import expand_mechanisms
from fg_env.registry import mode


def _contract(turns):
    return {"name": "Hooks", "clock": {"rounds": 2},
            "types": {"player": {"agent": True, "props": {"ends": 0, "acted": -1}}},
            "entities": {"ann": {"type": "player"}, "bo": {"type": "player"}},
            "actions": {"peek": {"by": "player", "terminal": True, "do": ["$actor.acted = $actor.ends"],
                                 "outcome": "ends {$actor.ends}"}},
            "stages": [{"name": "play", "turns": turns}],
            "events": [{"on": "stage.play.turn", "do": ["$actor.ends += 1"]}]}


def test_a_turn_event_follows_every_sequential_turn():
    env = fg_env.load(_contract("sequential"), seed=1)
    told = []

    def play(wake):
        told.append(wake.call("peek").text)

    result = env.run(play)
    assert result.status == "completed", result.error
    assert told == ["ends 0", "ends 0", "ends 1", "ends 1"]
    assert all(env.entity(p)["props"]["ends"] == 2 for p in ("ann", "bo"))


def test_a_simultaneous_turn_event_runs_after_the_choices_are_committed():
    env = fg_env.load(_contract("simultaneous"), seed=1)
    result = env.run(lambda wake: wake.call("peek"))
    assert result.status == "completed", result.error
    for p in ("ann", "bo"):
        props = env.entity(p)["props"]
        assert props["ends"] == 2 and props["acted"] == 1  # the action saw ends before the event


@pytest.fixture
def upkeep():
    with scratch_family("test_upkeep"):
        mode("test_upkeep", "end", Nothing, "Adds an upkeep after each turn.")(
            lambda name, cfg, contract: {"events": [{"on": "stage.play.turn", "do": ["$actor.ends += 10"]}]})
        yield


def test_a_mechanism_adds_its_turn_effects_as_an_event_after_the_authors(upkeep):
    data, issues = expand_mechanisms({**_contract("sequential"),
                                      "mechanisms": {"upkeep": {"kind": "test_upkeep", "mode": "end"}}})
    assert issues == []
    assert data["events"] == [{"on": "stage.play.turn", "do": ["$actor.ends += 1"]},
                              {"on": "stage.play.turn", "do": ["$actor.ends += 10"]}]
