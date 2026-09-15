"""Small game contracts shared by the game, gym, fork and clone tests."""

NIM = {
    "name": "Nim",
    "brief": {"situation": "A heap of stones between two players.",
              "rules": "Take 1 to 3 stones on your move. Whoever takes the last stone wins."},
    "clock": {"rounds": 50, "unit": "move"},
    "inputs": {"stones": {"type": "int", "default": 5, "min": 1}},
    "world": {"stones": {"type": "int", "default": "$inputs.stones"},
              "turn": {"type": "text", "default": "a"},
              "winner": {"type": "text", "default": ""}},
    "types": {"player": {"agent": True, "props": {"seat": 0}}},
    "entities": {"a": {"type": "player", "name": "Ann", "props": {"seat": 0}},
                 "b": {"type": "player", "name": "Bob", "props": {"seat": 1}}},
    "actions": {"take": {
        "by": "player", "description": "Take stones from the heap.",
        "params": {"count": {"type": "int", "min": 1, "max": "$min(3, $world.stones)"}},
        "when": ["$world.turn == $actor.id"],
        "do": ["$world.stones -= $params.count",
               "$world.turn = 'b' if $actor.id == 'a' else 'a'",
               {"if": "$world.stones == 0", "then": ["$world.winner = $actor.id", {"end": "last_stone", "winner": "$actor"}]}],
        "terminal": True}},
    "stages": [{"name": "move", "who": "$world.turn == $it.id", "must_act": True}],
    "views": {"heap": {"for": "player", "show": "Stones left: {$world.stones}."}},
    "game": {"players": "player", "seat": "$it.seat", "utility": "zero_sum",
             "returns": "(1 if $world.winner == $actor.id else -1) if $world.winner != '' else 0"},
    "outputs": {"winner": {"expr": "$world.winner", "type": "text"}},
}

LINES = "[[0, 1, 2], [3, 4, 5], [6, 7, 8], [0, 3, 6], [1, 4, 7], [2, 5, 8], [0, 4, 8], [2, 4, 6]]"

TIC_TAC_TOE = {
    "name": "Tic-tac-toe",
    "brief": {"situation": "A 3x3 board.", "rules": "Mark an empty cell (0-8, row by row). Three in a row wins."},
    "clock": {"rounds": 9, "unit": "move"},
    "world": {"board": {"type": "list", "default": [".", ".", ".", ".", ".", ".", ".", ".", "."]},
              "turn": {"type": "text", "default": "x"},
              "winner": {"type": "text", "default": ""}},
    "types": {"player": {"agent": True, "props": {"mark": {"type": "text", "default": "x"}, "seat": 0}}},
    "entities": {"x": {"type": "player", "props": {"mark": "x", "seat": 0}},
                 "o": {"type": "player", "props": {"mark": "o", "seat": 1}}},
    "defs": {"three": {"args": ["m"], "expr": f"$any({LINES}, $world.board[$it[0]] == $m and "
                                              "$world.board[$it[1]] == $m and $world.board[$it[2]] == $m)"}},
    "actions": {"mark": {
        "by": "player", "params": {"cell": {"type": "int", "min": 0, "max": 8}},
        "when": ["$world.turn == $actor.mark"],
        "do": [{"if": "$world.board[$params.cell] != '.'", "then": [{"fail": "That cell is taken."}]},
               "$world.board[$params.cell] = $actor.mark",
               "$world.turn = 'o' if $actor.mark == 'x' else 'x'",
               {"if": "$three($actor.mark)", "then": ["$world.winner = $actor.mark", {"end": "three", "winner": "$actor"}]}],
        "terminal": True}},
    "stages": [{"name": "move", "who": "$world.turn == $it.mark", "must_act": True}],
    "views": {"board": {"for": "player", "show": "Board: {$join($world.board, '')}. You play {$actor.mark}."}},
    "end": [{"name": "draw", "when": "$world.winner == '' and $count($world.board, $it == '.') == 0"}],
    "game": {"players": "player", "seat": "$it.seat", "utility": "zero_sum",
             "returns": "(1 if $world.winner == $actor.mark else -1) if $world.winner != '' else 0"},
    "outputs": {"winner": {"expr": "$world.winner", "type": "text"}},
}

