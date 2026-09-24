"""Copies of a run for lookahead: taken between rounds, part-way through a round, or inside a turn."""
import asyncio
import copy
import threading
import time

from test_host_judge import DEBATE, speaker
from test_runtime import AUCTION, SHOP
from test_time_limits import GAME

import fg_env
from fg_env import host
from fg_env.host.stubs import StubEvaluator
from fg_env.participants import RandomAgent

LUCK = {
    "name": "Luck",
    "clock": {"rounds": 5},
    "world": {"rolls": {"type": "list", "default": []}},
    "types": {"gambler": {"agent": True, "props": {}}},
    "entities": {"g": {"type": "gambler"}},
    "actions": {"roll": {"by": "gambler", "terminal": True, "do": ["$world.rolls += $dice('d6')"]}},
    "stages": [{"name": "play", "must_act": True}],
    "outputs": {"rolls": {"expr": "$world.rolls", "type": "list"}},
}


def _roller(wake):
    wake.call("roll", {})


def test_a_clone_taken_in_a_turn_never_changes_the_real_run():
    def play(clone):
        base = RandomAgent(7)
        looked = []

        def agent(wake):
            if clone and wake.round == 2:
                with wake.clone() as branch:
                    looked.append(branch.run("random").status)
            base(wake)

        return fg_env.load(SHOP, seed=5).run(agent), looked

    plain, _ = play(False)
    cloned, looked = play(True)
    assert looked and all(status in ("completed", "ended") for status in looked)
    assert cloned.events == plain.events
    assert cloned.outputs == plain.outputs
    assert cloned.stats == plain.stats


def test_a_clone_with_the_same_luck_plays_on_exactly_like_the_real_run():
    copies = []
    base = RandomAgent(3)

    def agent(wake):
        if wake.round == 2 and not copies:
            branch = wake.clone(same_luck=True, participants=RandomAgent(3))
            branch.play(RandomAgent(3))
            copies.append(branch.run(RandomAgent(3)))
            branch.close()
        base(wake)

    real = fg_env.load(SHOP, seed=11).run(agent)
    assert copies[0].events == real.events
    assert copies[0].outputs == real.outputs


def test_fresh_luck_is_shared_by_clones_of_one_turn_and_hides_the_real_future():
    seen = {}

    def agent(wake):
        if wake.round == 1:
            for label, options in (("fresh", {}), ("again", {}), ("same", {"same_luck": True})):
                with wake.clone(**options) as branch:
                    branch.call("roll", {})
                    seen[label] = branch.run(_roller).outputs["rolls"]
        _roller(wake)

    real = fg_env.load(LUCK, seed=8).run(agent)
    assert seen["fresh"] == seen["again"]
    assert seen["same"] == real.outputs["rolls"]
    assert seen["fresh"] != real.outputs["rolls"]


def test_env_clone_between_rounds_and_mid_round_continues_identically():
    env = fg_env.load(SHOP, seed=3)
    env.run("random", rounds=1)
    between = env.clone()
    reference = fg_env.load(SHOP, seed=3)
    reference.run("random", rounds=1)
    assert between.run("random").events == reference.run("random").events

    for contract in (SHOP, AUCTION):
        stops = {"n": 0}

        def stop(_env):
            stops["n"] += 1  # noqa: B023 — called within this iteration
            return stops["n"] == 3  # noqa: B023 — called within this iteration

        stopped = fg_env.load(contract, seed=6)
        stopped.run("random", stop=stop)
        assert stopped.status == "stopped" and stopped._in_round
        twin = stopped.clone()
        assert twin.status == "stopped"
        assert twin.run("random").events == stopped.run("random").events


def test_a_clone_inside_a_simultaneous_stage_continues_from_the_sealed_choices():
    pending = []

    def bidder(wake):
        if wake.entity_id == "bo":
            with wake.clone() as branch:
                pending.append(branch.pending)
                assert branch.call("bid", {"amount": 30}).ok
                assert branch.run("random").status == "completed"
        wake.call("bid", {"amount": 20})

    plain = fg_env.load(AUCTION, seed=2).run(lambda wake: wake.call("bid", {"amount": 20}))
    cloned = fg_env.load(AUCTION, seed=2).run(bidder)
    assert pending[0].simultaneous and pending[0].actor == "bo"
    assert cloned.events == plain.events


def test_recorded_timeouts_replay_in_a_copy():
    one_round = copy.deepcopy(GAME)
    one_round["clock"]["rounds"] = 1
    copies = []

    def participant(wake):
        if wake.entity_id == "ann":
            time.sleep(0.3)  # past the stage's 0.2 s limit
            wake.call("score", {"points": 3})
            return
        branch = wake.clone(same_luck=True)
        branch.call("score", {"points": 1})
        copies.append(branch.run("idle"))
        branch.close()
        wake.call("score", {"points": 1})

    real = fg_env.load(one_round, seed=1).run(participant)
    assert any(event["kind"] == "timeout" for event in real.events)
    assert copies[0].events == real.events


def test_a_run_with_async_participants_can_be_cloned_from_inside_a_turn():
    base = RandomAgent(2)
    copies = []

    async def agent(wake):
        await asyncio.sleep(0)
        if wake.round == 2 and not copies:
            with wake.clone(same_luck=True) as branch:
                copies.append(branch.pending)
        base(wake)

    async def plain_agent(wake):
        await asyncio.sleep(0)
        base(wake)

    cloned = fg_env.load(SHOP, seed=4).run(agent)
    plain = fg_env.load(SHOP, seed=4).run(plain_agent)
    assert copies and cloned.events == plain.events


def test_a_copy_never_asks_a_host_again_for_answers_the_run_recorded():
    evaluator = StubEvaluator()
    counts = []

    def cloning_speaker(wake):
        if wake.round == 2 and not counts:
            before = len(evaluator.calls)
            branch = wake.clone(same_luck=True)
            counts.append((before, len(evaluator.calls)))
            branch.close()
        speaker(wake)

    env = host.load(DEBATE, hosts={"judge": evaluator}, seed=3)
    host.run(env, cloning_speaker)
    before, after = counts[0]
    assert before > 0 and after == before


def _copy_threads():
    return {t for t in threading.enumerate() if t.name == "fg-env-copy" and t.is_alive()}


def test_clones_of_clones_are_independent_and_closing_stops_their_threads():
    before = _copy_threads()

    def agent(wake):
        if wake.round == 1:
            outer = wake.clone(same_luck=True)
            inner = outer.clone()
            outer.call("roll", {})
            assert inner.pending is not None and inner.pending.actor == "g"
            assert outer.pending is None or outer.pending.round == 2
            inner.close()
            outer.close()
        _roller(wake)

    fg_env.load(LUCK, seed=1).run(agent, rounds=1)
    assert _copy_threads() <= before
