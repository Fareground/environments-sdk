"""The game family's board mode: geometry, the rules engine (perft), and whole games played through agent tools."""
import copy
import json
import time
from pathlib import Path

import pytest

import fg_env
from fg_env.sdk.errors import ContractError
from fg_env.sdk.mechanisms.board_engine import Pos, has_line, legal, make
from fg_env.sdk.mechanisms.board_geometry import graph, grid, hex_board, ring
from fg_env.sdk.mechanisms.board_rules import BoardConfig, compile_rules, parse_setup

EXAMPLES = Path(__file__).parents[2] / "examples" / "contracts"


def _example(name):
    return json.loads((EXAMPLES / name).read_text())


def _rules(config, name="board"):
    return compile_rules(name, BoardConfig.model_validate({k: v for k, v in config.items() if k not in ("kind", "mode")}))


def _position(rules, setup, turn=0):
    pos = Pos(rules.geo.size, len(rules.sides))
    for side, kind, cell in parse_setup(rules, setup):
        pos.add(side, kind, cell)
    pos.turn = turn
    return pos


def _texts(rules, pos, side=None):
    return sorted(m.text for m in legal(rules, pos, pos.turn if side is None else side))


def _perft(rules, pos, depth):
    moves = legal(rules, pos, pos.turn)
    if depth == 1:
        return len(moves)
    return sum(_perft(rules, make(rules, pos, m, pos.turn).pos, depth - 1) for m in moves)


def _with_setup(contract, board, position, turn=None, **config):
    """The contract with its board reset to ``position`` at the start of round 1, and config overrides."""
    out = copy.deepcopy(contract)
    out["mechanisms"][board].update(config)
    setup = {"game": board, "action": "setup", "position": position}
    if turn:
        setup["turn"] = turn
    out["events"] = [{"at": 1, "do": [setup]}]
    return out


def _replay(contract, board, moves, rounds=None):
    """Play ``moves`` in order through each player's `<board>_move` tool (a capture chain is several calls)."""
    env = fg_env.load(contract, seed=1)
    queue = list(moves)
    tool = f"{board}_move"

    def player(wake):
        while queue and not wake.done:
            if tool not in {t.name for t in wake.tools}:
                break
            result = wake.call(tool, {"move": queue[0]})
            assert result.ok, f"{queue[0]}: {result.text}"
            queue.pop(0)
            if result.ended:
                break
        if not wake.done:
            wake.end()

    result = env.run(player, rounds=rounds or len(moves) + 1)
    assert result.status != "failed", result.error
    assert not queue, f"moves not played: {queue}"
    return env, result


def _pieces(env):
    return {e["props"]["cell"]: (e["props"]["owner"], e["props"]["kind"]) for e in env.entities("piece")}


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_grid_names_directions_and_relative_turns():
    board = grid(8, 8)
    assert board.names[0] == "a8" and board.names[-1] == "h1"
    assert board.names[board.step["n"][board.index["e2"]]] == "e3"
    assert board.step["ne"][board.index["h1"]] == -1
    assert board.expand("fl", "e", "n") == ("nw",)
    assert board.expand("fl", "e", "s") == ("se",)  # facing south, left is east
    assert board.expand(["orthogonal", "n"], "e") == ("n", "e", "s", "w")
    assert grid(3, 4, "rc").names[:2] == ["1,1", "1,2"]


def test_hex_ring_and_graph_boards():
    hexagon = hex_board(radius=2)
    assert hexagon.size == 19 and max(len(a) for a in hexagon.adjacent) == 6
    assert len(hex_board(rows=11, cols=11).names) == 121
    track = ring(5, wrap=False)
    loop = ring(5)
    assert loop.names[loop.step["cw"][loop.index["5"]]] == "1"
    assert track.step["cw"][track.index["5"]] == -1
    places = graph(["a", "b", "c"], [["a", "b", "up", "down"], ["b", "c"]])
    assert places.names[places.step["up"][0]] == "b" and places.names[places.step["down"][1]] == "a"
    assert sorted(places.adjacent[1]) == [0, 2]


