"""A private record stays private through event expressions and truncated news."""
import json

import pytest

import fg_env
from fg_env.expr import evaluate


def contract(public=30):
    return {"name": "Private decisions", "clock": {"rounds": 1},
            "types": {"person": {"agent": True}},
            "entities": {"a": {"type": "person"}, "b": {"type": "person"}},
            "world": {"shared": False},
            "stages": [{"name": "decisions", "order": "$it.id"}],
            "records": {"decisions": {"fields": {"text": "text"}, "show": "{text}",
                                      "visible": "$world.shared or $viewer.id == $it.author"}},
            "actions": {"post": {"by": "person", "do": [
                {"post": "decisions", "text": "PRIVATE-DECISION"},
                *[{"emit": "public", "say": f"Public {i}"} for i in range(public)]]}},
            "views": {"events": {"show": "Record events: {$len($events(record))}"}}}


def run(c=None):
    env = fg_env.load(c or contract(), seed=1)
    updates = {}

    def participant(wake):
        updates[wake.entity_id] = wake.update
        if wake.entity_id == "a":
            assert wake.call("post", {}).ok
        wake.end()

    result = env.run(participant)
    assert result.status == "completed", result.error
    return env, updates


def test_normal_update_does_not_reveal_private_payload_or_omitted_count():
    env, updates = run()
    outsider = updates["b"]
    assert "PRIVATE-DECISION" not in outsider
    assert "earlier items" not in outsider
    assert "Record events: 0" in outsider
    assert outsider.count("- Public") == 30
    assert len(env.world.events("record")) == 1  # omniscient analysis retains the full log


def test_event_expressions_follow_the_viewer_and_logic_reads_them_all():
    env, _ = run()
    for who, expected in [("a", 1), ("b", 0)]:
        scope = env.world.evaluation.scope(viewer=env.world.entities[who])
        assert len(evaluate("$events(record)", scope)) == expected
        records = [e for e in evaluate("$events()", scope) if e.kind == "record"]
        assert len(records) == expected
        assert len(evaluate("$events(record)", env.world.evaluation.scope(actor=env.world.entities[who]))) == 1
    assert len(evaluate("$events(record)", env.world.evaluation.scope())) == 1
    scope = env.world.evaluation.scope(actor=env.world.entities["a"], viewer=env.world.entities["b"])
    assert evaluate("$events(record)", scope) == []  # rendering for b hides a's private entry


@pytest.mark.parametrize("limit", [0, 1, 30, None])
def test_omitted_counts_use_the_same_record_visibility_as_rendered_news(limit):
    env, _ = run()
    actor = env.world.entities["b"]
    lines, hidden = env.information.news(actor, 0, limit)
    visible_count = 31 if limit is None else min(limit, 31)  # 30 public events plus the run-end notice
    assert len(lines) == visible_count
    assert hidden == 31 - visible_count
    env.world.set_world("shared", True)
    lines, hidden = env.information.news(actor, 0, limit)
    visible_count = 32 if limit is None else min(limit, 32)
    assert len(lines) == visible_count
    assert hidden == 32 - visible_count


def test_event_visibility_updates_after_access_changes_and_snapshot_restore():
    c = contract()
    env, _ = run(c)
    env.world.set_world("shared", True)
    assert len(env.world.events("record", env.world.entities["b"])) == 1
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    assert len(restored.world.events("record", restored.world.entities["b"])) == 1
    restored.world.set_world("shared", False)
    assert restored.world.events("record", restored.world.entities["b"]) == []
    assert restored.information.news(restored.world.entities["b"], 0, 30)[1] == 1


def test_retired_entries_do_not_reappear_through_notification_payloads():
    c = contract(0)
    c["records"]["decisions"]["keep"] = 1
    c["world"]["shared"] = True
    c["actions"]["post"]["do"].append({"post": "decisions", "text": "LATEST"})
    env, _ = run(c)
    events = env.world.events("record", env.world.entities["b"])
    assert len(events) == 1
    assert events[0].data["fields"]["text"] == "LATEST"
    assert len(env.world.events("record")) == 2


