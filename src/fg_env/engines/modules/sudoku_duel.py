"""Sudoku Duel — sealed-tick competitive Sudoku.

Two players race to FULLY solve the SAME puzzle. Each round both
submit ONE (row, col, value) sealed. Reveal happens simultaneously.
Correct value (matches the puzzle's solution) → cell fills on that
player's grid. Wrong value → strike. A player wins by completing
their grid; loses by hitting `max_strikes` first. Match wraps the
inner game as best-of-N (sudden-death extension on ties).

Public events
~~~~~~~~~~~~~
  - `sd_init`        { grid_size, box_rows, box_cols, max_strikes,
                       best_of, wins_to_clinch, puzzle, match_score,
                       game_number }                                 one-shot
  - `sd_pending`     { player, side, row, col, value, round }       guess accepted
  - `sd_round`       { round, game_number, match_score,
                       white_fill, white_correct, white_strikes,
                       white_filled_count, white_solved, white_eliminated,
                       black_fill, black_correct, black_strikes,
                       black_filled_count, black_solved, black_eliminated } both submitted; reveal
  - `sd_game_end`    { game_number, solution, winner, reason,
                       match_score, rounds_used }                    per-game terminal
  - `sd_next_game`   { game_number, match_score, sudden_death,
                       grid_size, box_rows, box_cols, puzzle }      reset
  - `sd_invalid`     { actor_id, reason, message }                   bad submit (defensive)
  - `sd_win`         { winner, loser, match_score, best_of }         match terminal

Engine-level chat suppression is on — same model as Wordle / Hangman
Duel. Opponents never see each other's reasoning or speech.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


DEFAULT_GRID_SIZE = 6
ALLOWED_GRID_SIZES: Dict[int, Tuple[int, int]] = {
    4: (2, 2),   # 2-row, 2-col boxes
    6: (2, 3),   # 2-row, 3-col boxes
    9: (3, 3),   # classic
}

DEFAULT_MAX_STRIKES = 6
MIN_MAX_STRIKES = 4
MAX_MAX_STRIKES = 12

DEFAULT_WINS_NEEDED = 2
MIN_WINS_NEEDED = 1
MAX_WINS_NEEDED = 5

# Fraction of cells removed from the solved grid to make the puzzle,
# keyed by (grid_size, difficulty). Higher fraction = more empty cells
# the player has to deduce. Tuned so:
#   - "easy" puzzles are LLM-tractable in a few rounds
#   - "hard" puzzles force constraint propagation past naked-singles
#   - "expert" approaches the unique-solution minimum (17 for 9×9)
# We intentionally bias toward the harder end of each size — the
# previous defaults (≤ 50% removal) let LLMs blow through 6×6 with
# zero strikes.
DIFFICULTY_REMOVAL: Dict[str, Dict[int, float]] = {
    "easy":   {4: 0.40, 6: 0.50, 9: 0.55},
    "medium": {4: 0.55, 6: 0.65, 9: 0.65},
    "hard":   {4: 0.65, 6: 0.75, 9: 0.72},
    "expert": {4: 0.75, 6: 0.83, 9: 0.78},
}
DEFAULT_DIFFICULTY = "hard"
ALLOWED_DIFFICULTIES = tuple(DIFFICULTY_REMOVAL.keys())

# Legacy compatibility alias.
PUZZLE_REMOVAL_FRACTION: Dict[int, float] = DIFFICULTY_REMOVAL[DEFAULT_DIFFICULTY]


# ---------------------------------------------------------------------------
# Puzzle generation
#
# We start from a canonical solved grid for each supported size, apply a
# sequence of permutations that preserve sudoku validity (relabel
# digits, swap rows within a band, swap cols within a stack, swap
# bands, swap stacks), then mask a fraction of cells. Result: a fresh,
# valid puzzle with a known solution drawn from the same RNG used by
# the rest of the module.
# ---------------------------------------------------------------------------


def _canonical_grid(size: int) -> List[List[int]]:
    """Build a canonical solved sudoku grid for the given size.

    Uses the shift-by-box-row pattern: row r in band b is the baseline
    shifted by (b + r * box_cols) mod size.
    """
    box_rows, box_cols = ALLOWED_GRID_SIZES[size]
    grid: List[List[int]] = []
    for r in range(size):
        band = r // box_rows
        row_in_band = r % box_rows
        shift = band + row_in_band * box_cols
        row = [((c + shift) % size) + 1 for c in range(size)]
        grid.append(row)
    return grid


def _permute_grid(grid: List[List[int]], rng: random.Random) -> List[List[int]]:
    """Apply random sudoku-preserving permutations: relabel digits,
    swap rows within bands, swap cols within stacks, swap bands, swap
    stacks. Returns a fresh grid (input is untouched)."""
    size = len(grid)
    box_rows, box_cols = ALLOWED_GRID_SIZES[size]
    n_bands = size // box_rows
    n_stacks = size // box_cols

    out = [row[:] for row in grid]

    # 1. Relabel digits — a random permutation of 1..size.
    digit_map = list(range(1, size + 1))
    rng.shuffle(digit_map)
    out = [[digit_map[v - 1] for v in row] for row in out]

    # 2. Swap rows within each band.
    for band in range(n_bands):
        rows = list(range(band * box_rows, (band + 1) * box_rows))
        rng.shuffle(rows)
        new_band = [out[r] for r in rows]
        for i, new_row in enumerate(new_band):
            out[band * box_rows + i] = new_row

    # 3. Swap cols within each stack.
    for stack in range(n_stacks):
        cols = list(range(stack * box_cols, (stack + 1) * box_cols))
        new_order = cols[:]
        rng.shuffle(new_order)
        if new_order != cols:
            mapping = dict(zip(cols, new_order))
            for r in range(size):
                src_row = out[r][:]
                for c in cols:
                    out[r][c] = src_row[mapping[c]]

    # 4. Swap bands of rows.
    band_order = list(range(n_bands))
    rng.shuffle(band_order)
    if band_order != list(range(n_bands)):
        new_grid: List[List[int]] = []
        for b in band_order:
            for i in range(box_rows):
                new_grid.append(out[b * box_rows + i])
        out = new_grid

    # 5. Swap stacks of cols.
    stack_order = list(range(n_stacks))
    rng.shuffle(stack_order)
    if stack_order != list(range(n_stacks)):
        new_grid = []
        for r in range(size):
            new_row: List[int] = []
            for s in stack_order:
                new_row.extend(out[r][s * box_cols:(s + 1) * box_cols])
            new_grid.append(new_row)
        out = new_grid

    return out


def _make_puzzle(solution: List[List[int]], rng: random.Random,
                 removal_fraction: float) -> List[List[int]]:
    """Return the puzzle (givens + 0 for empty cells) by removing
    a randomized fraction of the solution's cells."""
    size = len(solution)
    n_cells = size * size
    n_remove = int(n_cells * removal_fraction)
    coords = [(r, c) for r in range(size) for c in range(size)]
    rng.shuffle(coords)
    puzzle = [row[:] for row in solution]
    for r, c in coords[:n_remove]:
        puzzle[r][c] = 0
    return puzzle