def test_movement_on_hex_ring_and_graph_boards():
    hexagon = _rules({"shape": "hex", "size": 2, "sides": ["a"], "pieces": {"q": {"moves": [{"slide": "all"}]}},
                      "setup": {"a": {"q": ["c3"]}}})
    assert len(legal(hexagon, _position(hexagon, {"a": {"q": ["c3"]}}), 0)) == 12
    track = _rules({"shape": "ring", "size": 6, "sides": ["a"], "pieces": {"r": {"moves": [{"step": "f", "distance": 2}]}}})
    assert _texts(track, _position(track, {"a": {"r": ["5"]}})) == ["5-1"]
    places = _rules({"shape": "graph", "nodes": ["a", "b", "c", "d"], "edges": [["a", "b"], ["b", "c"], ["c", "d"]],
                     "sides": ["x", "y"], "pieces": {"s": {"moves": [{"step": "adjacent"}]}}, "lines": [["a", "b", "c"]],
                     "line": 3})
    pos = _position(places, {"x": {"s": ["b"]}, "y": {"s": ["c"]}})
    assert _texts(places, pos) == ["b-a", "bxc"]
    assert not has_line(places, pos, 0, 3)
    assert has_line(places, _position(places, {"x": {"s": ["a", "b", "c"]}}), 0, 3)


# ---------------------------------------------------------------------------
# Chess: perft and games
# ---------------------------------------------------------------------------

CHESS = _example("chess.json")
KIWIPETE = "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R"
POSITION_3 = "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8"


@pytest.fixture(scope="module")
def chess():
    return _rules(CHESS["mechanisms"]["chess"], "chess")


def test_chess_perft_from_the_start(chess):
    start = _position(chess, CHESS["mechanisms"]["chess"]["setup"])
    assert _perft(chess, start, 1) == 20
    after_e4 = make(chess, start, next(m for m in legal(chess, start, 0) if m.text == "e2-e4"), 0).pos
    assert _perft(chess, after_e4, 1) == 20
    assert _perft(chess, start, 3) == 8902


def test_chess_perft_with_castling_en_passant_promotion_and_pins(chess):
    kiwipete = _position(chess, KIWIPETE)
    assert _perft(chess, kiwipete, 1) == 48
    assert _perft(chess, kiwipete, 2) == 2039
    assert _perft(chess, _position(chess, POSITION_3), 3) == 2812


def test_scholars_mate_ends_the_game_by_checkmate():
    env, result = _replay(CHESS, "chess", ["e2-e4", "e7-e5", "f1-c4", "b8-c6", "d1-h5", "g8-f6", "h5xf7"])
    assert result.status == "ended" and env.ended_by == "checkmate"
    assert result.outputs["chess_result"] == {"winner": "white", "reason": "checkmate"}
    assert "White wins (checkmate)" in result.events[-1]["text"]


def test_en_passant_promotion_and_both_castlings_through_the_tools():
    line = ["e2-e4", "d7-d5", "e4-e5", "f7-f5", "e5xf6", "b8-c6", "f6xg7", "c8-f5", "g7xh8=Q", "d8-d7",
            "g1-f3", "O-O-O", "f1-e2", "a7-a6", "O-O"]
    env, result = _replay(CHESS, "chess", line)
    board = _pieces(env)
    assert board["g1"] == ("white", "K") and board["f1"] == ("white", "R")
    assert board["c8"] == ("black", "K") and board["d8"] == ("black", "R")
    assert board["h8"] == ("white", "Q") and board["f5"] == ("black", "B")
    assert "f6" not in board and "e5" not in board
    assert env.props["chess_ply"] == len(line) and result.status == "running"


def test_the_move_tool_lists_only_legal_moves_and_refuses_others():
    env = fg_env.load(CHESS, seed=1)
    seen = {}

    def player(wake):
        schema = next(t for t in wake.tools if t.name == "chess_move").input_schema
        seen["enum"] = schema["properties"]["move"]["enum"]
        seen["bad"] = wake.call("chess_move", {"move": "e2-e5"})
        seen["update"] = wake.update
        wake.call("chess_move", {"move": "E2-E4"})

    env.run(player, rounds=1)
    assert len(seen["enum"]) == 20 and "g1-f3" in seen["enum"]
    assert not seen["bad"].ok and "e2-e4" in seen["bad"].text
    assert "8 r n b q k b n r" in seen["update"] and "White to move" in seen["update"]
    assert env.props["chess_last"] == "e2-e4" and env.props["chess_turn"] == "black"


