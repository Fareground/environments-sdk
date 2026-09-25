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
    "stages": [{"name": "play"}],
    "events": [{"on": "stage.play.turn", "when": "$timed_out", "do": ["$actor.strikes += 1"]},
               {"on": "stage.play.turn", "when": "not $acted and not $timed_out", "do": ["$world.idle += 1"]}],
}
LIMIT = 0.2


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
        result = env.run(participant, time_limit=LIMIT)
    finally:
        release.set()
    assert time.monotonic() - start < 3
    assert result.status == "completed", result.error
    assert result.degraded == ["agents_often_failed"]  # every one of ann's turns ran out of time
    assert env.entity("ann")["props"] == {"score": 0, "strikes": 2}
    assert env.entity("bo")["props"]["score"] == 4
    assert env.props["idle"] == 0  # the timeout's event ran instead of the idle one
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

    env = fg_env.load(GAME, seed=1)
    result = env.run({"ann": slow, "bo": "idle"}, rounds=1, time_limit=0.1)
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

    result = fg_env.load(GAME, seed=1).run({"ann": dawdles, "bo": "idle"}, time_limit=0.1)
    assert result.status == "completed", result.error
    assert result.degraded == ["agents_often_failed"]  # every one of ann's turns ran out of time
    assert result.stats["timeouts"] == 2
    assert cancelled.wait(2)


def test_participants_that_finish_in_time_play_exactly_as_without_a_limit():
    plain = fg_env.run(SHOP, seed=4)
    limited = fg_env.run(SHOP, seed=4, time_limit=30)
    assert limited.events == plain.events and limited.outputs == plain.outputs

    sealed = _with_stage(turns="simultaneous")
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
        result = env.run(participant, rounds=1, time_limit=LIMIT)
    finally:
        release.set()
    assert result.status == "running", result.error
    assert env.entity("bo")["props"]["score"] == 3
    assert env.entity("ann")["props"]["strikes"] == 1
    assert [e["actor"] for e in result.events if e["kind"] == "timeout"] == ["ann"]


def test_under_a_limit_participants_not_marked_concurrent_still_take_their_turns_one_at_a_time():
    contract = _with_stage(turns="simultaneous")
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
    fg_env.load(contract, seed=1).run(careful, rounds=1, time_limit=5)
    eager, eager_counts = tracked(True)
    fg_env.load(contract, seed=1).run(eager, rounds=1, time_limit=5)
    assert careful_counts["most"] == 1
    assert eager_counts["most"] > 1


def test_every_agent_is_told_the_run_limit():
    env = fg_env.load(GAME, seed=1)
    env.time_limit = 7
    assert env.preview("ann")["time_limit"] == 7
    assert "You have 7 seconds for this turn" in env.preview("ann")["update"]
    env.time_limit = 1
    assert "You have 1 second for this turn" in env.preview("ann")["update"]  # audit 11 L5

    limits = {}

    def notes(wake):
        limits[wake.entity_id] = (wake.time_limit, wake.time_left)
        wake.end()

    env.run(notes, rounds=1, time_limit=9)
    assert limits["ann"][0] == 9 and 0 < limits["bo"][1] <= 9


def test_a_limit_that_cannot_work_is_refused():
    with pytest.raises(ValueError, match="time_limit"):
        fg_env.load(GAME, seed=1).run(time_limit=0)


def _hanging_ann(release):
    def participant(wake):
        if wake.entity_id == "ann":
            release.wait(10)
        else:
            wake.call("score", {"points": 2})
    return participant


def test_experiments_run_jobs_and_tournaments_take_a_turn_time_limit():
    from fg_env.experiments.experiment import Job, run_jobs

    release = threading.Event()
    try:
        runs = fg_env.experiment(GAME, runs=1, participants=_hanging_ann(release), time_limit=LIMIT).arms["baseline"]
        assert runs.runs[0].stats["timeouts"] == 2
        [job] = run_jobs(GAME, [Job({}, None, 1)], participants=_hanging_ann(release), time_limit=LIMIT)
        assert job.stats["timeouts"] == 2
        played = fg_env.rl.tournament(GAME, {"slow": _hanging_ann(release), "quick": _hanging_ann(release)},
                                   seats=["ann", "bo"], time_limit=LIMIT)
        assert all(run.stats["timeouts"] == 2 for run in played.runs)
    finally:
        release.set()


@pytest.mark.parametrize("bad", [0, -1, "5", True])
def test_a_bad_time_limit_raises_before_anything_runs(bad):
    with pytest.raises(ValueError, match="time_limit must be a number of seconds"):
        fg_env.experiment(GAME, runs=1, time_limit=bad)
