"""Checkers domain module — multi-variant engine.

Drives a real checkers game on top of the generic action engine.
The variant is chosen at world-load time via the `variant` init
parameter (settable from the pre-match UI as `domain.variant`).

Supported variants (rule axes that vary):

  | variant       | board | back-cap | flying king | must-take-max | mid-cap promo |
  |---------------|-------|----------|-------------|---------------|----------------|
  | american      |  8×8  |    no    |     no      |      no       |  no (ends)     |
  | pool          |  8×8  |   yes    |    yes      |      no       |  no (at end)     |
  | italian       |  8×8  |    no    |     no      |     yes       |  no (ends)     |
  | spanish       |  8×8  |    no    |    yes      |     yes       |  no (ends)     |
  | russian       |  8×8  |   yes    |    yes      |      no       |  yes (king)    |
  | brazilian     |  8×8  |   yes    |    yes      |     yes       |  no (at end)    |
  | international | 10×10 |   yes    |    yes      |     yes       |  no (at end)    |
  | canadian      | 12×12 |   yes    |    yes      |     yes       |  no (at end)    |

Custom actions:
  - `make_move` { move: "c3-d4" | "c3xe5xg7" }
    Also accepts chess-style `{from, to[, path]}` (coerced server-side).
  - `resign`
  - `offer_draw` / `accept_draw` / `decline_draw`

Events:
  - `checkers_move` { player, move, board_position, captures[], promoted, side_to_move, … }
  - `checkers_all_captured` { winner, loser } — terminal
  - `checkers_blockade`     { winner, loser } — terminal
  - `checkers_resign`       { loser, winner } — terminal
  - `checkers_draw`         { reason }         — terminal
  - `checkers_invalid`      { reason }         — non-fatal

Square notation: file 'a'..'l' (up to 12 cols), rank '1'..'12'.
Pieces sit only on DARK squares: (file_idx + rank_idx) % 2 == 0.
Piece chars: 'w'/'W' (white man/king), 'b'/'B' (black man/king).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Variant rules
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VariantRules:
    """All the rule knobs that vary across checkers variants."""
    name: str                              # human label
    board_size: int                        # 8, 10, or 12 (must be even)
    men_capture_backward: bool             # False = American/Italian
    king_flying: bool                      # False = American (1-square kings)
    must_take_max: bool                    # True = forced longest sequence
    mid_capture_promotion_continues: bool  # True = man→king mid-jump keeps capturing as king
    crown_at_capture_end: bool = False     # Continue as a man; crown only on the final square
    italian_capture_priority: bool = False # Men cannot take kings; FID Art. 6 tie-breaks


VARIANTS: Dict[str, VariantRules] = {
    "american": VariantRules(
        name="American", board_size=8,
        men_capture_backward=False, king_flying=False,
        must_take_max=False, mid_capture_promotion_continues=False,
    ),
    "pool": VariantRules(
        name="Pool", board_size=8,
        men_capture_backward=True, king_flying=True,
        must_take_max=False, mid_capture_promotion_continues=False,
        crown_at_capture_end=True,
    ),
    "italian": VariantRules(
        name="Italian", board_size=8,
        men_capture_backward=False, king_flying=False,
        must_take_max=True, mid_capture_promotion_continues=False,
        italian_capture_priority=True,
    ),
    "spanish": VariantRules(
        name="Spanish", board_size=8,
        men_capture_backward=False, king_flying=True,
        must_take_max=True, mid_capture_promotion_continues=False,
    ),
    "russian": VariantRules(
        name="Russian", board_size=8,
        men_capture_backward=True, king_flying=True,
        must_take_max=False, mid_capture_promotion_continues=True,
    ),
    "brazilian": VariantRules(
        name="Brazilian", board_size=8,
        men_capture_backward=True, king_flying=True,
        must_take_max=True, mid_capture_promotion_continues=True,
        crown_at_capture_end=True,
    ),
    "international": VariantRules(
        name="International", board_size=10,
        men_capture_backward=True, king_flying=True,
        must_take_max=True, mid_capture_promotion_continues=True,
        crown_at_capture_end=True,
    ),
    "canadian": VariantRules(
        name="Canadian", board_size=12,
        men_capture_backward=True, king_flying=True,
        must_take_max=True, mid_capture_promotion_continues=True,
        crown_at_capture_end=True,
    ),
}

DEFAULT_VARIANT = "american"


def resolve_variant(name: Any) -> VariantRules:
    """Look up a variant by name (case-insensitive). Falls back to default
    for unknown names — the runtime_parameter UI is the canonical list."""
    key = str(name or DEFAULT_VARIANT).strip().lower()
    if key not in VARIANTS:
        if name:  # an actual unknown value was passed (not just missing)
            logger.warning(
                "[checkers] unknown variant %r — falling back to %r. "
                "Known: %s",
                name, DEFAULT_VARIANT, sorted(VARIANTS.keys()),
            )
        return VARIANTS[DEFAULT_VARIANT]
    return VARIANTS[key]


# ---------------------------------------------------------------------------
# Square helpers — parameterized by board size
# ---------------------------------------------------------------------------

ALL_FILES = "abcdefghijkl"  # up to 12 columns


def files_for(n: int) -> str:
    return ALL_FILES[:n]


def sq(f: int, r: int, n: int) -> Optional[str]:
    if 0 <= f < n and 0 <= r < n:
        return f"{ALL_FILES[f]}{r + 1}"
    return None


def file_rank(square: str) -> Tuple[int, int]:
    """Parse 'a3' or 'a10' into (file_idx, rank_idx). Works for any
    file letter a..l and any rank 1..12."""
    return ALL_FILES.index(square[0]), int(square[1:]) - 1


def is_dark_square(f: int, r: int) -> bool:
    return (f + r) % 2 == 0


def is_white_piece(p: str) -> bool:
    return p in ("w", "W")


def is_black_piece(p: str) -> bool:
    return p in ("b", "B")


def is_king(p: str) -> bool:
    return p in ("W", "B")


def piece_side(p: str) -> str:
    return "white" if is_white_piece(p) else "black"


def opposite(side: str) -> str:
    return "black" if side == "white" else "white"


def initial_board(n: int) -> Dict[str, str]:
    """Standard starting position for an N×N board. Home rows = (N-2)/2
    per side. Pieces only on dark squares."""
    home_rows = (n - 2) // 2
    board: Dict[str, str] = {}
    for r in range(home_rows):  # white home (bottom)
        for f in range(n):
            if is_dark_square(f, r):
                board[sq(f, r, n)] = "w"
    for r in range(n - home_rows, n):  # black home (top)
        for f in range(n):
            if is_dark_square(f, r):
                board[sq(f, r, n)] = "b"
    return board


def render_ascii_board(board: Dict[str, str], perspective: str, n: int) -> str:
    files = list(files_for(n))
    ranks_top_to_bottom = [str(i + 1) for i in range(n - 1, -1, -1)]
    if perspective == "black":
        files = list(reversed(files))
        ranks_top_to_bottom = list(reversed(ranks_top_to_bottom))
    lines: List[str] = []
    rank_pad = len(str(n))
    for rank in ranks_top_to_bottom:
        row_cells: List[str] = []
        for f in files:
            square_name = f"{f}{rank}"
            f_idx, r_idx = file_rank(square_name)
            if not is_dark_square(f_idx, r_idx):
                row_cells.append("·")
            else:
                row_cells.append(board.get(square_name, "."))
        lines.append(f"  {rank.rjust(rank_pad)}  " + " ".join(row_cells))
    lines.append(" " * (rank_pad + 4) + " ".join(files))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Move generation (parameterized by board size + variant rules)
# ---------------------------------------------------------------------------

ALL_DIAGS = [(1, 1), (1, -1), (-1, 1), (-1, -1)]


def _man_forward_diags(side: str) -> List[Tuple[int, int]]:
    return [(1, 1), (-1, 1)] if side == "white" else [(1, -1), (-1, -1)]


def _simple_moves_for(board: Dict[str, str], origin: str,
                      n: int, rules: VariantRules) -> List[str]:
    piece = board.get(origin)
    if piece is None:
        return []
    out: List[str] = []
    f, r = file_rank(origin)
    if is_king(piece):
        if rules.king_flying:
            for df, dr in ALL_DIAGS:
                nf, nr = f + df, r + dr
                while 0 <= nf < n and 0 <= nr < n:
                    dest = sq(nf, nr, n)
                    if dest in board:
                        break
                    out.append(dest)
                    nf += df
                    nr += dr
        else:
            # Non-flying king: 1 square in any of 4 diagonals.
            for df, dr in ALL_DIAGS:
                dest = sq(f + df, r + dr, n)
                if dest is not None and dest not in board:
                    out.append(dest)
    else:
        side = piece_side(piece)
        for df, dr in _man_forward_diags(side):
            dest = sq(f + df, r + dr, n)
            if dest is None or dest in board:
                continue
            out.append(dest)
    return out


def _all_simple_moves(board: Dict[str, str], side: str,
                      n: int, rules: VariantRules) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for origin, piece in board.items():
        if piece_side(piece) != side:
            continue
        for dest in _simple_moves_for(board, origin, n, rules):
            out.append((origin, dest))
    return out


def _man_jump_steps(board: Dict[str, str], origin: str, side: str,
                     already_jumped: set, n: int,
                     rules: VariantRules) -> List[Tuple[str, str]]:
    """One-leg captures available for a MAN. If `men_capture_backward` is
    False, restrict to the man's two forward diagonals."""
    out: List[Tuple[str, str]] = []
    f, r = file_rank(origin)
    if rules.men_capture_backward:
        diags = ALL_DIAGS
    else:
        diags = _man_forward_diags(side)
    for df, dr in diags:
        adj = sq(f + df, r + dr, n)
        landing = sq(f + 2 * df, r + 2 * dr, n)
        if adj is None or landing is None:
            continue
        enemy = board.get(adj)
        if enemy is None or piece_side(enemy) == side:
            continue
        if rules.italian_capture_priority and is_king(enemy):
            continue
        if adj in already_jumped:
            continue
        if landing in board:
            continue
        out.append((landing, adj))
    return out


