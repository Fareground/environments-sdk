"""Game logic reads the true state of records and events; only what agents are shown or offered is filtered.

The auditor below cannot see the bidders' private note, yet its `audit` action, the round's event, a trigger,
metrics and outputs must all count it — while its views, tools, outcome text and policy stay blind. What selects a
stage is seen by every agent, so a stage condition may not read it (audit 13 M1).
"""
import json

import fg_env


def contract(**overrides):
    c = {
        "name": "Hidden notes", "clock": {"rounds": 1},
        "world": {"in_action": -1, "events_in_action": -1, "visible_in_action": -1, "in_event": -1,
                  "triggered": False, "entered": False},
        "types": {"bidder": {"agent": True}, "auditor": {"agent": True}},
        "entities": {"a": {"type": "bidder"}, "b": {"type": "bidder"}, "k": {"type": "auditor"}},
        "records": {"notes": {"fields": {"text": "text"}, "show": "{text}",
                              "visible": "$viewer.id == $it.author or $viewer.id in $it.to"}},
        "actions": {
            "note": {"by": "bidder", "private": True, "do": [{"post": "notes", "text": "RIG-IT", "to": "b"}]},
            "audit": {"by": "auditor",
                      "when": [{"expr": "$len($records(notes)) > 0", "why": "nothing to audit"}],
                      "params": {"pick": {"type": "enum", "values": "$map($records(notes), $it.text) + ['none']"}},
                      "do": ["$world.in_action = $len($records(notes))",
                             "$world.events_in_action = $len($events(record))",
                             "$world.visible_in_action = $len($records(notes, $it.author == $actor or $actor.id in "
                             "($it.to or [])))"],
                      "outcome": "You can see {$len($records(notes))} note(s)."},
        },
        "stages": [
            {"name": "talk", "who": "$it.id == a", "actions": ["note"]},
            {"name": "audit", "who": "$it.type == auditor", "actions": ["audit"],
             "on_enter": ["$world.entered = true"]},
        ],
        "events": [{"name": "tally", "phase": "end", "do": ["$world.in_event = $len($records(notes))"]}],
        "triggers": [{"name": "noticed", "when": "$len($records(notes)) > 0", "do": ["$world.triggered = true"]}],
        "views": {"notes_seen": {"for": "auditor", "title": "Notes", "of": "$records(notes)", "show": "{text}",
                                 "empty": "No notes."},
                  "count_seen": {"for": "auditor", "show": "Visible notes: {$len($records(notes))}."}},
        "metrics": {"notes": "$len($records(notes))"},
        "outputs": {"notes": "$len($records(notes))", "record_events": "$len($events(record))",
                    "in_action": "$world.in_action", "events_in_action": "$world.events_in_action",
                    "visible_in_action": "$world.visible_in_action", "in_event": "$world.in_event",
                    "triggered": "$world.triggered", "entered": "$world.entered"},
    }
    c.update(overrides)
    return c


def play(c):
    seen = {}

    def participant(wake):
        if wake.entity_id == "a":
            assert wake.call("note", {}).ok
        elif wake.entity_id == "k":
            seen["update"] = wake.update
            seen["tools"] = json.dumps([t.input_schema for t in wake.tools if t.name == "audit"])
            result = wake.call("audit", {"pick": "none"})
            seen["audit"] = (result.ok, result.text)
        wake.end()

    result = fg_env.run(c, participant, seed=1)
    assert result.status == "completed", result.error
    return result.outputs, seen


def test_action_requires_and_effects_read_every_entry():
    outputs, seen = play(contract())
    assert seen["audit"][0], seen["audit"]
    assert outputs["in_action"] == 1
    assert outputs["events_in_action"] == 1


def test_events_triggers_stages_metrics_and_outputs_read_every_entry():
    outputs, _ = play(contract())
    assert outputs["in_event"] == 1
    assert outputs["triggered"] is True
    assert outputs["entered"] is True
    assert outputs["notes"] == 1 and outputs["record_events"] == 1


def test_logic_asks_what_one_agent_may_see_with_where():
    outputs, _ = play(contract())
    assert outputs["visible_in_action"] == 0  # the auditor is neither author nor recipient


def test_views_tools_and_outcome_text_stay_filtered_for_the_reader():
    _, seen = play(contract())
    assert "No notes." in seen["update"] and "RIG-IT" not in seen["update"]
    assert "Visible notes: 0." in seen["update"]
    assert "RIG-IT" not in seen["tools"]  # enum choices are what the agent is offered
    assert seen["audit"][1] == "You can see 0 note(s)."


def test_policies_act_on_what_their_agent_can_see():
    c = contract(policies={"blind": {"rules": [
        {"when": "$len($records(notes)) > 0", "do": "audit", "with": {"pick": "none"}}]}})
    c["world"]["audited"] = False
    c["actions"]["audit"]["do"].append("$world.audited = true")
    c["outputs"]["audited"] = "$world.audited"

    def bidder(wake):
        assert wake.call("note", {}).ok
        wake.end()

    result = fg_env.run(c, {"a": bidder, "b": bidder, "k": "policy:blind"}, seed=1)
    assert result.status == "completed", result.error
    assert result.outputs["audited"] is False  # the policy's auditor cannot see the note



def test_a_stage_held_by_what_not_every_agent_sees_is_a_check_error():
    held = contract()
    held["stages"][1]["when"] = "$len($records(notes)) > 0"
    assert [i.path for i in fg_env.check(held, rounds=0) if i.severity == "error"] == ["stages[1].when"]
