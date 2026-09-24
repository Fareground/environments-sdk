"""Indexed record-event candidates agree with a full live-permission scan."""
import json

import pytest

import fg_env
from fg_env.copying.direct import _copy_world


def contract(rule="$viewer.id == $it.author", keep=None):
    notes = {"fields": {"value": "int"}, "visible": rule}
    if keep is not None:
        notes["keep"] = keep
    return {"name": "Record event candidates", "clock": {"rounds": 2},
            "world": {"shared": False}, "types": {"reader": {"agent": True}},
            "entities": {n: {"type": "reader"} for n in ("a", "b")},
            "records": {"notes": notes, "public": {"fields": {"value": "int"}}}}


def post(w, author, value, record="notes", to=None):
    return w.post(record, {"value": value}, author, to, "probe")


def read(w, who):
    actor = w.entities[who]
    expected = [e for e in w.log if e.kind == "record" and w.event_visible(e, actor)]
    actual = w.events("record", actor)
    assert actual == expected
    assert w.events("record") == [e for e in w.log if e.kind == "record"]
    return [e.data.get("fields", {}).get("value") for e in actual]


@pytest.mark.parametrize("rule", ["$viewer.id == $it.author", "$it.author == $viewer.id"])
def test_mixed_records_keep_chronological_order_and_recipients(rule):
    w = fg_env.load(contract(rule)).world
    post(w, "a", 1)
    post(w, "b", 2, "public")
    post(w, "b", 3)
    post(w, "a", 4, to=("b",))  # Event recipients still constrain even the author.
    post(w, "a", 5)
    assert read(w, "a") == [1, 2, 5]
    assert read(w, "b") == [2, 3]


def test_retention_and_rollback_with_reused_sequences():
    w = fg_env.load(contract(keep=2)).world
    post(w, "a", 1)
    post(w, "b", 2)
    mark = w.journal.mark()
    post(w, "b", 3)
    post(w, "b", 4)
    assert read(w, "a") == []
    w.journal.rollback(mark)
    post(w, "a", 5)
    assert read(w, "a") == [5]
    assert read(w, "b") == [2]


@pytest.mark.parametrize("rule", ["$world.shared or $viewer.id == $it.author",
                                   "$world.shared and $viewer.id == $it.author"])
def test_general_rules_recheck_current_state(rule):
    w = fg_env.load(contract(rule)).world
    post(w, "a", 1)
    post(w, "b", 2)
    before = [read(w, who) for who in ("a", "b")]
    w.set_world("shared", True)
    assert before != [read(w, who) for who in ("a", "b")]


@pytest.mark.parametrize("keep", [None, 1])
def test_restore_and_fast_copy_rebuild_independent_candidates(keep):
    c = contract(keep=keep)
    env = fg_env.load(c)
    post(env.world, "a", 1)
    post(env.world, "b", 2)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    copied = _copy_world(env.world)
    for w in (restored.world, copied):
        assert w.record_events is not env.world.record_events
        for who in ("a", "b"):
            assert read(w, who) == read(env.world, who)
        post(w, "a", 3)
        assert read(w, "a")[-1] == 3
    assert 3 not in read(env.world, "a")


@pytest.mark.parametrize("before,after", [("all", "$viewer.id == $it.author"),
                                         ("$viewer.id == $it.author", "all")])
def test_fork_uses_destination_permissions(before, after):
    env = fg_env.load(contract(before))
    post(env.world, "a", 1)
    post(env.world, "b", 2)
    forked = env.fork(patch={"records": {"notes": {"visible": after}}})
    assert read(forked.world, "a") == ([1, 2] if after == "all" else [1])
    assert read(env.world, "a") == ([1, 2] if before == "all" else [1])


def test_forward_and_nonstandard_references_are_not_cached_as_invisible():
    w = fg_env.load(contract()).world
    w.emit("record", "", data={"record": "notes", "entry": 1})
    w.emit("record", "", data={"record": "notes", "entry": 1.0})
    assert read(w, "a") == []
    post(w, "a", 1)
    assert read(w, "a") == [None, None, 1]
    assert read(w, "b") == []


def test_notification_author_and_source_record_are_not_assumed_from_event_metadata():
    w = fg_env.load(contract()).world
    row = post(w, "a", 1, "public")
    w.emit("record", "", actor="b", data={"record": "notes", "entry": row["seq"]})
    assert read(w, "a") == [1, None]
    assert read(w, "b") == [1]


def test_unowned_entries_and_unknown_records_preserve_scan_behavior():
    w = fg_env.load(contract()).world
    row = post(w, None, 1)
    w.emit("record", "", data={"record": "missing", "entry": row["seq"]})
    assert read(w, "a") == read(w, "b") == []
