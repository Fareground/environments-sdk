"""Constraint puzzles: seeded generation with exactly one solution, solution counting and checking attempts."""
from pathlib import Path

import pytest

import fg_env
from fg_env.expr import ExprError, compile_expr, shared_budget

WORLD = {"name": "Puzzles", "clock": {"rounds": 1}, "types": {"solver": {"agent": True}}}


def _eval(source, seed=1, **roots):
    env = fg_env.load(WORLD, seed=seed)
    return compile_expr(source)(env.world.scope(**roots))


KNUTH = "{sets: {A: [1, 4, 7], B: [1, 4], C: [4, 5, 7], D: [3, 5, 6], E: [2, 3, 6, 7], F: [2, 7]}}"


def test_a_generated_sudoku_has_exactly_one_solution_and_it_is_the_one_given():
    game = _eval("$puzzle(sudoku)")
    assert len(game["puzzle"]) == 9 and all(len(row) == 9 for row in game["puzzle"])
    assert game["clues"] == sum(1 for row in game["puzzle"] for cell in row if cell) and game["clues"] < 40
    solved = _eval("$solve(sudoku, $puzzle)", puzzle=game["puzzle"])
    assert solved["solutions"] == 1 and solved["unique"] and solved["solution"] == game["solution"]
    assert all(game["puzzle"][r][c] in (0, game["solution"][r][c]) for r in range(9) for c in range(9))
    assert _eval("$solve(sudoku, $grid, check)", grid=game["solution"]) == {"valid": True, "complete": True, "solved": True,
                                                                             "conflicts": []}


def test_generation_follows_the_seed_and_respects_the_clue_floor():
    assert _eval("$puzzle(sudoku, {box: 2})", seed=5) == _eval("$puzzle(sudoku, {box: 2})", seed=5)
    assert len({str(_eval("$puzzle(sudoku)", seed=s)["solution"]) for s in range(3)}) == 3
    roomy = _eval("$puzzle(sudoku, {clues: 45})", seed=2)
    assert roomy["clues"] == 45 and _eval("$solve(sudoku, $p)", p=roomy["puzzle"])["unique"]


def test_counting_stops_at_two_and_contradictory_clues_have_none():
    empty = [[0] * 4 for _ in range(4)]
    assert _eval("$solve(sudoku, $g)", g=empty)["solutions"] == 2
    clash = [[1, 1, 0, 0], [0] * 4, [0] * 4, [None] * 4]
    assert _eval("$solve(sudoku, $g)", g=clash) == {"solutions": 0, "unique": False, "solution": None}
    check = _eval("$solve(sudoku, $g, check)", g=clash)
    assert check == {"valid": False, "complete": False, "solved": False, "conflicts": [[0, 0], [0, 1]]}


def test_exact_cover_finds_knuths_unique_cover_and_checks_a_choice():
    solved = _eval(f"$solve(exact_cover, {KNUTH})")
    assert solved == {"solutions": 1, "unique": True, "solution": ["B", "D", "F"]}
    loose = _eval("$solve(exact_cover, {sets: {a: [x], b: [x], c: [y]}})")
    assert loose["solutions"] == 2 and loose["solution"] == ["a", "c"]
    assert _eval("$solve(exact_cover, {sets: {a: [x]}, universe: [x, y]})")["solutions"] == 0
    check = _eval("$solve(exact_cover, {sets: {A: [1, 4, 7], D: [3, 5, 6], E: [2, 3, 6, 7]}, chosen: [A, D, E]}, check)")
    assert check == {"valid": False, "complete": True, "solved": False, "overlaps": [7, 3, 6], "missing": []}


@pytest.mark.parametrize("source, message", [
    ("$puzzle(crossword)", "kind must be one of sudoku, exact_cover"),
    ("$puzzle(sudoku, {box: 4})", "box must be 2 .4×4. or 3 .9×9."),
    ("$puzzle(sudoku, {size: 9})", "options are box and clues"),
    ("$puzzle(exact_cover)", "not generated"),
    ("$solve(sudoku, [[1, 2], [3, 4]])", "4, 9 or 16 rows"),
    ("$solve(sudoku, [[0, 0, 0, 5], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]])", "whole number 0–4"),
    ("$solve(sudoku, [[0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]], guess)", "mode must be one of count, check"),
    ("$solve(exact_cover, {sets: {a: [x]}, universe: [y]})", "outside the universe"),
    ("$solve(exact_cover, {sets: {a: [x]}, chosen: [b]}, check)", "sets that do not exist"),
])
def test_puzzle_mistakes_say_what_to_pass(source, message):
    with pytest.raises(ExprError, match=message):
        _eval(source)


def test_sudoku_relay_example_ends_when_a_perfect_solver_fills_the_generated_grid():
    path = Path(__file__).parents[1] / "examples" / "contracts" / "sudoku_relay.json"
    env = fg_env.load(path, seed=3)

    def solver(wake):
        board, solution = env.props["board"], env.props["game"]["solution"]
        empty = next((r, c) for r, row in enumerate(board) for c, cell in enumerate(row) if not cell)
        assert wake.call("place", {"row": empty[0], "col": empty[1], "digit": solution[empty[0]][empty[1]]}).ok

    result = env.run(solver)
    assert result.status == "ended" and result.ended_by == "solved", result.error
    assert result.outputs["filled"] == 16 and result.outputs["clues"] >= 6


def test_a_search_larger_than_the_work_budget_stops_with_an_error():
    env = fg_env.load(WORLD, seed=1)
    with pytest.raises(ExprError, match="work budget"):
        with shared_budget(2_000, "puzzle"):
            compile_expr("$puzzle(sudoku)")(env.world.scope())
