"""Async participants: awaited by plain runs, run concurrently in simultaneous stages, and native under `arun`."""
import asyncio
import copy
import time

import pytest

import fg_env

GAME = {
    "name": "Tally",
    "clock": {"rounds": 2},
    "types": {"player": {"agent": True, "props": {"score": 0}}},
    "entities": {"ann": {"type": "player", "name": "Ann"}, "bo": {"type": "player", "name": "Bo"},
                 "cy": {"type": "player", "name": "Cy"}, "di": {"type": "player", "name": "Di"}},
    "actions": {"score": {"by": "player", "params": {"points": {"type": "int", "min": 1, "max": 3}},
                          "do": ["$actor.score += $params.points"], "terminal": True}},
    "stages": [{"name": "play"}],
}


def _points(wake) -> int:
    return 1 + sum(map(ord, wake.entity_id)) % 3


def _sync(wake):
    wake.call("score", {"points": _points(wake)})


async def _async(wake):
    await asyncio.sleep(0)
    wake.call("score", {"points": _points(wake)})


def test_an_async_participant_plays_exactly_like_the_same_plain_one():
    plain = fg_env.load(GAME, seed=1).run(_sync)
    awaited = fg_env.load(GAME, seed=1).run(_async)
    assert awaited.ok, awaited.error
    assert awaited.events == plain.events
    assert awaited.stats["actions"] == 8


def test_objects_with_an_async_call_and_functions_returning_coroutines_are_awaited():
    class Agent:
        async def __call__(self, wake):
            await _async(wake)

    def hands_back(wake):
        return _async(wake)

    for participant in (Agent(), hands_back, {"player": Agent(), "ann": hands_back}):
        result = fg_env.load(GAME, seed=1).run(participant)
        assert result.ok, result.error
        assert result.stats["actions"] == 8


def test_async_participants_of_a_simultaneous_stage_run_concurrently_and_deterministically():
    sealed = copy.deepcopy(GAME)
    sealed["clock"]["rounds"] = 1
    sealed["stages"] = [{"name": "play", "turns": "simultaneous"}]

    async def thinks(wake):
        await asyncio.sleep(0.25)
        wake.call("score", {"points": _points(wake)})

    start = time.monotonic()
    first = fg_env.load(sealed, seed=3).run(thinks)
    elapsed = time.monotonic() - start
    second = fg_env.load(sealed, seed=3).run(thinks)
    plain = fg_env.load(sealed, seed=3).run(_sync)
    assert first.ok, first.error
    assert elapsed < 0.7  # four agents thinking 0.25 s each, together
    assert first.events == second.events == plain.events


def test_arun_works_inside_a_running_event_loop_and_runs_participants_on_that_loop():
    on_caller_loop = []

    async def main():
        loop = asyncio.get_running_loop()

        async def agent(wake):
            on_caller_loop.append(asyncio.get_running_loop() is loop)
            await _async(wake)

        return await fg_env.load(GAME, seed=1).arun(agent, rounds=1)

    result = asyncio.run(main())
    assert result.status == "running" and result.stats["actions"] == 4
    assert on_caller_loop == [True] * 4


def test_cancelling_arun_stops_the_run_at_its_next_safe_point_and_it_can_continue():
    long_game = {**GAME, "clock": {"rounds": 40}}
    env = fg_env.load(long_game, seed=1)

    played = asyncio.Event()

    async def agent(wake):
        await asyncio.sleep(0.01)
        wake.call("score", {"points": 1})
        if wake.round == 2:
            played.set()

    async def main():
        task = asyncio.create_task(env.arun(agent))
        await played.wait()  # cancelled mid-game, after a round has certainly finished, however slow the machine
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(300):  # the engine finishes the turn in progress, then stops
            if not env._running.locked():
                break
            await asyncio.sleep(0.01)

    asyncio.run(main())
    assert env.status == "stopped" and 0 < env.round < 40
    assert env.run(agent).status == "completed"


def test_an_async_generator_is_refused_with_what_to_use_instead():
    async def generates(wake):
        yield

    with pytest.raises(TypeError, match="async generator"):
        fg_env.load(GAME, seed=1).run(generates)


def test_a_plain_run_started_inside_an_async_participant_says_to_use_arun():
    inner = fg_env.load(GAME, seed=2)
    errors = []

    async def outer(wake):
        errors.append(inner.run(_async).error)
        wake.call("score", {"points": 1})

    result = fg_env.load(GAME, seed=1).run({"ann": outer, "*": "idle"}, rounds=1)
    assert result.status == "running", result.error
    assert errors and "arun" in errors[0]


def test_an_async_participant_that_raises_fails_the_run_with_its_entity():
    async def breaks(wake):
        await asyncio.sleep(0)
        raise ValueError("model offline")

    result = fg_env.load(GAME, seed=1).run({"bo": breaks, "*": _sync})
    assert result.status == "failed"
    assert "participant for bo raised ValueError: model offline" in result.error
