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
        scope = env.world.scope(viewer=env.world.entities[who])
        assert len(evaluate("$events(record)", scope)) == expected
        records = [e for e in evaluate("$events()", scope) if e.kind == "record"]
        assert len(records) == expected
        assert len(evaluate("$events(record)", env.world.scope(actor=env.world.entities[who]))) == 1
    assert len(evaluate("$events(record)", env.world.scope())) == 1
    scope = env.world.scope(actor=env.world.entities["a"], viewer=env.world.entities["b"])
    assert evaluate("$events(record)", scope) == []  # rendering for b hides a's private entry


@pytest.mark.parametrize("limit", [0, 1, 30, None])
def test_omitted_counts_use_the_same_record_visibility_as_rendered_news(limit):
    env, _ = run()
    actor = env.world.entities["b"]
    lines, hidden = env.perception.news(actor, 0, limit)
    visible_count = 31 if limit is None else min(limit, 31)  # 30 public events plus the run-end notice
    assert len(lines) == visible_count
    assert hidden == 31 - visible_count
    env.world.set_world("shared", True)
    lines, hidden = env.perception.news(actor, 0, limit)
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
    assert restored.perception.news(restored.world.entities["b"], 0, 30)[1] == 1


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