def _king_jump_steps(board: Dict[str, str], origin: str, side: str,
                      already_jumped: set, n: int,
                      rules: VariantRules) -> List[Tuple[str, str]]:
    """One-leg captures available for a KING. Flying kings fly along the
    diagonal; non-flying kings (American) capture like men over a single
    adjacent enemy."""
    out: List[Tuple[str, str]] = []
    f, r = file_rank(origin)
    if not rules.king_flying:
        # Non-flying king: same as a man capture, but in all 4 directions.
        for df, dr in ALL_DIAGS:
            adj = sq(f + df, r + dr, n)
            landing = sq(f + 2 * df, r + 2 * dr, n)
            if adj is None or landing is None:
                continue
            enemy = board.get(adj)
            if enemy is None or piece_side(enemy) == side:
                continue
            if adj in already_jumped:
                continue
            if landing in board:
                continue
            out.append((landing, adj))
        return out
    # Flying king.
    for df, dr in ALL_DIAGS:
        nf, nr = f + df, r + dr
        enemy_at: Optional[str] = None
        while 0 <= nf < n and 0 <= nr < n:
            sq_name = sq(nf, nr, n)
            occupant = board.get(sq_name)
            if enemy_at is None:
                if occupant is None:
                    pass
                elif piece_side(occupant) == side:
                    break
                else:
                    if sq_name in already_jumped:
                        break
                    enemy_at = sq_name
            else:
                if occupant is None:
                    out.append((sq_name, enemy_at))
                else:
                    break
            nf += df
            nr += dr
    return out


