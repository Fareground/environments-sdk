"""Go — classic 2-player territory game.

Rules implemented
~~~~~~~~~~~~~~~~~
  - NxN grid (9 / 13 / 19), stones at intersections, 4-connected adjacency.
  - Black moves first, white gets komi (default 6.5).
  - Capture: any group of stones reduced to ZERO liberties is removed.
    Captures of opposing groups happen BEFORE the suicide check for the
    placed stone, so a self-suicide that captures is legal.
  - Suicide forbidden: after captures, if your OWN group has zero
    liberties the move is rejected (falls through to the engine's
    defensive recovery).
  - Simple KO rule: a move that would EXACTLY recreate the previous
    board position is rejected. This is the standard "positional super-
    ko" simplification used in computer Go.
  - PASS action: a player may pass instead of placing. Two consecutive
    passes end the game.
  - Chinese scoring at game end: each side scores
      (own stones on board) + (empty regions bordered ONLY by own
       stones) + (komi, white only).
    Highest score wins; .5 komi guarantees no draws.

Public events
~~~~~~~~~~~~~
  - `go_init`     { board_size, komi, board, side_to_move,
                    best_of, wins_to_clinch }                       one-shot
  - `go_move`     { player, side, row, col, captured, board,
                    side_to_move, move_count }                      placement
  - `go_pass`     { player, side, consecutive_passes,
                    side_to_move }                                  pass
  - `go_game_end` { game_number, winner, score, board,
                    match_score, moves_used }                       per-game
  - `go_next_game`{ game_number, match_score, board, side_to_move,
                    board_size, komi }
  - `go_invalid`  { actor_id, reason, message }
  - `go_win`      { winner, loser, match_score, best_of }           match
"""

from __future__ import annotations

import logging
import re
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


ALLOWED_BOARD_SIZES = (9, 13, 19)
DEFAULT_BOARD_SIZE = 9
DEFAULT_KOMI = 6.5
DEFAULT_WINS_NEEDED = 1
MIN_WINS_NEEDED = 1
MAX_WINS_NEEDED = 5

EMPTY = "."
BLACK = "B"
WHITE = "W"

# Column letters — straight A..S, no I-skip, to keep parsing simple
# (LLMs often confuse "I" with "1" anyway when we skip).
COL_LETTERS = "ABCDEFGHIJKLMNOPQRS"

