"""Declaring a stage a mechanism generates refines it: the author's fields win, the rest of the generated stage stays,
and the generated stages keep their order."""
import fg_env

HOLDEM = {
    "name": "Holdem",
    "clock": {"rounds": 1},
    "types": {"player": {"agent": True, "props": {"seat": 0, "flashed": 0}}},
    "entities": {name: {"type": "player", "props": {"seat": seat}}
                 for seat, name in enumerate(["ana", "ben", "cy"], 1)},
    "mechanisms": {
        "deck": {"kind": "game", "mode": "cards", "who": "player", "deal": "never",
                 "zones": {"board": {"visible": "public", "title": "Board"}}},
        "table": {"kind": "game", "mode": "pot", "who": "player", "stack": 100, "blinds": [1, 2], "seat": "$it.seat",
                  "setup": [{"game": "deck", "action": "collect"},
                            {"game": "deck", "action": "deal", "qty": 2, "to": "$filter(player, $it.in_hand)"}],
                  "streets": {"preflop": [],
                              "flop": [{"game": "deck", "action": "deal", "qty": 3, "zone": "board", "face_up": True}],
                              "river": [{"game": "deck", "action": "deal", "qty": 1, "zone": "board",
                                         "face_up": True}]},
                  "score": "$poker_rank($hand($it) + $zone(board)).score"},
    },
    "actions": {"flash": {"by": "player", "do": ["$actor.flashed += 1"], "terminal": False}},
}
TABLE_ACTIONS = ["table_fold", "table_check", "table_call", "table_bet", "table_raise", "table_all_in"]


def _passive(wake):
    names = [tool.name for tool in wake.tools]
    wake.call(next(name for name in ("table_check", "table_call") if name in names))


def test_a_declared_generated_stage_keeps_its_generated_fields_and_place():
    generated = fg_env.load(HOLDEM, seed=1).contract.stage_list()
    refined = {**HOLDEM, "stages": [{"name": "flop", "actions": [*TABLE_ACTIONS, "flash"]}]}
    env = fg_env.load(refined, seed=1)
    stages = env.contract.stage_list()
    assert [s.name for s in stages] == [s.name for s in generated] == ["preflop", "flop", "river"]
    flop = next(s for s in stages if s.name == "flop")
    original = next(s for s in generated if s.name == "flop")
    assert "flash" in flop.actions and (flop.who, flop.until) == (original.who, original.until)
    starts = [event.do for event in env.contract.events if event.on == "stage.flop.start"]
    plain = fg_env.load(HOLDEM, seed=1).contract.events
    assert starts == [event.do for event in plain if event.on == "stage.flop.start"]
    assert env.run(_passive).status == "completed"
    assert sum(1 for card in env.entities("card") if card["props"].get("zone") == "board") == 4


def test_stages_the_author_adds_keep_their_place_before_the_generated_ones():
    contract = {**HOLDEM, "stages": [{"name": "warmup", "actions": ["flash"]}]}
    assert ([s.name for s in fg_env.load(contract, seed=1).contract.stage_list()]
            == ["warmup", "preflop", "flop", "river"])
