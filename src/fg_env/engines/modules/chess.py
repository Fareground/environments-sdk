"""Chess domain module — full 8x8 chess engine.

Drives a real chess game on top of the generic action engine:
  - Standard piece movement + capture rules
  - Castling (king-side / queen-side), en passant, pawn promotion
  - Check / checkmate / stalemate detection
  - Threefold repetition + 50-move rule (basic)
  - Two players only (one White, one Black)

Custom actions:
  - `make_move`: parameters `from` (e.g. "e2") and `to` (e.g. "e4"),
    plus optional `promotion` ('Q'|'R'|'B'|'N') for pawn promotion.
  - `resign`: forfeit immediately.
  - `offer_draw`, `accept_draw`, `decline_draw`: draw negotiation.

Events emitted (consumed by ChessBoardViz + UI):
  - `chess_move` { from, to, piece, captured?, promotion?, san, board_position }
  - `chess_check` { side }
  - `chess_checkmate` { winner }
  - `chess_stalemate`
  - `chess_resign` { loser, winner }
  - `chess_draw` { reason: 'agreement'|'stalemate'|'fifty_move'|'threefold' }

Board encoding: dict mapping algebraic square ('a1'..'h8') → piece char.
Pieces use FEN convention: uppercase = white (K Q R B N P),
lowercase = black (k q r b n p).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static board helpers
# ---------------------------------------------------------------------------

FILES = "abcdefgh"
RANKS = "12345678"


def sq(file_idx: int, rank_idx: int) -> Optional[str]:
    if 0 <= file_idx < 8 and 0 <= rank_idx < 8:
        return f"{FILES[file_idx]}{RANKS[rank_idx]}"
    return None


def file_rank(square: str) -> Tuple[int, int]:
    return FILES.index(square[0]), RANKS.index(square[1])


def is_white(piece: str) -> bool:
    return piece.isupper()


def is_black(piece: str) -> bool:
    return piece.islower()


def opposite(side: str) -> str:
    return "black" if side == "white" else "white"


def piece_side(piece: str) -> str:
    return "white" if is_white(piece) else "black"


def _render_ascii_board(board: Dict[str, str], perspective: str = "white") -> str:
    """Render the board as ASCII so the LLM can read it visually.

    Format:
        8  r n b q k b n r
        7  p p p p p p p p
        6  . . . . . . . .
        5  . . . . . . . .
        4  . . . . . . . .
        3  . . . . . . . .
        2  P P P P P P P P
        1  R N B Q K B N R
           a b c d e f g h

    Uppercase = white, lowercase = black, '.' = empty. From `perspective`'s
    point of view their own pieces are on the bottom two ranks.
    """
    files = list(FILES)
    ranks = ["8", "7", "6", "5", "4", "3", "2", "1"]
    if perspective == "black":
        files = list(reversed(files))
        ranks = list(reversed(ranks))
    lines = []
    for rank in ranks:
        row = [board.get(f"{f}{rank}", ".") for f in files]
        lines.append(f"  {rank}  " + " ".join(row))
    lines.append("     " + " ".join(files))
    return "\n".join(lines)


INITIAL_BOARD: Dict[str, str] = {
    # rank 8 — black back rank
    "a8": "r", "b8": "n", "c8": "b", "d8": "q",
    "e8": "k", "f8": "b", "g8": "n", "h8": "r",
    "a7": "p", "b7": "p", "c7": "p", "d7": "p",
    "e7": "p", "f7": "p", "g7": "p", "h7": "p",
    # rank 1 — white back rank
    "a1": "R", "b1": "N", "c1": "B", "d1": "Q",
    "e1": "K", "f1": "B", "g1": "N", "h1": "R",
    "a2": "P", "b2": "P", "c2": "P", "d2": "P",
    "e2": "P", "f2": "P", "g2": "P", "h2": "P",
}


# ---------------------------------------------------------------------------
# Move generation
# ---------------------------------------------------------------------------

KNIGHT_OFFSETS = [(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)]
KING_OFFSETS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
ROOK_DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
BISHOP_DIRS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]


def _slide(board: Dict[str, str], origin: str, dirs, side: str) -> List[str]:
    out: List[str] = []
    f, r = file_rank(origin)
    for df, dr in dirs:
        nf, nr = f + df, r + dr
        while 0 <= nf < 8 and 0 <= nr < 8:
            target = sq(nf, nr)
            occ = board.get(target)
            if occ is None:
                out.append(target)
            else:
                if piece_side(occ) != side:
                    out.append(target)
                break
            nf += df
            nr += dr
    return out


def _pawn_pseudo(board: Dict[str, str], origin: str, side: str,
                 en_passant: Optional[str]) -> List[str]:
    out: List[str] = []
    f, r = file_rank(origin)
    direction = 1 if side == "white" else -1
    start_rank = 1 if side == "white" else 6
    # forward 1
    one = sq(f, r + direction)
    if one and one not in board:
        out.append(one)
        # forward 2 from start rank
        if r == start_rank:
            two = sq(f, r + 2 * direction)
            if two and two not in board:
                out.append(two)
    # captures (diagonal)
    for df in (-1, 1):
        diag = sq(f + df, r + direction)
        if diag is None:
            continue
        occ = board.get(diag)
        if occ and piece_side(occ) != side:
            out.append(diag)
        elif diag == en_passant:
            out.append(diag)
    return out


def pseudo_moves(board: Dict[str, str], origin: str,
                 en_passant: Optional[str]) -> List[str]:
    """All target squares the piece *could* move to ignoring own-king-in-check
    constraints. Castling is added in `legal_moves`."""
    piece = board.get(origin)
    if piece is None:
        return []
    side = piece_side(piece)
    p = piece.upper()
    if p == "P":
        return _pawn_pseudo(board, origin, side, en_passant)
    if p == "N":
        return [
            target for df, dr in KNIGHT_OFFSETS
            for target in (sq(file_rank(origin)[0] + df, file_rank(origin)[1] + dr),)
            if target is not None and (board.get(target) is None or piece_side(board[target]) != side)
        ]
    if p == "K":
        return [
            target for df, dr in KING_OFFSETS
            for target in (sq(file_rank(origin)[0] + df, file_rank(origin)[1] + dr),)
            if target is not None and (board.get(target) is None or piece_side(board[target]) != side)
        ]
    if p == "B":
        return _slide(board, origin, BISHOP_DIRS, side)
    if p == "R":
        return _slide(board, origin, ROOK_DIRS, side)
    if p == "Q":
        return _slide(board, origin, ROOK_DIRS + BISHOP_DIRS, side)
    return []


def find_king(board: Dict[str, str], side: str) -> Optional[str]:
    target = "K" if side == "white" else "k"
    for square, piece in board.items():
        if piece == target:
            return square
    return None


def is_square_attacked(board: Dict[str, str], square: str, by_side: str) -> bool:
    """True if any of `by_side`'s pieces attacks `square`."""
    for origin, piece in board.items():
        if piece_side(piece) != by_side:
            continue
        # Use pseudo-moves; for pawns we need attack squares specifically
        # (not push squares). Compute pawn attacks separately.
        if piece.upper() == "P":
            f, r = file_rank(origin)
            d = 1 if by_side == "white" else -1
            for df in (-1, 1):
                if sq(f + df, r + d) == square:
                    return True
            continue
        if square in pseudo_moves(board, origin, en_passant=None):
            return True
    return False