def test_stalemate_threefold_repetition_and_the_move_limit():
    stalemate = _with_setup(CHESS, "chess", "7k/5Q2/8/6K1/8/8/8/8")
    env, _ = _replay(stalemate, "chess", ["g5-g6"])
    assert env.ended_by == "stalemate" and env.props["chess_result"]["winner"] is None
    shuffle = ["g1-f3", "g8-f6", "f3-g1", "f6-g8"]
    env, _ = _replay(CHESS, "chess", shuffle * 2)
    assert env.ended_by == "repetition" and env.props["chess_ply"] == 8
    limited = copy.deepcopy(CHESS)
    limited["mechanisms"]["chess"]["move_limit"] = 4
    env, _ = _replay(limited, "chess", ["g1-f3", "g8-f6", "b1-c3", "b8-c6"])
    assert env.ended_by == "move_limit"
    env, _ = _replay(limited, "chess", ["g1-f3", "g8-f6", "e2-e4", "b8-c6", "b1-c3"])
    assert env.status != "ended"  # the pawn move reset the count


def test_a_random_chess_game_is_fast_and_resumes_exactly():
    started = time.perf_counter()
    result = fg_env.load(CHESS, seed=3).run(rounds=60)
    elapsed = time.perf_counter() - started
    assert result.status in ("running", "ended") and elapsed < 5, elapsed
    env = fg_env.load(CHESS, seed=3)
    env.run(rounds=30)
    if not env.finished:
        env = fg_env.Env.restore(CHESS, json.loads(json.dumps(env.snapshot())))
        env.run(rounds=30)
    assert env.result().to_dict() == result.to_dict()


# ---------------------------------------------------------------------------
# Go, Othello, Connect Four, Checkers
# ---------------------------------------------------------------------------

GO = _example("go_9x9.json")
KO = "9/9/9/3XO4/2XO1O3/3XO4/9/9/9"


def test_go_captures_forbids_suicide_and_retaking_a_ko_at_once():
    env, _ = _replay(_with_setup(GO, "go", KO), "go", ["e5"])
    board = _pieces(env)
    assert "d5" not in board and board["e5"] == ("black", "stone")
    assert env.props["go_report"] == "Black played e5, capturing 1." and env.props["go_ko"] == "d5"
    rules = _rules(GO["mechanisms"]["go"], "go")
    pos = _position(rules, KO)
    after = make(rules, pos, next(m for m in legal(rules, pos, 0) if m.text == "e5"), 0).pos
    assert rules.geo.names[after.ko] == "d5" and "d5" not in _texts(rules, after)
    suicide = _position(rules, "9/9/9/9/9/9/9/1O7/O1O6")
    assert "b1" not in _texts(rules, suicide) and "b1" in _texts(rules, suicide, 1)


def test_go_two_passes_end_with_area_scoring_and_komi():
    env = fg_env.load(_with_setup(GO, "go", "9/9/9/9/4X4/9/9/9/9", turn="white"), seed=1)

    def passer(wake):
        wake.call("go_pass", {})

    result = env.run(passer, rounds=3)
    assert env.ended_by == "passes"
    assert result.outputs["go_result"] == {"winner": "black", "reason": "passes", "score": {"black": 81, "white": 7.5}}


OTHELLO = _example("othello.json")


def test_othello_flips_passes_when_stuck_and_counts_discs():
    rules = _rules(OTHELLO["mechanisms"]["othello"], "othello")
    assert _texts(rules, _position(rules, OTHELLO["mechanisms"]["othello"]["setup"])) == ["c4", "d3", "e6", "f5"]
    env, _ = _replay(OTHELLO, "othello", ["d3"])
    assert sum(1 for owner, _ in _pieces(env).values() if owner == "black") == 4
    stuck = _with_setup(OTHELLO, "othello", "BW6/8/8/8/8/8/8/BW6")
    env, result = _replay(stuck, "othello", ["c8", "c1"])
    assert any("White has no legal move and passes" in e.get("text", "") for e in result.events)
    assert env.ended_by == "no_moves"
    assert env.props["othello_result"] == {"winner": "black", "reason": "no_moves", "score": {"black": 6, "white": 0}}


CONNECT = _example("connect_four.json")


