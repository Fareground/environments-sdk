"""Author-only records never produce news, even in truncated mixed histories."""
import pytest

import fg_env
from fg_env.exposure import Shown


def contract(rule="$viewer.id == $it.author"):
    return {"name": "Private record news", "types": {"person": {"agent": True}},
            "entities": {n: {"type": "person"} for n in ("a", "b")},
            "world": {"shared": False},
            "records": {"private": {"visible": rule, "fields": {"text": "text"}, "show": "{text}"}}}


@pytest.mark.parametrize("rule", ["$viewer.id == $it.author", "$it.author == $viewer.id"])
@pytest.mark.parametrize("limit", [0, 2, None])
def test_private_entries_never_affect_lines_counts_or_exposures(rule, limit):
    env = fg_env.load(contract(rule))
    w = env.world
    for i in range(5):
        for author in ("a", "b", None):
            w.post("private", {"text": f"SECRET-{i}"}, author, None, "probe")
        w.emit("notice", f"Notice {i}")
    for actor in w.entities.values():
        shown, attached = Shown(), []
        lines, hidden = env.perception.news(actor, 0, limit, shown, attached)
        expected = [f"Notice {i}" for i in range(5)]
        expected = expected if limit is None else expected[-limit:] if limit else []
        assert lines == expected
        assert hidden == 5 - len(expected)
        assert len(shown.news) == len(expected)
        assert shown.entries == [] and attached == []
    # Cursor position is still the original global log sequence, not a filtered offset.
    since = next(e.seq for e in w.log if e.text == "Notice 2")
    assert env.perception.news(w.entities["b"], since) == (["Notice 3", "Notice 4"], 0)


def test_extra_permission_term_still_reveals_shared_entries_and_revokes_them():
    env = fg_env.load(contract("$viewer.id == $it.author or $world.shared"))
    env.world.post("private", {"text": "RELEASED"}, "a", None, "probe")
    viewer = env.world.entities["b"]
    assert env.perception.news(viewer, 0) == ([], 0)
    env.world.set_world("shared", True)
    lines, hidden = env.perception.news(viewer, 0)
    assert len(lines) == 1 and "RELEASED" in lines[0] and hidden == 0
    env.world.set_world("shared", False)
    assert env.perception.news(viewer, 0) == ([], 0)


def test_fork_can_release_old_private_notifications_without_changing_the_source():
    env = fg_env.load(contract())
    env.world.post("private", {"text": "RELEASED"}, "a", None, "probe")
    forked = env.fork(patch={"records": {"private": {"visible": "all"}}})
    lines, hidden = forked.perception.news(forked.world.entities["b"], 0)
    assert len(lines) == 1 and "RELEASED" in lines[0] and hidden == 0
    assert env.perception.news(env.world.entities["b"], 0) == ([], 0)
