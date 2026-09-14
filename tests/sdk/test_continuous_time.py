"""Continuous, event-driven time: scheduled turns, durations, timed effects, horizons."""
import json

import fg_env

CLINIC = {
    "name": "Clinic",
    "clock": {"mode": "continuous", "unit": "minute", "horizon": 60},
    "world": {"treated": 0, "alarm_at": {"type": "number", "default": -1}},
    "types": {"doctor": {"agent": True, "props": {"speed": 15, "seen": 0}}},
    "entities": {"ann": {"type": "doctor", "name": "Ann"},
                 "bo": {"type": "doctor", "name": "Bo", "props": {"speed": 20}}},
    "physics": {"vars": {"elapsed": {"start": 0, "rate": "1"}}},
    "actions": {"treat": {"by": "doctor", "duration": "$actor.speed",
                          "do": ["$actor.seen += 1", "$world.treated += 1"], "terminal": True}},
    "events": [{"at": 1, "do": [{"after": 7.5, "do": ["$world.alarm_at = $clock.time"]}]}],
    "stages": [{"name": "work", "turns": "scheduled"}],
    "outputs": {"treated": "$world.treated", "elapsed": "$physics.elapsed"},
}


def _treat(wake):
    wake.call("treat")
    wake.end()


def test_scheduled_turns_follow_durations_and_jump_between_due_moments():
    env = fg_env.load(CLINIC, seed=1)
    labels = []

    def watch(wake):
        labels.append((wake.entity_id, wake.update.splitlines()[0]))
        _treat(wake)

    result = env.run(watch)
    assert result.status == "completed" and result.ended_by == "horizon"
    assert env.entity("ann")["props"]["seen"] == 5   # 0, 15, 30, 45, 60
    assert env.entity("bo")["props"]["seen"] == 4    # 0, 20, 40, 60
    assert env.props["alarm_at"] == 7.5              # a timed effect gets its own moment
    assert result.time == 60 and result.outputs["elapsed"] == 60
    moments = sorted({e["time"] for e in result.events if "time" in e})
    assert moments == [0, 15, 20, 30, 40, 45, 60]  # plus 7.5, a moment where only the timed effect ran
    assert result.rounds == 8
    assert ("bo", "Minute 20 of 60 · work") in labels


def test_idle_agents_wake_after_the_stage_interval_and_wake_in_reschedules():
    contract = json.loads(json.dumps(CLINIC))
    contract["stages"][0]["interval"] = 25
    contract["actions"]["nap"] = {"by": "doctor", "do": [{"wake": "$actor", "in": 50}], "terminal": True}
    turns = []

    def play(wake):
        turns.append((wake.entity_id, round(wake._turn.env.world.time, 3)))
        if wake.entity_id == "ann" and not any(t[0] == "ann" for t in turns[:-1]):
            wake.call("nap")
        wake.end()

    fg_env.load(contract, seed=1).run(play)
    assert [t for t in turns if t[0] == "ann"] == [("ann", 0.0), ("ann", 50.0)]
    assert [t for t in turns if t[0] == "bo"] == [("bo", 0.0), ("bo", 25.0), ("bo", 50.0)]


def test_continuous_runs_resume_exactly_from_snapshots_and_stops():
    straight = fg_env.load(CLINIC, seed=4).run(_treat).to_dict()
    env = fg_env.load(CLINIC, seed=4)
    env.run(_treat, rounds=3)
    restored = fg_env.Env.restore(CLINIC, json.loads(json.dumps(env.snapshot())))
    assert restored.run(_treat).to_dict() == straight
    points = {"n": 0}

    def stop(_env):
        points["n"] += 1
        return points["n"] == 5

    env = fg_env.load(CLINIC, seed=4)
    env.run(_treat, stop=stop)
    assert env.run(_treat).to_dict() == straight


def test_continuous_clock_contract_errors():
    no_horizon = {**CLINIC, "clock": {"mode": "continuous"}}
    assert any("needs a `horizon`" in i.message for i in fg_env.check(no_horizon))
    rounds_mode = {**CLINIC, "clock": {"rounds": 3}}
    messages = [i.message for i in fg_env.check(rounds_mode)]
    assert any("scheduled turns need a continuous clock" in m for m in messages)
    assert any("duration only applies" in m for m in messages)
