"""Common random numbers: the world's luck does not depend on what the participants did.

Every block of world logic and every agent's action draws from its own stream, derived from the run's seed and
where the block is written, so a policy that draws more (or makes the world draw more) never shifts another draw.
"""
import copy
import json

import fg_env

WARD = {
    "name": "Ward",
    "clock": {"rounds": 8},
    "world": {"arrivals": {"type": "list", "default": []}, "tests": {"type": "list", "default": []}},
    "types": {"doctor": {"agent": True, "props": {}},
              "patient": {"props": {"state": {"type": "text", "default": "waiting"}, "severity": 1}}},
    "entities": {"d": {"type": "doctor"}},
    "events": [
        {"name": "arrivals", "phase": "start",
         "do": ["$n = $poisson(3)", "$world.arrivals += [$n]", {"repeat": "$n", "do": [{"create": "patient"}]}]},
        {"name": "deteriorate", "phase": "end", "each": "patient", "where": "$it.state == waiting",
         "do": [{"if": "$chance(0.5)", "then": ["$it.severity += 1"]}]},
    ],
    "actions": {"test": {"by": "doctor", "params": {"patient": {"type": "entity", "of": "patient",
                                                                 "where": "$it.state == waiting"}},
                         "do": ["$world.tests += [$random()]", "$params.patient.state = seen"]}},
    "stages": [{"name": "work", "max_actions": 3}],
    "outputs": {"arrivals": {"expr": "$world.arrivals", "type": "list"}},
}


def _busy(wake):
    """Sees every patient it can: fewer patients wait, so the end event draws less, and its own tests draw."""
    for _ in range(3):
        tool = next((t for t in wake.tools if t.name == "test"), None)
        if tool is None:
            return
        wake.call("test", {"patient": tool.input_schema["properties"]["patient"]["enum"][0]})


def test_a_policy_does_not_change_the_worlds_draws():
    idle = fg_env.run(WARD, "idle", seed=1)
    busy = fg_env.run(WARD, _busy, seed=1)
    assert busy.outputs["arrivals"] == idle.outputs["arrivals"]
    assert fg_env.run(WARD, _busy, seed=2).outputs["arrivals"] != idle.outputs["arrivals"]  # the seed still matters


def test_an_added_event_with_a_chance_roll_changes_no_other_draw():
    extra = copy.deepcopy(WARD)
    extra["world"]["coin"] = {"type": "list", "default": []}
    extra["events"].append({"name": "coin", "phase": "start", "do": ["$world.coin += [$chance(0.5)]"]})
    assert fg_env.run(extra, _busy, seed=3).outputs["arrivals"] == fg_env.run(WARD, _busy, seed=3).outputs["arrivals"]


GAMBLE = {
    "name": "Gamble",
    "clock": {"rounds": 6},
    "types": {"player": {"agent": True, "props": {"wins": 0}}},
    "entities": {"a": {"type": "player"}},
    "actions": {"gamble": {"by": "player", "do": ["$r = $random()", {"if": "$r < 0.8", "then": [{"fail": "You lost."}]},
                                                 "$actor.wins += 1"]}},
    "stages": [{"name": "play", "max_calls": 20}],
    "outputs": {"wins": "$entity(a).wins"},
}


def test_retrying_a_refused_random_action_in_the_same_turn_rolls_the_same_luck():
    turns = []

    def retry(wake):
        turns.append([wake.call("gamble", {}).ok for _ in range(20)])

    fg_env.run(GAMBLE, retry, seed=3)
    for calls in turns:
        lost = calls.index(False)  # 0.8 to lose: twenty straight wins would be a bug too
        assert not any(calls[lost:])  # once refused, the same roll comes up however often it is retried
    assert any(calls.index(False) > 0 for calls in turns)  # a success is kept, and the next call rolls afresh


def test_a_snapshot_resumes_with_the_same_luck():
    straight = fg_env.load(WARD, seed=5).run(_busy).to_dict()
    env = fg_env.load(WARD, seed=5)
    env.run(_busy, rounds=3)
    resumed = fg_env.Env.restore(WARD, json.loads(json.dumps(env.snapshot())))
    assert resumed.run(_busy).to_dict() == straight
