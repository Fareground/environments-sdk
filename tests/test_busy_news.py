"""An agent's news in a busy world: what is sent to it first, then the newest world news, then the newest of others'
actions — its own never — and a count of the rest, however the log changes between reads."""
import fg_env

CONTRACT = {"name": "Busy", "types": {"person": {"agent": True}},
            "entities": {name: {"type": "person"} for name in ("a", "b", "c")}}


def _busy():
    env = fg_env.load(CONTRACT)
    world = env.world
    for i in range(6):
        world.emit("action", f"b acted {i}", actor="b")
    world.emit("action", "a acted", actor="a")
    world.emit("action", "", actor="c")  # says nothing: never news
    for i in range(3):
        world.emit("notice", f"Notice {i}")
    world.emit("notice", "Psst", to=("a",))
    world.emit("notice", "Not for a", to=("b",))
    world.emit("action", "b acted last", actor="b")
    return env, world


def test_what_is_sent_to_the_agent_comes_first_then_world_news_then_others_actions():
    env, world = _busy()
    a = world.entities["a"]
    assert env.perception.news(a, 0, 5) == (["Notice 0", "Notice 1", "Notice 2", "Psst", "b acted last"], 6)
    assert env.perception.news(a, 0, 2) == (["Notice 2", "Psst"], 9)
    assert env.perception.news(a, 0, 0) == (["Psst"], 10)  # what is sent to it is always kept
    lines, hidden = env.perception.news(a, 0)
    assert hidden == 0 and len(lines) == 11 and "a acted" not in lines and "Not for a" not in lines
    since = next(event.seq for event in world.log if event.text == "Notice 1")
    assert env.perception.news(a, since, 3) == (["Notice 2", "Psst", "b acted last"], 0)
    assert env.perception.news(world.entities["b"], since) == (["Notice 2", "Not for a"], 0)


def test_news_follows_undone_and_forgotten_events():
    env, world = _busy()
    a = world.entities["a"]
    mark = world.journal.mark()
    world.emit("notice", "Tried")
    assert "Tried" in env.perception.news(a, 0)[0]
    world.journal.rollback(mark)
    assert "Tried" not in env.perception.news(a, 0)[0]
    world.emit("notice", "Kept")
    assert env.perception.news(a, 0)[0][-1] == "Kept"
    del world.log[:6]  # forgotten: b's first six actions
    lines, hidden = env.perception.news(a, 0)
    assert hidden == 0 and not any(line.startswith("b acted ") and line != "b acted last" for line in lines)
    assert env.perception.news(a, 0, 3) == (["Notice 2", "Psst", "Kept"], 3)