def generate_puzzle(size: int, rng: random.Random,
                    difficulty: str = DEFAULT_DIFFICULTY
                    ) -> Tuple[List[List[int]], List[List[int]]]:
    """Generate (puzzle, solution) for the requested grid size. Both
    are 2D lists indexed [row][col]. The puzzle uses 0 for empty
    cells. `difficulty` selects how many cells to remove (one of
    'easy' / 'medium' / 'hard' / 'expert')."""
    if size not in ALLOWED_GRID_SIZES:
        size = DEFAULT_GRID_SIZE
    if difficulty not in DIFFICULTY_REMOVAL:
        difficulty = DEFAULT_DIFFICULTY
    solution = _permute_grid(_canonical_grid(size), rng)
    puzzle = _make_puzzle(
        solution, rng, DIFFICULTY_REMOVAL[difficulty].get(size, 0.6),
    )
    return puzzle, solution


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_int(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        if isinstance(raw, bool):
            return None
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return None


def _render_grid_ascii(grid: List[List[int]], box_rows: int,
                       box_cols: int) -> str:
    """Format the grid as a human-readable ASCII grid with box
    separators. Empty cells render as `.`."""
    size = len(grid)
    lines: List[str] = []
    # Column header (1-indexed).
    header = "    " + " ".join(
        (f" {c+1}" if c % box_cols == 0 and c > 0 else f"{c+1}")
        if (c + 1) <= 9 else "?" for c in range(size)
    )
    lines.append(header)
    for r in range(size):
        parts: List[str] = []
        for c in range(size):
            if c > 0 and c % box_cols == 0:
                parts.append("|")
            v = grid[r][c]
            parts.append(str(v) if v != 0 else ".")
        row_str = " ".join(parts)
        prefix = f"  {r+1} "
        if r > 0 and r % box_rows == 0:
            sep_units = len(row_str)
            lines.append("    " + "-" * sep_units)
        lines.append(prefix + row_str)
    return "\n".join(lines)


def _empty_cells(grid: List[List[int]]) -> List[Tuple[int, int]]:
    return [(r, c) for r in range(len(grid))
            for c in range(len(grid)) if grid[r][c] == 0]


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class SudokuDuelModule(DomainModule):
    """Sealed-tick competitive Sudoku."""

    suppress_chat = True

    def __init__(self, name: str = "sudoku_duel",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}

        gs = int(p.get("grid_size") or DEFAULT_GRID_SIZE)
        if gs not in ALLOWED_GRID_SIZES:
            gs = DEFAULT_GRID_SIZE
        self._grid_size: int = gs
        self._box_rows, self._box_cols = ALLOWED_GRID_SIZES[gs]

        diff = str(p.get("difficulty") or DEFAULT_DIFFICULTY).lower().strip()
        if diff not in DIFFICULTY_REMOVAL:
            diff = DEFAULT_DIFFICULTY
        self._difficulty: str = diff

        ms = int(p.get("max_strikes") or DEFAULT_MAX_STRIKES)
        self._max_strikes: int = max(MIN_MAX_STRIKES, min(MAX_MAX_STRIKES, ms))

        wn = int(p.get("wins_needed") or DEFAULT_WINS_NEEDED)
        wn = max(MIN_WINS_NEEDED, min(MAX_WINS_NEEDED, wn))
        self._wins_to_clinch: int = wn
        self._best_of: int = wn * 2 - 1

        seed = p.get("rng_seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()

        self._puzzle, self._solution = generate_puzzle(self._grid_size, self._rng, self._difficulty)
        # Cells that are pre-filled (givens) — frozen for the game.
        self._givens: Set[Tuple[int, int]] = {
            (r, c) for r in range(self._grid_size) for c in range(self._grid_size)
            if self._puzzle[r][c] != 0
        }
        self._target_empty_count: int = (
            self._grid_size * self._grid_size - len(self._givens)
        )

        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None

        # Match-level score.
        self._white_games: int = 0
        self._black_games: int = 0
        self._game_number: int = 1
        self._games_log: List[Dict[str, Any]] = []

        # Per-game state (resets between games).
        self._white_filled: Dict[Tuple[int, int], int] = {}
        self._black_filled: Dict[Tuple[int, int], int] = {}
        self._white_wrong: int = 0
        self._black_wrong: int = 0
        self._white_attempted: Set[Tuple[int, int, int]] = set()
        self._black_attempted: Set[Tuple[int, int, int]] = set()
        # Sealed pending fills.
        self._pending_white: Optional[Tuple[int, int, int]] = None
        self._pending_black: Optional[Tuple[int, int, int]] = None
        self._round_number: int = 1

        self._terminal: Optional[Dict[str, Any]] = None
        self._initialized = False
        logger.info(
            "[sd] init grid=%dx%d max_strikes=%d wins_needed=%d empty=%d",
            self._grid_size, self._grid_size, self._max_strikes,
            self._wins_to_clinch, self._target_empty_count,
        )

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return (
            f"Sudoku Duel — race the same {self._grid_size}×{self._grid_size} "
            f"puzzle, {self._max_strikes} strikes max. Sealed-tick rounds: "
            "both submit one (row, col, value), both reveal simultaneously."
        )

    @property
    def custom_actions(self) -> List[str]:
        return ["fill_cell"]

    @property
    def required_properties(self) -> List[str]:
        return ["color"]

    # ------------------------------------------------------------------ #
    # Seat assignment
    # ------------------------------------------------------------------ #

    def _seed_from_state(self, state: Any) -> None:
        if self._initialized:
            return
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        if len(agents) < 2:
            return
        self._white_id = agents[0].id
        self._black_id = agents[1].id
        for ent, color in ((agents[0], "white"), (agents[1], "black")):
            if hasattr(ent, "properties"):
                ent.properties["color"] = color
                ent.properties["strikes"] = 0
                ent.properties["cells_filled"] = 0
                ent.properties["solved"] = False
        self._initialized = True
        logger.info(
            "[sd] seated white=%s black=%s grid=%dx%d",
            agents[0].name, agents[1].name,
            self._grid_size, self._grid_size,
        )

    # ------------------------------------------------------------------ #
    # tick — emit init event once
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        was_seeded = self._initialized
        self._seed_from_state(state)
        if not was_seeded and self._initialized:
            bo_text = (f"best-of-{self._best_of}" if self._best_of > 1
                       else "single game")
            return [{
                "type": "sd_init",
                "grid_size": self._grid_size,
                "box_rows": self._box_rows,
                "box_cols": self._box_cols,
                "max_strikes": self._max_strikes,
                "best_of": self._best_of,
                "wins_to_clinch": self._wins_to_clinch,
                "puzzle": [row[:] for row in self._puzzle],
                "match_score": {"white": 0, "black": 0},
                "game_number": 1,
                "narrative": (
                    f"Sudoku Duel — {bo_text}, both players race a "
                    f"{self._grid_size}×{self._grid_size} puzzle "
                    f"({self._target_empty_count} cells to fill); "
                    f"{self._max_strikes} strikes per game."
                ),
            }]
        return []

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        self._seed_from_state(state)
        if self._terminal is not None:
            return []
        if entity_id not in (self._white_id, self._black_id):
            return []
        side = self._side_for_entity(entity_id)
        if self._has_pending(side):
            return []
        if self._is_done(side):
            return []
        return valid_actions

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                         state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        self._seed_from_state(state)
        actor_id = getattr(actor, "id", None)
        if actor_id not in (self._white_id, self._black_id):
            return "Not a player"
        if action_name == "fill_cell":
            side = self._side_for_entity(actor_id)
            if self._has_pending(side):
                return "You already submitted this round — waiting on opponent"
            if self._is_done(side):
                return "You have already finished this game"
        return None

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        if not success or action_name not in self.custom_actions:
            return []
        raw = getattr(result, "details", None) or {}
        if isinstance(raw, dict):
            raw["speech"] = None
        params = raw.get("_action_params") or {}
        details = {**raw, **params}
        if action_name == "fill_cell":
            return self._handle_fill(actor_id, details, state)
        return []

    def _handle_fill(self, actor_id: str, details: Dict[str, Any],
                     state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        raw_row = details.get("row")
        raw_col = details.get("col") or details.get("column")
        raw_val = details.get("value") or details.get("val") or details.get("digit")
        row = _coerce_int(raw_row)
        col = _coerce_int(raw_col)
        val = _coerce_int(raw_val)
        logger.info(
            "[sd] submit: actor=%s side=%s raw=(%r,%r,%r) cleaned=(%r,%r,%r) keys=%s",
            actor_id, side, raw_row, raw_col, raw_val, row, col, val,
            list(details.keys())[:8],
        )

        # Normalize to 0-indexed and validate ranges. If anything is
        # broken, fall back to a sensible default (a known-empty cell
        # filled with the correct value) so the round still resolves.
        size = self._grid_size
        fill: Optional[Tuple[int, int, int]] = None
        if row is not None and col is not None and val is not None:
            r0 = row - 1
            c0 = col - 1
            if (0 <= r0 < size and 0 <= c0 < size
                    and 1 <= val <= size):
                fill = (r0, c0, val)

        own_filled = (self._white_filled if side == "white"
                      else self._black_filled)

        # If the chosen cell is a given or already-filled by this
        # player, override with a fresh empty cell — the LLM
        # contradicted the rules.
        if fill is not None:
            r, c, v = fill
            if (r, c) in self._givens or (r, c) in own_filled:
                logger.warning(
                    "[sd] %s targeted occupied cell (%d,%d) — substituting",
                    side, r, c,
                )
                fill = None

        if fill is None:
            fallback = self._fallback_fill(side)
            logger.warning(
                "[sd] %s gave invalid fill raw=(%r,%r,%r) — substituting %r",
                side, raw_row, raw_col, raw_val, fallback,
            )
            fill = fallback

        if side == "white":
            self._pending_white = fill
        else:
            self._pending_black = fill

        events: List[Dict[str, Any]] = [{
            "type": "sd_pending",
            "player": actor_id,
            "side": side,
            "row": fill[0] + 1,
            "col": fill[1] + 1,
            "value": fill[2],
            "round": self._round_number,
            "narrative": (
                f"{actor_name} fills a cell (sealed — waiting on opponent)."
            ),
        }]

        opp_side = "black" if side == "white" else "white"
        opp_pending = (self._pending_black if side == "white"
                       else self._pending_white)
        if opp_pending is not None or self._is_done(opp_side):
            events.extend(self._resolve_round(state))
        return events

    # ------------------------------------------------------------------ #
    # Round resolution
    # ------------------------------------------------------------------ #

    def _apply_fill(self, side: str, fill: Tuple[int, int, int]) -> bool:
        """Apply a fill against THIS player's board. Returns True if
        the value matched the solution, False on strike."""
        r, c, v = fill
        correct = (self._solution[r][c] == v)
        if correct:
            if side == "white":
                self._white_filled[(r, c)] = v
            else:
                self._black_filled[(r, c)] = v
        else:
            if side == "white":
                self._white_wrong += 1
            else:
                self._black_wrong += 1
        attempted = (self._white_attempted if side == "white"
                     else self._black_attempted)
        attempted.add(fill)
        return correct

    def _resolve_round(self, state: Any) -> List[Dict[str, Any]]:
        w_fill = self._pending_white
        b_fill = self._pending_black

        w_correct: Optional[bool] = None
        b_correct: Optional[bool] = None
        if w_fill is not None and not self._is_done("white"):
            w_correct = self._apply_fill("white", w_fill)
        if b_fill is not None and not self._is_done("black"):
            b_correct = self._apply_fill("black", b_fill)

        self._pending_white = None
        self._pending_black = None

        w_filled_count = len(self._white_filled)
        b_filled_count = len(self._black_filled)
        w_solved = (w_filled_count == self._target_empty_count)
        b_solved = (b_filled_count == self._target_empty_count)
        w_eliminated = self._white_wrong >= self._max_strikes
        b_eliminated = self._black_wrong >= self._max_strikes
        self._refresh_player_props(state, w_solved, b_solved)

        white_name = self._name_of(state, self._white_id)
        black_name = self._name_of(state, self._black_id)

        events: List[Dict[str, Any]] = [{
            "type": "sd_round",
            "round": self._round_number,
            "game_number": self._game_number,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "white_fill": ({"row": w_fill[0] + 1, "col": w_fill[1] + 1,
                            "value": w_fill[2]} if w_fill else None),
            "white_correct": w_correct,
            "white_strikes": self._white_wrong,
            "white_filled_count": w_filled_count,
            "white_solved": w_solved,
            "white_eliminated": w_eliminated and not w_solved,
            "black_fill": ({"row": b_fill[0] + 1, "col": b_fill[1] + 1,
                            "value": b_fill[2]} if b_fill else None),
            "black_correct": b_correct,
            "black_strikes": self._black_wrong,
            "black_filled_count": b_filled_count,
            "black_solved": b_solved,
            "black_eliminated": b_eliminated and not b_solved,
            "narrative": (
                f"Round {self._round_number}: "
                f"{white_name} → "
                f"{self._fill_text(w_fill, w_correct)}; "
                f"{black_name} → "
                f"{self._fill_text(b_fill, b_correct)}."
            ),
        }]

        self._round_number += 1

        # Per-game terminal check.
        game_winner: Optional[str] = None
        game_reason: Optional[str] = None
        if w_solved and b_solved:
            if self._white_wrong < self._black_wrong:
                game_winner = self._white_id
                game_reason = "double_solve_fewer_strikes"
            elif self._black_wrong < self._white_wrong:
                game_winner = self._black_id
                game_reason = "double_solve_fewer_strikes"
            else:
                game_reason = "double_solve"
        elif w_solved and (b_eliminated or self._is_done("black")):
            game_winner = self._white_id
            game_reason = "solved_only"
        elif b_solved and (w_eliminated or self._is_done("white")):
            game_winner = self._black_id
            game_reason = "solved_only"
        elif w_solved:
            game_winner = self._white_id
            game_reason = "solved"
        elif b_solved:
            game_winner = self._black_id
            game_reason = "solved"
        elif w_eliminated and b_eliminated:
            game_reason = "double_elimination"
        elif w_eliminated:
            game_winner = self._black_id
            game_reason = "white_eliminated"
        elif b_eliminated:
            game_winner = self._white_id
            game_reason = "black_eliminated"
        else:
            return events

        # Inner game ended.
        if game_winner == self._white_id:
            self._white_games += 1
        elif game_winner == self._black_id:
            self._black_games += 1
        self._games_log.append({
            "game_number": self._game_number,
            "solution": [row[:] for row in self._solution],
            "winner": game_winner,
            "reason": game_reason,
            "rounds": self._round_number - 1,
        })
        events.append({
            "type": "sd_game_end",
            "game_number": self._game_number,
            "solution": [row[:] for row in self._solution],
            "winner": game_winner,
            "reason": game_reason,
            "rounds_used": self._round_number - 1,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "narrative": (
                f"Game {self._game_number} complete. "
                + (f"{self._name_of(state, game_winner)} wins the game."
                   if game_winner else
                   f"Game tied — {game_reason}.")
                + f" Match score: {white_name} {self._white_games}–"
                f"{self._black_games} {black_name}."
            ),
        })

        # Match-level terminal check.
        match_winner: Optional[str] = None
        if self._white_games >= self._wins_to_clinch:
            match_winner = self._white_id
        elif self._black_games >= self._wins_to_clinch:
            match_winner = self._black_id

        if match_winner is not None:
            self._terminal = {"reason": "match_won", "winner": match_winner}
            events.append({
                "event_type": "sd_win",
                "type": "sd_win",
                "winner": match_winner,
                "loser": self._opp_id(match_winner),
                "match_score": {"white": self._white_games, "black": self._black_games},
                "best_of": self._best_of,
                "narrative": (
                    f"{self._name_of(state, match_winner)} clinches the "
                    f"best-of-{self._best_of} match {self._white_games}–"
                    f"{self._black_games}."
                ),
            })
            return events

        # Otherwise reset for the next inner game (with sudden-death
        # extension if cap reached on a tie).
        self._game_number += 1
        is_sudden_death = self._game_number > self._best_of
        # Fresh puzzle + solution; reset per-game state.
        self._puzzle, self._solution = generate_puzzle(self._grid_size, self._rng, self._difficulty)
        self._givens = {
            (r, c) for r in range(self._grid_size) for c in range(self._grid_size)
            if self._puzzle[r][c] != 0
        }
        self._target_empty_count = (
            self._grid_size * self._grid_size - len(self._givens)
        )
        self._white_filled = {}
        self._black_filled = {}
        self._white_wrong = 0
        self._black_wrong = 0
        self._white_attempted = set()
        self._black_attempted = set()
        self._pending_white = None
        self._pending_black = None
        self._round_number = 1
        logger.info(
            "[sd] next game #%d (best_of=%d, score=%d–%d, empty=%d, sd=%s)",
            self._game_number, self._best_of, self._white_games,
            self._black_games, self._target_empty_count, is_sudden_death,
        )
        narrative = (
            f"Sudden-death game {self._game_number} — match score "
            f"{self._white_games}–{self._black_games} (best-of-"
            f"{self._best_of} ran tied; play continues until a "
            f"winner emerges)."
            if is_sudden_death else
            f"Starting game {self._game_number} of {self._best_of}. "
            "(New puzzle; both boards reset.)"
        )
        events.append({
            "type": "sd_next_game",
            "game_number": self._game_number,
            "match_score": {"white": self._white_games, "black": self._black_games},
            "best_of": self._best_of,
            "sudden_death": is_sudden_death,
            "grid_size": self._grid_size,
            "box_rows": self._box_rows,
            "box_cols": self._box_cols,
            "puzzle": [row[:] for row in self._puzzle],
            "narrative": narrative,
        })
        return events

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in (self._white_id, self._black_id):
            return {
                "role": "spectator",
                "puzzle": [row[:] for row in self._puzzle],
                "solution": ([row[:] for row in self._solution]
                             if self._terminal else None),
                "max_strikes": self._max_strikes,
            }
        side = self._side_for_entity(entity_id)
        own_filled = (self._white_filled if side == "white"
                      else self._black_filled)
        own_wrong = self._white_wrong if side == "white" else self._black_wrong
        own_pending = (self._pending_white if side == "white"
                       else self._pending_black)
        opp_pending = (self._pending_black if side == "white"
                       else self._pending_white)

        # Build the OBSERVED grid: givens + this player's correct fills.
        observed = [row[:] for row in self._puzzle]
        for (r, c), v in own_filled.items():
            observed[r][c] = v
        empty_cells = _empty_cells(observed)
        strikes_left = max(0, self._max_strikes - own_wrong)

        if self._terminal is not None:
            instructions = "Game over."
        elif own_pending is not None:
            r, c, v = own_pending
            instructions = (
                f"You submitted ({r+1}, {c+1}) = {v} this round — "
                f"sealed. Waiting on opponent."
                if opp_pending is None else
                f"You submitted ({r+1}, {c+1}) = {v} this round — "
                f"sealed. Opponent has also submitted; round about "
                f"to resolve."
            )
        else:
            grid_ascii = _render_grid_ascii(
                observed, self._box_rows, self._box_cols,
            )
            empties_str = ", ".join(
                f"({r+1},{c+1})" for r, c in empty_cells[:30]
            )
            if len(empty_cells) > 30:
                empties_str += f", … ({len(empty_cells) - 30} more)"
            instructions = (
                f"IT IS YOUR TURN. You are {side.upper()} in Sudoku Duel.\n"
                f"\n"
                f"Your goal: correctly fill every empty cell of the "
                f"{self._grid_size}×{self._grid_size} grid (rows / "
                f"columns / boxes each contain 1..{self._grid_size}). "
                f"You have {strikes_left} strike"
                f"{'s' if strikes_left != 1 else ''} remaining and "
                f"{len(empty_cells)} empty cell"
                f"{'s' if len(empty_cells) != 1 else ''} to fill.\n"
                f"\n"
                f"YOUR BOARD (givens + your correct fills; '.' = empty; "
                f"rows are 1-indexed top→bottom; cols 1-indexed left→right):\n"
                f"{grid_ascii}\n"
                f"\n"
                f"Empty cells you can target: {empties_str}\n"
                f"\n"
                f"Pick ONE empty cell and a value in 1..{self._grid_size}. "
                f"Pick a cell where the value is FULLY FORCED by the "
                f"sudoku constraints — every wrong fill costs a strike "
                f"and the cell stays empty.\n"
                f"\n"
                f"YOUR TOOL CALL — output exactly this shape:\n"
                f'  fill_cell(row=3, col=5, value=7)      # all 1-indexed ints\n'
                f"\n"
                f"All three parameters `row`, `col`, `value` are "
                f"REQUIRED. Valid range: row 1-{self._grid_size}, col 1-"
                f"{self._grid_size}, value 1-{self._grid_size}. If you "
                f"omit any the engine will substitute a known-correct "
                f"fill (you give the opponent free tempo). Both players "
                f"submit sealed; both reveals happen simultaneously."
            )

        return {
            "instructions": instructions,
            "your_color": side,
            "match_best_of": self._best_of,
            "match_wins_to_clinch": self._wins_to_clinch,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "game_number": self._game_number,
            "grid_size": self._grid_size,
            "box_rows": self._box_rows,
            "box_cols": self._box_cols,
            "max_strikes": self._max_strikes,
            "your_board": observed,
            "your_filled_count": len(own_filled),
            "your_strikes": own_wrong,
            "your_strikes_left": strikes_left,
            "your_pending_fill": (
                {"row": own_pending[0] + 1, "col": own_pending[1] + 1,
                 "value": own_pending[2]} if own_pending else None
            ),
            "empty_cells": [
                {"row": r + 1, "col": c + 1} for r, c in empty_cells
            ],
            "target_empty_count": self._target_empty_count,
            "opponent_strikes": (self._black_wrong if side == "white"
                                  else self._white_wrong),
            "opponent_filled_count": (
                len(self._black_filled) if side == "white"
                else len(self._white_filled)
            ),
            "opponent_has_submitted_this_round": opp_pending is not None,
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _side_for_entity(self, entity_id: str) -> str:
        return "white" if entity_id == self._white_id else "black"

    def _has_pending(self, side: str) -> bool:
        return (
            (self._pending_white if side == "white" else self._pending_black)
            is not None
        )

    def _is_done(self, side: str) -> bool:
        filled = self._white_filled if side == "white" else self._black_filled
        wrong = self._white_wrong if side == "white" else self._black_wrong
        if len(filled) >= self._target_empty_count:
            return True
        if wrong >= self._max_strikes:
            return True
        return False

    def _fallback_fill(self, side: str) -> Tuple[int, int, int]:
        """Pick a known-empty cell on THIS player's board with the
        correct solution value. Used when the LLM hands back a broken
        / occupied / out-of-range submission so the round still
        resolves."""
        own_filled = (self._white_filled if side == "white"
                      else self._black_filled)
        for r in range(self._grid_size):
            for c in range(self._grid_size):
                if (r, c) in self._givens:
                    continue
                if (r, c) in own_filled:
                    continue
                return (r, c, self._solution[r][c])
        # Pathological — board already full. Shouldn't reach here
        # because the round wouldn't resolve in that state. Return
        # something deterministic.
        return (0, 0, self._solution[0][0])

    def _fill_text(self, fill: Optional[Tuple[int, int, int]],
                   correct: Optional[bool]) -> str:
        if fill is None:
            return "(no submission)"
        r, c, v = fill
        verdict = ("hit" if correct
                   else "miss" if correct is False
                   else "done")
        return f"({r+1},{c+1})={v} [{verdict}]"

    def _refresh_player_props(self, state: Any, w_solved: bool,
                              b_solved: bool) -> None:
        if not hasattr(state, "entities"):
            return
        for entity_id, side, filled_count, strikes, solved in (
            (self._white_id, "white", len(self._white_filled),
             self._white_wrong, w_solved),
            (self._black_id, "black", len(self._black_filled),
             self._black_wrong, b_solved),
        ):
            ent = state.entities.get(entity_id) if entity_id else None
            if ent is None or not hasattr(ent, "properties"):
                continue
            ent.properties["color"] = side
            ent.properties["strikes"] = strikes
            ent.properties["cells_filled"] = filled_count
            ent.properties["solved"] = solved

    def _name_of(self, state: Any, player_id: Optional[str]) -> str:
        if player_id is None:
            return "?"
        if hasattr(state, "entities"):
            ent = state.entities.get(player_id)
            if ent is not None and getattr(ent, "name", None):
                return ent.name
        return player_id

    def _opp_id(self, player_id: str) -> Optional[str]:
        if player_id == self._white_id:
            return self._black_id
        if player_id == self._black_id:
            return self._white_id
        return None
