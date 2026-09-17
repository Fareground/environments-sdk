"""Hex — classic 2-player abstract strategy.

Players alternate placing stones on a rhombus hex grid. White connects
the TOP and BOTTOM edges with their stones; Black connects LEFT and
RIGHT. **Draws are mathematically impossible** — one side's chain
always blocks the other.

Coordinate system: offset (row, col), 0-indexed internally,
1-indexed in the public API and perception text. Each cell (r, c)
has up to 6 neighbors:
   (r-1, c)   (r-1, c+1)        upper row
   (r, c-1)   (r, c+1)           same row
   (r+1, c-1) (r+1, c)           lower row

Public events
~~~~~~~~~~~~~
  - `hex_init`     { board_size, swap_rule, board, side_to_move,
                     best_of, wins_to_clinch }                       one-shot
  - `hex_move`     { player, side, row, col, board, side_to_move,
                     stones_placed }                                 each placement
  - `hex_swap`     { player, side, board, side_to_move }             pie-rule used
  - `hex_game_end` { game_number, winner, reason, winning_path,
                     match_score, moves_used }
  - `hex_next_game`{ game_number, match_score, board, side_to_move,
                     board_size, swap_rule }
  - `hex_invalid`  { actor_id, reason, message }
  - `hex_win`      { winner, loser, match_score, best_of }           match terminal

Chat is left ENABLED — Hex has no hidden information; reasoning and
speech don't leak anything actionable.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


ALLOWED_BOARD_SIZES = (7, 9, 11, 13)
DEFAULT_BOARD_SIZE = 9
DEFAULT_WINS_NEEDED = 1
MIN_WINS_NEEDED = 1
MAX_WINS_NEEDED = 5

EMPTY = "."
WHITE = "W"
BLACK = "B"

# 6 hex-neighbor offsets in (row, col) offset coords for a rhombus board
# skewed right: each row shifts half a hex right as you go down.
NEIGHBORS: Tuple[Tuple[int, int], ...] = (
    (-1, 0), (-1, 1),
    (0, -1), (0, 1),
    (1, -1), (1, 0),
)

# Column letters for the 'A1'-style coordinate strings.
COL_LETTERS = "ABCDEFGHIJKLM"


# ---------------------------------------------------------------------------
# Coordinate parsing
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


def _parse_coord(raw: Any, board_size: int) -> Optional[Tuple[int, int]]:
    """Parse 'A1' / 'a1' / 'a 1' / '1,1' / '(1, 1)' → (row, col) 0-indexed.
    Returns None if unparseable."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    # Strip surrounding quotes / parens.
    s = s.strip("\"'()[]{}")
    # Pattern 1: letter + digits — e.g. 'a1', 'D 11'.
    m = re.match(r"^\s*([a-m])\s*(\d{1,2})\s*$", s)
    if m:
        col = COL_LETTERS.index(m.group(1).upper())
        row = int(m.group(2)) - 1
        if 0 <= row < board_size and 0 <= col < board_size:
            return (row, col)
        return None
    # Pattern 2: digit-only pair separated by comma / space / 'x'.
    parts = re.split(r"[,\s/x×]+", s)
    if len(parts) >= 2:
        try:
            row = int(parts[0]) - 1
            col = int(parts[1]) - 1
            if 0 <= row < board_size and 0 <= col < board_size:
                return (row, col)
        except ValueError:
            pass
    return None


def _coord_to_str(row: int, col: int) -> str:
    """Convert 0-indexed (row, col) to 1-indexed display string 'A1'."""
    return f"{COL_LETTERS[col]}{row + 1}"


# ---------------------------------------------------------------------------
# Board rendering
# ---------------------------------------------------------------------------

