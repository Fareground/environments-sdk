"""`events=False` runs a crowd for as long as it likes in flat memory: the result carries no event log, the run forgets
each event once nothing can read it any more, and everything the run does is exactly what it does with the log kept.
"""
import gc
import itertools

import pytest

import fg_env

#: Chatters act every round; watchers only every fifth, so their news spans five rounds of other agents' actions.
TOWN = {"name": "Town", "clock": {"rounds": 40}, "world": {"said": 0},
        "types": {"chatter": {"agent": True}, "watcher": {"agent": True, "props": {"heard": 0}}},
        "population": [{"type": "chatter", "count": 6}, {"type": "watcher", "count": 3}],
        "stages": [{"name": "talk", "actions": {"chatter": ["talk"]}},
                   {"name": "watch", "when": "$round % 5 == 0", "actions": {"watcher": ["note"]}}],
        "actions": {"talk": {"by": "chatter", "do": "$world.said += 1", "announce": "{$actor.name} talks."},
                    "note": {"by": "watcher", "params": {"lines": {"type": "int", "min": 0, "max": 1000}},
                             "do": "$actor.heard += $params.lines"}},
        "outputs": {"said": "$world.said", "heard": "$sum(watcher, $it.heard)"}}


def reading(transcript):
    """A participant whose choice depends on the news it is given, so news that went missing would change the run."""
    def play(wake):
        update = wake.update
        transcript.append((wake.entity_id, wake.round, update))
        acts = [tool.name for tool in wake.tools if tool.kind == "act"]
        if "note" in acts:
            wake.call("note", {"lines": update.count("talks.")})
        elif acts:
            wake.call(acts[0], {})
        wake.end()
    return play


def test_a_run_without_its_event_log_plays_exactly_as_one_with_it():
    kept, forgotten = [], []
    full = fg_env.run(TOWN, reading(kept), seed=3)
    lean = fg_env.run(TOWN, reading(forgotten), seed=3, events=False)
    assert lean.events == [] and full.events
    assert forgotten == kept
    assert lean.outputs == full.outputs and full.outputs["heard"] > 0


def test_every_event_is_still_streamed_to_on_event():
    streamed = []
    fg_env.run(TOWN, seed=3, events=False, on_event=streamed.append)
    assert streamed == fg_env.run(TOWN, seed=3).events


def test_a_contract_that_reads_the_log_still_reads_all_of_it():
    counted = {**TOWN, "outputs": {"talks": "$count($events('action'), $it.action == 'talk')"}}
    assert fg_env.run(counted, seed=3, events=False).outputs == {"talks": 6 * 40}


def _retained(rounds, events):
    """Objects a crowd run still holds after ``rounds`` rounds, beyond what it held after its first."""
    contract = {"name": "Crowd", "clock": {"rounds": rounds}, "world": {"pot": 0},
                "types": {"p": {"agent": True, "policy": "give"}}, "population": [{"type": "p", "count": 100}],
                "actions": {"give": {"by": "p", "do": "$world.pot += 1"}},
                "policies": {"give": {"rules": [{"do": "give"}]}}}
    env = fg_env.load(contract, seed=1, events=events)
    env.run(rounds=1)
    gc.collect()
    before = len(gc.get_objects())
    env.run()
    gc.collect()
    return len(gc.get_objects()) - before


@pytest.mark.slow
def test_memory_stays_flat_however_long_the_run():
    short, long = _retained(10, events=False), _retained(40, events=False)
    assert long < short + 5_000, f"40 rounds kept {long:,} more objects, 10 rounds {short:,}"
    assert _retained(40, events=True) > 4 * max(long, 1_000)  # the log kept grows with every turn


def test_snapshot_and_restore_are_exact_without_the_log():
    env = fg_env.load(TOWN, seed=3, events=False)
    env.run(rounds=17)
    restored = fg_env.Env.restore(TOWN, env.snapshot())
    assert restored.run().outputs == env.run().outputs == fg_env.run(TOWN, seed=3).outputs
    assert restored.result().events == []


def test_a_clone_taken_late_in_a_long_run_continues_exactly():
    env = fg_env.load(TOWN, seed=3, events=False)
    env.run(rounds=33)
    copy = env.clone()
    assert copy.run().outputs == env.run().outputs == fg_env.run(TOWN, seed=3).outputs


#: Enough turns that the run moves its replay base forward several times (its tape outgrows the world).
CROWD = {"name": "Crowd", "clock": {"rounds": 30}, "world": {"pot": 0},
         "types": {"p": {"agent": True, "props": {"given": 0}}}, "population": [{"type": "p", "count": 100}],
         "actions": {"give": {"by": "p", "params": {"amount": {"type": "int", "min": 0, "max": 10}},
                              "do": ["$world.pot += $params.amount", "$actor.given += $params.amount"]}},
         "outputs": {"pot": "$world.pot", "most": "$max(p, $it.given)"}}


def test_copies_and_snapshots_taken_part_way_through_a_long_run_continue_exactly():
    expected = fg_env.run(CROWD, "random", seed=5).outputs
    env = fg_env.load(CROWD, seed=5, events=False)
    env.run("random", rounds=24)
    checks = itertools.count()
    env.run("random", stop=lambda e: e.round == 25 and next(checks) > 40)
    assert env.status == "stopped"
    restored = fg_env.Env.restore(CROWD, env.snapshot())
    copy = env.clone()
    assert restored.run("random").outputs == copy.run("random").outputs == env.run("random").outputs == expected


def test_recording_exposures_needs_the_log():
    with pytest.raises(ValueError, match="events=False"):
        fg_env.load(TOWN, seed=3, events=False, exposures=True)
