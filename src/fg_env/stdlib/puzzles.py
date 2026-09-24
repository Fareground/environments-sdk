"""Constraint puzzles: generate them from the run's seed with exactly one solution, count solutions, check attempts.

``$puzzle(kind, options?)`` makes a puzzle; ``$solve(kind, problem, mode?)`` counts its solutions (up to two:
none, exactly one, or more) and gives the first, or checks an attempt. Kinds:

* ``sudoku`` — a grid of ``box²`` rows (4×4, 9×9 or 16×16), 0 or null for a blank. Generating fills a
  random complete grid, then removes clues in random order, keeping each removal only while the
  puzzle still has exactly one solution.
* ``exact_cover`` — ``{sets: {name: [elements]}, universe?: [elements]}``: choose sets that cover every
  element of the universe (default: every element named) exactly once.

Every search step is charged to the expression work budget, so a hard puzzle stops with a clear error
instead of stalling the run. All randomness comes from the run's seeded generator.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..expr import Call, _describe, _entity_id, charge, function
from ._args import fail, map_arg, text_arg

__all__ = ["sudoku_search", "exact_cover_search"]

KINDS = ("sudoku", "exact_cover")
MODES = ("count", "check")
#: Largest sudoku box (16×16 grids); generation stops at 9×9, where clue removal stays fast.
MAX_BOX = 4
MAX_GENERATED_BOX = 3
#: Most sets and elements an exact cover problem may name.
MAX_COVER = 5_000
Charge = Callable[[int], None]


# ---------------------------------------------------------------------------
# Sudoku
# ---------------------------------------------------------------------------


def _popcount(mask: int) -> int:
    return bin(mask).count("1")


def sudoku_search(cells: list[int], box: int, limit: int, spend: Charge,
                  rng: Any = None) -> tuple[int, list[int] | None]:
    """Solutions of a flat grid (0 = blank), counted up to ``limit``, and the first one found.

    Candidates are tried most-constrained cell first; with ``rng`` each cell's digits are tried in random order."""
    n = box * box
    rows, cols, boxes = [0] * n, [0] * n, [0] * n
    grid = list(cells)
    for index, value in enumerate(grid):
        if value:
            row, col = divmod(index, n)
            bit, square = 1 << (value - 1), (row // box) * box + col // box
            if (rows[row] | cols[col] | boxes[square]) & bit:
                return 0, None
            rows[row] |= bit
            cols[col] |= bit
            boxes[square] |= bit
    blanks = [index for index, value in enumerate(grid) if not value]
    full = (1 << n) - 1
    found: list[Any] = [0, None]

    def search() -> None:
        spend(len(blanks))
        best, best_mask, best_count = -1, 0, n + 1
        for index in blanks:
            if grid[index]:
                continue
            row, col = divmod(index, n)
            mask = full & ~(rows[row] | cols[col] | boxes[(row // box) * box + col // box])
            count = _popcount(mask)
            if count < best_count:
                best, best_mask, best_count = index, mask, count
                if count <= 1:
                    break
        if best < 0:
            found[0] += 1
            if found[1] is None:
                found[1] = list(grid)
            return
        digits = [d for d in range(n) if best_mask >> d & 1]
        if rng is not None:
            rng.shuffle(digits)
        row, col = divmod(best, n)
        square = (row // box) * box + col // box
        for digit in digits:
            bit = 1 << digit
            grid[best] = digit + 1
            rows[row] |= bit
            cols[col] |= bit
            boxes[square] |= bit
            search()
            rows[row] &= ~bit
            cols[col] &= ~bit
            boxes[square] &= ~bit
            grid[best] = 0
            if found[0] >= limit:
                return

    search()
    return found[0], found[1]


def _grid(call: Call, value: Any, what: str) -> tuple[list[int], int]:
    """A sudoku grid as flat cells and its box size."""
    if not isinstance(value, list) or not value:
        raise fail(call, f"{what} must be a list of rows, got {_describe(value)}")
    n = len(value)
    box = int(round(n ** 0.5))
    if box * box != n or not 2 <= box <= MAX_BOX:
        raise fail(call, f"{what} must have 4, 9 or 16 rows, got {n}")
    cells: list[int] = []
    for r, row in enumerate(value):
        if not isinstance(row, list) or len(row) != n:
            raise fail(call, f"{what} row {r} must be a list of {n} cells, got {_describe(row)}")
        for c, cell in enumerate(row):
            cell = 0 if cell is None else cell
            if isinstance(cell, bool) or not isinstance(cell, (int, float)) or cell != int(cell) or not 0 <= cell <= n:
                raise fail(call,
                           f"{what} cell [{r}, {c}] must be a whole number 0–{n} (0 or null for a blank), got "
                           f"{_describe(cell)}")
            cells.append(int(cell))
    charge(len(cells), call.source)
    return cells, box


def _rows(cells: list[int], n: int) -> list[list[int]]:
    return [cells[i:i + n] for i in range(0, n * n, n)]


def _sudoku_puzzle(call: Call, options: dict[Any, Any]) -> dict[str, Any]:
    unknown = sorted(set(options) - {"box", "clues"})
    if unknown:
        raise fail(call, f"sudoku options are box and clues, not {', '.join(map(str, unknown))}")
    box = options.get("box", 3)
    if isinstance(box, bool) or box not in range(2, MAX_GENERATED_BOX + 1):
        raise fail(call, f"sudoku box must be 2 (4×4) or 3 (9×9), got {_describe(box)}")
    n = box * box
    target = options.get("clues", 0)
    if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= n * n:
        raise fail(call, f"sudoku clues must be a whole number 0–{n * n} (the fewest to keep; 0 = as few as stay "
                         f"unique), got {_describe(target)}")
    spend = _spender(call)
    _, solution = sudoku_search([0] * (n * n), box, 1, spend, call.rng)
    assert solution is not None
    puzzle = list(solution)
    order = list(range(n * n))
    call.rng.shuffle(order)
    clues = n * n
    for index in order:
        if clues <= target:
            break
        kept, puzzle[index] = puzzle[index], 0
        if sudoku_search(puzzle, box, 2, spend)[0] == 1:
            clues -= 1
        else:
            puzzle[index] = kept
    return {"puzzle": _rows(puzzle, n), "solution": _rows(solution, n), "clues": clues, "box": box}


def _sudoku_check(cells: list[int], box: int) -> dict[str, Any]:
    n = box * box
    conflicts = []
    for index, value in enumerate(cells):
        if not value:
            continue
        row, col = divmod(index, n)
        for other in range(n * n):
            if other != index and cells[other] == value:
                r, c = divmod(other, n)
                if r == row or c == col or (r // box, c // box) == (row // box, col // box):
                    conflicts.append([row, col])
                    break
    complete = all(cells)
    return {"valid": not conflicts, "complete": complete, "solved": complete and not conflicts, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# Exact cover
# ---------------------------------------------------------------------------


def exact_cover_search(universe: list[Any], sets: dict[str, list[Any]], limit: int,
                       spend: Charge) -> tuple[int, list[str] | None]:
    """Exact covers counted up to ``limit`` and the first found (set names in declaration order): Knuth's Algorithm X.
    """
    order = {name: position for position, name in enumerate(sets)}
    columns: dict[Any, set] = {element: set() for element in universe}
    for name, elements in sets.items():
        for element in elements:
            columns[element].add(name)
    found: list[Any] = [0, None]
    chosen: list[str] = []

    def select(name: str) -> list[set]:
        removed = []
        for element in sets[name]:
            for other in columns[element]:
                for touched in sets[other]:
                    if touched != element:
                        columns[touched].discard(other)
            removed.append(columns.pop(element))
        return removed

    def deselect(name: str, removed: list[set]) -> None:
        for element in reversed(sets[name]):
            columns[element] = removed.pop()
            for other in columns[element]:
                for touched in sets[other]:
                    if touched != element:
                        columns[touched].add(other)

    def search() -> None:
        spend(len(columns) + 1)
        if not columns:
            found[0] += 1
            if found[1] is None:
                found[1] = sorted(chosen, key=order.__getitem__)
            return
        element = min(columns, key=lambda e: len(columns[e]))
        for name in sorted(columns[element], key=order.__getitem__):
            chosen.append(name)
            removed = select(name)
            search()
            deselect(name, removed)
            chosen.pop()
            if found[0] >= limit:
                return

    search()
    return found[0], found[1]


def _cover(call: Call, problem: dict[Any, Any]) -> tuple[list[Any], dict[str, list[Any]]]:
    unknown = sorted(set(problem) - {"sets", "universe", "chosen"})
    if unknown:
        raise fail(call, f"an exact cover problem has sets, universe and chosen, not {', '.join(map(str, unknown))}")
    raw_sets = problem.get("sets")
    if not isinstance(raw_sets, dict) or not raw_sets:
        raise fail(call, f"sets must be a map like {{a: [1, 2], b: [3]}}, got {_describe(raw_sets)}")
    if len(raw_sets) > MAX_COVER:
        raise fail(call, f"{len(raw_sets):,} sets; an exact cover problem takes at most {MAX_COVER:,}")
    sets: dict[str, list[Any]] = {}
    for name, elements in raw_sets.items():
        if not isinstance(elements, list):
            raise fail(call, f"set {name!r} must be a list of elements, got {_describe(elements)}")
        members = list(dict.fromkeys(_element(call, e) for e in elements))
        charge(len(members) + 1, call.source)
        sets[str(name)] = members
    named = list(dict.fromkeys(e for members in sets.values() for e in members))
    universe = (named if problem.get("universe") is None
                else list(dict.fromkeys(_element(call, e) for e in problem["universe"])))
    if len(universe) > MAX_COVER:
        raise fail(call, f"{len(universe):,} elements; an exact cover problem takes at most {MAX_COVER:,}")
    allowed = set(universe)
    outside = [e for e in named if e not in allowed]
    if outside:
        raise fail(call, f"sets name elements outside the universe: {', '.join(map(str, outside[:5]))}")
    return universe, sets


def _element(call: Call, value: Any) -> Any:
    value = _entity_id(value)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise fail(call, f"elements must be text, whole numbers or entities, got {_describe(value)}")
    return value


def _cover_check(call: Call, universe: list[Any], sets: dict[str, list[Any]], chosen: Any) -> dict[str, Any]:
    if not isinstance(chosen, list):
        raise fail(call, f"chosen must be a list of set names, got {_describe(chosen)}")
    unknown = [name for name in chosen if name not in sets]
    if unknown:
        raise fail(call, f"chosen names sets that do not exist: {', '.join(map(str, unknown[:5]))}")
    covered: dict[Any, int] = {}
    for name in chosen:
        for element in sets[name]:
            covered[element] = covered.get(element, 0) + 1
    twice = [e for e, times in covered.items() if times > 1]
    missing = [e for e in universe if e not in covered]
    return {"valid": not twice and len(set(chosen)) == len(chosen), "complete": not missing,
            "solved": not twice and not missing and len(set(chosen)) == len(chosen), "overlaps": twice,
            "missing": missing}


# ---------------------------------------------------------------------------
# The functions
# ---------------------------------------------------------------------------


def _spender(call: Call) -> Charge:
    return lambda amount: charge(amount, call.source)


def _kind(call: Call) -> str:
    kind = text_arg(call, 0, "a puzzle kind")
    if kind not in KINDS:
        raise fail(call, f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    return kind


@function("puzzle(kind, options?)",
          "A new puzzle with exactly one solution, drawn from the run's seed. sudoku: options "
          "{box: 2 or 3 (default), clues: the fewest clues to keep (default 0: remove all it can)} → "
          "{puzzle, solution, clues, box}; blanks are 0.",
          min_args=1, max_args=2)
def _puzzle(call: Call) -> dict[str, Any]:
    kind = _kind(call)
    if kind != "sudoku":
        raise fail(call, f"{kind} puzzles are not generated; describe one and check it with $solve({kind}, ...)")
    options = map_arg(call, 1) if len(call) > 1 and call.arg(1) is not None else {}
    return _sudoku_puzzle(call, options)


@function("solve(kind, problem, mode?)",
          "Solve or check a puzzle. mode count (default): {solutions: 0, 1 or 2 (two or more), unique, solution}. "
          "mode check (an attempt): sudoku → {valid, complete, solved, conflicts: [[row, col]]}; exact_cover "
          "(problem.chosen: set names) → {valid, complete, solved, overlaps, missing}. sudoku problem: rows with 0 "
          "or null for blanks; exact_cover problem: {sets: {name: [elements]}, universe?, chosen?}.",
          min_args=2, max_args=3)
def _solve(call: Call) -> dict[str, Any]:
    kind = _kind(call)
    mode = text_arg(call, 2, "a mode") if len(call) > 2 and call.arg(2) is not None else "count"
    if mode not in MODES:
        raise fail(call, f"mode must be one of {', '.join(MODES)}, got {mode!r}")
    if kind == "sudoku":
        cells, box = _grid(call, call.arg(1), "the grid")
        if mode == "check":
            charge(len(cells) * len(cells), call.source)
            return _sudoku_check(cells, box)
        count, first = sudoku_search(cells, box, 2, _spender(call))
        return {"solutions": count, "unique": count == 1, "solution": _rows(first, box * box) if first else None}
    universe, sets = _cover(call, map_arg(call, 1))
    if mode == "check":
        return _cover_check(call, universe, sets, map_arg(call, 1).get("chosen"))
    count, names = exact_cover_search(universe, sets, 2, _spender(call))
    return {"solutions": count, "unique": count == 1, "solution": names}
