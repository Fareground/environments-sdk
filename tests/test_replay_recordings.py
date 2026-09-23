"""Recordings that replay exactly: outcomes a chance chooser picked, and runs that continue a fork."""
import copy

import fg_env

from test_runtime import SHOP
from test_traces import overreacher

COIN = {
    "name": "Coin",
    "clock": {"rounds": 4},
    "world": {"heads": 0},
    "types": {"player": {"agent": True, "props": {"calls": 0}}},
    "entities": {"ann": {"type": "player", "name": "Ann"}},
    "actions": {"call": {"by": "player", "terminal": True, "do": ["$actor.calls += 1"]}},
    "events": [{"name": "flip", "phase": "start",
                "do": [{"chance": [{"p": 0.5, "label": "heads", "do": ["$world.heads += 1"]},
                                   {"p": 0.5, "label": "tails"}], "as": "coin"}]}],
    "stages": [{"name": "guess"}],
    "outputs": {"heads": {"expr": "$world.heads", "type": "int"}},
}


def _call(wake):
    wake.call("call", {})


def _always_heads():
    return fg_env.load(COIN, seed=1, exposures=True, chance=lambda node: 0).run(_call)


def test_outcomes_a_chance_chooser_picked_are_recorded_and_replay_without_the_chooser():
    result = _always_heads()
    assert result.outputs["heads"] == 4
    assert result.exposures["chance"][0] == {"chance": "coin", "site": "events[0].do[0]", "index": 0,
                                             "label": "heads", "round": 1}
    assert [pick["round"] for pick in result.exposures["chance"]] == [1, 2, 3, 4]
    replayed = fg_env.analysis.trace(result).replay(COIN)
    assert replayed.ok, replayed.message
    assert replayed.result.outputs == result.outputs


def test_a_chance_node_that_changed_since_the_recording_is_a_divergence():
    changed = copy.deepcopy(COIN)
    changed["events"][0]["do"][0]["chance"][0]["label"] = "crown"
    replayed = fg_env.analysis.trace(_always_heads()).replay(changed)
    assert not replayed.ok
    assert replayed.divergence["what"] == "chance"
    assert "chance pick 1 (coin at events[0].do[0], round 1)" in replayed.message
    assert "the recording chose 0 (heads)" in replayed.message


def test_a_saved_json_lines_recording_keeps_its_chance_picks(tmp_path):
    path = tmp_path / "coin.jsonl"
    _always_heads().save(path)
    assert fg_env.analysis.trace(path).replay(COIN).ok


def _history():
    env = fg_env.load(SHOP, seed=3, exposures=True)
    env.run(overreacher, rounds=1)
    return env.snapshot()


def test_a_forked_run_replays_from_the_snapshot_it_continued_from(tmp_path):
    result = fg_env.fork(SHOP, _history(), inputs={"budget": 50}).run(overreacher)
    start = result.exposures["start"]
    assert start["round"] == 1 and start["exposures"] == {"wakes": 4, "chance": 0}
    replayed = fg_env.analysis.trace(result).replay(SHOP)
    assert replayed.ok, replayed.message
    path = tmp_path / "fork.jsonl"
    result.save(path)
    assert fg_env.analysis.trace(path).replay(SHOP).ok


def test_a_run_restored_unchanged_still_replays_from_its_build():
    result = fg_env.Env.restore(SHOP, _history()).run(overreacher)
    assert "start" not in result.exposures
    assert fg_env.analysis.trace(result).replay(SHOP).ok