def test_connect_four_drops_by_gravity_and_wins_with_four_in_a_row():
    env = fg_env.load(CONNECT, seed=1)
    seen = {}

    def first(wake):
        seen["moves"] = next(t for t in wake.tools if t.name == "connect4_move").input_schema["properties"]["move"]["enum"]
        wake.end()

    env.run(first, rounds=1)
    assert seen["moves"] == ["a1", "b1", "c1", "d1", "e1", "f1", "g1"]
    env, result = _replay(CONNECT, "connect4", ["d1", "e1", "d2", "e2", "d3", "e3", "d4"])
    assert env.ended_by == "line" and result.outputs["connect4_result"]["winner"] == "red"


CHECKERS = _example("checkers.json")


def test_checkers_mandatory_multi_jump_with_crowning():
    rules = _rules(CHECKERS["mechanisms"]["checkers"], "checkers")
    assert len(legal(rules, _position(rules, CHECKERS["mechanisms"]["checkers"]["setup"]), 0)) == 7
    position = "8/w5w1/8/4w3/8/2w5/1b6/6b1"
    assert _texts(rules, _position(rules, position)) == ["b2xd4"]
    env, _ = _replay(_with_setup(CHECKERS, "checkers", position), "checkers", ["b2xd4", "d4xf6", "f6xh8"], rounds=1)
    board = _pieces(env)
    assert board["h8"] == ("black", "K") and set(board) == {"h8", "a7", "g1"}
    assert env.props["checkers_turn"] == "white" and env.props["checkers_chain"] == ""


# ---------------------------------------------------------------------------
# Other capture rules, reserves, config errors, guide
# ---------------------------------------------------------------------------


def test_custodial_captures_and_drops_from_a_hand():
    tablut = {"name": "Sandwich", "clock": {"rounds": 4}, "mechanisms": {"b": {
        "kind": "game", "mode": "board", "size": 5, "sides": [{"id": "black", "mark": "B"}, {"id": "white", "mark": "W"}],
        "pieces": {"soldier": {"moves": [{"slide": "orthogonal", "only": "move"}]}},
        "setup": "5/B4/2W2/2B2/5", "captures": [{"rule": "custodial"}]}}}
    env, _ = _replay(tablut, "b", ["a4-c4"])
    assert "c3" not in _pieces(env) and env.props["b_report"] == "Black played a4-c4, capturing 1."
    drops = {"name": "Drops", "clock": {"rounds": 4}, "mechanisms": {"b": {
        "kind": "game", "mode": "board", "size": 3, "sides": ["x", "o"], "pieces": {"stone": {}}, "place": {"from": "hand"},
        "hand": {"x": {"stone": 1}, "o": {"stone": 1}}, "line": 3, "no_moves": "draw"}}}
    env, _ = _replay(drops, "b", ["b2", "a1"])
    assert env.props["b_hand"] == {"x": {"stone": 0}, "o": {"stone": 0}}
    assert env.ended_by == "no_moves" and env.props["b_result"]["winner"] is None


def test_config_errors_say_what_to_fix():
    def issues(**changes):
        contract = copy.deepcopy(CHESS)
        contract["mechanisms"]["chess"].update(changes)
        return " | ".join(str(i) for i in fg_env.check(contract))

    assert "'diagonals' is not a direction" in issues(pieces={"B": {"moves": [{"slide": "diagonals"}]}})
    assert "'Z' is not a board symbol" in issues(setup="rnbqkbnZ/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")
    assert "'X' is not a piece kind" in issues(pieces={"P": {"promote": {"to": ["X"]}}})
    assert "`colour` is not a field of `game` mode `board`" in issues(colour="red")
    both = {"name": "Two", "mechanisms": {"a": {"kind": "game", "mode": "board", "sides": ["x"], "pieces": {"s": {}}},
                                          "b": {"kind": "game", "mode": "board", "sides": ["y"], "pieces": {"s": {}}}}}
    with pytest.raises(ContractError, match="give each board its own piece_type"):
        fg_env.parse(both)
    wrong_op = {**CHESS, "events": [{"do": [{"game": "nope", "action": "pass"}]}]}
    assert any("`game` names a declared game mechanism, got 'nope'" in i.message for i in fg_env.check(wrong_op))


def _board_issues(contract):
    return [i for i in fg_env.check(contract) if i.severity == "error"]