def test_explicit_recipient_restriction_still_applies_when_record_is_public():
    c = contract(0)
    c["records"]["decisions"]["visible"] = "all"
    c["actions"]["post"]["do"][0]["to"] = ["a"]
    env, updates = run(c)
    assert env.world.events("record", env.world.entities["b"]) == []
    assert "PRIVATE-DECISION" not in updates["b"]
    assert len(env.world.events("record", env.world.entities["a"])) == 1


def test_visibility_can_read_non_record_events_without_reentering_record_permissions():
    c = contract(0)
    c["records"]["decisions"]["visible"] = "$len($events(permission)) > 0"
    c["actions"]["post"]["do"].append({"emit": "permission", "say": "Released"})
    env, updates = run(c)
    assert "PRIVATE-DECISION" in updates["b"]
    assert len(env.world.events("record", env.world.entities["b"])) == 1


def test_circular_event_visibility_fails_at_the_authored_rule_instead_of_leaking():
    c = contract(0)
    c["records"]["decisions"]["visible"] = "$len($events()) > 0"
    env = fg_env.load(c, seed=1)

    def participant(wake):
        _ = wake.update
        if wake.entity_id == "a":
            assert wake.call("post", {}).ok
        wake.end()

    result = env.run(participant)
    assert result.status == "failed"
    assert "records.decisions.visible" in result.error
    assert "evaluation nested too deeply" in result.error


def whisper_contract(notify=True):
    return {"name": "Whispers", "clock": {"rounds": 1},
            "types": {"p": {"agent": True}},
            "entities": {"a": {"type": "p"}, "b": {"type": "p"}, "c": {"type": "p"}},
            "records": {"dm": {"fields": {"text": "text"}, "show": "{author}: {text}",
                               "visible": "$viewer.id in ($it.to or [])", "notify": notify}},
            "actions": {"whisper": {"by": "p", "params": {"t": {"type": "entity", "of": "p"}, "text": "text"},
                                    "do": {"post": "dm", "text": "$params.text", "to": "$params.t"}}},
            "views": {"log": {"of": "$events(action)", "show": "{actor} {$it.action} {$it.params}", "empty": "-"}}}


@pytest.mark.parametrize("notify", [True, False])
def test_an_action_that_posted_a_private_entry_is_seen_only_by_who_may_see_the_entry(notify):
    updates = {}

    def participant(wake):
        if wake.entity_id == "a":
            assert wake.call("whisper", {"t": "c", "text": "meet at noon"}).ok
        updates[wake.entity_id] = wake.update
        wake.end()

    env = fg_env.load(whisper_contract(notify), seed=1)
    assert env.run(participant).status == "completed"
    world = env.world
    assert "whisper" not in updates["b"]
    assert [e.data["action"] for e in world.events("action", world.entities["b"])] == []
    for reader in ("a", "c"):  # the author and the recipient see the entry, so they see the action that posted it
        assert [e.data["action"] for e in world.events("action", world.entities[reader])] == ["whisper"]
    assert len(world.events("action")) == 1  # the full log keeps it


def test_keeping_few_entries_of_a_record_sent_to_agents_is_a_check_warning():
    """`keep` drops the oldest entries as new ones come, addressed ones too: a message to b dropped before b's turn
    never reaches it (audit 11 M3), so check says so."""
    contract = {"name": "Kept DMs", "clock": {"rounds": 1},
                "types": {"p": {"agent": True}},
                "entities": {"a": {"type": "p"}, "b": {"type": "p"}, "c": {"type": "p"}},
                "records": {"dm": {"fields": {"text": "text"}, "keep": 1}},
                "actions": {"dm": {"by": "p", "params": {"to": {"type": "entity", "of": "p"},
                                                         "text": {"type": "text", "max_len": 20}},
                                   "do": [{"post": "dm", "text": "$params.text", "to": ["$params.to"]}]}},
                "stages": [{"name": "s", "max_actions": 2}]}
    found = [i for i in fg_env.check(contract, rounds=0) if i.path == "records.dm.keep"]
    assert [i.severity for i in found] == ["warning"] and "never reaches it" in found[0].message
    del contract["records"]["dm"]["keep"]
    assert not [i for i in fg_env.check(contract, rounds=0) if i.path == "records.dm.keep"]


