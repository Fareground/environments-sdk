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
                         "do": ["$world.tests += [$uniform(0, 1)]", "$params.patient.state = seen"]}},
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


def test_a_snapshot_resumes_with_the_same_luck():
    straight = fg_env.load(WARD, seed=5).run(_busy).to_dict()
    env = fg_env.load(WARD, seed=5)
    env.run(_busy, rounds=3)
    resumed = fg_env.Env.restore(WARD, json.loads(json.dumps(env.snapshot())))
    assert resumed.run(_busy).to_dict() == straight


DET = {"name": "Det", "clock": {"rounds": 3},
       "types": {"p": {"agent": True, "props": {"n": 0, "luck": 0}}},
       "entities": {k: {"type": "p"} for k in "abcd"},
       "actions": {"roll": {"by": "p", "do": ["$actor.luck += $randint(1, 1000)"]},
                   "spawn": {"by": "p", "do": [{"create": "p"}]}},
       "events": [{"phase": "end", "each": "p", "do": ["$it.n += $randint(1, 1000)"]}],
       "outputs": {"count": "$count(p)"}}


def _others_luck(first_agent):
    env = fg_env.load(DET, seed=11)
    env.run({"a": first_agent, "*": lambda w: w.call("roll", {})})
    return {e["id"]: e["props"] for e in env.entities() if e["id"] in "bcd"}


def test_what_one_agent_draws_or_creates_does_not_shift_the_other_agents_luck():
    idle = _others_luck(lambda w: None)
    assert _others_luck(lambda w: w.call("roll", {})) == idle
    assert _others_luck(lambda w: w.call("spawn", {}) if w.round == 1 else None) == idle


def test_reading_an_update_whose_view_draws_luck_does_not_change_the_run():
    c = {"name": "Views", "clock": {"rounds": 4},
         "types": {"p": {"agent": True, "props": {"cash": 0, "luck": 0}}},
         "entities": {k: {"type": "p"} for k in "abcd"},
         "views": {"leader": {"show": "Leader: {$best(p, $it.cash).name} {$chance(0.5)} {$randint(1, 9)}"}},
         "actions": {"roll": {"by": "p", "do": ["$actor.luck += $randint(1, 1000)"]}},
         "events": [{"phase": "end", "each": "p", "do": ["$it.cash += $randint(1, 3)"]}],
         "stages": [{"name": "s", "order": "random"}], "outputs": {"luck": "$sum(p, $it.luck)"}}

    def run(read):
        result = fg_env.run(c, {"*": lambda w: ((w.update, w.brief, w.tools) if read else None, w.call("roll", {}))},
                            seed=5)
        return result.outputs, result.events  # stats differ: reading costs tokens

    assert run(read=True) == run(read=False)


def test_an_each_loop_draws_each_items_luck_on_its_own_so_removing_an_item_shifts_no_other_draw():
    """ann removes a cell bob never sees; bob's rolls inside and after his loop over the cells stay the same, and so
    does a world draw after a round event's loop."""
    c = {"name": "Loops", "clock": {"rounds": 2}, "world": {"after": []},
         "types": {"player": {"agent": True, "props": {"r": []}}, "cell": {"props": {"v": []}}},
         "entities": {"ann": {"type": "player"}, "bob": {"type": "player"}, "cell": {"type": "cell", "count": 3}},
         "actions": {"kill": {"by": "player", "description": "k", "do": [{"remove": "$entity(cell_1)"}]},
                     "farm": {"by": "player", "description": "f",
                              "do": [{"each": "cell", "do": ["$it.v += [$randint(1, 1000)]"]},
                                     "$actor.r += [$randint(1, 1000)]"]}},
         "events": [{"on": "round.end", "do": [{"each": "cell", "do": ["$it.v += [$randint(1, 1000)]"]},
                                               "$world.after += [$randint(1, 1000)]"]}],
         "outputs": {"bob": {"expr": "$entity(bob).r", "type": "list"}, "after": {"expr": "$world.after", "type": "list"},
                     "cell_3": {"expr": "$entity(cell_3).v", "type": "list"}}}

    def bob(wake):
        wake.call("farm", {})

    idle = fg_env.run(c, {"ann": lambda wake: wake.end(), "bob": bob}, seed=2).outputs
    kill = fg_env.run(c, {"ann": lambda wake: wake.call("kill", {}), "bob": bob}, seed=2).outputs
    assert idle == kill
