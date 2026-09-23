"""Delivery latency and lossy channels: delay and drop on post, emit and wake."""
import copy
import json

import fg_env
from fg_env.expr import Untrusted

RADIO = {
    "name": "Radio",
    "clock": {"rounds": 5},
    "types": {"station": {"agent": True, "props": {"cash": 10}}},
    "entities": {"ana": {"type": "station", "name": "Ana"}, "ben": {"type": "station", "name": "Ben"}},
    "records": {"air": {"fields": {"text": "text", "cash": "number"}}},
    "actions": {
        "say": {"by": "station", "params": {"text": "text"},
                "do": [{"post": "air", "text": "$params.text", "cash": "$actor.cash", "delay": 2}, "$actor.cash += 5"]},
        "say_then_fail": {"by": "station", "do": [{"post": "air", "text": "lost", "delay": 1}, {"fail": "static"}]},
        "flare": {"by": "station", "do": [{"emit": "flare", "say": "{$actor.name} fired a flare.", "delay": 1, "to": "ben"}]},
        "shout": {"by": "station", "do": [{"post": "air", "text": "hey", "drop": "$world.loss"}]},
        "ping": {"by": "station", "do": [{"wake": "ben", "why": "ping", "drop": 1}]},
        "wait": {"by": "station", "do": []},
    },
    "world": {"loss": 0.5},
    "stages": [{"name": "air", "max_actions": 3}],
    "outputs": {"heard": "$count($records(air))"},
}


def _play(calls):
    def play(wake):
        for name, args in calls.get((wake.round, wake.entity_id), []):
            wake.call(name, args)
        wake.end()

    return play


def test_a_delayed_post_arrives_later_with_what_it_said_when_sent():
    env = fg_env.load(RADIO, seed=1)
    env.run(_play({(1, "ana"): [("say", {"text": "hello $actor.cash {$world.loss}"})]}), rounds=2)
    assert env.world.records("air") == []
    env.run(_play({}), rounds=1)
    [entry] = env.world.records("air")
    assert entry["round"] == 3 and entry["author"] == "ana" and entry["cash"] == 10  # the cash when sent
    assert isinstance(entry["text"], Untrusted) and entry["text"] == "hello $actor.cash {$world.loss}"  # never evaluated


def test_a_refused_action_sends_nothing():
    env = fg_env.load(RADIO, seed=1)
    result = env.run(_play({(1, "ana"): [("say_then_fail", {})]}))
    assert result.outputs["heard"] == 0 and env.world.scheduled == []


def test_a_delayed_emit_reaches_its_audience_at_its_moment_on_a_continuous_clock():
    contract = copy.deepcopy(RADIO)
    contract["clock"] = {"mode": "continuous", "horizon": 10}
    contract["actions"]["flare"]["do"][0]["delay"] = 2.5
    contract["stages"] = [{"name": "air", "turns": "scheduled", "interval": 4, "max_actions": 3}]
    env = fg_env.load(contract, seed=1)
    result = env.run(_play({(1, "ana"): [("flare", {})]}))
    [flare] = [event for event in result.events if event["kind"] == "flare"]
    assert flare["time"] == 2.5 and flare["to"] == ["ben"] and flare["text"] == "Ana fired a flare."


def test_drop_loses_messages_by_the_seed_and_can_lose_wakes():
    calls = {(r, "ana"): [("shout", {}), ("shout", {}), ("shout", {})] for r in range(1, 6)}
    heard = [fg_env.load(RADIO, seed=s).run(_play(calls)).outputs["heard"] for s in (1, 1)]
    assert heard[0] == heard[1] and 0 < heard[0] < 15
    never = copy.deepcopy(RADIO)
    never["world"]["loss"] = 1
    assert fg_env.load(never, seed=1).run(_play(calls)).outputs["heard"] == 0
    always = copy.deepcopy(RADIO)
    always["world"]["loss"] = 0
    assert fg_env.load(always, seed=1).run(_play(calls)).outputs["heard"] == 15
    env = fg_env.load(RADIO, seed=1)
    env.run(_play({(1, "ana"): [("ping", {})]}), rounds=1)
    assert env.world.wake_requests == {}


def test_a_run_split_by_a_snapshot_delivers_pending_messages_exactly_once():
    calls = {(1, "ana"): [("say", {"text": "one"})], (2, "ben"): [("say", {"text": "two"})]}
    straight = fg_env.load(RADIO, seed=3).run(_play(calls)).to_dict()
    env = fg_env.load(RADIO, seed=3)
    env.run(_play(calls), rounds=2)
    snapshot = json.loads(json.dumps(env.snapshot()))
    restored = fg_env.Env.restore(RADIO, snapshot)
    assert len(restored.world.scheduled) == 2
    result = restored.run(_play(calls))
    assert result.to_dict() == straight and result.outputs["heard"] == 2
    assert all(isinstance(entry["text"], Untrusted) for entry in restored.world.records("air"))


def test_the_checker_names_bad_delivery_options():
    contract = copy.deepcopy(RADIO)
    contract["records"]["air"]["fields"]["delay"] = "number"
    contract["actions"]["wait"]["do"] = [{"post": "air", "text": "x", "drop": 2, "delay": 1.5},
                                         {"wake": "ben", "delay": 1}]
    found = [(i.path, i.message) for i in fg_env.check(contract) if i.severity == "error"]
    for issue in [
        ("records.air.fields.delay", "'delay' is a `post` option, so a post cannot set it"),
        ("actions.wait.do[0].drop", "is 2; a drop chance runs from 0 to 1"),
        ("actions.wait.do[0].delay", "is 1.5; a delay is a whole number of rounds ≥ 0"),
        ("actions.wait.do[1].delay", "`wake` takes `in` (continuous clock) instead of `delay`"),
    ]:
        assert issue in found, (issue, found)
    assert [i for i in fg_env.check(RADIO) if i.severity == "error"] == []