CapturePath = List[str]


def _enumerate_capture_sequences(
    board: Dict[str, str],
    origin: str,
    piece: str,
    path: CapturePath,
    captured_set: set,
    promote_rank: int,
    became_king_this_seq: bool,
    n: int,
    rules: VariantRules,
) -> List[Tuple[CapturePath, List[str], bool]]:
    """Enumerate all maximal capture sequences from `origin`."""
    side = piece_side(piece)
    if is_king(piece):
        steps = _king_jump_steps(board, origin, side, captured_set, n, rules)
    else:
        steps = _man_jump_steps(board, origin, side, captured_set, n, rules)

    if not steps:
        if len(path) > 1:
            crowned_at_end = rules.crown_at_capture_end and file_rank(origin)[1] == promote_rank
            return [(path, sorted(captured_set), became_king_this_seq or is_king(piece) or crowned_at_end)]
        return []

    sequences: List[Tuple[CapturePath, List[str], bool]] = []
    for landing, enemy in steps:
        # Check for mid-sequence promotion.
        new_piece = piece
        new_became_king = became_king_this_seq
        promotion_terminated_chain = False
        if not is_king(piece):
            _, lr = file_rank(landing)
            if lr == promote_rank and not rules.crown_at_capture_end:
                new_became_king = True
                if rules.mid_capture_promotion_continues:
                    # Promote and continue capturing AS A KING.
                    new_piece = "W" if side == "white" else "B"
                else:
                    # American/Italian style: promotion ENDS the turn —
                    # the piece becomes king but does not continue.
                    promotion_terminated_chain = True
                    new_piece = "W" if side == "white" else "B"

        new_captured = set(captured_set)
        new_captured.add(enemy)

        if promotion_terminated_chain:
            sequences.append((
                path + [landing],
                sorted(new_captured),
                True,
            ))
            continue

        deeper = _enumerate_capture_sequences(
            board, landing, new_piece, path + [landing],
            new_captured, promote_rank, new_became_king, n, rules,
        )
        if deeper:
            sequences.extend(deeper)
        else:
            sequences.append((
                path + [landing],
                sorted(new_captured),
                new_became_king,
            ))
    return sequences


