"""Minimal permission scopes preserve results, implicit function context and work limits."""
import pytest

import fg_env
from fg_env.errors import RunError
from fg_env.expr import compile_expr, truthy
from fg_env.expr.base import _held, shared_budget
from fg_env.expr.calls import FUNCTIONS, FunctionSpec


def world(rule):
    env = fg_env.load({"name": "Record scope", "clock": {"rounds": 1},
                       "world": {"allow": True},
                       "types": {"reader": {"props": {"allowed": True}}},
                       "entities": {"a": {"type": "reader"}, "b": {"type": "reader", "props": {"allowed": False}}},
                       "records": {"notes": {"fields": {"text": "text"}, "visible": rule}}}, seed=4)
    entry = env.world.post("notes", {"text": "hello"}, "a", None, "probe")
    return env.world, entry


@pytest.mark.parametrize("rule", ["$viewer.id == $it.author", "$it.author == $viewer.id",
                                   "$viewer.allowed and $it.text == hello", "$it.author.id != $viewer.id",
                                   "false", "$world.allow and $viewer.allowed", "$round == 0",
                                   "$len($it.text) > 0 and $viewer.allowed"])
def test_permissions_match_full_scope_evaluation_for_each_reader(rule):
    w, entry = world(rule)
    expr = compile_expr(rule)
    for viewer in w.entities.values():
        expected = truthy(expr(w.evaluation.scope(viewer=viewer, it=entry)))
        assert w.evaluation.entry_visible("notes", entry, viewer) == expected


def test_permission_does_not_cache_reader_or_world_changes():
    w, entry = world("$viewer.allowed")
    viewer = w.entities["a"]
    assert w.evaluation.entry_visible("notes", entry, viewer)
    w.set_prop(viewer, "allowed", False)
    assert not w.evaluation.entry_visible("notes", entry, viewer)
    w.set_prop(viewer, "allowed", True)
    assert w.evaluation.entry_visible("notes", entry, viewer)


def test_function_with_implicit_context_keeps_the_full_scope(monkeypatch):
    name = "visibility_context_probe"
    monkeypatch.setitem(FUNCTIONS, name, FunctionSpec(
        name, lambda call: call.scope.vars["round"] == 0 and call.scope.vars["world"].expr_attr("allow", None),
        f"{name}()", "Test implicit context", 0, 0))
    w, entry = world(f"${name}()")
    assert w.evaluation.entry_visible("notes", entry, w.entities["a"])
    w.set_world("allow", False)
    assert not w.evaluation.entry_visible("notes", entry, w.entities["a"])


def test_pure_permission_errors_keep_the_authored_field():
    w, entry = world("$it.text == hello")
    w.contract.records["notes"].visible = "$it.missing == hello"
    with pytest.raises(RunError, match=r"records.notes.visible.*record entry has no field 'missing'"):
        w.evaluation.entry_visible("notes", entry, w.entities["a"])


def test_repeated_permission_evaluations_still_consume_the_shared_work_budget():
    w, entry = world("$viewer.id == $it.author")
    with shared_budget(4, "permission probe"):
        for _ in range(4):
            assert _held(lambda: w.evaluation.entry_visible("notes", entry, w.entities["a"]))
        with pytest.raises(RunError, match="work budget"):
            _held(lambda: w.evaluation.entry_visible("notes", entry, w.entities["a"]))


@pytest.mark.parametrize("rule", ["$viewer.id == $it.author", "$it.author == $viewer.id"])
def test_proven_foreign_authorship_is_rejected_without_spending_expression_work(rule):
    w, entry = world(rule)
    with shared_budget(0, "permission probe"):
        assert not _held(lambda: w.evaluation.entry_visible("notes", entry, w.entities["b"]))
        with pytest.raises(RunError, match="work budget"):
            _held(lambda: w.evaluation.entry_visible("notes", entry, w.entities["a"]))


def test_additional_permission_terms_cannot_be_short_circuited_by_the_author_guard():
    w, entry = world("$viewer.id == $it.author or $world.allow")
    assert w.evaluation.entry_visible("notes", entry, w.entities["b"])
    w.set_world("allow", False)
    assert not w.evaluation.entry_visible("notes", entry, w.entities["b"])


def test_a_def_reads_records_as_the_agent_its_view_renders_for_may_see_them():
    """A def called in a view sees the viewer's records, not every record; its cached value is per viewer."""
    contract = {"name": "Diaries", "clock": {"rounds": 2},
                "types": {"writer": {"agent": True}},
                "entities": {"a": {"type": "writer"}, "b": {"type": "writer"}},
                "records": {"notes": {"fields": {"text": "text"}, "visible": "$viewer.id == $it.author"}},
                "defs": {"mine": {"expr": "$len($records(notes))"}},
                "actions": {"write": {"by": "writer", "do": [{"post": "notes", "text": "hi"}]}},
                "views": {"count": {"for": "writer", "show": "def {$mine} direct {$len($records(notes))}"}}}
    seen = {}

    def play(wake):
        if wake.round == 1 and wake.entity_id == "a":
            wake.call("write", {})
        elif wake.round == 2:
            seen[wake.entity_id] = wake.update
        wake.end()

    fg_env.run(contract, play, seed=1)
    assert "def 1 direct 1" in seen["a"] and "def 0 direct 0" in seen["b"]