def in_check(board: Dict[str, str], side: str) -> bool:
    king = find_king(board, side)
    if king is None:
        return False
    return is_square_attacked(board, king, opposite(side))


def apply_move(board: Dict[str, str], origin: str, dest: str,
               en_passant: Optional[str], promotion: Optional[str] = None
               ) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """Return (new_board, meta). Meta carries castling/en-passant/promotion
    info needed by the caller to update game state."""
    new = dict(board)
    piece = new.get(origin)
    if piece is None:
        return new, {"error": "no piece"}
    meta: Dict[str, Any] = {"piece": piece, "captured": new.get(dest)}
    new.pop(origin, None)
    # Castling: king moves two files
    if piece.upper() == "K":
        f_from = file_rank(origin)[0]
        f_to = file_rank(dest)[0]
        if abs(f_to - f_from) == 2:
            rank = origin[1]
            if f_to > f_from:  # king-side
                rook_from, rook_to = f"h{rank}", f"f{rank}"
            else:              # queen-side
                rook_from, rook_to = f"a{rank}", f"d{rank}"
            rook = new.pop(rook_from, None)
            if rook:
                new[rook_to] = rook
            meta["castle"] = "K" if f_to > f_from else "Q"
    # En passant capture
    if piece.upper() == "P" and dest == en_passant and new.get(dest) is None:
        # Captured pawn is on the same file as `dest`, but the rank of `origin`.
        captured_sq = f"{dest[0]}{origin[1]}"
        meta["captured"] = new.pop(captured_sq, None)
        meta["en_passant_capture"] = True
    # Place the moving piece (with promotion if applicable)
    if piece.upper() == "P":
        rank_to = file_rank(dest)[1]
        if (piece == "P" and rank_to == 7) or (piece == "p" and rank_to == 0):
            promo = (promotion or "Q").upper()
            if promo not in ("Q", "R", "B", "N"):
                promo = "Q"
            new[dest] = promo if is_white(piece) else promo.lower()
            meta["promotion"] = promo
        else:
            new[dest] = piece
    else:
        new[dest] = piece
    # Track if this move enables an en-passant target square for the opponent
    next_ep: Optional[str] = None
    if piece.upper() == "P":
        f_from, r_from = file_rank(origin)
        _, r_to = file_rank(dest)
        if abs(r_to - r_from) == 2:
            next_ep = sq(f_from, (r_from + r_to) // 2)
    meta["next_en_passant"] = next_ep
    return new, meta


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class ChessModule(DomainModule):
    """Real chess engine. Two players, full move validation, check/mate."""

    def __init__(self, name: str = "chess", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        self._board: Dict[str, str] = dict(INITIAL_BOARD)
        # White and Black entity ids assigned on first tick (seat order).
        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        self._side_to_move: str = "white"
        # Castling rights — start as full.
        self._castle = {"K": True, "Q": True, "k": True, "q": True}
        self._en_passant: Optional[str] = None
        # Draw/repetition/50-move bookkeeping.
        self._halfmove_clock: int = 0       # increments unless pawn move / capture
        self._fullmove_number: int = 1
        self._position_history: List[str] = []
        # Draw offer state — id of the player who offered, else None.
        self._draw_offer_by: Optional[str] = None
        # Terminal state — once set, no more moves.
        self._terminal: Optional[Dict[str, Any]] = None
        self._initialized = False
        self._turn_log: List[str] = []

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return "Standard 2-player chess. Capture the king (checkmate) to win."

    @property
    def custom_actions(self) -> List[str]:
        return ["make_move", "resign", "offer_draw", "accept_draw", "decline_draw"]

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
        # Deterministic seat assignment: first agent = white, second = black.
        self._white_id = agents[0].id
        self._black_id = agents[1].id
        # Mirror color onto entity props so perception + viz can see it.
        for ent, color in ((agents[0], "white"), (agents[1], "black")):
            if hasattr(ent, "properties"):
                ent.properties["color"] = color
                ent.properties["is_in_check"] = False
        self._initialized = True
        self._position_history.append(self._position_key())
        logger.info("Chess seated: white=%s, black=%s",
                    agents[0].name, agents[1].name)

    # ------------------------------------------------------------------ #
    # tick / filter_valid_actions
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        return []

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        if self._terminal is not None:
            # Game over — nobody acts.
            return []
        if entity_id not in (self._white_id, self._black_id):
            return [a for a in valid_actions if a not in self.custom_actions]
        out: List[str] = [a for a in valid_actions if a not in self.custom_actions]
        # Pending draw offer: receiver can accept/decline; offerer waits.
        if self._draw_offer_by and self._draw_offer_by != entity_id:
            out.extend(["accept_draw", "decline_draw"])
            return out
        # On-move side may make_move, resign, offer_draw.
        if entity_id == self._active_player_id():
            out.extend(["make_move", "resign", "offer_draw"])
        return out

    def _active_player_id(self) -> Optional[str]:
        return self._white_id if self._side_to_move == "white" else self._black_id

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                         state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        actor_id = getattr(actor, "id", None)
        if action_name in ("make_move", "resign", "offer_draw"):
            if actor_id != self._active_player_id():
                return "Not your turn"
        if action_name in ("accept_draw", "decline_draw"):
            if not self._draw_offer_by:
                return "No draw offer to respond to"
            if actor_id == self._draw_offer_by:
                return "You can't respond to your own draw offer"
        return None

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        if not success or action_name not in self.custom_actions:
            return []
        raw = getattr(result, "details", {}) if hasattr(result, "details") else {}
        # Engine stores the action's invocation params under `_action_params`
        # (added in engine.py:resolve_action). The rest of `details` is the
        # resolution outcome. Surface a flat view for handlers.
        params = raw.get("_action_params") or {}
        details = {**raw, **params}
        if action_name == "make_move":
            return self._handle_make_move(actor_id, details, state)
        if action_name == "resign":
            return self._handle_resign(actor_id, state)
        if action_name == "offer_draw":
            self._draw_offer_by = actor_id
            return [{"type": "chess_draw_offer", "by": actor_id}]
        if action_name == "accept_draw":
            self._terminal = {"reason": "agreement", "winner": None}
            return [{"type": "chess_draw", "reason": "agreement",
                     "narrative": "Draw agreed."}]
        if action_name == "decline_draw":
            self._draw_offer_by = None
            return [{"type": "chess_draw_decline", "by": actor_id}]
        return []

    def _handle_make_move(self, actor_id: str, details: Dict[str, Any],
                          state: Any) -> List[Dict[str, Any]]:
        side = "white" if actor_id == self._white_id else "black"
        origin = str(details.get("from") or "").lower().strip()
        dest = str(details.get("to") or "").lower().strip()
        promotion = details.get("promotion")
        actor_name = self._name_of(state, actor_id)
        if not self._is_valid_square(origin) or not self._is_valid_square(dest):
            return [{
                "type": "chess_invalid", "player": actor_id,
                "reason": "invalid_squares", "from": origin, "to": dest,
                "narrative": f"{actor_name} tried an invalid move "
                              f"({origin or '?'}→{dest or '?'}) — bad squares.",
            }]
        piece = self._board.get(origin)
        if piece is None or piece_side(piece) != side:
            return [{
                "type": "chess_invalid", "player": actor_id,
                "reason": "no_piece_of_yours", "from": origin,
                "narrative": f"{actor_name} tried to move from {origin} "
                              "but has no piece there.",
            }]
        legal_moves = self._legal_moves_for_side(side)
        legal = legal_moves.get(origin, [])
        alternatives = [square for square, targets in legal_moves.items()
                        if square != origin and self._board.get(square) == piece and dest in targets]
        if dest not in legal:
            return [{
                "type": "chess_invalid", "player": actor_id,
                "reason": "illegal_move", "from": origin, "to": dest,
                "narrative": f"{actor_name} tried {origin}→{dest} — illegal.",
            }]
        extra: List[Dict[str, Any]] = []
        new_board, meta = apply_move(self._board, origin, dest,
                                      self._en_passant, promotion)
        # Bookkeeping
        captured = meta.get("captured")
        is_pawn = piece.upper() == "P"
        if is_pawn or captured:
            self._halfmove_clock = 0
        else:
            self._halfmove_clock += 1
        # Castling rights — clear when relevant pieces move/capture.
        self._update_castling_rights(piece, origin, dest)
        self._en_passant = meta.get("next_en_passant")
        self._board = new_board
        if side == "black":
            self._fullmove_number += 1
        san = self._make_san(piece, origin, dest, captured, meta.get("promotion"),
                              meta.get("castle"), alternatives)
        if in_check(self._board, opposite(side)):
            san += "+" if self._has_legal_moves(opposite(side)) else "#"
        self._turn_log.append(san)
        actor_name = self._name_of(state, actor_id)
        events: List[Dict[str, Any]] = list(extra) + [{
            "type": "chess_move",
            "player": actor_id,
            "from": origin,
            "to": dest,
            "piece": piece,
            "captured": captured,
            "promotion": meta.get("promotion"),
            "castle": meta.get("castle"),
            "san": san,
            "board_position": dict(self._board),
            "side_to_move": opposite(side),
            "narrative": (
                f"{actor_name} plays {san}"
                + (f" (captures {captured})" if captured else "")
            ),
        }]
        # Side has flipped — check status for opponent.
        next_side = opposite(side)
        self._side_to_move = next_side
        # Any draw offer auto-clears once a move is made.
        self._draw_offer_by = None
        self._position_history.append(self._position_key())
        # Update entity's in-check property for UI.
        opp_in_check = in_check(self._board, next_side)
        self._set_in_check(state, next_side, opp_in_check)
        self._set_in_check(state, side, False)
        if opp_in_check:
            events.append({
                "type": "chess_check", "side": next_side,
                "narrative": f"{self._name_of(state, self._opp_id(actor_id) or '')} is in CHECK.",
            })
        # Terminal detection. Every terminal event surfaces `event_type`
        # so the engine emits it as a first-class SimEvent — required for
        # termination conditions to match AND for the arena outcome
        # derivation to find the winner.
        if not self._has_legal_moves(next_side):
            if opp_in_check:
                winner_id = actor_id
                winner_name = self._name_of(state, winner_id)
                self._terminal = {"reason": "checkmate", "winner": winner_id}
                events.append({
                    "event_type": "chess_checkmate",
                    "type": "chess_checkmate",
                    "winner": winner_id, "loser": self._opp_id(actor_id),
                    "narrative": f"Checkmate. {winner_name} wins.",
                })
            else:
                self._terminal = {"reason": "stalemate", "winner": None}
                events.append({
                    "event_type": "chess_stalemate",
                    "type": "chess_stalemate",
                    "narrative": "Stalemate — draw.",
                })
            return events
        # 50-move + threefold
        if self._halfmove_clock >= 100:
            self._terminal = {"reason": "fifty_move", "winner": None}
            events.append({
                "event_type": "chess_draw",
                "type": "chess_draw", "reason": "fifty_move",
                "narrative": "Draw by 50-move rule.",
            })
        elif self._position_history.count(self._position_key()) >= 3:
            self._terminal = {"reason": "threefold", "winner": None}
            events.append({
                "event_type": "chess_draw",
                "type": "chess_draw", "reason": "threefold",
                "narrative": "Draw by threefold repetition.",
            })
        return events

    def _handle_resign(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        winner = self._opp_id(actor_id)
        self._terminal = {"reason": "resign", "winner": winner}
        loser_name = self._name_of(state, actor_id)
        winner_name = self._name_of(state, winner) if winner else "opponent"
        return [{
            "event_type": "chess_resign",
            "type": "chess_resign", "loser": actor_id, "winner": winner,
            "narrative": f"{loser_name} resigns. {winner_name} wins.",
        }]

    # ------------------------------------------------------------------ #
    # Legal-move generation (with own-king-safety check)
    # ------------------------------------------------------------------ #

    def _legal_moves_for_side(self, side: str) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for origin, piece in list(self._board.items()):
            if piece_side(piece) != side:
                continue
            targets = pseudo_moves(self._board, origin, self._en_passant)
            legal: List[str] = []
            for dest in targets:
                new_board, _ = apply_move(self._board, origin, dest,
                                           self._en_passant)
                if not in_check(new_board, side):
                    legal.append(dest)
            # Castling — only legal when not in check, and king doesn't pass
            # through / land on an attacked square.
            if piece.upper() == "K":
                legal.extend(self._castling_moves(side))
            if legal:
                out[origin] = legal
        return out

    def _has_legal_moves(self, side: str) -> bool:
        for moves in self._legal_moves_for_side(side).values():
            if moves:
                return True
        return False

    def _castling_moves(self, side: str) -> List[str]:
        rank = "1" if side == "white" else "8"
        king_sq = f"e{rank}"
        if self._board.get(king_sq) != ("K" if side == "white" else "k"):
            return []
        if in_check(self._board, side):
            return []
        opp = opposite(side)
        out: List[str] = []
        # King-side
        right = "K" if side == "white" else "k"
        if self._castle.get(right) and \
                self._board.get(f"f{rank}") is None and \
                self._board.get(f"g{rank}") is None and \
                self._board.get(f"h{rank}") == ("R" if side == "white" else "r") and \
                not is_square_attacked(self._board, f"f{rank}", opp) and \
                not is_square_attacked(self._board, f"g{rank}", opp):
            out.append(f"g{rank}")
        # Queen-side
        left = "Q" if side == "white" else "q"
        if self._castle.get(left) and \
                self._board.get(f"d{rank}") is None and \
                self._board.get(f"c{rank}") is None and \
                self._board.get(f"b{rank}") is None and \
                self._board.get(f"a{rank}") == ("R" if side == "white" else "r") and \
                not is_square_attacked(self._board, f"d{rank}", opp) and \
                not is_square_attacked(self._board, f"c{rank}", opp):
            out.append(f"c{rank}")
        return out

    def _update_castling_rights(self, piece: str, origin: str, dest: str) -> None:
        # King moved → both castling rights for that side lost.
        if piece == "K":
            self._castle["K"] = False
            self._castle["Q"] = False
        if piece == "k":
            self._castle["k"] = False
            self._castle["q"] = False
        # Rook moved → its side's right lost.
        if piece == "R" and origin == "a1":
            self._castle["Q"] = False
        if piece == "R" and origin == "h1":
            self._castle["K"] = False
        if piece == "r" and origin == "a8":
            self._castle["q"] = False
        if piece == "r" and origin == "h8":
            self._castle["k"] = False
        # Captures on a rook square also kill that side's right.
        for square, right in (("a1", "Q"), ("h1", "K"), ("a8", "q"), ("h8", "k")):
            if dest == square:
                self._castle[right] = False

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in (self._white_id, self._black_id):
            return {"role": "spectator", "board_position": dict(self._board)}
        side = "white" if entity_id == self._white_id else "black"
        is_your_turn = (entity_id == self._active_player_id()) and not self._terminal
        legal = self._legal_moves_for_side(side) if is_your_turn else {}
        # Flatten legal moves into a UCI-style list for the LLM.
        legal_uci = sorted(f"{frm}{to}" for frm, dests in legal.items() for to in dests)
        # ASCII board (perspective = your color, your pieces on bottom rank).
        board_ascii = _render_ascii_board(self._board, perspective=side)
        # Critical instructions — agents WILL skip or misread JSON; lead with
        # a hand-held call-to-action sentence so the LLM has zero ambiguity.
        if is_your_turn:
            instructions = (
                f"IT IS YOUR TURN. You are playing {side.upper()}. "
                "Pick ONE move from `legal_moves` below and call "
                "`make_move` with parameters `from` and `to` (e.g. "
                "from='e2', to='e4'). DO NOT set a target — chess actions "
                "have no target entity. DO NOT invent moves; if a move is "
                "not in `legal_moves` it is illegal and will be rejected."
            )
        else:
            instructions = (
                f"Opponent ({opposite(side).upper()}) is on move. "
                "You have no legal moves this turn — wait."
            )
        return {
            "instructions": instructions,
            "your_color": side,
            "is_your_turn": is_your_turn,
            "in_check": in_check(self._board, side),
            "board_ascii": board_ascii,
            "board_position": dict(self._board),
            "legal_moves": legal_uci,
            "legal_moves_count": len(legal_uci),
            "move_history": list(self._turn_log[-40:]),
            "halfmove_clock": self._halfmove_clock,
            "fullmove_number": self._fullmove_number,
            "draw_offered_by_opponent": (
                self._draw_offer_by is not None and self._draw_offer_by != entity_id
            ),
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

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

    def _set_in_check(self, state: Any, side: str, value: bool) -> None:
        entity_id = self._white_id if side == "white" else self._black_id
        if not entity_id or not hasattr(state, "entities"):
            return
        ent = state.entities.get(entity_id)
        if ent and hasattr(ent, "properties"):
            ent.properties["is_in_check"] = bool(value)

    def _is_valid_square(self, square: str) -> bool:
        return (isinstance(square, str) and len(square) == 2
                and square[0] in FILES and square[1] in RANKS)

    def _make_san(self, piece: str, origin: str, dest: str,
                  captured: Optional[str], promotion: Optional[str],
                  castle: Optional[str], alternatives: Optional[List[str]] = None) -> str:
        if castle:
            return "O-O" if castle == "K" else "O-O-O"
        letter = piece.upper() if piece.upper() != "P" else ""
        if letter and alternatives:
            if all(square[0] != origin[0] for square in alternatives):
                letter += origin[0]
            elif all(square[1] != origin[1] for square in alternatives):
                letter += origin[1]
            else:
                letter += origin
        cap = "x" if captured else ""
        # For pawn captures, prefix the origin file.
        if piece.upper() == "P" and captured:
            letter = origin[0]
        promo = f"={promotion}" if promotion else ""
        return f"{letter}{cap}{dest}{promo}"

    def _position_key(self) -> str:
        """Canonical key for threefold-repetition detection. Includes side
        to move, castling rights, and en-passant square."""
        board_str = ",".join(f"{k}{v}" for k, v in sorted(self._board.items()))
        castle = "".join(k for k, v in self._castle.items() if v)
        return f"{board_str}|{self._side_to_move}|{castle}|{self._en_passant or '-'}"

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "board": dict(self._board),
            "white_id": self._white_id,
            "black_id": self._black_id,
            "side_to_move": self._side_to_move,
            "castle": dict(self._castle),
            "en_passant": self._en_passant,
            "halfmove_clock": self._halfmove_clock,
            "fullmove_number": self._fullmove_number,
            "position_history": list(self._position_history),
            "draw_offer_by": self._draw_offer_by,
            "terminal": dict(self._terminal) if self._terminal else None,
            "turn_log": list(self._turn_log),
            "initialized": self._initialized,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "ChessModule":
        mod = cls(name=data.get("name", "chess"), params=data.get("params", {}))
        s = data.get("state", {})
        if s.get("board"):
            mod._board = dict(s["board"])
        mod._white_id = s.get("white_id")
        mod._black_id = s.get("black_id")
        mod._side_to_move = s.get("side_to_move", "white")
        mod._castle = dict(s.get("castle") or {"K": True, "Q": True, "k": True, "q": True})
        mod._en_passant = s.get("en_passant")
        mod._halfmove_clock = int(s.get("halfmove_clock") or 0)
        mod._fullmove_number = int(s.get("fullmove_number") or 1)
        mod._position_history = list(s.get("position_history") or [])
        mod._draw_offer_by = s.get("draw_offer_by")
        mod._terminal = dict(s["terminal"]) if s.get("terminal") else None
        mod._turn_log = list(s.get("turn_log") or [])
        mod._initialized = bool(s.get("initialized", False))
        return mod
