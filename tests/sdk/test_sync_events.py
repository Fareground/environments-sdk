"""Sync events read the world as it was before the event and land every write together; `order` orders items."""
import json

import pytest

import fg_env
from fg_env.sdk.check import parse_contract
from fg_env.sdk.runtime import Env

GLIDER = [[0, 1], [1, 2], [2, 0], [2, 1], [2, 2]]

LIFE = {
    "name": "Life",
    "clock": {"rounds": 4},
    "space": {"grid": {"rows": 8, "cols": 8, "neighborhood": "moore", "torus": True}},
    "types": {"cell": {"props": {"on": False}}},
    "population": [{"type": "cell", "count": 64, "at": "[($i - 1) // 8, ($i - 1) % 8]",
                    "props": {"on": f"$it.at in {json.dumps(GLIDER)}"}}],
    "events": [{"phase": "end", "each": "cell", "sync": True, "do": [
        "$n = $count($near($it, 1), $it.on)",
        "$it.on = $n == 3 or ($it.on and $n == 2)"]}],
    "metrics": {"on": "$map($filter(cell, $it.on), $it.at)"},
}


def test_game_of_life_is_one_sync_event_and_a_glider_glides():
    result = fg_env.run(LIFE, seed=1)
    assert result.status == "completed", result.error
    assert result.series["on"][-1] == [[r + 1, c + 1] for r, c in sorted(GLIDER)]


def test_without_sync_later_items_read_the_writes_of_earlier_ones():
    contract = json.loads(json.dumps(LIFE))
    contract["events"][0]["sync"] = False
    assert fg_env.run(contract, seed=1).series["on"] != fg_env.run(LIFE, seed=1).series["on"]


def test_a_split_sync_run_ends_exactly_like_a_straight_run():
    straight = fg_env.run(LIFE, seed=3).to_dict()
    env = fg_env.load(LIFE, seed=3)
    env.run(rounds=2)
    env = fg_env.Env.restore(LIFE, json.loads(json.dumps(env.snapshot())))
    assert env.run().to_dict() == straight


ROW = {
    "name": "Row",
    "clock": {"rounds": 1},
    "world": {"total": 0, "flag": False, "seen": {"type": "list", "default": []}},
    "types": {"box": {"props": {"x": 0}}},
    "population": [{"type": "box", "count": 4, "props": {"x": "$i"}}],
}


def _row(event):
    return {**ROW, "events": [{"each": "box", **event}]}


def test_two_items_writing_different_values_to_one_property_is_an_error_naming_both():
    with pytest.raises(fg_env.RunError) as failed:
        fg_env.run(_row({"sync": True, "do": ["$world.total = $it.x"]}), seed=1)
    result = failed.value.result
    assert result.status == "failed"
    assert "'box_1' and 'box_2' write different values to $world.total" in result.error


def test_items_that_agree_may_write_the_same_property():
    env = fg_env.load(_row({"sync": True, "do": ["$world.flag = true", "$it.x += $world.total + 1"]}), seed=1)
    result = env.run()
    assert result.status == "completed", result.error
    assert env.props["flag"] is True and [e["props"]["x"] for e in env.entities("box")] == [2, 3, 4, 5]


def test_a_refused_item_drops_only_its_own_writes():
    env = fg_env.load(_row({"sync": True, "do": ["$it.x *= 10", {"if": "$it.x == 2", "then": [{"fail": "Not this one."}]}]}),
                      seed=1)
    result = env.run()
    assert [e["props"]["x"] for e in env.entities("box")] == [10, 2, 30, 40]
    assert any(e["kind"] == "refused" and "Not this one" in e["text"] for e in result.events)


def test_a_sync_event_that_changes_the_world_directly_is_reported():
    contract = _row({"sync": True, "do": [{"create": "box"}]})
    issues = [i for i in fg_env.check(contract) if i.severity == "error"]
    assert issues and "cannot run `create`" in issues[0].message
    unchecked = Env(parse_contract(contract), {}, 1)  # the engine refuses it even when nothing checked it
    result = unchecked.run()
    assert result.status == "failed" and "a sync event can only assign properties" in result.error
    assert unchecked.entities("box") == fg_env.load(_row({}), seed=1).entities("box")


def test_random_order_is_seeded_and_an_order_expression_sorts_lowest_first():
    def seen(event, seed):
        env = fg_env.load(_row({**event, "do": ["$world.seen = $world.seen + [$it.x]"]}), seed=seed)
        env.run()
        return env.props["seen"]

    shuffled = {tuple(seen({"order": "random"}, seed)) for seed in range(6)}
    assert len(shuffled) > 1 and all(sorted(order) == [1, 2, 3, 4] for order in shuffled)
    assert seen({"order": "random"}, 2) == seen({"order": "random"}, 2)
    assert seen({"order": "-$it.x"}, 1) == [4, 3, 2, 1]


def test_order_and_sync_need_each():
    contract = {**ROW, "events": [{"order": "random", "do": ["$world.total = 1"]}]}
    assert any(i.path == "events[0].order" for i in fg_env.check(contract))
