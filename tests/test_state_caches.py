"""Cached def results never reuse a random draw; tool choices always reflect the current state."""
import fg_env

BASE = {"name": "Caches", "clock": {"rounds": 1},
        "types": {"player": {"agent": True, "props": {"cash": 1}},
                  "thing": {"props": {"owner": {"type": "text", "default": ""}}}},
        "entities": {"ann": {"type": "player"}, "bo": {"type": "player"},
                     "cup": {"type": "thing"}, "pen": {"type": "thing"}},
        "stages": [{"name": "play", "turns": "sequential"}]}


def test_a_def_that_draws_through_any_random_function_is_never_reused():
    contract = {**BASE, "world": {"a": 0.0},
                "defs": {"draw": {"expr": "$triangular(0, 1, 0.5)"}},  # a mechanism's random function
                "events": [{"phase": "start", "do": ["$world.a = $draw - $draw"]}]}
    env = fg_env.load(contract, seed=3)
    env.run("idle")
    assert env.props["a"] != 0


def _choices(wake):
    tool = next(t for t in wake.tools if t.name == "take")
    return sorted(tool.input_schema["properties"]["item"].get("enum", []))


def test_tool_choices_refresh_after_a_change_in_the_same_turn_and_for_the_next_agent():
    contract = {**BASE, "stages": [{"name": "play", "turns": "sequential", "max_actions": 3}],
                "actions": {"take": {"by": "player", "params": {
        "item": {"type": "entity", "of": "thing", "where": "$it.owner == ''"}},
                                     "do": ["$params.item.owner = $actor.id"]}}}
    env = fg_env.load(contract, seed=1)
    seen = {}

    def play(wake):
        before = _choices(wake)
        if wake.entity_id == "ann":
            wake.call("take", {"item": "cup"})
        seen[wake.entity_id] = (before, _choices(wake))
        wake.end()

    result = env.run(play)
    assert result.status == "completed", result.error
    assert seen["ann"] == (["cup", "pen"], ["pen"])
    assert seen["bo"] == (["pen"], ["pen"])
