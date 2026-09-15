"""Author-only record selection remains ordered and transactional."""
import json

import pytest

import fg_env
from fg_env.sdk.record_index import author_only


def contract(rule="$viewer.id == $it.author", keep=None):
    record = {"fields": {"value": "int"}, "visible": rule}
    if keep is not None:
        record["keep"] = keep
    return {"name": "Indexed records", "clock": {"rounds": 2}, "world": {"shared": False},
            "types": {"reader": {"agent": True}},
            "entities": {who: {"type": "reader"} for who in ("a", "b", "c")},
            "records": {"notes": record},
            "views": {"notes": {"of": "notes", "show": "{value}"}}}


def post(env, who, value, to=None):
    return env.world.post("notes", {"value": value}, who, to, "probe")


def values(env, who):
    viewer = env.world.entities[who]
    rows = env.world.visible_records("notes", viewer)
    expected = [r for r in env.world.records("notes") if env.world.entry_visible("notes", r, viewer)]
    assert rows == expected
    return [row["value"] for row in rows]


@pytest.mark.parametrize("rule", ["$viewer.id == $it.author", "$it.author == $viewer.id",
                                   " $viewer.id\t==  $it.author "])
def test_each_author_sees_only_its_rows_in_order(rule):
    env = fg_env.load(contract(rule))
    for i, who in enumerate(("a", "b", "a", "c", "b", "a")):
        post(env, who, i)
    assert values(env, "a") == [0, 2, 5]
    assert values(env, "b") == [1, 4]
    assert values(env, "c") == [3]
    assert [r["value"] for r in env.world.visible_records("notes", None)] == list(range(6))


def test_retention_prunes_each_author_without_reordering_remaining_rows():
    env = fg_env.load(contract(keep=3))
    for i, who in enumerate(("a", "b", "a", "c", "b", "a")):
        post(env, who, i)
    assert values(env, "a") == [5]
    assert values(env, "b") == [4]
    assert values(env, "c") == [3]


def test_rollback_restores_pruned_rows_and_accepts_reused_sequence_numbers():
    env = fg_env.load(contract(keep=3))
    for i, who in enumerate(("a", "b", "a")):
        post(env, who, i)
    mark = env.world.journal.mark()
    for i in range(3, 7):
        post(env, "b", i)
    assert values(env, "a") == []
    env.world.journal.rollback(mark)
    assert values(env, "a") == [0, 2]
    assert values(env, "b") == [1]
    post(env, "c", 10)
    assert values(env, "a") == [2]
    assert values(env, "b") == [1]
    assert values(env, "c") == [10]


def test_snapshot_rebuilds_the_derived_index_and_continues_retention():
    c = contract(keep=3)
    env = fg_env.load(c, seed=4)
    for i, who in enumerate(("a", "b", "a")):
        post(env, who, i)
    restored = fg_env.Env.restore(c, json.loads(json.dumps(env.snapshot())))
    for who in ("a", "b", "c"):
        assert values(restored, who) == values(env, who)
    for e in (env, restored):
        post(e, "b", 3)
    assert values(restored, "a") == values(env, "a") == [2]
    assert values(restored, "b") == values(env, "b") == [1, 3]


@pytest.mark.parametrize("rule", ["$world.shared or $viewer.id == $it.author",
                                   "$viewer.id == $it.author and $world.shared"])
def test_general_conditions_keep_live_visibility_evaluation(rule):
    assert not author_only(rule)
    env = fg_env.load(contract(rule))
    post(env, "a", 1)
    initial = {who: values(env, who) for who in ("a", "b")}
    env.world.set_world("shared", True)
    changed = {who: values(env, who) for who in ("a", "b")}
    assert changed != initial


def test_direct_record_view_uses_the_same_private_rows():
    env = fg_env.load(contract())
    post(env, "a", 111)
    post(env, "b", 222)
    view = env.contract.views["notes"]
    text = env.perception.render_view("notes", view, env.world.entities["a"])
    assert "111" in text and "222" not in text


def test_missing_authors_and_explicit_recipients_preserve_full_scan_semantics():
    env = fg_env.load(contract())
    post(env, None, 0)
    post(env, "missing", 1)
    post(env, "a", 2, to=("b",))
    assert values(env, "a") == [2]  # record authors retain access to their own records
    assert values(env, "b") == []
    assert values(env, "c") == []


def test_indexed_reads_charge_accessible_work_and_still_bound_large_results():
    from fg_env.sdk.expr import evaluate
    from fg_env.sdk.expr_base import shared_budget
    from fg_env.sdk.errors import RunError

    env = fg_env.load(contract())
    for i in range(50):
        post(env, "b", i)
    post(env, "a", 100)
    scope = env.world.scope(viewer=env.world.entities["a"])
    with shared_budget(10, "private read"):
        assert [r["value"] for r in evaluate("$records(notes)", scope)] == [100]
    for i in range(20):
        post(env, "a", 101 + i)
    with shared_budget(10, "private read"):
        with pytest.raises(RunError, match="work budget"):
            evaluate("$records(notes)", scope)


@pytest.mark.parametrize("before, after, original, changed", [
    ("$viewer.id == $it.author", "all", [1], [1, 2]),
    ("all", "$viewer.id == $it.author", [1, 2], [1]),
])
def test_fork_can_change_visibility_without_reusing_the_original_index_policy(before, after, original, changed):
    env = fg_env.load(contract(before), seed=4)
    post(env, "a", 1)
    post(env, "b", 2)
    forked = env.fork(patch={"records": {"notes": {"visible": after}}})
    assert values(forked, "a") == changed
    assert values(env, "a") == original


def test_fast_world_copy_rebuilds_index_with_independent_record_entries():
    from fg_env.sdk.run_copy import _copy_world

    env = fg_env.load(contract(keep=3), seed=4)
    for i, who in enumerate(("a", "b", "a")):
        post(env, who, i)
    copied = _copy_world(env.world)
    assert copied.record_authors is not env.world.record_authors
    for who in ("a", "b"):
        rows = copied.visible_records("notes", copied.entities[who])
        original = env.world.visible_records("notes", env.world.entities[who])
        assert rows == original
        assert all(row.world is copied for row in rows)
        assert all(row is not old for row, old in zip(rows, original))
    copied.post("notes", {"value": 3}, "b", None, "probe")
    assert [r["value"] for r in copied.visible_records("notes", copied.entities["a"])] == [2]
    assert values(env, "a") == [0, 2]