def test_an_old_board_kind_names_the_game_family_and_a_typo_names_the_field():
    old = copy.deepcopy(CHESS)
    old["mechanisms"]["chess"] = {k: v for k, v in old["mechanisms"]["chess"].items() if k != "mode"}
    old["mechanisms"]["chess"]["kind"] = "board"
    issue = next(i for i in _board_issues(old) if i.path == "mechanisms.chess.kind")
    assert issue.message == "'board' is a mode of kind 'game'"
    typo = copy.deepcopy(CHESS)
    typo["mechanisms"]["chess"]["players"] = "player"
    issue = _board_issues(typo)[0]
    assert issue.message == "`players` is not a field of `game` mode `board`" and issue.path == "mechanisms.chess.players"


def test_board_actions_check_their_own_keys():
    def op(*effects):
        return [(i.path, i.message, i.fix) for i in _board_issues({**CHESS, "events": [{"do": list(effects)}]})]

    assert any(m == "`game.setup` needs `position`" for _, m, _ in op({"game": "chess", "action": "setup"}))
    assert any(m == "'text' is not part of `game.pass`" for _, m, _ in op({"game": "chess", "action": "pass", "text": "e4"}))
    path, message, fix = op({"game": "chess", "action": "mvoe", "text": "e2-e4"})[0]
    assert path.endswith(".action") and message == "'mvoe' is not an action of chess (game board)" and fix == "did you mean 'move'?"
    _, _, fix = op({"pass": "chess"})[0]
    assert fix.startswith('`pass` is an action of the `game` or `flow` op: {"game": "<mechanism>", "action": "pass"')


def test_tools_one_offers_moving_and_passing_as_one_tool():
    contract = _with_setup(GO, "go", "9/9/9/9/4X4/9/9/9/9", turn="white", tools="one")
    env = fg_env.load(contract, seed=1)
    offered = []

    def passer(wake):
        tools = {t.name: t for t in wake.tools if t.kind == "act"}
        offered.append(sorted(tools))
        assert wake.call("go", {"action": "pass"}).ok

    env.run(passer, rounds=3)
    assert offered[0] == ["go"] and env.ended_by == "passes"


def test_a_board_fills_the_game_section_so_the_winner_scores_against_the_loser():
    game = fg_env.parse(CHESS).game
    assert (game.players, game.utility) == ("player", "zero_sum")
    assert _board_issues(CHESS) == []
    _, result = _replay(CHESS, "chess", ["e2-e4", "e7-e5", "f1-c4", "b8-c6", "d1-h5", "g8-f6", "h5xf7"])
    assert result.returns == {"white": 1.0, "black": -1.0}
    _, drawn = _replay(_with_setup(CHESS, "chess", "7k/5Q2/8/6K1/8/8/8/8"), "chess", ["g5-g6"])
    assert drawn.returns == {"white": 0.0, "black": 0.0}
    three = {"name": "Three", "clock": {"rounds": 9}, "mechanisms": {"b": {
        "kind": "game", "mode": "board", "size": 3, "sides": ["x", "o", "z"], "pieces": {"stone": {}}, "place": {},
        "line": 3, "no_moves": "draw"}}}
    _, result = _replay(three, "b", ["a1", "a2", "b3", "b1", "b2", "c3", "c1"])
    assert result.returns == {"x": 2.0, "o": -1.0, "z": -1.0}


def test_an_authors_game_section_or_a_second_scoring_mechanism_leaves_the_game_section_alone():
    authored = fg_env.parse({**CHESS, "game": {"returns": "$actor.id == 'white'"}}).game
    assert authored.returns == "$actor.id == 'white'" and authored.utility == "general_sum" and authored.players is None
    two = copy.deepcopy(CHESS)
    two["mechanisms"]["other"] = {"kind": "game", "mode": "board", "size": 3, "sides": ["red", "blue"], "piece_type": "stone",
                                  "pieces": {"mark": {}}, "place": {}, "line": 3, "stage": "chess"}
    assert fg_env.parse(two).game is None


def test_guide_documents_the_board_grammar():
    page = fg_env.guide("game.board")
    assert page.startswith("### `game.board`") and "`castling`" in page and "- `setup`" in page and "- `move`" in page
    assert "$board_moves(" in fg_env.guide("game") and "$board_moves(" in fg_env.guide("all")