def _render_board_ascii(board: List[List[str]]) -> str:
    """Render the rhombus as an indented ASCII grid so the offset
    geometry is visible to the LLM."""
    n = len(board)
    lines: List[str] = []
    header = "      " + " ".join(COL_LETTERS[i] for i in range(n))
    lines.append(header)
    for r in range(n):
        prefix = f"{r + 1:>3} " + " " * r
        row_str = " ".join(board[r][c] for c in range(n))
        lines.append(f"{prefix} {row_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Win check
# ---------------------------------------------------------------------------

def _connection_path(board: List[List[str]], color: str) -> Optional[List[Tuple[int, int]]]:
    """BFS for `color` from one edge to the opposite edge. Returns the
    list of cells (0-indexed) on the discovered path, or None if no
    connection exists. White: top↔bottom. Black: left↔right."""
    n = len(board)
    visited: Set[Tuple[int, int]] = set()
    parent: Dict[Tuple[int, int], Tuple[int, int]] = {}
    queue: deque = deque()
    if color == WHITE:
        starts = [(0, c) for c in range(n) if board[0][c] == WHITE]
        is_goal = lambda cell: cell[0] == n - 1
    else:
        starts = [(r, 0) for r in range(n) if board[r][0] == BLACK]
        is_goal = lambda cell: cell[1] == n - 1
    for s in starts:
        visited.add(s)
        queue.append(s)
    while queue:
        cur = queue.popleft()
        if is_goal(cur):
            # Reconstruct path back to the seed.
            path: List[Tuple[int, int]] = [cur]
            while cur in parent:
                cur = parent[cur]
                path.append(cur)
            path.reverse()
            return path
        r0, c0 = cur
        for dr, dc in NEIGHBORS:
            nr, nc = r0 + dr, c0 + dc
            if 0 <= nr < n and 0 <= nc < n and (nr, nc) not in visited:
                if board[nr][nc] == color:
                    visited.add((nr, nc))
                    parent[(nr, nc)] = cur
                    queue.append((nr, nc))
    return None


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class HexModule(DomainModule):
    """Classic Hex — turn-based connection race."""

    # Chat allowed — Hex has no hidden info, trash-talk is fair game.
    suppress_chat = False

    def __init__(self, name: str = "hex",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        bs = int(p.get("board_size") or DEFAULT_BOARD_SIZE)
        if bs not in ALLOWED_BOARD_SIZES:
            bs = DEFAULT_BOARD_SIZE
        self._N: int = bs
        self._swap_rule: bool = bool(p.get("swap_rule", True))
        wn = int(p.get("wins_needed") or DEFAULT_WINS_NEEDED)
        wn = max(MIN_WINS_NEEDED, min(MAX_WINS_NEEDED, wn))
        self._wins_to_clinch: int = wn
        self._best_of: int = wn * 2 - 1

        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        # Match-level state.
        self._white_games: int = 0
        self._black_games: int = 0
        self._game_number: int = 1
        # Per-game state (resets between games).
        self._board: List[List[str]] = self._fresh_board()
        self._side_to_move: str = "white"
        self._move_count: int = 0
        self._swap_used: bool = False

        self._terminal: Optional[Dict[str, Any]] = None
        self._initialized = False
        logger.info(
            "[hex] init size=%dx%d wins_needed=%d swap_rule=%s",
            bs, bs, wn, self._swap_rule,
        )

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        sw = "with pie-rule swap" if self._swap_rule else "no swap"
        return (f"Hex — {self._N}×{self._N} rhombus, best-of-{self._best_of}, "
                f"{sw}. White connects top↔bottom; Black left↔right.")

    @property
    def custom_actions(self) -> List[str]:
        # `pie_swap` is exposed only when swap is currently legal — but
        # the kernel asks for the full list once at init, so include it
        # here. filter_valid_actions trims it down per turn.
        return ["place_stone", "pie_swap"] if self._swap_rule else ["place_stone"]

    @property
    def required_properties(self) -> List[str]:
        return ["color"]

    # ------------------------------------------------------------------ #
    # Board helpers
    # ------------------------------------------------------------------ #

    def _fresh_board(self) -> List[List[str]]:
        return [[EMPTY for _ in range(self._N)] for _ in range(self._N)]

    def _color_for(self, side: str) -> str:
        return WHITE if side == "white" else BLACK

    def _opp_side(self, side: str) -> str:
        return "black" if side == "white" else "white"

    def _side_for_entity(self, entity_id: str) -> str:
        return "white" if entity_id == self._white_id else "black"

    def _active_player_id(self) -> Optional[str]:
        return self._white_id if self._side_to_move == "white" else self._black_id

    def _swap_legal_now(self) -> bool:
        """Pie-rule swap is legal ONLY on Black's first move (i.e. after
        White's opening stone) and only if the swap_rule is enabled."""
        return (
            self._swap_rule
            and not self._swap_used
            and self._move_count == 1
            and self._side_to_move == "black"
        )

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
                ent.properties["stones_placed"] = 0
                ent.properties["games_won"] = 0
        self._initialized = True
        logger.info(
            "[hex] seated white=%s black=%s size=%d",
            agents[0].name, agents[1].name, self._N,
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
                "type": "hex_init",
                "white_id": self._white_id, "black_id": self._black_id,
                "board_size": self._N,
                "swap_rule": self._swap_rule,
                "board": self._board_snapshot(),
                "side_to_move": self._side_to_move,
                "best_of": self._best_of,
                "wins_to_clinch": self._wins_to_clinch,
                "match_score": {"white": 0, "black": 0},
                "game_number": 1,
                "narrative": (
                    f"Hex {self._N}×{self._N} — {bo_text}. "
                    f"White (top↔bottom) moves first."
                    + (" Pie-rule swap available on Black's first move."
                       if self._swap_rule else "")
                ),
            }]
        return []

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        self._seed_from_state(state)
        if self._terminal is not None:
            return []
        if entity_id not in (self._white_id, self._black_id):
            return [a for a in valid_actions if a not in self.custom_actions]
        out: List[str] = [a for a in valid_actions if a not in self.custom_actions]
        if entity_id == self._active_player_id():
            out.append("place_stone")
            if self._swap_legal_now():
                out.append("pie_swap")
        return out

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                         state: Any) -> Optional[str]:
        if action_name == "pie_swap" and not self._swap_rule:
            return "Pie-rule swap is disabled"
        if action_name not in self.custom_actions:
            return None
        self._seed_from_state(state)
        actor_id = getattr(actor, "id", None)
        if actor_id not in (self._white_id, self._black_id):
            return "Not a player"
        if actor_id != self._active_player_id():
            return "Not your turn"
        if action_name == "pie_swap" and not self._swap_legal_now():
            return "Pie-rule swap is only legal on Black's first move (and only if enabled)"
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
        params = raw.get("_action_params") or {}
        details = {**raw, **params}
        if action_name == "place_stone":
            return self._handle_place(actor_id, details, state)
        if action_name == "pie_swap":
            return self._handle_swap(actor_id, state)
        return []

    def _handle_place(self, actor_id: str, details: Dict[str, Any],
                      state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        # Accept row/col integers OR a string `coord` ("A1").
        row = _coerce_int(details.get("row"))
        col = _coerce_int(details.get("col") or details.get("column"))
        cell: Optional[Tuple[int, int]] = None
        if row is not None and col is not None:
            r0 = row - 1
            c0 = col - 1
            if 0 <= r0 < self._N and 0 <= c0 < self._N:
                cell = (r0, c0)
        if cell is None:
            cell = _parse_coord(
                details.get("coord") or details.get("cell") or details.get("position"),
                self._N,
            )

        # Speech / reasoning sweep — many LLMs forget to populate the
        # structured args but TYPE the coord in their chain-of-thought
        # ("I'll play D5" / "place at (4, 2)"). If the structured args
        # didn't yield a legal cell, scan free-text fields for any
        # coord-shaped tokens and try them in order.
        if cell is None or self._board[cell[0]][cell[1]] != EMPTY:
            recovered = self._recover_coord_from_text(details, occupied_ok=False)
            if recovered is not None:
                logger.info(
                    "[hex] %s recovered coord %s from speech/reasoning",
                    side, _coord_to_str(*recovered),
                )
                cell = recovered

        # If still unparseable OR occupied, fall back to a STRATEGIC
        # cell so the game makes a sensible move instead of a random
        # one. Heuristic: prefer cells adjacent to this player's
        # existing stones that move toward their goal edge.
        if cell is None or self._board[cell[0]][cell[1]] != EMPTY:
            fallback = self._strategic_fallback(side)
            if fallback is None:
                return [self._invalid(actor_id, actor_name, "no_cells",
                                       "no legal cells remain")]
            logger.warning(
                "[hex] %s invalid move details=%r — substituting %s (strategic)",
                side, details, _coord_to_str(*fallback),
            )
            cell = fallback

        r, c = cell
        color = self._color_for(side)
        self._board[r][c] = color
        self._move_count += 1
        self._refresh_player_props(state)
        next_side = self._opp_side(side)
        self._side_to_move = next_side

        events: List[Dict[str, Any]] = [{
            "type": "hex_move",
            "player": actor_id,
            "side": side,
            "color": color,
            "row": r + 1,
            "col": c + 1,
            "coord": _coord_to_str(r, c),
            "board": self._board_snapshot(),
            "side_to_move": next_side,
            "move_count": self._move_count,
            "narrative": (
                f"{actor_name} ({side}) places at {_coord_to_str(r, c)}."
            ),
        }]

        # Win check.
        path = _connection_path(self._board, color)
        if path is not None:
            events.extend(self._handle_game_end(state, side, "connection", path))
        return events

    def _handle_swap(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        # Switch participant sides, leaving the opening stone and its
        # connection geometry intact. Scores belong to people, not colors.
        actor_name = self._name_of(state, actor_id)
        self._white_id, self._black_id = self._black_id, self._white_id
        self._white_games, self._black_games = self._black_games, self._white_games
        self._swap_used = True
        # The swap consumes the responder's turn. The original opener,
        # now Black, places the next stone.
        self._side_to_move = "black"
        self._refresh_player_props(state)
        # Update entity properties to reflect color flip.
        if hasattr(state, "entities"):
            for ent_id, color in ((self._white_id, "white"),
                                  (self._black_id, "black")):
                ent = state.entities.get(ent_id) if ent_id else None
                if ent and hasattr(ent, "properties"):
                    ent.properties["color"] = color
        return [{
            "type": "hex_swap",
            "player": actor_id,
            "match_score": {"white": self._white_games, "black": self._black_games},
            "narrative": (
                f"{actor_name} invokes the pie rule — colors swap. "
                f"{actor_name} now owns the opening white stone; Black moves next."
            ),
            "board": self._board_snapshot(),
            "side_to_move": self._side_to_move,
            "white_id": self._white_id,
            "black_id": self._black_id,
        }]

    # ------------------------------------------------------------------ #
    # Game-end / match wrapper
    # ------------------------------------------------------------------ #

    def _handle_game_end(self, state: Any, winner_side: str, reason: str,
                         winning_path: Optional[List[Tuple[int, int]]]
                         ) -> List[Dict[str, Any]]:
        winner_id = self._white_id if winner_side == "white" else self._black_id
        white_name = self._name_of(state, self._white_id)
        black_name = self._name_of(state, self._black_id)
        # Increment match score.
        if winner_side == "white":
            self._white_games += 1
        else:
            self._black_games += 1
        self._refresh_player_props(state)
        events: List[Dict[str, Any]] = [{
            "type": "hex_game_end",
            "game_number": self._game_number,
            "winner": winner_id,
            "winner_side": winner_side,
            "reason": reason,
            "winning_path": [
                {"row": r + 1, "col": c + 1, "coord": _coord_to_str(r, c)}
                for r, c in (winning_path or [])
            ],
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "moves_used": self._move_count,
            "board": self._board_snapshot(),
            "narrative": (
                f"Game {self._game_number} — {self._name_of(state, winner_id)} "
                f"({winner_side}) completes the connection in "
                f"{self._move_count} moves. "
                f"Match {white_name} {self._white_games}–"
                f"{self._black_games} {black_name}."
            ),
        }]

        match_winner_id: Optional[str] = None
        if self._white_games >= self._wins_to_clinch:
            match_winner_id = self._white_id
        elif self._black_games >= self._wins_to_clinch:
            match_winner_id = self._black_id

        if match_winner_id is not None:
            self._terminal = {"reason": "match_won", "winner": match_winner_id}
            events.append({
                "event_type": "hex_win",
                "type": "hex_win",
                "score": {self._white_id: self._white_games, self._black_id: self._black_games},
                "winner": match_winner_id,
                "loser": self._opp_id(match_winner_id),
                "match_score": {"white": self._white_games, "black": self._black_games},
                "best_of": self._best_of,
                "narrative": (
                    f"{self._name_of(state, match_winner_id)} clinches the "
                    f"best-of-{self._best_of} match "
                    f"{self._white_games}–{self._black_games}."
                ),
            })
            return events

        # Reset for next inner game. Sudden-death extension if cap reached.
        self._game_number += 1
        is_sudden_death = self._game_number > self._best_of
        self._board = self._fresh_board()
        self._move_count = 0
        self._swap_used = False
        self._side_to_move = "white"
        self._refresh_player_props(state)
        events.append({
            "type": "hex_next_game",
            "white_id": self._white_id, "black_id": self._black_id,
            "game_number": self._game_number,
            "match_score": {"white": self._white_games, "black": self._black_games},
            "board": self._board_snapshot(),
            "side_to_move": "white",
            "board_size": self._N,
            "swap_rule": self._swap_rule,
            "sudden_death": is_sudden_death,
            "narrative": (
                f"Starting game {self._game_number} of {self._best_of}. "
                f"(Fresh board; White moves first.)"
            ),
        })
        return events

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in (self._white_id, self._black_id):
            return {
                "role": "spectator",
                "board": self._board_snapshot(),
                "board_size": self._N,
            }
        side = self._side_for_entity(entity_id)
        is_active = (entity_id == self._active_player_id())
        empty_cells = [
            (r, c) for r in range(self._N) for c in range(self._N)
            if self._board[r][c] == EMPTY
        ]

        if self._terminal is not None:
            instructions = "Match over."
        elif not is_active:
            instructions = (
                f"It is the opponent's turn. You play "
                f"{'white (top↔bottom)' if side == 'white' else 'black (left↔right)'}. "
                f"Wait for their move."
            )
        else:
            grid = _render_board_ascii(self._board)
            n_empty = len(empty_cells)
            sample_cells = ", ".join(
                _coord_to_str(r, c) for r, c in empty_cells[:20]
            )
            if n_empty > 20:
                sample_cells += f", … ({n_empty - 20} more)"
            swap_hint = ""
            if self._swap_legal_now() and side == "black":
                swap_hint = (
                    f"\n\nPIE-RULE OPTION: This is your first move and "
                    f"the swap rule is enabled. Instead of placing a "
                    f"stone you may call `pie_swap` — colors will swap, "
                    f"the opening stone becomes yours, and you'll be "
                    f"playing top↔bottom from now on. Use this when "
                    f"White's opening move looks too strong (typically: "
                    f"central or near-central placements).\n"
                )
            goal_text = (
                "Connect the TOP edge (row 1) to the BOTTOM edge (row "
                f"{self._N}) with an unbroken chain of WHITE stones (W)."
                if side == "white" else
                "Connect the LEFT edge (col A) to the RIGHT edge (col "
                f"{COL_LETTERS[self._N - 1]}) with an unbroken chain of "
                "BLACK stones (B)."
            )
            instructions = (
                f"IT IS YOUR TURN. You are playing {side.upper()} "
                f"in Hex {self._N}×{self._N}.\n"
                f"\n"
                f"GOAL: {goal_text}\n"
                f"\n"
                f"BOARD ('.' = empty, 'W' = white stone, 'B' = black "
                f"stone; rows are offset right going down so each row "
                f"is shifted one half-hex):\n"
                f"{grid}\n"
                f"\n"
                f"Empty cells you can play ({n_empty} total): {sample_cells}\n"
                f"{swap_hint}\n"
                f"YOUR TOOL CALL — output exactly this shape:\n"
                f'  place_stone(row=4, col=3)        # any empty cell\n'
                f"\n"
                f"Both `row` and `col` are REQUIRED 1-indexed integers "
                f"in [1, {self._N}]. The cell must be EMPTY. If you omit "
                f"either parameter the engine will pick a chain-extending "
                f"cell for you (you lose tempo)."
            )

        return {
            "instructions": instructions,
            "your_color": side,
            "board": self._board_snapshot(),
            "board_size": self._N,
            "side_to_move": self._side_to_move,
            "is_your_turn": is_active,
            "swap_legal": self._swap_legal_now() and side == "black",
            "swap_used": self._swap_used,
            "move_count": self._move_count,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "game_number": self._game_number,
            "best_of": self._best_of,
            "wins_to_clinch": self._wins_to_clinch,
            "empty_cells": [
                {"row": r + 1, "col": c + 1, "coord": _coord_to_str(r, c)}
                for r, c in empty_cells
            ],
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _board_snapshot(self) -> List[List[str]]:
        return [row[:] for row in self._board]

    def _fallback_cell(self) -> Optional[Tuple[int, int]]:
        """Pick a cell near the center if possible, otherwise any empty
        cell. Spiral fallback used by the strategic helper when there
        are no own-stones to extend from."""
        if all(self._board[r][c] != EMPTY for r in range(self._N) for c in range(self._N)):
            return None
        center = self._N // 2
        # Spiral out from the center.
        for d in range(self._N):
            for dr in range(-d, d + 1):
                for dc in range(-d, d + 1):
                    if abs(dr) != d and abs(dc) != d:
                        continue
                    r = center + dr
                    c = center + dc
                    if 0 <= r < self._N and 0 <= c < self._N and self._board[r][c] == EMPTY:
                        return (r, c)
        return None

    def _recover_coord_from_text(self, details: Dict[str, Any],
                                  occupied_ok: bool = False
                                  ) -> Optional[Tuple[int, int]]:
        """Scan the speech / reasoning fields for the FIRST coord-shaped
        token that lands on an empty cell. Lets us recover the LLM's
        intent when it typed the move in its chain-of-thought but
        forgot to forward it as a structured arg.
        Patterns matched:
          - 'A1', 'd4', 'D 5' (case-insensitive letter A-M + digits)
          - '(3, 5)', '3,5', 'row 3 col 5'
        """
        haystack_parts: List[str] = []
        for key in ("reasoning", "speech", "narrative", "thought", "cot"):
            v = details.get(key)
            if isinstance(v, str) and v:
                haystack_parts.append(v)
        if not haystack_parts:
            return None
        text = " ".join(haystack_parts).lower()
        # Pattern 1: letter + 1-2 digits (A1, d12, M9).
        candidates: List[Tuple[int, int]] = []
        for m in re.finditer(r"\b([a-m])\s*(\d{1,2})\b", text):
            col = COL_LETTERS.index(m.group(1).upper())
            row = int(m.group(2)) - 1
            if 0 <= row < self._N and 0 <= col < self._N:
                candidates.append((row, col))
        # Pattern 2: 'row N col M' / 'row N, col M' / '(N, M)' / 'N,M'.
        for m in re.finditer(
            r"\brow\s*(\d{1,2})\s*[, ]\s*col(?:umn)?\s*(\d{1,2})\b",
            text,
        ):
            r = int(m.group(1)) - 1
            c = int(m.group(2)) - 1
            if 0 <= r < self._N and 0 <= c < self._N:
                candidates.append((r, c))
        for m in re.finditer(r"\(\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)", text):
            r = int(m.group(1)) - 1
            c = int(m.group(2)) - 1
            if 0 <= r < self._N and 0 <= c < self._N:
                candidates.append((r, c))
        # Return the first candidate that's on an empty cell. If
        # `occupied_ok` is True, return the first that's in range
        # regardless (used for diagnostics).
        for r, c in candidates:
            if occupied_ok or self._board[r][c] == EMPTY:
                return (r, c)
        return None

    def _strategic_fallback(self, side: str) -> Optional[Tuple[int, int]]:
        """Pick an empty cell that ADVANCES this player's connection
        goal. Heuristic:
          1. Find empty cells adjacent to at least one own-color stone
             (extending the chain).
          2. Rank them by how close they bring the player to completing
             their connection — measured as (distance from the
             nearest own-stone to the player's near edge) +
             (distance from the candidate cell to the player's far
             edge). Lower = better; ties broken by board-center
             proximity.
          3. If no own-stones exist yet (opening move), fall back to
             a center-of-board cell — the canonical Hex opening.
        Always returns a legal empty cell unless the board is full.
        """
        color = self._color_for(side)
        # Collect own-color positions and empty cells.
        own_cells: List[Tuple[int, int]] = []
        empty_cells: List[Tuple[int, int]] = []
        for r in range(self._N):
            for c in range(self._N):
                v = self._board[r][c]
                if v == color:
                    own_cells.append((r, c))
                elif v == EMPTY:
                    empty_cells.append((r, c))
        if not empty_cells:
            return None
        if not own_cells:
            # No own stones yet — play near center (the textbook Hex
            # opener). Hand off to the spiral fallback.
            return self._fallback_cell()

        # Candidates: empty cells adjacent to at least one own stone.
        own_set = set(own_cells)
        adj_candidates: List[Tuple[int, int]] = []
        for r, c in empty_cells:
            for dr, dc in NEIGHBORS:
                if (r + dr, c + dc) in own_set:
                    adj_candidates.append((r, c))
                    break
        # If no adjacent empties (rare — own stones boxed in), score
        # ALL empty cells.
        pool = adj_candidates or empty_cells

        # Distance to far edge per side:
        #   white wants to reach BOTH row 0 and row N-1 → the candidate
        #   helps most when it's far from where the chain currently
        #   stops. We score by `(N - 1) - max_distance_along_axis`.
        # Simpler heuristic: minimize the candidate's distance to the
        # NEAREST goal edge that isn't already adjacent to own stones.
        center = (self._N - 1) / 2
        def score(rc: Tuple[int, int]) -> float:
            r, c = rc
            if side == "white":
                # Reward proximity to whichever goal edge is closer to
                # this candidate. Bonus for cells that EXTEND the chain
                # toward the far edge.
                edge_dist = min(r, (self._N - 1) - r)
            else:
                edge_dist = min(c, (self._N - 1) - c)
            # Center proximity tiebreaker (smaller is more central).
            center_dist = abs(r - center) + abs(c - center)
            return edge_dist * 10 + center_dist * 0.1

        pool.sort(key=score)
        return pool[0]

    def _refresh_player_props(self, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        for entity_id, side in (
            (self._white_id, "white"), (self._black_id, "black"),
        ):
            ent = state.entities.get(entity_id) if entity_id else None
            if ent is None or not hasattr(ent, "properties"):
                continue
            color = self._color_for(side)
            stones = sum(
                1 for r in range(self._N) for c in range(self._N)
                if self._board[r][c] == color
            )
            games_won = self._white_games if side == "white" else self._black_games
            ent.properties["stones_placed"] = stones
            ent.properties["games_won"] = games_won

    def _invalid(self, actor_id: str, actor_name: str, reason: str,
                 message: str) -> Dict[str, Any]:
        return {
            "type": "hex_invalid",
            "actor_id": actor_id,
            "actor_name": actor_name,
            "reason": reason,
            "message": message,
            "narrative": f"{actor_name}: {message}",
        }

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