KUHN = {
    "name": "Kuhn poker",
    "brief": {"situation": "Two players, a deck of three cards (1, 2, 3), one card each, ante 1.",
              "rules": "P0 acts first: pass or bet 1. A bet facing a bet calls; a pass facing a bet folds. "
                       "The higher card wins at showdown."},
    "clock": {"rounds": 1, "unit": "hand"},
    "world": {"history": {"type": "text", "default": ""}, "to_act": {"type": "text", "default": "p0"},
              "over": False, "winner": {"type": "text", "default": ""}, "stake": 0},
    "types": {"player": {"agent": True, "props": {"seat": 0, "card": {"type": "int", "default": 0, "private": True}}}},
    "entities": {"p0": {"type": "player", "name": "P0", "props": {"seat": 0}},
                 "p1": {"type": "player", "name": "P1", "props": {"seat": 1}}},
    "events": [{"at": 1, "phase": "start", "do": [
        {"chance": "deal p0", "outcomes": [1, 2, 3], "as": "first", "do": ["$entity('p0').card = $first"]},
        {"chance": "deal p1", "outcomes": "$filter([1, 2, 3], $it != $entity('p0').card)", "as": "second",
         "do": ["$entity('p1').card = $second"]}]}],
    "blocks": {"settle": {"args": ["who", "stake"], "do": [
        "$world.winner = $who.id", "$world.stake = $stake", "$world.over = true", {"end": "hand_over", "winner": "$who"}]}},
    "actions": {
        "pass": {"by": "player", "description": "Check, or fold facing a bet.", "terminal": True,
                 "when": ["$world.to_act == $actor.id and not $world.over"],
                 "do": [{"if": "$world.history == ''", "then": ["$world.history = 'p'", "$world.to_act = 'p1'"],
                         "else": [{"if": "$world.history == 'p'",
                                   "then": [{"block": "settle", "with": {"who": "$top(player, $it.card, 1)[0]", "stake": 1}}],
                                   "else": [{"block": "settle",
                                             "with": {"who": "$filter(player, $it.id != $actor.id)[0]", "stake": 1}}]}]}]},
        "bet": {"by": "player", "description": "Bet or call 1.", "terminal": True,
                "when": ["$world.to_act == $actor.id and not $world.over"],
                "do": [{"if": "$world.history == ''", "then": ["$world.history = 'b'", "$world.to_act = 'p1'"],
                        "else": [{"if": "$world.history == 'p'", "then": ["$world.history = 'pb'", "$world.to_act = 'p0'"],
                                  "else": [{"block": "settle",
                                            "with": {"who": "$top(player, $it.card, 1)[0]", "stake": 2}}]}]}]}},
    "stages": [{"name": "betting", "order": "$it.seat", "who": "$world.to_act == $it.id", "until": "$world.over",
                "passes": 3, "must_act": True}],
    "views": {"table": {"for": "player", "show": "Your card: {$actor.card}. Betting so far: {$world.history}."}},
    "game": {"players": "player", "seat": "$it.seat", "utility": "zero_sum",
             "returns": "($world.stake if $actor.id == $world.winner else -$world.stake) if $world.over else 0"},
    "outputs": {"history": {"expr": "$world.history", "type": "text"}},
}

MATCHING_PENNIES = {
    "name": "Matching pennies",
    "brief": {"situation": "Two players show a coin at once.", "rules": "The matcher wins when the coins match."},
    "clock": {"rounds": 1},
    "types": {"player": {"agent": True, "props": {"seat": 0, "side": {"type": "text", "default": "", "private": True},
                                                  "score": 0}}},
    "entities": {"m": {"type": "player", "props": {"seat": 0}}, "n": {"type": "player", "props": {"seat": 1}}},
    "actions": {"show": {"by": "player", "private": True, "terminal": True,
                         "params": {"side": {"type": "enum", "values": ["heads", "tails"]}},
                         "do": ["$actor.side = $params.side"]}},
    "stages": [{"name": "show", "turns": "simultaneous", "must_act": True}],
    "events": [{"phase": "end", "do": [
        "$same = $entity('m').side == $entity('n').side",
        "$entity('m').score = 1 if $same else -1", "$entity('n').score = -1 if $same else 1"]}],
    "game": {"players": "player", "seat": "$it.seat", "returns": "$actor.score", "utility": "zero_sum"},
    "outputs": {"match": {"expr": "$entity('m').score", "type": "number"}},
}
