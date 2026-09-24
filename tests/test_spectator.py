"""Spectator views: an omniscient picture for UIs and reports that no agent ever sees."""
import copy
import json

import fg_env

TABLE = {
    "name": "Hidden hands",
    "clock": {"rounds": 3},
    "types": {"player": {"agent": True, "props": {"card": {"type": "int", "default": 0, "private": True},
                                                     "chips": 10}}},
    "entities": {"ann": {"type": "player", "name": "Ann"}, "bo": {"type": "player", "name": "Bo"}},
    "events": [{"phase": "start", "each": "player", "do": ["$it.card = $randint(1, 10)"]}],
    "actions": {"bet": {"by": "player", "do": ["$actor.chips -= 1"], "terminal": True}},
    "views": {
        "table": {"for": "spectator", "title": "Whole table", "of": "player", "bullet": False,
                  "show": "{name}: card {card}, chips {chips}"},
        "note": {"for": "spectator", "show": "Spectator note for round {$round}"},
        "mine": {"for": "player", "show": "Your chips: {chips}"},
    },
    "stages": [{"name": "betting"}],
}


def test_frames_are_rendered_every_round_and_the_last_is_final():
    env = fg_env.load(TABLE, seed=1)
    result = env.run()
    assert result.ok, result.summary()
    assert [f["round"] for f in result.frames] == [1, 2, 3]
    assert [f.get("final", False) for f in result.frames] == [False, False, True]
    assert result.frames[-1]["views"] == env.spectate()
    table = result.frames[0]["views"]["table"]
    assert table.startswith("Whole table:\nAnn: card ") and "Bo: card " in table
    assert result.frames[1]["views"]["note"] == "Spectator note for round 2"


def test_spectator_views_never_reach_an_agent():
    updates = []

    def reader(wake):
        updates.append(wake.update)
        assert all(tool.name != "look" for tool in wake.tools)
        wake.end()

    env = fg_env.load(TABLE, seed=1)
    preview = env.preview("ann")
    env.run(reader)
    for text in updates + [preview["update"], preview["brief"]]:
        assert "Whole table" not in text and "Spectator note" not in text
    assert "Your chips" in updates[0]


def test_spectator_views_do_not_change_the_run_even_when_they_draw_randomness():
    plain = copy.deepcopy(TABLE)
    plain["views"] = {"mine": TABLE["views"]["mine"]}
    watched = copy.deepcopy(TABLE)
    watched["views"]["coin"] = {"for": "spectator", "when": "$chance(0.5)", "show": "Heads"}
    assert fg_env.run(watched, seed=5).events == fg_env.run(plain, seed=5).events


def test_a_run_without_spectator_views_keeps_no_frames():
    plain = copy.deepcopy(TABLE)
    plain["views"] = {"mine": TABLE["views"]["mine"]}
    env = fg_env.load(plain, seed=1)
    assert env.run().frames == [] and env.spectate() == {}


def test_a_run_split_by_snapshot_and_restore_keeps_the_same_frames():
    straight = fg_env.load(TABLE, seed=9).run()
    env = fg_env.load(TABLE, seed=9)
    env.run(rounds=1)
    resumed = fg_env.Env.restore(TABLE, json.loads(json.dumps(env.snapshot())))
    assert resumed.run().to_dict() == straight.to_dict()


def test_the_checker_keeps_spectator_views_apart_from_agents():
    def errors(**view):
        contract = copy.deepcopy(TABLE)
        contract["views"]["bad"] = view
        return [str(i) for i in fg_env.check(contract) if i.severity == "error"]

    assert any("views.bad.look" in e for e in errors(**{"for": "spectator", "show": "x", "look": True}))
    assert any("$actor is not available" in e for e in errors(**{"for": "spectator", "show": "{$actor.chips}"}))
    assert any("spectators alone" in e for e in errors(**{"for": ["spectator", "player"], "show": "x"}))
    named = copy.deepcopy(TABLE)
    named["types"]["spectator"] = {"props": {}}
    assert any("reserved for spectator views" in str(i) for i in fg_env.check(named))
