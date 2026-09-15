"""The exposure log: what each agent actually received on every wake, and `$seen`."""
import copy
import json
import time

import fg_env

TOWN = {
    "name": "Town square",
    "clock": {"rounds": 2},
    "types": {"citizen": {"agent": True, "props": {"said": 0}}},
    "entities": {"ann": {"type": "citizen", "name": "Ann"}, "bo": {"type": "citizen", "name": "Bo"}},
    "records": {"chat": {"fields": {"text": "text"}, "show": "{author}: {text}"}},
    "actions": {"say": {"by": "citizen", "description": "Say something to the square.",
                        "params": {"text": {"type": "text", "max_len": 80}},
                        "do": [{"post": "chat", "text": "$params.text"}, "$actor.said += 1"], "terminal": True}},
    "views": {"square": {"for": "citizen", "title": "Square", "show": "{$count(citizen)} citizens are here."},
              "board": {"for": "citizen", "of": "chat", "look": True, "show": "{author}: {text}",
                        "empty": "Nothing yet."}},
    "stages": [{"name": "talk"}],
}


def reader(wake):
    wake.brief
    wake.update
    wake.tools
    wake.call("look", {"view": "board"})
    wake.call("say", {"text": f"hi from {wake.name}"})


def test_exposures_are_not_recorded_unless_asked():
    assert fg_env.run(TOWN, reader, seed=1).exposures == {}


def test_each_wake_records_what_the_agent_read_offered_and_did():
    result = fg_env.run(TOWN, reader, seed=1, exposures=True)
    log = result.exposures
    wakes, texts = log["wakes"], log["texts"]
    assert json.loads(json.dumps(log)) == log
    assert [(w["wake"], w["entity"], w["round"], w["turn"]) for w in wakes] == \
        [(0, "ann", 1, 1), (1, "bo", 1, 2), (2, "ann", 2, 3), (3, "bo", 2, 4)]
    first = wakes[0]
    assert first["kind"] == "turn" and first["stage"] == "talk" and first["reason"] == "It is your turn."
    assert first["brief"]["hash"] == wakes[2]["brief"]["hash"]  # the same brief, stored once
    assert texts[first["brief"]["hash"]].startswith("# Town square")
    assert first["update"]["tokens"] == first["update"]["chars"] // 4
    assert "3 citizens" not in texts[first["update"]["hash"]] and "2 citizens" in texts[first["update"]["hash"]]
    assert [(v["name"], v.get("look", False)) for v in first["views"]] == [("square", False), ("board", True)]
    assert texts[first["views"][1]["hash"]] == "Board:\nNothing yet."
    assert first["tools"] == ["say", "look", "inspect", "end_turn"]
    assert [json.loads(texts[h])[0]["name"] for h in first["tool_sets"]] == ["say"]
    look, say = first["calls"]
    assert look == {"tool": "look", "args": {"view": "board"}, "ok": True, "ended": False, "result": look["result"],
                    "offered": first["tool_sets"][0]}
    assert say["args"] == {"text": "hi from Ann"} and say["ok"] and say["ended"]
    assert texts[say["result"]] == "Done: say (text=«hi from Ann»)."
    assert first["invalid"] == 0 and first["timed_out"] is False and first["undone"] == 0


def test_news_and_listed_entries_are_recorded_by_sequence_number():
    result = fg_env.run(TOWN, reader, seed=1, exposures=True)
    posted = [e for e in result.events if e["kind"] == "record"]
    ann_post = posted[0]
    bo_first = result.exposures["wakes"][1]
    assert ann_post["seq"] in bo_first["news"]
    assert ann_post["data"]["entry"] in bo_first["entries"]
    ann_second = result.exposures["wakes"][2]
    assert posted[1]["seq"] in ann_second["news"]  # Bo's post, delivered to Ann next round


def test_a_participant_that_never_reads_was_shown_nothing():
    def silent(wake):
        wake.call("say", {"text": "x"})

    wake = fg_env.run(TOWN, silent, seed=1, exposures=True).exposures["wakes"][0]
    assert wake["brief"] is None and wake["update"] is None
    assert wake["views"] == [] and wake["news"] == [] and wake["tools"] == []
    assert [c["tool"] for c in wake["calls"]] == ["say"]


SEEN = copy.deepcopy(TOWN)
SEEN["actions"]["say"]["when"] = [{"expr": "$seen($actor, 'board')", "why": "Look at the board first."}]
SEEN["outputs"] = {"bo_saw_ann": {"expr": "$seen(bo, $records(chat)[0])", "type": "bool"},
                   "ann_saw_bo": {"expr": "$seen(ann, $records(chat)[1])", "type": "bool"}}
SEEN["clock"]["rounds"] = 1


def test_seen_lets_rules_ask_what_an_agent_was_shown():
    attempts = []

    def ann(wake):
        wake.call("look", {"view": "board"})
        wake.call("say", {"text": "the fountain works"})

    def bo(wake):
        wake.update  # Ann's message arrives as news
        attempts.append(wake.call("say", {"text": "too soon"}))
        wake.call("look", {"view": "board"})
        attempts.append(wake.call("say", {"text": "agreed"}))

    env = fg_env.load(SEEN, seed=1)
    assert env.world.exposures is not None  # the contract asks $seen, so the run records exposures
    result = env.run({"ann": ann, "bo": bo})
    assert result.ok, result.summary()
    assert not attempts[0].ok and "Look at the board first" in attempts[0].text
    assert attempts[1].ok
    assert result.outputs == {"bo_saw_ann": True, "ann_saw_bo": False}


def test_seen_says_what_it_can_ask_about():
    contract = copy.deepcopy(SEEN)
    contract["outputs"] = {"odd": {"expr": "$seen(ann, 3)", "type": "bool"}}
    result = fg_env.run(contract, "idle", seed=1)
    assert "expected an event, a record entry or a view name" in result.output_issues[0]["message"]


def test_a_run_split_by_snapshot_and_restore_keeps_the_same_log_and_answers():
    for contract, participants in ((TOWN, reader), (SEEN, reader)):
        straight = fg_env.load(contract, seed=3, exposures=True).run(participants, rounds=2)
        env = fg_env.load(contract, seed=3, exposures=True)
        env.run(participants, rounds=1)
        if not env.finished:
            env = fg_env.Env.restore(contract, json.loads(json.dumps(env.snapshot())))
            env.run(participants, rounds=1)
        assert env.result().to_dict() == straight.to_dict()


def test_concurrent_simultaneous_turns_record_the_same_log_every_run():
    sealed = copy.deepcopy(TOWN)
    sealed["stages"] = [{"name": "talk", "turns": "simultaneous"}]
    sealed["entities"].update({f"c{i}": {"type": "citizen", "name": f"C{i}"} for i in range(6)})

    def thinks(wake):
        time.sleep(0.001 * (sum(map(ord, wake.entity_id)) % 5))
        reader(wake)

    runs = [fg_env.load(sealed, seed=2, exposures=True).run(thinks) for _ in range(2)]
    assert runs[0].exposures == runs[1].exposures
    assert len(runs[0].exposures["wakes"]) == 16