def test_an_event_a_reader_reads_is_numbered_in_its_own_view_as_an_entry_is():
    """`$events` numbers what a reader reads by its place among what it read, as `$records` does, so the numbers never
    count the events it may not know of: whispers between others leave a third agent's numbers as they were (audit 14
    H1). Game logic reads the world's numbering, and `$seen` knows an event by the world's number."""
    def numbers(hidden):
        c = {"name": "Numbers", "types": {"p": {"agent": True}},
             "entities": {"a": {"type": "p"}, "b": {"type": "p"}, "c": {"type": "p"}},
             "records": {"dm": {"fields": {"text": "text"},
                                "visible": "$viewer.id in ($it.to or []) or $viewer.id == $it.author"}},
             "stages": [{"name": "s", "max_actions": 5}], "clock": {"rounds": 1},
             "actions": {"whisper": {"by": "p", "announce": False,
                                     "params": {"to": {"type": "entity", "of": "p"}, "text": {"type": "text"}},
                                     "do": {"post": "dm", "to": "$params.to", "text": "$params.text"}}}}
        env = fg_env.load(c, seed=1)

        def play(wake):
            if wake.entity_id == "a":
                for _ in range(hidden):
                    wake.call("whisper", {"to": "b", "text": "psst"})
                wake.call("whisper", {"to": "c", "text": "hi"})
            wake.end()

        env.step(play)
        world = env.world
        seen = evaluate("$map($events(), $it.seq)", world.evaluation.scope(viewer=world.entities["c"]))
        kinds = evaluate("$map($events(record), $it.seq)", world.evaluation.scope(viewer=world.entities["c"]))
        logic = evaluate("$map($events(), $it.seq)", world.evaluation.scope())
        return seen, kinds, logic

    quiet, busy = numbers(0), numbers(3)
    assert quiet[:2] == busy[:2] == ([1, 2], [1])
    assert quiet[2] != busy[2]  # game logic counts every event


def _keep_contract(keep=3):
    return {"name": "Kept", "types": {"p": {"agent": True}},
            "entities": {"a": {"type": "p"}, "b": {"type": "p"}, "c": {"type": "p"}},
            "records": {"dm": {"fields": {"text": "text"}, "keep": keep,
                               "visible": "$it.to == null or $viewer.id in $it.to or $viewer.id == $it.author"}},
            "stages": [{"name": "s", "max_actions": 20}], "clock": {"rounds": 3},
            "actions": {"say": {"by": "p", "announce": False,
                                "params": {"to": {"type": "entity", "of": "p", "required": False},
                                           "text": {"type": "text"}},
                                "do": {"post": "dm", "to": "$params.to", "text": "$params.text"}}}}


def test_keep_counts_for_each_reader_only_the_entries_it_sees():
    """With `keep`, each reader keeps its own latest entries: whispers between others never push a reader's entries
    out of its view, so what it sees does not tell it how many it could not (audit 14 M4). The store keeps an entry
    while some reader's window holds it, and an undo brings back what a post dropped."""
    def views(hidden):
        env = fg_env.load(_keep_contract(), seed=1)

        def play(wake):
            if wake.entity_id == "a":
                wake.call("say", {"text": f"public {wake.round}"})
                for _ in range(hidden):
                    wake.call("say", {"to": "b", "text": "secret"})
            wake.end()

        env.run(play)
        world = env.world
        seen = {who: [r["text"] for r in world.visible_records("dm", world.entities[who])] for who in "abc"}
        return seen, len(world.records_store["dm"]), env

    quiet, busy = views(0), views(4)
    assert quiet[0]["c"] == busy[0]["c"] == ["public 1", "public 2", "public 3"]
    assert busy[0]["b"] == ["secret", "secret", "secret"]
    assert busy[1] <= 3 * 3 + 3  # bounded: at most each reader's window and the record's latest
    world = busy[2].world
    before = [r["seq"] for r in world.records_store["dm"]]
    mark = world.mark()
    for _ in range(4):
        world.post("dm", {"text": "late"}, "b", ("a",), "probe")
    world.rollback(mark)
    assert [r["seq"] for r in world.records_store["dm"]] == before
    assert [r["text"] for r in world.visible_records("dm", world.entities["c"])] == ["public 1", "public 2", "public 3"]
