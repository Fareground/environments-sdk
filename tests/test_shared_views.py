"""A list view whose items do not depend on who reads it is worked out once per world state for every reader.

What each reader is shown stays exactly what it would be shown alone: its own private properties, the items a `where`
names it the owner of, its own luck and its own [id] handles.
"""
import sys

import pytest

import fg_env


def _crowd(count, views, stage="simultaneous"):
    return {"name": "Crowd", "clock": {"rounds": 2},
            "types": {"a": {"agent": True, "inspect": True,
                            "props": {"cash": {"type": "int", "default": 100},
                                      "secret": {"type": "int", "default": 3, "private": True}}}},
            "population": [{"type": "a", "count": count}],
            "actions": {"bid": {"by": "a", "params": {"x": {"type": "int", "min": 0, "max": "$actor.cash"}},
                                "do": ["$actor.cash -= $params.x"]}},
            "stages": [{"name": "play", "turns": stage}],
            "views": views}


TOP = {"top": {"of": "a", "where": "$it.cash > 0", "sort": "-$it.cash", "limit": 5, "show": "{name}: {cash}"}}


def _reading(texts):
    def play(wake):
        texts[wake.entity_id] = wake.update
        wake.call("bid", {"x": int(wake.entity_id.split("_")[1])})
        wake.end()
    return play


def _calls_per_agent(count):
    """The function calls one round of ``count`` agents reading the top list and the last round's news costs each
    of them: a count, not a time, so it is the same on any machine."""
    env = fg_env.load(_crowd(count, TOP), seed=1)
    env.run(_reading({}), rounds=1)  # the second round's readers also get the first round's news
    calls = [0]

    def count_call(frame, event, arg):
        if event in ("call", "c_call"):
            calls[0] += 1

    sys.setprofile(count_call)
    try:
        env.run(_reading({}), rounds=1)
    finally:
        sys.setprofile(None)
    return calls[0] / count


def test_a_top_list_and_the_news_read_by_every_agent_cost_each_about_the_same_however_many_agents():
    few, many = _calls_per_agent(40), _calls_per_agent(320)
    assert many < 1.3 * few, (few, many)  # each reader sorting everyone, or reading everyone's news, costs 8 times


def test_every_reader_sees_the_same_top_list_as_a_preview_of_its_own_turn_shows():
    env = fg_env.load(_crowd(6, TOP), seed=1)
    env.run(_reading({}), rounds=1)
    previews = {agent: env.preview(agent)["update"] for agent in ("a_1", "a_4", "a_6")}
    texts: dict[str, str] = {}
    env.run(_reading(texts), rounds=1)
    assert {agent: texts[agent] for agent in previews} == previews
    assert "Top:\n- A 1: 99\n- A 2: 98\n- A 3: 97\n- A 4: 96\n- A 5: 95" in texts["a_6"]


def test_a_list_chosen_by_a_value_hidden_from_the_reader_is_still_refused_for_that_reader():
    view = {"h": {"of": "a", "sort": "$it['secret']", "limit": 3, "show": "{id}"}}
    result = fg_env.load(_crowd(3, view), seed=1).run(_reading({}), rounds=1)
    assert result.status == "failed"
    assert "secret is private, and this is what A 1 is shown" in result.error


def test_each_reader_is_shown_its_own_private_values_in_a_list_that_picks_its_own():
    view = {"mine": {"of": "a", "where": "$it.id == $actor.id", "show": "{name}: {secret} {cash}"}}
    texts: dict[str, str] = {}
    fg_env.load(_crowd(3, view), seed=1).run(_reading(texts), rounds=1)
    assert [text.splitlines()[-1] for text in texts.values()] == ["- A 1: 3 100", "- A 2: 3 100", "- A 3: 3 100"]


LUCKY = {"lucky": {"of": "a", "sort": "$it.cash + $random()", "limit": 3, "show": "{name}"}}


def test_a_list_drawn_at_random_is_each_readers_own_luck():
    texts: dict[str, str] = {}
    fg_env.load(_crowd(8, LUCKY), seed=1).run(_reading(texts), rounds=1)
    assert len(set(texts.values())) > 1  # the same state, but each reader's own draws


def test_a_preview_shows_the_list_drawn_at_random_the_turn_shows():
    env = fg_env.load(_crowd(8, LUCKY, "sequential"), seed=1)
    previews = {agent: env.preview(agent, participants=_reading({}))["update"] for agent in ("a_1", "a_2", "a_3")}
    texts: dict[str, str] = {}
    env.run(_reading(texts), rounds=1)
    assert {agent: texts[agent] for agent in previews} == previews


@pytest.mark.parametrize("stage", ["simultaneous", "sequential"])
def test_a_list_that_reads_the_readers_handles_is_worked_out_for_each_reader(stage):
    # Entities another agent may inspect read as `Name [id]` to it, but an agent's own name has no handle.
    view = {"named": {"of": "a", "where": "$fmt($it, 'upper') == 'A 2'", "show": "{id}", "empty": "nobody"}}
    texts: dict[str, str] = {}
    fg_env.load(_crowd(3, view, stage), seed=1).run(_reading(texts), rounds=1)
    assert [text.splitlines()[-1] for text in texts.values()] == ["nobody", "- a_2", "nobody"]
