"""Snapshots of a run stopped part-way through a round: a long single-round process can be saved and resumed."""
import json
from pathlib import Path

import pytest

import fg_env
from fg_env import host
from fg_env.errors import SnapshotError
from fg_env.host.stubs import StubEvaluator

DEBATE = Path(__file__).parents[1] / "examples" / "contracts" / "host" / "debate_judged.json"

#: One long round of haggling: sellers and buyers take turns until a price is agreed or the passes run out.
HAGGLE = {
    "name": "Haggle",
    "clock": {"rounds": 1},
    "world": {"ask": 100, "bid": 40, "deal": 0},
    "types": {"seller": {"agent": True}, "buyer": {"agent": True}},
    "entities": {"sam": {"type": "seller"}, "sue": {"type": "seller"}, "bob": {"type": "buyer"}, "bea": {"type": "buyer"}},
    "records": {"table": {"fields": {"text": "text"}}},
    "actions": {
        "lower": {"by": "seller", "params": {"by": {"type": "int", "min": 1, "max": 9}},
                  "do": ["$world.ask -= $params.by", {"post": "table", "text": "'ask ' + $text($world.ask)"}]},
        "raise": {"by": "buyer", "params": {"by": {"type": "int", "min": 1, "max": 9}},
                  "do": ["$world.bid += $params.by", {"post": "table", "text": "'bid ' + $text($world.bid)"}]},
    },
    "stages": [{"name": "haggle", "until": "$world.bid >= $world.ask", "passes": 12,
                "on_exit": ["$world.deal = $world.ask if $world.bid >= $world.ask else 0"]}],
    "outputs": {"deal": "$world.deal", "ask": "$world.ask", "bid": "$world.bid"},
}


def _stop_at(point):
    seen = {"n": 0}

    def stop(_env):
        seen["n"] += 1
        return seen["n"] == point
    return stop


def _saved(env):
    return json.loads(json.dumps(env.snapshot()))


@pytest.mark.parametrize("point", [3, 7, 12])
def test_a_run_stopped_between_turns_resumes_from_its_snapshot_exactly(point):
    straight = fg_env.load(HAGGLE, seed=5, exposures=True).run("random")
    env = fg_env.load(HAGGLE, seed=5, exposures=True)
    env.run("random", stop=_stop_at(point))
    assert env.status == "stopped" and env.round == 1
    resumed = fg_env.Env.restore(HAGGLE, _saved(env))
    assert resumed.status == "stopped" and resumed.world.stage == "haggle"
    assert resumed.run("random").to_dict() == straight.to_dict()


def test_a_resumed_run_can_be_saved_again_part_way_through_the_same_round():
    straight = fg_env.load(HAGGLE, seed=8).run("random")
    env = fg_env.load(HAGGLE, seed=8)
    env.run("random", stop=_stop_at(4))
    env = fg_env.Env.restore(HAGGLE, _saved(env))
    env.run("random", stop=_stop_at(4))
    assert env.status == "stopped"
    env = fg_env.Env.restore(HAGGLE, _saved(env))
    assert env.run("random").to_dict() == straight.to_dict()


def test_a_run_restored_between_rounds_then_stopped_part_way_resumes_exactly():
    contract = {**HAGGLE, "clock": {"rounds": 3},
                "events": [{"phase": "start", "do": ["$world.ask = 100", "$world.bid = 40"]}]}
    straight = fg_env.load(contract, seed=2).run("random")
    env = fg_env.load(contract, seed=2)
    env.run("random", rounds=1)
    env = fg_env.Env.restore(contract, _saved(env))
    env.run("random", stop=_stop_at(5))
    assert env.status == "stopped" and env.round == 2
    assert fg_env.Env.restore(contract, _saved(env)).run("random").to_dict() == straight.to_dict()


def test_a_snapshot_taken_part_way_through_a_round_cannot_be_forked():
    env = fg_env.load(HAGGLE, seed=5)
    env.run("random", stop=_stop_at(3))
    with pytest.raises(SnapshotError, match="between rounds"):
        fg_env.fork(HAGGLE, _saved(env), inputs={})


def test_host_answers_given_earlier_in_the_round_are_not_asked_again_on_restore():
    debate = {**json.loads(DEBATE.read_text()), "stages": [*json.loads(DEBATE.read_text())["stages"],
                                                            {"name": "recess", "actions": []}]}

    def speaker(wake):
        wake.call("speak", {"text": f"Round {wake.round}: free buses cut congestion costs."})
        wake.end()

    straight = host.run(host.load(debate, hosts={"judge": StubEvaluator()}, seed=5), speaker)
    env = host.load(debate, hosts={"judge": StubEvaluator()}, seed=5)
    host.run(env, speaker, stop=lambda e: e.round == 2 and len(e.world.records("judge")) == 4)
    assert env.status == "stopped" and env.world.stage == "speeches"  # this round's speeches are judged; recess is next
    evaluator = StubEvaluator()
    resumed = host.run(host.restore(debate, _saved(env), hosts={"judge": evaluator}), speaker)
    assert resumed.outputs == straight.outputs and resumed.events == straight.events
    assert len(evaluator.calls) == 2  # only the last round's speeches are judged: nothing recorded is asked again
