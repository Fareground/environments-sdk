"""Turn time limits: a slow or hung participant loses its turn, never the run."""
import asyncio
import copy
import threading
import time

import pytest
from test_runtime import SHOP

import fg_env

GAME = {
    "name": "Blitz",
    "clock": {"rounds": 2},
    "world": {"idle": 0},
    "types": {"player": {"agent": True, "props": {"score": 0, "strikes": 0}}},
    "entities": {"ann": {"type": "player", "name": "Ann"}, "bo": {"type": "player", "name": "Bo"}},
    "actions": {"score": {"by": "player", "params": {"points": {"type": "int", "min": 1, "max": 3}},
                          "do": ["$actor.score += $params.points"], "terminal": True}},
    "stages": [{"name": "play", "time_limit": 0.2, "on_timeout": ["$actor.strikes += 1"],
                "on_idle": ["$world.idle += 1"]}],
}


def _with_stage(**changes):
    contract = copy.deepcopy(GAME)
    contract["stages"][0].update(changes)
    return contract


def test_a_hung_participant_times_out_and_the_run_goes_on():
    release = threading.Event()

    def participant(wake):
        if wake.entity_id == "ann":
            release.wait(10)  # hangs well past the limit
        else:
            wake.call("score", {"points": 2})

    env = fg_env.load(GAME, seed=1)
    start = time.monotonic()
    try:
        result = env.run(participant)
    finally:
        release.set()
    assert time.monotonic() - start < 3
    assert result.status == "completed", result.error
    assert result.degraded == ["agents_often_failed"]  # every one of ann's turns ran out of time
    assert env.entity("ann")["props"] == {"score": 0, "strikes": 2}
    assert env.entity("bo")["props"]["score"] == 4
    assert env.props["idle"] == 0  # on_timeout ran instead of on_idle
    assert result.stats["timeouts"] == 2
    timeouts = [e for e in result.events if e["kind"] == "timeout"]
    assert [e["actor"] for e in timeouts] == ["ann", "ann"]
    assert timeouts[0]["text"] == "Ann ran out of time." and timeouts[0]["data"]["limit"] == 0.2


def test_calls_after_the_deadline_are_refused_and_change_nothing():
    late = []
    finished = threading.Event()

    def slow(wake):
        time.sleep(0.4)
        late.append(wake.call("score", {"points": 3}))
        late.append(wake.update)
        finished.set()

    env = fg_env.load(_with_stage(time_limit=0.1), seed=1)
    result = env.run({"ann": slow, "bo": "idle"}, rounds=1)
    assert finished.wait(3)
    refused, update = late
    assert not refused.ok and refused.ended and refused.data["error"] == "timeout"
    assert "time for this turn ran out" in refused.text
    assert update == "This turn is over."
    assert result.stats["timeouts"] == 1 and env.entity("ann")["props"]["score"] == 0


def test_an_async_participant_past_its_deadline_is_cancelled():
    cancelled = threading.Event()

    async def dawdles(wake):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    result = fg_env.load(_with_stage(time_limit=0.1), seed=1).run({"ann": dawdles, "bo": "idle"})
    assert result.status == "completed", result.error
    assert result.degraded == ["agents_often_failed"]  # every one of ann's turns ran out of time
    assert result.stats["timeouts"] == 2
    assert cancelled.wait(2)


def test_participants_that_finish_in_time_play_exactly_as_without_a_limit():
    plain = fg_env.run(SHOP, seed=4)
    limited = fg_env.run(SHOP, seed=4, time_limit=30)
    assert limited.events == plain.events and limited.outputs == plain.outputs

    sealed = _with_stage(turns="simultaneous", time_limit=None, on_timeout=[])
    free = fg_env.load(sealed, seed=6).run()
    timed = fg_env.load(sealed, seed=6).run(time_limit=30)
    assert timed.events == free.events and timed.stats["timeouts"] == 0


def test_in_a_simultaneous_stage_the_others_choices_still_count_when_one_agent_times_out():
    release = threading.Event()

    def participant(wake):
        if wake.entity_id == "ann":
            release.wait(10)
        else:
            wake.call("score", {"points": 3})

    env = fg_env.load(_with_stage(turns="simultaneous"), seed=1)
    try:
        result = env.run(participant, rounds=1)
    finally:
        release.set()
    assert result.status == "running", result.error
    assert env.entity("bo")["props"]["score"] == 3
    assert env.entity("ann")["props"]["strikes"] == 1
    assert [e["actor"] for e in result.events if e["kind"] == "timeout"] == ["ann"]


def test_under_a_limit_participants_not_marked_concurrent_still_take_their_turns_one_at_a_time():
    contract = _with_stage(turns="simultaneous", time_limit=5)
    contract["entities"].update({"cy": {"type": "player", "name": "Cy"}, "di": {"type": "player", "name": "Di"}})

    def tracked(concurrent):
        counts = {"active": 0, "most": 0}
        guard = threading.Lock()

        def play(wake):
            with guard:
                counts["active"] += 1
                counts["most"] = max(counts["most"], counts["active"])
            time.sleep(0.05)
            with guard:
                counts["active"] -= 1
            wake.end()

        play.concurrent = concurrent
        return play, counts

    careful, careful_counts = tracked(False)
    fg_env.load(contract, seed=1).run(careful, rounds=1)
    eager, eager_counts = tracked(True)
    fg_env.load(contract, seed=1).run(eager, rounds=1)
    assert careful_counts["most"] == 1
    assert eager_counts["most"] > 1


def test_the_limit_can_depend_on_the_agent_and_falls_back_to_the_run_default():
    contract = _with_stage(time_limit="5 if $actor.id == ann else null")
    env = fg_env.load(contract, seed=1)
    env.time_limit = 7
    ann, bo = env.preview("ann"), env.preview("bo")
    assert ann["time_limit"] == 5 and bo["time_limit"] == 7
    assert "You have 5 seconds for this turn" in ann["update"]

    limits = {}

    def notes(wake):
        limits[wake.entity_id] = (wake.time_limit, wake.time_left)
        wake.end()

    env.run(notes, rounds=1, time_limit=9)
    assert limits["ann"][0] == 5 and limits["bo"][0] == 9
    assert 0 < limits["bo"][1] <= 9


def test_a_run_limit_applies_to_stages_that_set_none():
    release = threading.Event()

    def hangs(wake):
        release.wait(10)

    contract = _with_stage(time_limit=None)
    try:
        result = fg_env.load(contract, seed=1).run(hangs, rounds=1, time_limit=0.1)
    finally:
        release.set()
    assert result.stats["timeouts"] == 2


def test_limits_that_cannot_work_are_reported_before_the_run():
    for limit, message in ((0, "above 0"), (-2, "above 0"), ("30", "not a number"),
                           ("$nobody.patience", "not available")):
        errors = [str(i) for i in fg_env.check(_with_stage(time_limit=limit)) if i.severity == "error"]
        assert any("time_limit" in e and message in e for e in errors), (limit, errors)
    with pytest.raises(ValueError, match="time_limit"):
        fg_env.load(GAME, seed=1).run(time_limit=0)
    bad = fg_env.load(_with_stage(time_limit="$actor.score - 1"), seed=1).run(rounds=1)
    assert bad.status == "failed" and "stages.play.time_limit" in bad.error
