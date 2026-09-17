"""Royal Game of Ur — full engine.

The world's oldest known board game (~2600 BC, Sumerian — British Museum
tablet U.7771). Two players race 7 pieces around their own 14-square
track. Movement is driven by FOUR TETRAHEDRAL DICE — not a six-sided
cube. Each die has 2 marked corners and 2 unmarked corners; only the
top corner counts. So each die is effectively a fair coin flip, and the
sum (number of marked tops, 0–4) is Binomial(4, ½):

    0 → 1/16  (6.25%)   no move, turn passes
    1 → 4/16  (25%)
    2 → 6/16  (37.5%)   modal — most common
    3 → 4/16  (25%)
    4 → 1/16  (6.25%)

# Board (3 × 8 grid with two missing notches)

    row 0 (white private):  c0 c1 c2 c3  .   .  c6 c7
    row 1 (shared battle):  c0 c1 c2 c3 c4 c5 c6 c7
    row 2 (black private):  c0 c1 c2 c3  .   .  c6 c7

The two `.` notches per outer row are NOT squares — pieces cannot sit
there. Each player has 14 path squares total: 4 private start, 8 shared
middle, 2 private end.

# Path (per player, indexed 1..14)

    pos 1 → entry square in player's private start strip
    pos 2, 3 → next two private squares
    pos 4 → ROSETTE (private rosette — safe, extra turn)
    pos 5..12 → SHARED middle row (capture zone)
    pos 8  → CENTRAL ROSETTE (shared, safe even from opponent)
    pos 13 → first private end square
    pos 14 → ROSETTE (private rosette — safe, extra turn)
    pos 15 → bear-off (off the board)

Position 0 = piece is off-board waiting to enter.
Position 15 = piece has been borne off.

# Rules (Finkel reconstruction — the standard)

1. Roll 4 tetrahedral dice; sum (0–4) is the move distance.
2. Roll of 0 → turn auto-passes.
3. With a roll ≥ 1, pick ONE of your pieces to advance by `roll`
   squares. A piece at position 0 must roll the entry square exactly
   (position 1..4 depending on roll value).
4. Two pieces of the same color cannot share a square.
5. Capture: landing on a SHARED-row square (pos 5..12) occupied by an
   opponent piece sends that piece back to position 0 — UNLESS the
   square is the central rosette (pos 8), where pieces are safe.
6. Landing on ANY rosette (pos 4, 8, 14) grants an EXTRA TURN.
7. To bear off, a piece must move EXACTLY past position 14 (roll of
   `15 - current_position`). Overshoot is illegal — pick another piece.
8. If no piece has a legal move for the rolled value, the turn passes.
9. First player to bear off all 7 pieces WINS.

Events:
  - `ur_init`         { board, ... }                          one-shot
  - `ur_roll`         { player, roll, dice[4] }
  - `ur_move`         { player, piece_idx, from, to, captured?, rosette? }
  - `ur_pass`         { player, reason }
  - `ur_bear_off`     { player, piece_idx, pieces_off }
  - `ur_resign`       { loser, winner }                       terminal
  - `ur_win`          { winner, loser, score }                terminal
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Board / path
# ---------------------------------------------------------------------------

PIECES_PER_PLAYER = 7
PATH_LENGTH = 14
BEAR_OFF = 15

ROSETTES = {4, 8, 14}        # path positions that grant extra turn + safety
CENTRAL_ROSETTE = 8           # the shared safe square
SHARED_PATH_RANGE = range(5, 13)  # positions 5..12 are the shared middle row

# (row, col) coordinates for each path position, per player.
# Used only by the FE viz / ASCII renderer — the engine is purely
# path-position based.
WHITE_PATH_COORDS: Dict[int, Tuple[int, int]] = {
    1:  (0, 3), 2:  (0, 2), 3:  (0, 1), 4:  (0, 0),  # private start (rosette @4)
    5:  (1, 0), 6:  (1, 1), 7:  (1, 2), 8:  (1, 3),  # shared (central rosette @8)
    9:  (1, 4), 10: (1, 5), 11: (1, 6), 12: (1, 7),  # shared
    13: (0, 7), 14: (0, 6),                            # private end (rosette @14)
}
BLACK_PATH_COORDS: Dict[int, Tuple[int, int]] = {
    1:  (2, 3), 2:  (2, 2), 3:  (2, 1), 4:  (2, 0),
    5:  (1, 0), 6:  (1, 1), 7:  (1, 2), 8:  (1, 3),
    9:  (1, 4), 10: (1, 5), 11: (1, 6), 12: (1, 7),
    13: (2, 7), 14: (2, 6),
}


def path_coords_for(side: str) -> Dict[int, Tuple[int, int]]:
    return WHITE_PATH_COORDS if side == "white" else BLACK_PATH_COORDS


def opposite(side: str) -> str:
    return "black" if side == "white" else "white"


# ---------------------------------------------------------------------------
# Tetrahedral dice
# ---------------------------------------------------------------------------

def roll_tetrahedral_dice(rng: random.Random) -> Tuple[int, List[int]]:
    """Roll 4 tetrahedral dice. Each die independently shows a marked or
    unmarked corner at the top (50/50 per die). Return (sum, dice_list)
    where each entry in dice_list is 0 (unmarked top) or 1 (marked top).
    Sum is the move distance (0..4)."""
    dice = [rng.randint(0, 1) for _ in range(4)]
    return sum(dice), dice


# ---------------------------------------------------------------------------
# Move legality
# ---------------------------------------------------------------------------

def legal_moves(
    side: str,
    own_positions: List[int],
    opp_positions: List[int],
    roll: int,
) -> List[int]:
    """Return the list of `from` positions a player can legally move FROM
    given the current roll. `from` ∈ {0..14}; 0 means bringing on a new
    piece, 1..14 means advancing the piece at that path position.

    Returns [] if the roll is 0 (no move possible) or if no piece can move.
    """
    if roll < 1 or roll > 4:
        return []

    # Build occupancy maps.
    own_at: Dict[int, int] = {}
    for pos in own_positions:
        own_at[pos] = own_at.get(pos, 0) + 1
    opp_at = {pos: 1 for pos in opp_positions if 1 <= pos <= PATH_LENGTH}

    out: List[int] = []
    # Distinct source positions to try: 0 (if any waiting), plus each
    # occupied path square.
    sources: List[int] = []
    if 0 in own_at:
        sources.append(0)
    for p in sorted({pos for pos in own_positions if 1 <= pos <= PATH_LENGTH}):
        sources.append(p)

    for frm in sources:
        dest = frm + roll
        # Bear off — must be exact (dest == BEAR_OFF == 15)
        if dest == BEAR_OFF:
            out.append(frm)
            continue
        # Overshoot — illegal
        if dest > PATH_LENGTH:
            continue
        # Can't land on own piece
        if dest in own_at:
            continue
        # Can't capture on the central rosette (opponent is safe there)
        if dest == CENTRAL_ROSETTE and dest in opp_at:
            continue
        # Private squares (1-4 and 13-14) never have opponent pieces
        # (separate per-player paths), so no capture-block logic needed.
        out.append(frm)
    return out


def apply_move(
    side: str,
    own_positions: List[int],
    opp_positions: List[int],
    frm: int,
    roll: int,
) -> Tuple[List[int], List[int], Dict[str, Any]]:
    """Apply a legal move. Returns (new_own, new_opp, info) where info
    contains: to, captured (bool), captured_piece_idx (int or None),
    rosette (bool), borne_off (bool)."""
    new_own = list(own_positions)
    new_opp = list(opp_positions)
    dest = frm + roll
    info: Dict[str, Any] = {
        "from": frm,
        "to": dest if dest < BEAR_OFF else BEAR_OFF,
        "captured": False,
        "captured_piece_idx": None,
        "rosette": False,
        "borne_off": False,
    }
    # Find a piece at `frm` in own_positions and move it.
    try:
        idx = new_own.index(frm)
    except ValueError:
        return new_own, new_opp, info
    if dest >= BEAR_OFF:
        new_own[idx] = BEAR_OFF
        info["borne_off"] = True
        return new_own, new_opp, info
    new_own[idx] = dest
    if dest in ROSETTES:
        info["rosette"] = True
    # Capture an opponent piece on the destination shared square (NOT on
    # the central rosette — that's a safe square).
    if dest in SHARED_PATH_RANGE and dest != CENTRAL_ROSETTE:
        for j, opp_pos in enumerate(new_opp):
            if opp_pos == dest:
                new_opp[j] = 0  # back to start
                info["captured"] = True
                info["captured_piece_idx"] = j
                break
    return new_own, new_opp, info


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def render_ascii_board(white_pos: List[int], black_pos: List[int]) -> str:
    """3-row × 8-col ASCII board. 'W'/'B' = white/black piece, '.' = empty
    playable square, '·' = the two missing notches per outer row."""
    cells: Dict[Tuple[int, int], str] = {}
    # Empty playable squares
    for c in range(8):
        cells[(1, c)] = "."
    for c in range(8):
        if c < 4 or c >= 6:
            cells[(0, c)] = "."
            cells[(2, c)] = "."
    # Place pieces
    for pos in white_pos:
        if 1 <= pos <= PATH_LENGTH:
            cells[WHITE_PATH_COORDS[pos]] = "W"
    for pos in black_pos:
        if 1 <= pos <= PATH_LENGTH:
            cells[BLACK_PATH_COORDS[pos]] = "B"
    lines: List[str] = []
    rosette_coords_white = {WHITE_PATH_COORDS[r] for r in ROSETTES}
    rosette_coords_black = {BLACK_PATH_COORDS[r] for r in ROSETTES}
    rosette_coords = rosette_coords_white | rosette_coords_black
    for r in range(3):
        row: List[str] = []
        for c in range(8):
            ch = cells.get((r, c), "·")
            if (r, c) in rosette_coords and ch == ".":
                ch = "*"
            row.append(ch)
        lines.append("  " + " ".join(row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class RoyalGameOfUrModule(DomainModule):
    """Tetrahedral-dice Royal Game of Ur engine."""

    def __init__(self, name: str = "royal_game_of_ur",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        seed = p.get("rng_seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()

        # 7 pieces per player; each entry is a path position (0 = off-board,
        # 1..14 = on path, 15 = borne off).
        self._white: List[int] = [0] * PIECES_PER_PLAYER
        self._black: List[int] = [0] * PIECES_PER_PLAYER
        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        self._side_to_move: str = "white"
        # current_roll == None means "must roll next"; current_roll == 0
        # means "rolled but no move possible" (about to auto-pass);
        # 1..4 = legal move pending.
        self._current_roll: Optional[int] = None
        self._current_dice: List[int] = []

        self._terminal: Optional[Dict[str, Any]] = None
        self._turn_log: List[str] = []
        self._initialized = False
        self._round = 0
        logger.info("[ur] init seed=%s", seed)

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return ("Royal Game of Ur — 2-player ancient Sumerian race game. "
                "7 pieces per side, 4 tetrahedral dice.")

    @property
    def custom_actions(self) -> List[str]:
        return ["roll_dice", "move_piece", "pass_turn", "resign"]

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
                ent.properties["pieces_at_start"] = PIECES_PER_PLAYER
                ent.properties["pieces_on_board"] = 0
                ent.properties["pieces_borne_off"] = 0
        self._initialized = True
        logger.info(
            "[ur] seated white=%s black=%s",
            agents[0].name, agents[1].name,
        )

    # ------------------------------------------------------------------ #
    # tick — auto-pass on dead rolls, emit init event once
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        was_seeded = self._initialized
        self._seed_from_state(state)
        self._round = round_number
        events: List[Dict[str, Any]] = []
        if not was_seeded and self._initialized:
            events.append({
                "type": "ur_init",
                "board": self._board_snapshot(),
                "narrative": (
                    "Royal Game of Ur — game begins. Each player has 7 "
                    "pieces, 4 tetrahedral dice, and 14 squares to race "
                    "around their track."
                ),
            })
        return events

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        self._seed_from_state(state)
        if self._terminal is not None:
            return []
        if entity_id not in (self._white_id, self._black_id):
            return [a for a in valid_actions if a not in self.custom_actions]
        out: List[str] = [a for a in valid_actions if a not in self.custom_actions]
        active = self._active_player_id()
        # Only the active player acts.
        if entity_id == active:
            if self._current_roll is None:
                out.append("roll_dice")
                out.append("resign")
            else:
                legal = legal_moves(
                    self._side_to_move,
                    self._own_positions(self._side_to_move),
                    self._opp_positions(self._side_to_move),
                    self._current_roll,
                )
                if legal:
                    out.append("move_piece")
                else:
                    out.append("pass_turn")
                out.append("resign")
        return out

    def _active_player_id(self) -> Optional[str]:
        return self._white_id if self._side_to_move == "white" else self._black_id

    def _own_positions(self, side: str) -> List[int]:
        return self._white if side == "white" else self._black

    def _opp_positions(self, side: str) -> List[int]:
        return self._black if side == "white" else self._white

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
        active = self._active_player_id()
        if action_name in ("roll_dice", "move_piece", "pass_turn", "resign"):
            if actor_id != active:
                return "Not your turn"
        if action_name == "roll_dice":
            if self._current_roll is not None:
                return "You have already rolled this sub-turn"
        if action_name == "move_piece":
            if self._current_roll is None:
                return "Roll the dice first"
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
            "[ur] post_resolution actor=%s action=%s details=%s",
            actor_id, action_name, {k: details.get(k) for k in
                                     ("piece_id", "from", "from_pos")},
        )
        if action_name == "roll_dice":
            return self._handle_roll(actor_id, state)
        if action_name == "move_piece":
            return self._handle_move(actor_id, details, state)
        if action_name == "pass_turn":
            return self._handle_pass(actor_id, "no_legal_moves", state)
        if action_name == "resign":
            return self._handle_resign(actor_id, state)
        return []

    def _handle_roll(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        roll, dice = roll_tetrahedral_dice(self._rng)
        self._current_roll = roll
        self._current_dice = dice
        actor_name = self._name_of(state, actor_id)
        events: List[Dict[str, Any]] = [{
            "type": "ur_roll",
            "player": actor_id,
            "roll": roll,
            "dice": list(dice),
            "narrative": (
                f"{actor_name} rolls {roll} ("
                + " ".join("●" if d else "○" for d in dice) + ")."
            ),
        }]
        if roll == 0:
            # Automatic pass — no need for explicit pass_turn from agent.
            events.extend(self._end_turn_pass(actor_id, "rolled_zero", state))
            return events
        # If no legal moves, the agent's next action will be `pass_turn`
        # (filter_valid_actions hides move_piece in that case).
        legal = legal_moves(
            self._side_to_move,
            self._own_positions(self._side_to_move),
            self._opp_positions(self._side_to_move),
            roll,
        )
        if not legal:
            events.extend(self._end_turn_pass(actor_id, "no_legal_moves", state))
        return events

    def _handle_move(self, actor_id: str, details: Dict[str, Any],
                     state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        if self._current_roll is None or self._current_roll < 1:
            return [self._invalid(actor_id, actor_name, "no_active_roll",
                                   "no roll to consume")]
        roll = self._current_roll
        # Accept several parameter shapes: `from`, `from_pos`, `piece`, `piece_pos`.
        frm_raw = (details.get("from") if details.get("from") is not None
                    else details.get("from_pos") if details.get("from_pos") is not None
                    else details.get("piece") if details.get("piece") is not None
                    else details.get("piece_pos"))
        try:
            frm = int(frm_raw)
        except (TypeError, ValueError):
            legal = legal_moves(side, self._own_positions(side),
                                 self._opp_positions(side), roll)
            return [self._invalid(
                actor_id, actor_name, "bad_from_param",
                f"need `from` integer (0 = enter a new piece, 1..14 = "
                f"path position). Legal `from` values for roll {roll}: {legal}",
            )]
        legal = legal_moves(side, self._own_positions(side),
                             self._opp_positions(side), roll)
        if frm not in legal:
            return [self._invalid(
                actor_id, actor_name, "illegal_from",
                f"from={frm} is not legal for roll {roll}; legal: {legal}",
            )]

        own = self._own_positions(side)
        opp = self._opp_positions(side)
        new_own, new_opp, info = apply_move(side, own, opp, frm, roll)
        if side == "white":
            self._white = new_own
            self._black = new_opp
        else:
            self._black = new_own
            self._white = new_opp

        san_parts = [f"{frm}→{info['to']}"]
        if info["captured"]:
            san_parts.append("×")
        if info["rosette"]:
            san_parts.append("☆")
        if info["borne_off"]:
            san_parts.append("OFF")
        san = " ".join(san_parts)
        self._turn_log.append(f"{side[0].upper()} r{roll} {san}")

        events: List[Dict[str, Any]] = [{
            "type": "ur_move",
            "player": actor_id,
            "roll": roll,
            "from": frm,
            "to": info["to"],
            "captured": info["captured"],
            "rosette": info["rosette"],
            "borne_off": info["borne_off"],
            "board": self._board_snapshot(),
            "narrative": (
                f"{actor_name} moves {frm}→{info['to']}"
                + (" (capture!)" if info["captured"] else "")
                + (" (rosette — extra turn)" if info["rosette"] else "")
                + (" (bear off!)" if info["borne_off"] else "")
                + "."
            ),
        }]
        self._refresh_player_props(state)
        # Clear the roll regardless — rosette only gives an EXTRA roll
        # opportunity on the same player's turn.
        self._current_roll = None
        self._current_dice = []

        # Win check.
        if all(p == BEAR_OFF for p in self._own_positions(side)):
            winner_id = actor_id
            loser_id = self._opp_id(actor_id)
            score = sum(1 for p in self._opp_positions(side) if p == BEAR_OFF)
            self._terminal = {"reason": "all_borne_off", "winner": winner_id}
            events.append({
                "event_type": "ur_win",
                "type": "ur_win",
                "winner": winner_id, "loser": loser_id,
                "score": {actor_id: PIECES_PER_PLAYER,
                          loser_id or "": score},
                "narrative": (
                    f"{actor_name} bears off all {PIECES_PER_PLAYER} pieces — wins!"
                ),
            })
            return events

        if info["rosette"]:
            # Same player rolls again — DO NOT switch sides.
            events.append({
                "type": "ur_extra_turn",
                "player": actor_id,
                "narrative": f"{actor_name} landed on a rosette — rolls again.",
            })
        else:
            self._side_to_move = opposite(side)
        return events

    def _handle_pass(self, actor_id: str, reason: str,
                     state: Any) -> List[Dict[str, Any]]:
        return self._end_turn_pass(actor_id, reason, state)

    def _end_turn_pass(self, actor_id: str, reason: str,
                       state: Any) -> List[Dict[str, Any]]:
        actor_name = self._name_of(state, actor_id)
        side = self._side_for_entity(actor_id)
        self._current_roll = None
        self._current_dice = []
        self._side_to_move = opposite(side)
        return [{
            "type": "ur_pass",
            "player": actor_id,
            "reason": reason,
            "narrative": (
                f"{actor_name} passes — "
                + {
                    "rolled_zero": "rolled 0",
                    "no_legal_moves": "no legal moves for this roll",
                }.get(reason, reason)
                + "."
            ),
        }]

    def _handle_resign(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        winner = self._opp_id(actor_id)
        self._terminal = {"reason": "resign", "winner": winner}
        loser_name = self._name_of(state, actor_id)
        winner_name = self._name_of(state, winner) if winner else "opponent"
        return [{
            "event_type": "ur_resign",
            "type": "ur_resign", "loser": actor_id, "winner": winner,
            "narrative": f"{loser_name} resigns — {winner_name} wins.",
        }]

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in (self._white_id, self._black_id):
            return {"role": "spectator", "board": self._board_snapshot()}
        side = self._side_for_entity(entity_id)
        is_your_turn = (entity_id == self._active_player_id()) and not self._terminal
        own = self._own_positions(side)
        opp = self._opp_positions(side)
        roll = self._current_roll
        legal = legal_moves(side, own, opp, roll) if (is_your_turn and roll) else []

        if self._terminal is not None:
            instructions = "Game over."
        elif not is_your_turn:
            instructions = (
                f"Opponent ({opposite(side).upper()}) is on move — wait."
            )
        elif roll is None:
            instructions = (
                f"IT IS YOUR TURN. You are playing {side.upper()}. Call "
                "`roll_dice` to roll 4 tetrahedral dice (sum 0–4 = move "
                "distance). Roll of 0 auto-passes."
            )
        elif roll == 0:
            instructions = "Rolled 0 — turn passes automatically."
        elif not legal:
            instructions = (
                f"Rolled {roll} but NO legal move is available. Call "
                "`pass_turn` to forfeit this roll."
            )
        else:
            instructions = (
                f"Rolled {roll}. Pick ONE piece to move and call "
                "`move_piece` with parameter `from` (an int). "
                f"Legal `from` values: {legal}. "
                "0 = bring a new piece onto the board; 1..14 = advance the "
                "piece at that path position. Landing on a rosette "
                f"(positions {sorted(ROSETTES)}) grants an extra turn. "
                "Bearing off requires an EXACT roll."
            )

        return {
            "instructions": instructions,
            "your_color": side,
            "is_your_turn": is_your_turn,
            "current_roll": roll,
            "current_dice": list(self._current_dice),
            "legal_from_positions": legal,
            "board_ascii": render_ascii_board(self._white, self._black),
            "board": self._board_snapshot(),
            "your_pieces_at_start": sum(1 for p in own if p == 0),
            "your_pieces_on_board": sum(1 for p in own if 1 <= p <= PATH_LENGTH),
            "your_pieces_borne_off": sum(1 for p in own if p == BEAR_OFF),
            "opponent_pieces_at_start": sum(1 for p in opp if p == 0),
            "opponent_pieces_on_board": sum(1 for p in opp if 1 <= p <= PATH_LENGTH),
            "opponent_pieces_borne_off": sum(1 for p in opp if p == BEAR_OFF),
            "rosette_positions": sorted(ROSETTES),
            "central_rosette": CENTRAL_ROSETTE,
            "move_history": list(self._turn_log[-40:]),
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _board_snapshot(self) -> Dict[str, Any]:
        """Compact board state for the FE viz + event payloads."""
        return {
            "white": list(self._white),
            "black": list(self._black),
            "side_to_move": self._side_to_move,
            "current_roll": self._current_roll,
            "current_dice": list(self._current_dice),
        }

    def _invalid(self, actor_id: str, actor_name: str, reason: str,
                  narrative: str) -> Dict[str, Any]:
        return {
            "type": "ur_invalid",
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

    def _refresh_player_props(self, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        for entity_id, side in (
            (self._white_id, "white"), (self._black_id, "black"),
        ):
            if not entity_id:
                continue
            ent = state.entities.get(entity_id)
            if not ent or not hasattr(ent, "properties"):
                continue
            own = self._own_positions(side)
            ent.properties["pieces_at_start"] = sum(1 for p in own if p == 0)
            ent.properties["pieces_on_board"] = sum(1 for p in own if 1 <= p <= PATH_LENGTH)
            ent.properties["pieces_borne_off"] = sum(1 for p in own if p == BEAR_OFF)

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "white": list(self._white),
            "black": list(self._black),
            "white_id": self._white_id,
            "black_id": self._black_id,
            "side_to_move": self._side_to_move,
            "current_roll": self._current_roll,
            "current_dice": list(self._current_dice),
            "terminal": dict(self._terminal) if self._terminal else None,
            "turn_log": list(self._turn_log),
            "initialized": self._initialized,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "RoyalGameOfUrModule":
        params = data.get("params", {}) or {}
        s = data.get("state", {})
        mod = cls(name=data.get("name", "royal_game_of_ur"), params=params)
        mod._white = list(s.get("white") or [0] * PIECES_PER_PLAYER)
        mod._black = list(s.get("black") or [0] * PIECES_PER_PLAYER)
        mod._white_id = s.get("white_id")
        mod._black_id = s.get("black_id")
        mod._side_to_move = s.get("side_to_move", "white")
        mod._current_roll = s.get("current_roll")
        mod._current_dice = list(s.get("current_dice") or [])
        mod._terminal = dict(s["terminal"]) if s.get("terminal") else None
        mod._turn_log = list(s.get("turn_log") or [])
        mod._initialized = bool(s.get("initialized", False))
        return mod
