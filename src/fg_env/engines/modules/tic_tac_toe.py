"""Tic-Tac-Toe domain module — the classic 3x3 game, played as a best-of match.

Drives a real game of Tic-Tac-Toe on top of the generic action engine:
  - Two players, X and O, alternating one mark per turn.
  - A win is three in a row (horizontal, vertical or diagonal).
  - A full board with no line is a draw — draws are replayed.
  - A MATCH is a best-of series: the first player to win `wins_needed`
    games takes the match. The first move alternates between games.

Custom actions:
  - `place_mark`: parameter `cell` — a square numbered 1-9
    (1=top-left … 9=bottom-right). Invalid / occupied squares are
    auto-redirected to the first empty square so play always progresses.

Events emitted (consumed by TicTacToeViz + UI):
  - `ttt_setup`      { ...snapshot }                  — match begins
  - `ttt_move`       { player, cell, mark, ...snapshot }
  - `ttt_game_over`  { result, winner?, win_line?, ...snapshot }
  - `ttt_match_over` { winner, loser, ...snapshot }   (terminal)
  - `ttt_match_draw` { ...snapshot }                  (terminal, capped)

Cells are numbered 1-9 for players; stored 0-8 internally.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)

# The eight winning lines, as 0-indexed cell triples.
WIN_LINES: List[tuple] = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),   # rows
    (0, 3, 6), (1, 4, 7), (2, 5, 8),   # columns
    (0, 4, 8), (2, 4, 6),              # diagonals
]


class TicTacToeModule(DomainModule):
    """Classic 3x3 Tic-Tac-Toe, played as a configurable best-of match."""

    def __init__(self, name: str = "tic_tac_toe",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        # Games a player must win to take the match (best-of-N → N//2 + 1).
        self._wins_needed: int = max(1, int(p.get("wins_needed", 2) or 2))
        # Safety cap on total games so unlimited draws can't run forever.
        self._max_games: int = self._wins_needed * 2 - 1 + 12
        # Player ids assigned on first tick (seat order: X then O).
        self._players: List[str] = []
        self._marks: Dict[str, str] = {}        # pid -> "X" | "O"
        self._board: List[Optional[str]] = [None] * 9
        self._turn: Optional[str] = None
        self._first_mover: Optional[str] = None  # alternates each game
        self._scores: Dict[str, int] = {}        # pid -> games won
        self._draws: int = 0
        self._game_number: int = 1
        self._terminal: Optional[Dict[str, Any]] = None
        self._log: List[str] = []
        self._initialized: bool = False
        self._setup_emitted: bool = False
        # Carried into the latest event so the viz can highlight a win.
        self._last_win_line: Optional[List[int]] = None
        self._last_result: Optional[str] = None

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return "Classic 3x3 Tic-Tac-Toe, played as a best-of match."

    @property
    def custom_actions(self) -> List[str]:
        return ["place_mark"]

    @property
    def required_properties(self) -> List[str]:
        return ["mark"]

    # ------------------------------------------------------------------ #
    # Seat assignment
    # ------------------------------------------------------------------ #

    def _seed_from_state(self, state: Any) -> None:
        if self._initialized:
            return
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        if len(agents) < 2:
            return
        self._players = [agents[0].id, agents[1].id]
        self._marks = {agents[0].id: "X", agents[1].id: "O"}
        self._scores = {agents[0].id: 0, agents[1].id: 0}
        self._first_mover = self._players[0]
        self._turn = self._players[0]
        for ent in agents[:2]:
            if hasattr(ent, "properties"):
                ent.properties["mark"] = self._marks[ent.id]
                ent.properties["games_won"] = 0
        self._initialized = True
        logger.info("Tic-Tac-Toe seated: X=%s, O=%s (best of, first to %d)",
                    agents[0].name, agents[1].name, self._wins_needed)

    # ------------------------------------------------------------------ #
    # tick / filter_valid_actions
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        if self._initialized and not self._setup_emitted:
            self._setup_emitted = True
            return [{
                "type": "ttt_setup",
                **self._snapshot(state),
                "narrative": (
                    f"Match begins — first to {self._wins_needed} "
                    f"game{'s' if self._wins_needed != 1 else ''} wins."
                ),
            }]
        return []

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        out = [a for a in valid_actions if a not in self.custom_actions]
        if self._terminal is not None:
            return []
        if entity_id not in self._players:
            return out
        if entity_id == self._turn:
            out.append("place_mark")
        return out

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                        state: Any) -> Optional[str]:
        if action_name != "place_mark":
            return None
        if self._terminal is not None:
            return "The match is over"
        if getattr(actor, "id", None) != self._turn:
            return "Not your turn"
        return None

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        if not success or action_name != "place_mark":
            return []
        if self._terminal is not None or actor_id not in self._players:
            return []
        raw = getattr(result, "details", {}) if hasattr(result, "details") else {}
        params = raw.get("_action_params") or {}
        cell_raw = params.get("cell", raw.get("cell"))
        return self._handle_place(actor_id, cell_raw, state)

    def _handle_place(self, actor_id: str, cell_raw: Any,
                      state: Any) -> List[Dict[str, Any]]:
        mark = self._marks[actor_id]
        actor_name = self._name_of(state, actor_id)
        opponent_id = self._opponent(actor_id)

        # Resolve the square — auto-redirect invalid / occupied picks.
        idx = self._parse_cell(cell_raw)
        auto_corrected = False
        if idx is None or self._board[idx] is not None:
            fallback = next((i for i in range(9) if self._board[i] is None), None)
            if fallback is None:
                return []
            auto_corrected = True
            idx = fallback

        self._board[idx] = mark
        # A fresh move belongs to a live game — clear stale win highlight.
        self._last_win_line = None
        self._last_result = None

        narrative = f"{actor_name} plays {mark} on square {idx + 1}."
        if auto_corrected:
            narrative += f" (auto-placed — original pick was taken/invalid)"

        events: List[Dict[str, Any]] = [{
            "type": "ttt_move",
            "agent_choice_valid": not auto_corrected,
            "original_cell": cell_raw,
            "player": actor_id,
            "cell": idx + 1,
            "mark": mark,
            **self._snapshot(state),
            "narrative": narrative,
        }]
        self._log.append(narrative)

        win_line = self._winning_line(mark)
        board_full = all(c is not None for c in self._board)

        if win_line is None and not board_full:
            # Game continues — pass the turn.
            self._turn = opponent_id
            return events

        # --- The game has ended ------------------------------------------------
        if win_line is not None:
            self._scores[actor_id] += 1
            self._last_result = "win"
            self._last_win_line = list(win_line)
            game_narrative = (
                f"{actor_name} ({mark}) wins game {self._game_number}!"
            )
        else:
            self._draws += 1
            self._last_result = "draw"
            game_narrative = f"Game {self._game_number} is a draw."
        self._log.append(game_narrative)
        self._sync_props(state)

        events.append({
            "type": "ttt_game_over",
            "result": self._last_result,
            "winner": actor_id if win_line is not None else None,
            "win_line": self._last_win_line,
            "game_number": self._game_number,
            **self._snapshot(state),
            "narrative": game_narrative,
        })

        # --- Match resolution --------------------------------------------------
        if self._scores[actor_id] >= self._wins_needed:
            self._terminal = {"winner": actor_id, "loser": opponent_id}
            winner_name = actor_name
            loser_name = self._name_of(state, opponent_id)
            events.append({
                "event_type": "ttt_match_over",
                "type": "ttt_match_over",
                "winner": actor_id,
                "loser": opponent_id,
                **self._snapshot(state),
                "narrative": (
                    f"{winner_name} wins the match "
                    f"{self._scores[actor_id]}-{self._scores.get(opponent_id, 0)} "
                    f"and defeats {loser_name}."
                ),
            })
            return events

        games_played = self._game_number
        if games_played >= self._max_games:
            # Game cap reached — decide on score, else a true draw.
            a, b = self._players[0], self._players[1]
            if self._scores[a] != self._scores[b]:
                winner = a if self._scores[a] > self._scores[b] else b
                loser = b if winner == a else a
                self._terminal = {"winner": winner, "loser": loser}
                events.append({
                    "event_type": "ttt_match_over",
                    "type": "ttt_match_over",
                    "winner": winner,
                    "loser": loser,
                    **self._snapshot(state),
                    "narrative": f"Game cap reached — {self._name_of(state, winner)} "
                                 f"wins the match on score.",
                })
            else:
                self._terminal = {"winner": None, "loser": None}
                events.append({
                    "event_type": "ttt_match_draw",
                    "type": "ttt_match_draw",
                    **self._snapshot(state),
                    "narrative": "Game cap reached — the match is a draw.",
                })
            return events

        # --- Start the next game ----------------------------------------------
        self._game_number += 1
        self._board = [None] * 9
        # Alternate who moves first for fairness.
        self._first_mover = self._opponent(self._first_mover or self._players[0])
        self._turn = self._first_mover
        return events

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in self._players:
            return {"role": "spectator", "board": list(self._board)}
        opponent_id = self._opponent(entity_id)
        my_mark = self._marks.get(entity_id, "X")
        opp_mark = self._marks.get(opponent_id or "", "O")
        is_your_turn = (entity_id == self._turn) and self._terminal is None
        legal_cells = [i + 1 for i in range(9) if self._board[i] is None]

        if is_your_turn:
            instructions = (
                f"IT IS YOUR TURN. You are '{my_mark}'. Squares are numbered "
                "1-9 (1=top-left, 2=top-middle, 3=top-right, 4=middle-left, "
                "5=center, 6=middle-right, 7=bottom-left, 8=bottom-middle, "
                "9=bottom-right) — see `board` below. Call `place_mark` with "
                "`cell` set to ONE number from `legal_cells`. STRATEGY: "
                "(1) if you have two-in-a-row with an open third square, "
                "TAKE THE WIN. (2) Otherwise, if the opponent has "
                "two-in-a-row, BLOCK it. (3) Otherwise prefer the center, "
                "then a corner, and look to create a fork (two threats at "
                "once)."
            )
        else:
            instructions = (
                "The opponent is moving. You have no legal action this "
                "turn — wait for your next turn."
            )

        return {
            "instructions": instructions,
            "is_your_turn": is_your_turn,
            "your_mark": my_mark,
            "opponent_mark": opp_mark,
            "board": self._render_board(),
            "board_cells": [self._board[i] or (i + 1) for i in range(9)],
            "legal_cells": legal_cells,
            "game_number": self._game_number,
            "wins_needed": self._wins_needed,
            "match_score": {
                "you": self._scores.get(entity_id, 0),
                "opponent": self._scores.get(opponent_id or "", 0),
                "draws": self._draws,
            },
            "recent_log": list(self._log[-12:]),
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    def _render_board(self) -> str:
        """ASCII board — placed marks shown, empty squares show their number."""
        def sym(i: int) -> str:
            return self._board[i] if self._board[i] is not None else str(i + 1)
        rows = [
            f" {sym(0)} | {sym(1)} | {sym(2)} ",
            "---+---+---",
            f" {sym(3)} | {sym(4)} | {sym(5)} ",
            "---+---+---",
            f" {sym(6)} | {sym(7)} | {sym(8)} ",
        ]
        return "\n".join(rows)

    # ------------------------------------------------------------------ #
    # Snapshot for the visualization
    # ------------------------------------------------------------------ #

    def _snapshot(self, state: Any) -> Dict[str, Any]:
        """Ground-truth match state, embedded in every emitted event."""
        return {
            "board": list(self._board),
            "players": [
                {
                    "player_id": pid,
                    "name": self._name_of(state, pid),
                    "mark": self._marks.get(pid, "?"),
                    "score": self._scores.get(pid, 0),
                }
                for pid in self._players
            ],
            "game_number": self._game_number,
            "wins_needed": self._wins_needed,
            "draws": self._draws,
            "turn": self._turn if self._terminal is None else None,
            "win_line": self._last_win_line,
            "result": self._last_result,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _parse_cell(self, raw: Any) -> Optional[int]:
        """Parse a 1-9 square id into a 0-8 board index, or None."""
        if raw is None:
            return None
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return n - 1 if 1 <= n <= 9 else None

    def _winning_line(self, mark: str) -> Optional[tuple]:
        for line in WIN_LINES:
            if all(self._board[i] == mark for i in line):
                return line
        return None

    def _opponent(self, pid: Optional[str]) -> Optional[str]:
        for other in self._players:
            if other != pid:
                return other
        return None

    def _name_of(self, state: Any, pid: Optional[str]) -> str:
        if not pid:
            return "?"
        if hasattr(state, "entities"):
            ent = state.entities.get(pid)
            if ent is not None:
                return getattr(ent, "name", pid)
        return pid

    def _sync_props(self, state: Any) -> None:
        """Mirror game-win counts onto entity properties for the UI."""
        if not hasattr(state, "entities"):
            return
        for pid in self._players:
            ent = state.entities.get(pid)
            if ent is not None and hasattr(ent, "properties"):
                ent.properties["games_won"] = self._scores.get(pid, 0)

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "wins_needed": self._wins_needed,
            "max_games": self._max_games,
            "players": list(self._players),
            "marks": dict(self._marks),
            "board": list(self._board),
            "turn": self._turn,
            "first_mover": self._first_mover,
            "scores": dict(self._scores),
            "draws": self._draws,
            "game_number": self._game_number,
            "terminal": dict(self._terminal) if self._terminal else None,
            "log": list(self._log),
            "initialized": self._initialized,
            "setup_emitted": self._setup_emitted,
            "last_win_line": list(self._last_win_line) if self._last_win_line else None,
            "last_result": self._last_result,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "TicTacToeModule":
        mod = cls(name=data.get("name", "tic_tac_toe"), params=data.get("params", {}))
        s = data.get("state", {})
        mod._wins_needed = int(s.get("wins_needed", mod._wins_needed) or mod._wins_needed)
        mod._max_games = int(s.get("max_games", mod._max_games) or mod._max_games)
        mod._players = list(s.get("players") or [])
        mod._marks = dict(s.get("marks") or {})
        mod._board = list(s.get("board") or [None] * 9)
        mod._turn = s.get("turn")
        mod._first_mover = s.get("first_mover")
        mod._scores = dict(s.get("scores") or {})
        mod._draws = int(s.get("draws") or 0)
        mod._game_number = int(s.get("game_number") or 1)
        mod._terminal = dict(s["terminal"]) if s.get("terminal") else None
        mod._log = list(s.get("log") or [])
        mod._initialized = bool(s.get("initialized", False))
        mod._setup_emitted = bool(s.get("setup_emitted", False))
        mod._last_win_line = list(s["last_win_line"]) if s.get("last_win_line") else None
        mod._last_result = s.get("last_result")
        return mod