# 4-connected neighbors.
NEIGHBORS_4: Tuple[Tuple[int, int], ...] = (
    (-1, 0), (1, 0), (0, -1), (0, 1),
)


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
    """Parse 'D5' / 'd5' / 'd 5' / '(3, 5)' / 'row 3 col 5' → (row, col) 0-indexed.
    Returns None on failure. Honors no-I-skip alphabet."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    s = s.strip("\"'()[]{}")
    m = re.match(r"^\s*([a-s])\s*(\d{1,2})\s*$", s)
    if m:
        col = COL_LETTERS.index(m.group(1).upper())
        row = int(m.group(2)) - 1
        if 0 <= row < board_size and 0 <= col < board_size:
            return (row, col)
        return None
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
    return f"{COL_LETTERS[col]}{row + 1}"


# ---------------------------------------------------------------------------
# Board rendering
# ---------------------------------------------------------------------------


def _render_board_ascii(board: List[List[str]]) -> str:
    """ASCII board with row/col labels. Star points marked with '+'."""
    n = len(board)
    star_pts = _star_points(n)
    lines: List[str] = []
    header = "     " + " ".join(COL_LETTERS[i] for i in range(n))
    lines.append(header)
    for r in range(n):
        cells: List[str] = []
        for c in range(n):
            v = board[r][c]
            if v == EMPTY:
                cells.append("+" if (r, c) in star_pts else ".")
            else:
                cells.append(v)
        lines.append(f"  {r + 1:>2} " + " ".join(cells))
    return "\n".join(lines)


def _star_points(size: int) -> Set[Tuple[int, int]]:
    if size == 9:
        return {(2, 2), (2, 6), (6, 2), (6, 6), (4, 4)}
    if size == 13:
        return {(3, 3), (3, 9), (9, 3), (9, 9), (6, 6)}
    if size == 19:
        pts = [3, 9, 15]
        return {(r, c) for r in pts for c in pts}
    return set()


# ---------------------------------------------------------------------------
# Group / liberty helpers
# ---------------------------------------------------------------------------

def _group_and_liberties(board: List[List[str]], r: int, c: int
                          ) -> Tuple[Set[Tuple[int, int]], Set[Tuple[int, int]]]:
    """Flood-fill the group containing (r, c) and collect its liberties.
    Returns ({stones in group}, {empty neighbor intersections}). If
    (r, c) is empty, returns ({}, {}). """
    n = len(board)
    color = board[r][c]
    if color == EMPTY:
        return set(), set()
    group: Set[Tuple[int, int]] = set()
    liberties: Set[Tuple[int, int]] = set()
    queue: deque = deque([(r, c)])
    group.add((r, c))
    while queue:
        cur_r, cur_c = queue.popleft()
        for dr, dc in NEIGHBORS_4:
            nr, nc = cur_r + dr, cur_c + dc
            if not (0 <= nr < n and 0 <= nc < n):
                continue
            v = board[nr][nc]
            if v == EMPTY:
                liberties.add((nr, nc))
            elif v == color and (nr, nc) not in group:
                group.add((nr, nc))
                queue.append((nr, nc))
    return group, liberties


def _board_hash(board: List[List[str]]) -> str:
    """Compact string representation for KO detection."""
    return "".join("".join(row) for row in board)


# ---------------------------------------------------------------------------
# Scoring (Chinese)
# ---------------------------------------------------------------------------

def _score_board(board: List[List[str]], komi: float
                  ) -> Dict[str, Any]:
    """Chinese scoring: stones on board + empty regions bordered only
    by one color. Komi added to white. Returns a breakdown dict."""
    n = len(board)
    black_stones = sum(1 for r in range(n) for c in range(n) if board[r][c] == BLACK)
    white_stones = sum(1 for r in range(n) for c in range(n) if board[r][c] == WHITE)
    # Flood-fill empty regions.
    visited: Set[Tuple[int, int]] = set()
    black_territory = 0
    white_territory = 0
    neutral_territory = 0
    for r in range(n):
        for c in range(n):
            if board[r][c] != EMPTY or (r, c) in visited:
                continue
            # BFS this region.
            region: Set[Tuple[int, int]] = set()
            borders: Set[str] = set()
            queue: deque = deque([(r, c)])
            region.add((r, c))
            while queue:
                cr, cc = queue.popleft()
                for dr, dc in NEIGHBORS_4:
                    nr, nc = cr + dr, cc + dc
                    if not (0 <= nr < n and 0 <= nc < n):
                        continue
                    v = board[nr][nc]
                    if v == EMPTY:
                        if (nr, nc) not in region:
                            region.add((nr, nc))
                            queue.append((nr, nc))
                    else:
                        borders.add(v)
            visited |= region
            if borders == {BLACK}:
                black_territory += len(region)
            elif borders == {WHITE}:
                white_territory += len(region)
            else:
                neutral_territory += len(region)
    black_total = black_stones + black_territory
    white_total = white_stones + white_territory + komi
    return {
        "black_stones": black_stones,
        "black_territory": black_territory,
        "black_total": float(black_total),
        "white_stones": white_stones,
        "white_territory": white_territory,
        "white_komi": komi,
        "white_total": float(white_total),
        "neutral_territory": neutral_territory,
        "komi": komi,
    }


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class GoModule(DomainModule):
    """Turn-based Go with full capture / suicide / ko / pass rules."""

    # No hidden info — chat enabled.
    suppress_chat = False

    def __init__(self, name: str = "go",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        bs = int(p.get("board_size") or DEFAULT_BOARD_SIZE)
        if bs not in ALLOWED_BOARD_SIZES:
            bs = DEFAULT_BOARD_SIZE
        self._N: int = bs
        try:
            self._komi: float = float(p.get("komi") if p.get("komi") is not None else DEFAULT_KOMI)
        except (TypeError, ValueError):
            self._komi = DEFAULT_KOMI
        wn = int(p.get("wins_needed") or DEFAULT_WINS_NEEDED)
        wn = max(MIN_WINS_NEEDED, min(MAX_WINS_NEEDED, wn))
        self._wins_to_clinch: int = wn
        self._best_of: int = wn * 2 - 1

        # Seat IDs (assigned at _seed_from_state).
        self._black_id: Optional[str] = None
        self._white_id: Optional[str] = None
        # Match-level score.
        self._black_games: int = 0
        self._white_games: int = 0
        self._game_number: int = 1
        # Per-game state.
        self._board: List[List[str]] = self._fresh_board()
        self._side_to_move: str = "black"   # Black moves first
        self._move_count: int = 0
        self._black_captures: int = 0
        self._white_captures: int = 0
        self._previous_position: Optional[str] = None   # for ko
        self._consecutive_passes: int = 0

        self._terminal: Optional[Dict[str, Any]] = None
        self._initialized = False
        logger.info(
            "[go] init size=%d komi=%.1f wins_needed=%d",
            bs, self._komi, wn,
        )

    @property
    def description(self) -> str:
        return (f"Go — {self._N}×{self._N} board, komi {self._komi}. "
                "Black moves first; two passes end the game.")

    @property
    def custom_actions(self) -> List[str]:
        return ["place_stone", "pass_move"]

    @property
    def required_properties(self) -> List[str]:
        return ["color"]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _fresh_board(self) -> List[List[str]]:
        return [[EMPTY for _ in range(self._N)] for _ in range(self._N)]

    def _color_for(self, side: str) -> str:
        return BLACK if side == "black" else WHITE

    def _opp_side(self, side: str) -> str:
        return "white" if side == "black" else "black"

    def _side_for_entity(self, entity_id: str) -> str:
        # Black is the FIRST-listed agent (Go convention).
        return "black" if entity_id == self._black_id else "white"

    def _active_player_id(self) -> Optional[str]:
        return self._black_id if self._side_to_move == "black" else self._white_id

    # ------------------------------------------------------------------ #
    # Seat assignment
    # ------------------------------------------------------------------ #

    def _seed_from_state(self, state: Any) -> None:
        if self._initialized:
            return
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        if len(agents) < 2:
            return
        # Black = first listed (moves first), white = second (gets komi).
        self._black_id = agents[0].id
        self._white_id = agents[1].id
        for ent, color in ((agents[0], "black"), (agents[1], "white")):
            if hasattr(ent, "properties"):
                ent.properties["color"] = color
                ent.properties["captures"] = 0
                ent.properties["stones_on_board"] = 0
                ent.properties["games_won"] = 0
        self._initialized = True
        logger.info(
            "[go] seated black=%s white=%s size=%d komi=%.1f",
            agents[0].name, agents[1].name, self._N, self._komi,
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
                "type": "go_init",
                "board_size": self._N,
                "komi": self._komi,
                "board": self._board_snapshot(),
                "side_to_move": self._side_to_move,
                "best_of": self._best_of,
                "wins_to_clinch": self._wins_to_clinch,
                "match_score": {"black": 0, "white": 0},
                "game_number": 1,
                "narrative": (
                    f"Go {self._N}×{self._N} — {bo_text}, komi "
                    f"{self._komi}. Black moves first."
                ),
            }]
        return []

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        self._seed_from_state(state)
        if self._terminal is not None:
            return []
        if entity_id not in (self._black_id, self._white_id):
            return [a for a in valid_actions if a not in self.custom_actions]
        out: List[str] = [a for a in valid_actions if a not in self.custom_actions]
        if entity_id == self._active_player_id():
            out.extend(["place_stone", "pass_move"])
        return out

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                         state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        self._seed_from_state(state)
        actor_id = getattr(actor, "id", None)
        if actor_id not in (self._black_id, self._white_id):
            return "Not a player"
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
        if action_name == "place_stone":
            return self._handle_place(actor_id, details, state)
        if action_name == "pass_move":
            return self._handle_pass(actor_id, state)
        return []

    def _try_play(self, color: str, r: int, c: int
                   ) -> Optional[Tuple[List[List[str]], List[Tuple[int, int]]]]:
        """Attempt to play `color` at (r, c) on a COPY of the board.
        Returns (new_board, captured_cells) on success, or None on
        illegal move (occupied / suicide / ko)."""
        if self._board[r][c] != EMPTY:
            return None
        # Copy the board.
        new_board: List[List[str]] = [row[:] for row in self._board]
        new_board[r][c] = color
        opp = WHITE if color == BLACK else BLACK
        # 1. Capture opposing groups with zero liberties touching (r, c).
        captured: List[Tuple[int, int]] = []
        for dr, dc in NEIGHBORS_4:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < self._N and 0 <= nc < self._N):
                continue
            if new_board[nr][nc] != opp:
                continue
            group, libs = _group_and_liberties(new_board, nr, nc)
            if not libs:
                for gr, gc in group:
                    new_board[gr][gc] = EMPTY
                    captured.append((gr, gc))
        # 2. Suicide check: after captures, our own group must have
        #    at least one liberty.
        own_group, own_libs = _group_and_liberties(new_board, r, c)
        if not own_libs:
            return None
        # 3. KO check: must not recreate the previous position.
        new_hash = _board_hash(new_board)
        if self._previous_position is not None and new_hash == self._previous_position:
            return None
        return new_board, captured

    def _handle_place(self, actor_id: str, details: Dict[str, Any],
                      state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        color = self._color_for(side)
        attribution = "agent_executed"
        original_request = dict(details)
        # Resolve target cell.
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
        # Speech / reasoning recovery (like Hex).
        if cell is None or not self._is_legal(color, *cell):
            recovered = self._recover_coord_from_text(details, color)
            if recovered is not None:
                logger.info(
                    "[go] %s recovered coord %s from text",
                    side, _coord_to_str(*recovered),
                )
                cell = recovered
                attribution = "text_recovered"

        if cell is None or not self._is_legal(color, *cell):
            # Strategic fallback — play a random legal move (Go has too
            # many legal moves for a smart heuristic to be worth the
            # complexity at this level). If absolutely nothing legal,
            # auto-pass.
            fallback = self._fallback_cell(color)
            if fallback is None:
                logger.warning(
                    "[go] %s no legal move available → auto-pass",
                    side,
                )
                return self._attributed_pass(actor_id, state, original_request, "no_legal_move")
            logger.warning(
                "[go] %s invalid move details=%r — substituting %s",
                side, details, _coord_to_str(*fallback),
            )
            cell = fallback
            attribution = "engine_selected"

        r, c = cell
        result = self._try_play(color, r, c)
        if result is None:
            # Shouldn't happen — _is_legal already filtered. Defensive
            # pass to keep the game moving.
            logger.error(
                "[go] %s _try_play unexpectedly None at %s",
                side, _coord_to_str(r, c),
            )
            return self._attributed_pass(actor_id, state, original_request, "legality_recheck_failed")
        new_board, captured = result
        # Commit.
        self._previous_position = _board_hash(self._board)
        self._board = new_board
        self._move_count += 1
        if captured:
            if color == BLACK:
                self._black_captures += len(captured)
            else:
                self._white_captures += len(captured)
        self._consecutive_passes = 0
        next_side = self._opp_side(side)
        self._side_to_move = next_side
        self._refresh_player_props(state)
        return [{
            "type": "go_move",
            "attribution": attribution,
            "original_request": original_request,
            "agent_choice_valid": attribution != "engine_selected",
            "fallback_reason": "invalid_requested_move" if attribution == "engine_selected" else None,
            "player": actor_id,
            "side": side,
            "color": color,
            "row": r + 1,
            "col": c + 1,
            "coord": _coord_to_str(r, c),
            "captured": [
                {"row": gr + 1, "col": gc + 1, "coord": _coord_to_str(gr, gc)}
                for gr, gc in captured
            ],
            "board": self._board_snapshot(),
            "side_to_move": next_side,
            "move_count": self._move_count,
            "black_captures": self._black_captures,
            "white_captures": self._white_captures,
            "narrative": (
                (f"Engine selects {_coord_to_str(r, c)} for {actor_name} ({side}) after an invalid request"
                 if attribution == "engine_selected" else
                 f"{actor_name} ({side}) plays at {_coord_to_str(r, c)}"
                 + (" (recovered from text)" if attribution == "text_recovered" else ""))
                + (f" — captures {len(captured)}" if captured else "")
                + "."
            ),
        }]

    def _attributed_pass(self, actor_id: str, state: Any,
                         original_request: Dict[str, Any], reason: str) -> List[Dict[str, Any]]:
        events = self._handle_pass(actor_id, state)
        for event in events:
            if event.get("type") == "go_pass":
                event.update(attribution="auto_passed", original_request=original_request,
                             agent_choice_valid=False, fallback_reason=reason)
                event["narrative"] = "Engine automatically passes after an invalid placement request."
        return events

    def _handle_pass(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        self._consecutive_passes += 1
        self._previous_position = _board_hash(self._board)
        self._move_count += 1
        events: List[Dict[str, Any]] = [{
            "type": "go_pass",
            "attribution": "agent_executed",
            "agent_choice_valid": True,
            "player": actor_id,
            "side": side,
            "consecutive_passes": self._consecutive_passes,
            "side_to_move": self._opp_side(side),
            "narrative": (
                f"{actor_name} ({side}) passes "
                f"({self._consecutive_passes} consecutive)."
            ),
        }]
        if self._consecutive_passes >= 2:
            events.extend(self._handle_game_end(state, "two_passes"))
        else:
            self._side_to_move = self._opp_side(side)
        return events

    # ------------------------------------------------------------------ #
    # Game-end / scoring / match wrapper
    # ------------------------------------------------------------------ #

    def _handle_game_end(self, state: Any, reason: str) -> List[Dict[str, Any]]:
        score = _score_board(self._board, self._komi)
        black_total = score["black_total"]
        white_total = score["white_total"]
        if black_total > white_total:
            winner_side = "black"
        elif white_total > black_total:
            winner_side = "white"
        else:
            # Komi has half points so this shouldn't happen with default
            # komi, but defensively handle integer komi → tie.
            winner_side = "white"   # default tie-break to white (komi side)
        winner_id = self._black_id if winner_side == "black" else self._white_id
        black_name = self._name_of(state, self._black_id)
        white_name = self._name_of(state, self._white_id)
        if winner_side == "black":
            self._black_games += 1
        else:
            self._white_games += 1
        events: List[Dict[str, Any]] = [{
            "type": "go_game_end",
            "game_number": self._game_number,
            "winner": winner_id,
            "winner_side": winner_side,
            "reason": reason,
            "score": score,
            "match_score": {
                "black": self._black_games,
                "white": self._white_games,
            },
            "moves_used": self._move_count,
            "board": self._board_snapshot(),
            "narrative": (
                f"Game {self._game_number} — "
                f"{self._name_of(state, winner_id)} ({winner_side}) wins. "
                f"Black {black_total:.1f} ({score['black_stones']} stones + "
                f"{score['black_territory']} territory) · "
                f"White {white_total:.1f} ({score['white_stones']} stones + "
                f"{score['white_territory']} territory + {self._komi} komi). "
                f"Match {black_name} {self._black_games}–"
                f"{self._white_games} {white_name}."
            ),
        }]

        match_winner_id: Optional[str] = None
        if self._black_games >= self._wins_to_clinch:
            match_winner_id = self._black_id
        elif self._white_games >= self._wins_to_clinch:
            match_winner_id = self._white_id

        if match_winner_id is not None:
            self._terminal = {"reason": "match_won", "winner": match_winner_id}
            events.append({
                "event_type": "go_win",
                "type": "go_win",
                "winner": match_winner_id,
                "loser": self._opp_id(match_winner_id),
                "match_score": {"black": self._black_games, "white": self._white_games},
                "best_of": self._best_of,
                "narrative": (
                    f"{self._name_of(state, match_winner_id)} clinches the "
                    f"best-of-{self._best_of} match "
                    f"{self._black_games}–{self._white_games}."
                ),
            })
            return events

        # Reset for next game.
        self._game_number += 1
        self._board = self._fresh_board()
        self._side_to_move = "black"
        self._move_count = 0
        self._black_captures = 0
        self._white_captures = 0
        self._previous_position = None
        self._consecutive_passes = 0
        events.append({
            "type": "go_next_game",
            "game_number": self._game_number,
            "match_score": {"black": self._black_games, "white": self._white_games},
            "board": self._board_snapshot(),
            "side_to_move": "black",
            "board_size": self._N,
            "komi": self._komi,
            "narrative": (
                f"Starting game {self._game_number} of {self._best_of}. "
                f"(Fresh board; Black moves first.)"
            ),
        })
        return events

    # ------------------------------------------------------------------ #
    # Legality / fallback / recovery
    # ------------------------------------------------------------------ #

    def _is_legal(self, color: str, r: int, c: int) -> bool:
        return self._try_play(color, r, c) is not None

    def _legal_empty_cells(self, color: str) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for r in range(self._N):
            for c in range(self._N):
                if self._board[r][c] == EMPTY and self._is_legal(color, r, c):
                    out.append((r, c))
        return out

    def _fallback_cell(self, color: str) -> Optional[Tuple[int, int]]:
        """Pick a sensible legal cell. Prefer star points → corners /
        sides → anything legal. None if no legal placement exists."""
        legal = set(self._legal_empty_cells(color))
        if not legal:
            return None
        # 1. Star point if free.
        for sp in _star_points(self._N):
            if sp in legal:
                return sp
        # 2. 3-3 / 4-4 corner / side approximations: skip; just return
        #    first legal cell ranked by board-position quality. We pick
        #    the cell with the most empty neighbors (i.e. away from
        #    contact).
        ranked = sorted(
            legal,
            key=lambda rc: -sum(
                1 for dr, dc in NEIGHBORS_4
                if 0 <= rc[0] + dr < self._N and 0 <= rc[1] + dc < self._N
                and self._board[rc[0] + dr][rc[1] + dc] == EMPTY
            ),
        )
        return ranked[0]

    def _recover_coord_from_text(self, details: Dict[str, Any],
                                  color: str
                                  ) -> Optional[Tuple[int, int]]:
        """Scan speech / reasoning fields for the FIRST coord-shaped
        token that's a legal move for `color`. Recovers the LLM's
        intent when it typed the move in its CoT but forgot to send
        it as a structured arg."""
        parts: List[str] = []
        for key in ("reasoning", "speech", "narrative", "thought", "cot"):
            v = details.get(key)
            if isinstance(v, str) and v:
                parts.append(v)
        if not parts:
            return None
        text = " ".join(parts).lower()
        candidates: List[Tuple[int, int]] = []
        for m in re.finditer(r"\b([a-s])\s*(\d{1,2})\b", text):
            col = COL_LETTERS.index(m.group(1).upper())
            row = int(m.group(2)) - 1
            if 0 <= row < self._N and 0 <= col < self._N:
                candidates.append((row, col))
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
        for r, c in candidates:
            if self._board[r][c] == EMPTY and self._is_legal(color, r, c):
                return (r, c)
        return None

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in (self._black_id, self._white_id):
            return {
                "role": "spectator",
                "board": self._board_snapshot(),
                "board_size": self._N,
                "komi": self._komi,
            }
        side = self._side_for_entity(entity_id)
        is_active = (entity_id == self._active_player_id())
        if self._terminal is not None:
            instructions = "Match over."
        elif not is_active:
            instructions = (
                f"It is the opponent's turn. You play {side.upper()}. "
                f"Wait for their move."
            )
        else:
            grid = _render_board_ascii(self._board)
            color = self._color_for(side)
            legal = self._legal_empty_cells(color)
            sample = ", ".join(_coord_to_str(r, c) for r, c in legal[:30])
            if len(legal) > 30:
                sample += f", … ({len(legal) - 30} more)"
            score_now = _score_board(self._board, self._komi)
            pass_hint = ""
            if self._consecutive_passes == 1:
                opp = self._opp_side(side).upper()
                pass_hint = (
                    f"\n⚠ {opp} just passed. If YOU also pass, the game "
                    f"ends and scoring runs immediately. Only pass if "
                    f"you're confident the count is in your favor.\n"
                )
            instructions = (
                f"IT IS YOUR TURN. You are {side.upper()} in Go "
                f"{self._N}×{self._N}. Move {self._move_count + 1}.\n"
                f"\n"
                f"BOARD ('.' = empty, '+' = star point, 'B' = black, "
                f"'W' = white):\n"
                f"{grid}\n"
                f"\n"
                f"CURRENT SCORE (would-be if game ended now): "
                f"Black {score_now['black_total']:.1f} · "
                f"White {score_now['white_total']:.1f} "
                f"(komi {self._komi}).\n"
                f"\n"
                f"Captures so far — Black: {self._black_captures}, "
                f"White: {self._white_captures}.\n"
                f"\n"
                f"Sample legal moves: {sample}\n"
                f"{pass_hint}\n"
                f"YOUR TOOL CALL — output exactly one of these shapes:\n"
                f'  place_stone(row=4, col=4)        # any legal empty intersection\n'
                f"  pass_move()                       # no params; consider only if settled\n"
                f"\n"
                f"For `place_stone`, both `row` and `col` are REQUIRED "
                f"1-indexed integers in [1, {self._N}]. The intersection "
                f"must be empty, the move must not be suicide, and it "
                f"must not violate KO. If your move is illegal the "
                f"engine substitutes a safe star-point cell — you lose "
                f"tempo. Two consecutive passes end the game."
            )

        return {
            "instructions": instructions,
            "your_color": side,
            "board": self._board_snapshot(),
            "board_size": self._N,
            "komi": self._komi,
            "side_to_move": self._side_to_move,
            "is_your_turn": is_active,
            "move_count": self._move_count,
            "consecutive_passes": self._consecutive_passes,
            "black_captures": self._black_captures,
            "white_captures": self._white_captures,
            "match_score": {
                "black": self._black_games,
                "white": self._white_games,
            },
            "game_number": self._game_number,
            "best_of": self._best_of,
            "wins_to_clinch": self._wins_to_clinch,
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Misc helpers
    # ------------------------------------------------------------------ #

    def _board_snapshot(self) -> List[List[str]]:
        return [row[:] for row in self._board]

    def _refresh_player_props(self, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        for entity_id, side, captures in (
            (self._black_id, "black", self._black_captures),
            (self._white_id, "white", self._white_captures),
        ):
            ent = state.entities.get(entity_id) if entity_id else None
            if ent is None or not hasattr(ent, "properties"):
                continue
            color = self._color_for(side)
            stones = sum(
                1 for r in range(self._N) for c in range(self._N)
                if self._board[r][c] == color
            )
            games_won = self._black_games if side == "black" else self._white_games
            ent.properties["captures"] = captures
            ent.properties["stones_on_board"] = stones
            ent.properties["games_won"] = games_won

    def _name_of(self, state: Any, player_id: Optional[str]) -> str:
        if player_id is None:
            return "?"
        if hasattr(state, "entities"):
            ent = state.entities.get(player_id)
            if ent is not None and getattr(ent, "name", None):
                return ent.name
        return player_id

    def _opp_id(self, player_id: str) -> Optional[str]:
        if player_id == self._black_id:
            return self._white_id
        if player_id == self._white_id:
            return self._black_id
        return None