def _all_capture_sequences(
    board: Dict[str, str], side: str, n: int, rules: VariantRules,
) -> List[Tuple[str, CapturePath, List[str], bool]]:
    promote_rank = n - 1 if side == "white" else 0
    out: List[Tuple[str, CapturePath, List[str], bool]] = []
    for origin, piece in list(board.items()):
        if piece_side(piece) != side:
            continue
        seqs = _enumerate_capture_sequences(
            board, origin, piece, [origin], set(),
            promote_rank, False, n, rules,
        )
        for path, cap, ends_king in seqs:
            out.append((origin, path, cap, ends_king))
    return out


def _legal_moves_for_side(board: Dict[str, str], side: str,
                          n: int, rules: VariantRules) -> List[str]:
    """All legal moves in PDN notation. Captures are MANDATORY when any
    exist. If `must_take_max`, only the sequence(s) capturing the most
    pieces remain."""
    captures = _all_capture_sequences(board, side, n, rules)
    if captures:
        if rules.must_take_max:
            max_cap = max(len(c[2]) for c in captures)
            captures = [c for c in captures if len(c[2]) == max_cap]
        if rules.italian_capture_priority:
            # FID 6.7–6.9: capturing king, more captured kings, then
            # the first differing captured piece's value in path order.
            # Italian jumps are short, so each captured square is its midpoint.
            def priority(capture):
                origin, path, _, _ = capture
                values = []
                for start, end in zip(path, path[1:]):
                    sf, sr = file_rank(start)
                    ef, er = file_rank(end)
                    values.append(is_king(board[sq((sf + ef) // 2, (sr + er) // 2, n)]))
                return (is_king(board[origin]), sum(values), tuple(values))

            best = max(map(priority, captures))
            captures = [capture for capture in captures if priority(capture) == best]
        return sorted(set("x".join(path) for (_, path, _, _) in captures))
    simples = _all_simple_moves(board, side, n, rules)
    return sorted(f"{frm}-{to}" for (frm, to) in simples)


def _apply_simple_move(board: Dict[str, str], origin: str, dest: str,
                       n: int) -> Tuple[Dict[str, str], bool]:
    new = dict(board)
    piece = new.pop(origin, None)
    if piece is None:
        return new, False
    promoted = False
    if not is_king(piece):
        _, dr = file_rank(dest)
        side = piece_side(piece)
        promote_rank = n - 1 if side == "white" else 0
        if dr == promote_rank:
            piece = "W" if side == "white" else "B"
            promoted = True
    new[dest] = piece
    return new, promoted


def _apply_capture_sequence(
    board: Dict[str, str], path: CapturePath, captured_squares: List[str],
    n: int, rules: Optional[VariantRules] = None,
) -> Tuple[Dict[str, str], bool]:
    new = dict(board)
    origin = path[0]
    dest = path[-1]
    piece = new.pop(origin, None)
    if piece is None:
        return new, False
    side = piece_side(piece)
    promote_rank = n - 1 if side == "white" else 0
    was_king_at_start = is_king(piece)
    became_king = False
    for landing in ([dest] if rules and rules.crown_at_capture_end else path[1:]):
        if not is_king(piece):
            _, lr = file_rank(landing)
            if lr == promote_rank:
                piece = "W" if side == "white" else "B"
                became_king = True
    for cap_sq in captured_squares:
        new.pop(cap_sq, None)
    new[dest] = piece
    return new, (became_king and not was_king_at_start) or is_king(piece) and not was_king_at_start


# ---------------------------------------------------------------------------
# Input coercion — accept multiple shapes from the LLM
# ---------------------------------------------------------------------------

_SQUARE_RE = re.compile(r"[a-l]\d{1,2}")


def _coerce_move_str(details: Dict[str, Any], legal_moves: List[str]) -> str:
    """Reduce whatever the LLM sent into a PDN string from legal_moves.

    Accepted shapes (priority order):
      1. `{move: "c3-d4"}` / `{move: "c3xe5xg7"}`            — canonical
      2. `{move: "c3 to d4"}` / `{move: "c3d4"}`             — sloppy
      3. `{from: "c3", to: "d4"}`                            — chess-style
      4. `{from: "c3", to: "g7"}` (unique chain)             — chess-style chain
      5. `{from: "c3", to: "g7", path: ["e5"]}`              — explicit chain
    """
    raw = str(details.get("move") or "").strip().lower()
    if raw:
        cleaned = (
            raw.replace(" to ", "-")
               .replace(" -> ", "-")
               .replace("→", "-")
               .replace(" ", "")
        )
        if cleaned in legal_moves:
            return cleaned
        squares = _SQUARE_RE.findall(cleaned)
        if len(squares) >= 2:
            for sep in ("-", "x"):
                candidate = sep.join(squares)
                if candidate in legal_moves:
                    return candidate
            cand = [
                m for m in legal_moves
                if _SQUARE_RE.findall(m) == squares
            ]
            if len(cand) == 1:
                return cand[0]

    frm = str(details.get("from") or "").strip().lower()
    to = str(details.get("to") or "").strip().lower()
    if frm and to:
        simple = f"{frm}-{to}"
        if simple in legal_moves:
            return simple
        jump = f"{frm}x{to}"
        if jump in legal_moves:
            return jump
        path = details.get("path") or []
        if isinstance(path, list) and path:
            mids = [str(p).strip().lower() for p in path]
            full = "x".join([frm] + mids + [to])
            if full in legal_moves:
                return full
        cand = [
            m for m in legal_moves
            if "x" in m
            and _SQUARE_RE.findall(m)[:1] == [frm]
            and _SQUARE_RE.findall(m)[-1:] == [to]
        ]
        if len(cand) == 1:
            return cand[0]

    return ""


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class CheckersModule(DomainModule):
    """Multi-variant checkers engine."""

    def __init__(self, name: str = "checkers",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._rules: VariantRules = resolve_variant(p.get("variant"))
        self._N: int = self._rules.board_size
        self._board: Dict[str, str] = initial_board(self._N)
        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        self._side_to_move: str = "white"
        self._kings_only_no_progress: int = 0
        self._move_number: int = 1
        self._position_history: List[str] = []
        self._draw_offer_by: Optional[str] = None
        self._terminal: Optional[Dict[str, Any]] = None
        self._turn_log: List[str] = []
        self._initialized = False
        self._kings_only_draw_threshold: int = int(
            p.get("draw_after_kings_only_moves") or 15
        )
        self._threefold_repetition_draw: bool = bool(
            p.get("threefold_repetition_draw", True)
        )
        logger.info(
            "[checkers] init variant=%s board_size=%d (back_cap=%s flying=%s "
            "max_cap=%s mid_promo=%s)",
            self._rules.name, self._N,
            self._rules.men_capture_backward, self._rules.king_flying,
            self._rules.must_take_max, self._rules.mid_capture_promotion_continues,
        )

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        pieces_per_side = (self._N // 2) * ((self._N - 2) // 2)
        return (f"Checkers ({self._rules.name} variant) — 2-player {self._N}×{self._N} "
                f"game, {pieces_per_side} pieces per side.")

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
        self._white_id = agents[0].id
        self._black_id = agents[1].id
        pieces_per_side = (self._N // 2) * ((self._N - 2) // 2)
        for ent, color in ((agents[0], "white"), (agents[1], "black")):
            if hasattr(ent, "properties"):
                ent.properties["color"] = color
                ent.properties["pieces_remaining"] = pieces_per_side
                ent.properties["kings"] = 0
        self._initialized = True
        self._position_history.append(self._position_key())
        logger.info(
            "[checkers] seated variant=%s white=%s black=%s",
            self._rules.name, agents[0].name, agents[1].name,
        )

    # ------------------------------------------------------------------ #
    # tick / filter_valid_actions
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        was_seeded = self._initialized
        self._seed_from_state(state)
        # First time we successfully seat both players, emit an init event
        # so the FE viz can switch to the right board size + render the
        # starting position BEFORE the first move arrives. Subsequent
        # ticks don't repeat it.
        if not was_seeded and self._initialized:
            return [{
                "type": "checkers_init",
                "board_position": dict(self._board),
                "board_size": self._N,
                "variant": self._rules.name,
                "narrative": (
                    f"Checkers ({self._rules.name}, "
                    f"{self._N}×{self._N}) — game begins."
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
        if self._draw_offer_by and self._draw_offer_by != entity_id:
            out.extend(["accept_draw", "decline_draw"])
            return out
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
        self._seed_from_state(state)
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
        self._seed_from_state(state)
        logger.info(
            "[checkers] post_resolution actor=%s action=%s success=%s",
            actor_id, action_name, success,
        )
        if not success or action_name not in self.custom_actions:
            return []
        raw = getattr(result, "details", None) or {}
        params = raw.get("_action_params") or {}
        details = {**raw, **params}
        logger.info(
            "[checkers] post_resolution details_keys=%s move=%r from=%r to=%r",
            list(details.keys())[:8], details.get("move"),
            details.get("from"), details.get("to"),
        )
        if action_name == "make_move":
            return self._handle_make_move(actor_id, details, state)
        if action_name == "resign":
            return self._handle_resign(actor_id, state)
        if action_name == "offer_draw":
            self._draw_offer_by = actor_id
            return [{"type": "checkers_draw_offer", "by": actor_id}]
        if action_name == "accept_draw":
            self._terminal = {"reason": "agreement", "winner": None}
            return [{
                "event_type": "checkers_draw",
                "type": "checkers_draw", "reason": "agreement",
                "narrative": "Draw by mutual agreement.",
            }]
        if action_name == "decline_draw":
            self._draw_offer_by = None
            return [{"type": "checkers_draw_decline", "by": actor_id}]
        return []

    def _handle_make_move(self, actor_id: str, details: Dict[str, Any],
                          state: Any) -> List[Dict[str, Any]]:
        side = "white" if actor_id == self._white_id else "black"
        actor_name = self._name_of(state, actor_id)
        legal = _legal_moves_for_side(self._board, side, self._N, self._rules)
        move_str = _coerce_move_str(details, legal)
        logger.info(
            "[checkers] _handle_make_move side=%s coerced=%r legal_count=%d sample=%s",
            side, move_str, len(legal), legal[:8],
        )
        if not move_str:
            return [self._invalid(
                actor_id, actor_name, "empty_or_unparseable",
                str(details.get("move") or details.get("from") or "?"),
                f"could not parse move (got details keys: "
                f"{list(details.keys())[:6]}; legal sample: {legal[:6]})",
            )]
        if move_str not in legal:
            return [self._invalid(
                actor_id, actor_name, "not_in_legal_moves", move_str,
                f"'{move_str}' is not in legal_moves; legal: {legal[:8]}",
            )]
        is_capture = "x" in move_str
        promoted_this_move = False
        captured_squares: List[str] = []
        if is_capture:
            path = move_str.split("x")
            all_seqs = _all_capture_sequences(self._board, side, self._N, self._rules)
            match = next(
                ((p, c, k) for (_, p, c, k) in all_seqs if p == path), None
            )
            if match is None:
                return [self._invalid(
                    actor_id, actor_name, "capture_resolve_failed", move_str,
                    "could not resolve capture path",
                )]
            path_squares, captured_squares, ended_king = match
            origin_piece_before = self._board.get(path_squares[0], "")
            self._board, _ = _apply_capture_sequence(
                self._board, path_squares, captured_squares, self._N, self._rules,
            )
            promoted_this_move = ended_king and not is_king(origin_piece_before)
            self._kings_only_no_progress = 0
        else:
            origin, dest = move_str.split("-")
            origin_piece = self._board.get(origin, "")
            self._board, promoted_this_move = _apply_simple_move(
                self._board, origin, dest, self._N,
            )
            if self._all_kings_on_board() and is_king(origin_piece):
                self._kings_only_no_progress += 1
            else:
                self._kings_only_no_progress = 0

        self._refresh_player_props(state)
        san = self._format_move(move_str, captured_squares, promoted_this_move)
        self._turn_log.append(san)
        if side == "black":
            self._move_number += 1
        next_side = opposite(side)
        logger.info(
            "[checkers] EMIT checkers_move move=%s captures=%d board_pieces=%d "
            "next_side=%s",
            move_str, len(captured_squares), len(self._board), next_side,
        )
        events: List[Dict[str, Any]] = [{
            "type": "checkers_move",
            "player": actor_id,
            "move": move_str,
            "san": san,
            "captures": list(captured_squares),
            "promoted": promoted_this_move,
            "side_to_move": next_side,
            "board_position": dict(self._board),
            "board_size": self._N,
            "variant": self._rules.name,
            "narrative": (
                f"{actor_name} plays {san}"
                + (f" (captures {len(captured_squares)})" if captured_squares else "")
                + (" — promotes to KING" if promoted_this_move else "")
            ),
        }]
        self._side_to_move = next_side
        self._draw_offer_by = None
        self._position_history.append(self._position_key())

        opp_pieces = self._pieces_for_side(next_side)
        if not opp_pieces:
            winner_id = actor_id
            self._terminal = {"reason": "all_captured", "winner": winner_id}
            events.append({
                "event_type": "checkers_all_captured",
                "type": "checkers_all_captured",
                "winner": winner_id, "loser": self._opp_id(actor_id),
                "narrative": (
                    f"All of {self._name_of(state, self._opp_id(actor_id))}'s pieces captured. "
                    f"{self._name_of(state, winner_id)} wins."
                ),
            })
            return events
        if not _legal_moves_for_side(self._board, next_side, self._N, self._rules):
            winner_id = actor_id
            self._terminal = {"reason": "blockade", "winner": winner_id}
            events.append({
                "event_type": "checkers_blockade",
                "type": "checkers_blockade",
                "winner": winner_id, "loser": self._opp_id(actor_id),
                "narrative": (
                    f"{self._name_of(state, self._opp_id(actor_id))} has no legal moves. "
                    f"{self._name_of(state, winner_id)} wins by blockade."
                ),
            })
            return events
        if self._kings_only_no_progress >= self._kings_only_draw_threshold * 2:
            self._terminal = {"reason": "kings_only_stall", "winner": None}
            events.append({
                "event_type": "checkers_draw",
                "type": "checkers_draw", "reason": "kings_only_stall",
                "narrative": (
                    f"Draw — {self._kings_only_draw_threshold} full moves "
                    "of kings-only play without progress."
                ),
            })
            return events
        if self._threefold_repetition_draw and \
                self._position_history.count(self._position_key()) >= 3:
            self._terminal = {"reason": "threefold", "winner": None}
            events.append({
                "event_type": "checkers_draw",
                "type": "checkers_draw", "reason": "threefold",
                "narrative": "Draw by threefold repetition.",
            })
            return events
        return events

    def _handle_resign(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        winner = self._opp_id(actor_id)
        self._terminal = {"reason": "resign", "winner": winner}
        loser_name = self._name_of(state, actor_id)
        winner_name = self._name_of(state, winner) if winner else "opponent"
        return [{
            "event_type": "checkers_resign",
            "type": "checkers_resign", "loser": actor_id, "winner": winner,
            "narrative": f"{loser_name} resigns. {winner_name} wins.",
        }]

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in (self._white_id, self._black_id):
            return {"role": "spectator", "board_position": dict(self._board)}
        side = "white" if entity_id == self._white_id else "black"
        is_your_turn = (entity_id == self._active_player_id()) and not self._terminal
        legal = _legal_moves_for_side(self._board, side, self._N, self._rules) if is_your_turn else []
        any_capture_available = bool(legal) and any("x" in m for m in legal)
        board_ascii = render_ascii_board(self._board, side, self._N)
        if is_your_turn:
            extra: List[str] = []
            if any_capture_available:
                extra.append("CAPTURES ARE MANDATORY this turn — only capture moves are listed.")
            if self._rules.must_take_max and any_capture_available:
                extra.append("Variant rule: you MUST take the longest capture sequence.")
            cap_note = (" " + " ".join(extra)) if extra else ""
            instructions = (
                f"IT IS YOUR TURN. You are playing {side.upper()} in the "
                f"{self._rules.name} variant on a {self._N}×{self._N} board. "
                "Pick ONE move from `legal_moves` and call `make_move` with "
                "either `{move: '<pdn>'}` (e.g. 'c3-d4' or 'c3xe5xg7') OR "
                "`{from, to[, path]}` (chess-style). DO NOT set a target."
                + cap_note
            )
        else:
            instructions = (
                f"Opponent ({opposite(side).upper()}) is on move. Wait."
            )
        return {
            "instructions": instructions,
            "variant": self._rules.name,
            "board_size": self._N,
            "your_color": side,
            "is_your_turn": is_your_turn,
            "captures_mandatory_this_turn": any_capture_available,
            "board_ascii": board_ascii,
            "board_position": dict(self._board),
            "legal_moves": legal,
            "legal_moves_count": len(legal),
            "pieces_remaining": {
                "white": self._pieces_for_side("white"),
                "black": self._pieces_for_side("black"),
            },
            "kings_count": {
                "white": sum(1 for p in self._board.values() if p == "W"),
                "black": sum(1 for p in self._board.values() if p == "B"),
            },
            "move_history": list(self._turn_log[-40:]),
            "move_number": self._move_number,
            "kings_only_no_progress_plies": self._kings_only_no_progress,
            "draw_offered_by_opponent": (
                self._draw_offer_by is not None and self._draw_offer_by != entity_id
            ),
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _pieces_for_side(self, side: str) -> int:
        return sum(1 for p in self._board.values() if piece_side(p) == side)

    def _all_kings_on_board(self) -> bool:
        return all(is_king(p) for p in self._board.values())

    def _invalid(self, actor_id: str, actor_name: str, reason: str,
                  move_str: str, narrative: str) -> Dict[str, Any]:
        return {
            "type": "checkers_invalid",
            "player": actor_id,
            "reason": reason,
            "move": move_str,
            "narrative": f"{actor_name} tried {move_str or '?'} — {narrative}.",
        }

    def _format_move(self, move_str: str, captured: List[str],
                      promoted: bool) -> str:
        suffix = ""
        if captured:
            suffix += f" (×{len(captured)})"
        if promoted:
            suffix += "♔"
        return f"{move_str}{suffix}"

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
        for entity_id, color in (
            (self._white_id, "white"), (self._black_id, "black"),
        ):
            if not entity_id:
                continue
            ent = state.entities.get(entity_id)
            if not ent or not hasattr(ent, "properties"):
                continue
            ent.properties["pieces_remaining"] = self._pieces_for_side(color)
            king_char = "W" if color == "white" else "B"
            ent.properties["kings"] = sum(
                1 for p in self._board.values() if p == king_char
            )

    def _position_key(self) -> str:
        board_str = ",".join(f"{k}{v}" for k, v in sorted(self._board.items()))
        return f"{board_str}|{self._side_to_move}"

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
            "kings_only_no_progress": self._kings_only_no_progress,
            "move_number": self._move_number,
            "position_history": list(self._position_history),
            "draw_offer_by": self._draw_offer_by,
            "terminal": dict(self._terminal) if self._terminal else None,
            "turn_log": list(self._turn_log),
            "initialized": self._initialized,
            "variant": self._rules.name,
            "board_size": self._N,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "CheckersModule":
        params = data.get("params", {}) or {}
        # Allow variant resurrection from serialized state.
        s = data.get("state", {})
        if "variant" in s and "variant" not in params:
            params = {**params, "variant": s["variant"]}
        mod = cls(name=data.get("name", "checkers"), params=params)
        if s.get("board"):
            mod._board = dict(s["board"])
        mod._white_id = s.get("white_id")
        mod._black_id = s.get("black_id")
        mod._side_to_move = s.get("side_to_move", "white")
        mod._kings_only_no_progress = int(s.get("kings_only_no_progress") or 0)
        mod._move_number = int(s.get("move_number") or 1)
        mod._position_history = list(s.get("position_history") or [])
        mod._draw_offer_by = s.get("draw_offer_by")
        mod._terminal = dict(s["terminal"]) if s.get("terminal") else None
        mod._turn_log = list(s.get("turn_log") or [])
        mod._initialized = bool(s.get("initialized", False))
        return mod
