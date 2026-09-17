"""Connect Five — gravity-drop connection game.

Two players alternate dropping discs into columns. Discs fall to the
lowest empty slot in their column (gravity). First to align N discs in
a row in any allowed direction wins. Board fills = draw.

Configurable in pre-match (see template.json runtime parameters):
  - `board_cols`    — width  (default 10; range 4–15)
  - `board_rows`    — height (default 7;  range 4–12)
  - `connect_n`     — # in a row needed to win (default 5; range 3–7)
  - `allow_diagonals` — count diagonal lines toward wins? (default true)

The classic Connect Four is `cols=7 rows=6 connect_n=4 allow_diagonals=true`;
the user's preferred variant is `10×7 connect_n=5` (the default — wider
board makes the 4-connection trick less degenerate). Variants like
Tic-Tac-Toe-on-a-bigger-grid (allow_diagonals=true, connect_n=3) or
Pure-Vertical-Stacking (allow_diagonals=false) are all derivable from
the same engine.

Internal coordinates: `board[col][row]` for col ∈ [0, cols), row ∈ [0, rows).
Row 0 is the BOTTOM of the visible board (gravity floor); row rows-1 is
the top. Pieces are `'w'` (white) or `'b'` (black).

Action notation (LLM-facing): columns are 1-indexed integers 1..cols.
`drop_disc { column: 3 }` drops the active player's disc into column 3.

Events:
  - `cf_init`   { cols, rows, connect_n, allow_diagonals, board }   one-shot
  - `cf_drop`   { player, column, row, board, winning_line? }
  - `cf_win`    { winner, loser, winning_line, board }              terminal
  - `cf_resign` { loser, winner }                                   terminal
  - `cf_draw`   { reason: "board_full", board }                     terminal
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bounds (template enforces narrower ranges; engine accepts wider)
# ---------------------------------------------------------------------------

MIN_COLS = 4
MAX_COLS = 15
MIN_ROWS = 4
MAX_ROWS = 12
MIN_CONNECT = 3
MAX_CONNECT = 8


def opposite(side: str) -> str:
    return "black" if side == "white" else "white"


# ---------------------------------------------------------------------------
# Move / win helpers
# ---------------------------------------------------------------------------

def drop_row(board: List[List[Optional[str]]], col: int) -> Optional[int]:
    """Return the row a new disc would land on for `col`, or None if the
    column is full."""
    column = board[col]
    for r in range(len(column)):
        if column[r] is None:
            return r
    return None


def legal_columns(board: List[List[Optional[str]]]) -> List[int]:
    return [c for c in range(len(board)) if drop_row(board, c) is not None]


def all_axes(allow_diagonals: bool) -> List[Tuple[int, int]]:
    """Direction vectors to scan for a winning line from a just-placed
    disc. Each axis is checked bidirectionally."""
    axes: List[Tuple[int, int]] = [(1, 0), (0, 1)]  # horizontal, vertical
    if allow_diagonals:
        axes.extend([(1, 1), (1, -1)])               # / and \ diagonals
    return axes


def find_winning_line(
    board: List[List[Optional[str]]],
    col: int,
    row: int,
    color: str,
    connect_n: int,
    allow_diagonals: bool,
) -> Optional[List[Tuple[int, int]]]:
    """If placing `color` at (col, row) just created a run of ≥ connect_n,
    return the list of (col, row) coords forming the winning line.
    Otherwise return None.

    Scans each axis bidirectionally from (col, row), collecting all
    contiguous same-color squares. Picks the maximum-length line; if
    ≥ connect_n, that's the win.
    """
    cols, rows = len(board), len(board[0])
    for dx, dy in all_axes(allow_diagonals):
        line: List[Tuple[int, int]] = [(col, row)]
        # Walk forward.
        c, r = col + dx, row + dy
        while 0 <= c < cols and 0 <= r < rows and board[c][r] == color:
            line.append((c, r))
            c += dx
            r += dy
        # Walk backward.
        c, r = col - dx, row - dy
        while 0 <= c < cols and 0 <= r < rows and board[c][r] == color:
            line.insert(0, (c, r))
            c -= dx
            r -= dy
        if len(line) >= connect_n:
            return line
    return None


def empty_board(cols: int, rows: int) -> List[List[Optional[str]]]:
    return [[None for _ in range(rows)] for _ in range(cols)]


def board_snapshot(board: List[List[Optional[str]]]) -> List[List[Optional[str]]]:
    """Deep copy suitable for serialization / event payloads."""
    return [col[:] for col in board]


def render_ascii_board(board: List[List[Optional[str]]]) -> str:
    """Top-row at top of output. Column labels along the bottom."""
    cols = len(board)
    rows = len(board[0]) if cols else 0
    lines: List[str] = []
    rank_pad = len(str(rows))
    for r in range(rows - 1, -1, -1):
        row_str = " ".join(board[c][r] if board[c][r] else "." for c in range(cols))
        lines.append(f"  {str(r + 1).rjust(rank_pad)}  {row_str}")
    # Column labels along the bottom — 1-indexed, 2-digit aware.
    col_labels = " ".join(str(c + 1).rjust(1)[-1:] for c in range(cols))
    lines.append(" " * (rank_pad + 4) + col_labels)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class ConnectFiveModule(DomainModule):
    """Gravity-drop connection game. Configurable board size + win length
    + allowed directions."""

    def __init__(self, name: str = "connect_five",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}

        def bounded(name, default, minimum, maximum):
            raw = p.get(name, default)
            try:
                value = int(raw)
                valid = not isinstance(raw, bool) and value == float(raw) and minimum <= value <= maximum
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                raise ValueError(f'{name} must be a whole number between {minimum} and {maximum}.')
            return value

        self._cols = bounded('board_cols', 10, MIN_COLS, MAX_COLS)
        self._rows = bounded('board_rows', 7, MIN_ROWS, MAX_ROWS)
        self._connect_n = bounded('connect_n', 5, MIN_CONNECT, MAX_CONNECT)
        # `connect_n` can't exceed the longest possible line on the board.
        max_line = max(self._cols, self._rows)
        if self._connect_n > max_line:
            raise ValueError(f'Connect-N must be {max_line} or less on a {self._cols}×{self._rows} board.')
        self._allow_diagonals: bool = bool(p.get("allow_diagonals", True))

        self._board: List[List[Optional[str]]] = empty_board(self._cols, self._rows)
        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        self._side_to_move: str = "white"
        self._terminal: Optional[Dict[str, Any]] = None
        self._turn_log: List[str] = []
        self._initialized = False
        self._last_drop: Optional[Tuple[int, int]] = None  # (col, row)
        logger.info(
            "[cf] init cols=%d rows=%d connect_n=%d diagonals=%s",
            self._cols, self._rows, self._connect_n, self._allow_diagonals,
        )

    @staticmethod
    def _clamp(v: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, v))

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        diag_note = "all 8 directions" if self._allow_diagonals else "no diagonals"
        return (f"Connect Five — gravity-drop, {self._cols}×{self._rows} board, "
                f"first to {self._connect_n} in a row wins ({diag_note}).")

    @property
    def custom_actions(self) -> List[str]:
        return ["drop_disc", "resign"]

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
                ent.properties["discs_played"] = 0
        self._initialized = True
        logger.info(
            "[cf] seated white=%s black=%s", agents[0].name, agents[1].name,
        )

    # ------------------------------------------------------------------ #
    # tick — emit init event once
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        was_seeded = self._initialized
        self._seed_from_state(state)
        if not was_seeded and self._initialized:
            return [{
                "type": "cf_init",
                "cols": self._cols,
                "rows": self._rows,
                "connect_n": self._connect_n,
                "allow_diagonals": self._allow_diagonals,
                "board": board_snapshot(self._board),
                "narrative": (
                    f"Connect {self._connect_n} — {self._cols}×{self._rows} "
                    f"gravity-drop board, first to {self._connect_n} in a row wins"
                    + ("." if self._allow_diagonals else " (straight lines only).")
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
            out.extend(["drop_disc", "resign"])
        return out

    def _active_player_id(self) -> Optional[str]:
        return self._white_id if self._side_to_move == "white" else self._black_id

    def _side_for_entity(self, entity_id: str) -> str:
        return "white" if entity_id == self._white_id else "black"

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                         state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        self._seed_from_state(state)
        actor_id = getattr(actor, "id", None)
        if actor_id != self._active_player_id():
            return "Not your turn"
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
        logger.info(
            "[cf] post_resolution actor=%s action=%s column=%r",
            actor_id, action_name, details.get("column"),
        )
        if action_name == "drop_disc":
            return self._handle_drop(actor_id, details, state)
        if action_name == "resign":
            return self._handle_resign(actor_id, state)
        return []

    def _handle_drop(self, actor_id: str, details: Dict[str, Any],
                     state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        raw_col = details.get("column")
        if raw_col is None:
            return [self._invalid(
                actor_id, actor_name, "missing_column",
                f"need `column` (int 1..{self._cols})",
            )]
        try:
            col_1idx = int(raw_col)
        except (TypeError, ValueError):
            return [self._invalid(
                actor_id, actor_name, "bad_column",
                f"`column` must be an integer 1..{self._cols}; got {raw_col!r}",
            )]
        # Accept 1-indexed (UI / LLM convention).
        col = col_1idx - 1
        if not (0 <= col < self._cols):
            return [self._invalid(
                actor_id, actor_name, "column_out_of_range",
                f"column {col_1idx} is out of range (1..{self._cols})",
            )]
        row = drop_row(self._board, col)
        if row is None:
            legal = [c + 1 for c in legal_columns(self._board)]
            return [self._invalid(
                actor_id, actor_name, "column_full",
                f"column {col_1idx} is full; legal columns: {legal}",
            )]
        color = "w" if side == "white" else "b"
        self._board[col][row] = color
        self._last_drop = (col, row)
        # Update entity prop for any UI that wants it.
        ent = state.entities.get(actor_id) if hasattr(state, "entities") else None
        if ent and hasattr(ent, "properties"):
            ent.properties["discs_played"] = (ent.properties.get("discs_played") or 0) + 1

        san = f"{col_1idx}↓{row + 1}"
        self._turn_log.append(f"{side[0].upper()} {san}")

        # Win check.
        line = find_winning_line(
            self._board, col, row, color, self._connect_n, self._allow_diagonals,
        )
        events: List[Dict[str, Any]] = [{
            "type": "cf_drop",
            "player": actor_id,
            "column": col_1idx,
            "row": row + 1,
            "board": board_snapshot(self._board),
            "winning_line": ([(c + 1, r + 1) for (c, r) in line] if line else None),
            "narrative": (
                f"{actor_name} drops into column {col_1idx} (lands on row {row + 1})"
                + (" — WIN" if line else "")
                + "."
            ),
        }]
        if line:
            self._terminal = {"reason": "win", "winner": actor_id}
            events.append({
                "event_type": "cf_win",
                "type": "cf_win",
                "winner": actor_id,
                "loser": self._opp_id(actor_id),
                "winning_line": [(c + 1, r + 1) for (c, r) in line],
                "connect_n": self._connect_n,
                "board": board_snapshot(self._board),
                "narrative": (
                    f"{actor_name} connects {len(line)} in a row — wins!"
                ),
            })
            return events

        # Draw check — board completely full.
        if not legal_columns(self._board):
            self._terminal = {"reason": "draw_board_full", "winner": None}
            events.append({
                "event_type": "cf_draw",
                "type": "cf_draw",
                "reason": "board_full",
                "board": board_snapshot(self._board),
                "narrative": "Board is full — game is a draw.",
            })
            return events

        # Advance turn.
        self._side_to_move = opposite(side)
        return events

    def _handle_resign(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        winner = self._opp_id(actor_id)
        self._terminal = {"reason": "resign", "winner": winner}
        loser_name = self._name_of(state, actor_id)
        winner_name = self._name_of(state, winner) if winner else "opponent"
        return [{
            "event_type": "cf_resign",
            "type": "cf_resign",
            "loser": actor_id, "winner": winner,
            "narrative": f"{loser_name} resigns — {winner_name} wins.",
        }]

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in (self._white_id, self._black_id):
            return {
                "role": "spectator",
                "board": board_snapshot(self._board),
            }
        side = self._side_for_entity(entity_id)
        is_your_turn = (entity_id == self._active_player_id()) and not self._terminal
        legal = [c + 1 for c in legal_columns(self._board)] if is_your_turn else []
        if self._terminal is not None:
            instructions = "Game over."
        elif not is_your_turn:
            instructions = (
                f"Opponent ({opposite(side).upper()}) is on move — wait."
            )
        else:
            diag_note = " (diagonals count)" if self._allow_diagonals else " (NO diagonals — only horizontal + vertical)"
            instructions = (
                f"IT IS YOUR TURN. You are playing {side.upper()}. Call "
                "`drop_disc` with parameter `column` (int 1.."
                f"{self._cols}) to drop a disc into that column — it will "
                "fall to the lowest empty row. "
                f"Legal columns: {legal}. Connect {self._connect_n} of your "
                f"discs in a row to win{diag_note}."
            )
        return {
            "instructions": instructions,
            "your_color": side,
            "is_your_turn": is_your_turn,
            "board_cols": self._cols,
            "board_rows": self._rows,
            "connect_n": self._connect_n,
            "allow_diagonals": self._allow_diagonals,
            "legal_columns": legal,
            "board_ascii": render_ascii_board(self._board),
            "board": board_snapshot(self._board),
            "move_history": list(self._turn_log[-40:]),
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _invalid(self, actor_id: str, actor_name: str, reason: str,
                  narrative: str) -> Dict[str, Any]:
        return {
            "type": "cf_invalid",
            "player": actor_id,
            "reason": reason,
            "narrative": f"{actor_name} — {narrative}.",
        }

    def _name_of(self, state: Any, player_id: Optional[str]) -> str:
        if not player_id:
            return "?"
        if hasattr(state, "entities"):
            ent = state.entities.get(player_id)
            if ent is not None:
                return getattr(ent, "name", player_id)
        return player_id

    def _opp_id(self, player_id: str) -> Optional[str]:
        if player_id == self._white_id:
            return self._black_id
        if player_id == self._black_id:
            return self._white_id
        return None

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "cols": self._cols,
            "rows": self._rows,
            "connect_n": self._connect_n,
            "allow_diagonals": self._allow_diagonals,
            "board": board_snapshot(self._board),
            "white_id": self._white_id,
            "black_id": self._black_id,
            "side_to_move": self._side_to_move,
            "terminal": dict(self._terminal) if self._terminal else None,
            "turn_log": list(self._turn_log),
            "initialized": self._initialized,
            "last_drop": list(self._last_drop) if self._last_drop else None,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "ConnectFiveModule":
        params = data.get("params", {}) or {}
        s = data.get("state", {})
        if "cols" in s and "board_cols" not in params:
            params = {**params, "board_cols": s["cols"]}
        if "rows" in s and "board_rows" not in params:
            params = {**params, "board_rows": s["rows"]}
        if "connect_n" in s and "connect_n" not in params:
            params = {**params, "connect_n": s["connect_n"]}
        if "allow_diagonals" in s and "allow_diagonals" not in params:
            params = {**params, "allow_diagonals": s["allow_diagonals"]}
        mod = cls(name=data.get("name", "connect_five"), params=params)
        b = s.get("board")
        if isinstance(b, list):
            mod._board = [col[:] for col in b]
        mod._white_id = s.get("white_id")
        mod._black_id = s.get("black_id")
        mod._side_to_move = s.get("side_to_move", "white")
        mod._terminal = dict(s["terminal"]) if s.get("terminal") else None
        mod._turn_log = list(s.get("turn_log") or [])
        mod._initialized = bool(s.get("initialized", False))
        ld = s.get("last_drop")
        mod._last_drop = (int(ld[0]), int(ld[1])) if ld else None
        return mod
