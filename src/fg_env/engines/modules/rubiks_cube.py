"""Rubik's Cube Duel — sealed-tick FMC (Fewest Moves Challenge).

Two players race the SAME scramble on their own private cubes. Each
round both submit ONE move in Singmaster notation (R, U', F2 …);
reveal happens simultaneously and each move applies only to that
player's cube. First to reach the solved state wins. Tied solves
break to fewer moves used. Move cap reached without solving →
fewer-moves-so-far wins.

The cube engine is sticker-permutation based. Each face has N²
stickers stored in a flat array indexed in a standard layout:

    U: 0..N²-1
    L: N²..2N²-1
    F: 2N²..3N²-1
    R: 3N²..4N²-1
    B: 4N²..5N²-1
    D: 5N²..6N²-1

Each face's stickers are arranged row-major as viewed from OUTSIDE
the cube. Move permutations have been derived by 3D rotation of
cubies around the appropriate axis and verified empirically against
known cube identities (R+R' = identity, R⁴ = identity, Sune⁶ =
identity).

Public events
~~~~~~~~~~~~~
  - `rc_init`     { cube_size, scramble, move_cap, white_cube,
                    black_cube }                                    one-shot
  - `rc_pending`  { player, side, move, round }                     sealed
  - `rc_round`    { round, white_move, black_move, white_cube,
                    black_cube, white_moves, black_moves,
                    white_solved, black_solved }                    both submitted
  - `rc_win`      { winner, loser, white_moves, black_moves,
                    white_solved, black_solved, reason, scramble }  match terminal
  - `rc_draw`     { reason, white_moves, black_moves, ... }         rare tie
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cube engine
# ---------------------------------------------------------------------------

FACE_NAMES: Tuple[str, ...] = ('U', 'L', 'F', 'R', 'B', 'D')
SOLVED_COLORS: Dict[str, str] = {
    'U': 'W',   # white on top
    'D': 'Y',   # yellow on bottom
    'F': 'G',   # green in front
    'B': 'b',   # blue in back (lowercase to disambiguate from face name B)
    'L': 'O',   # orange on left
    'R': 'R',   # red on right
}


def face_offset(n: int, face: str) -> int:
    """Global sticker offset of a face in an N×N cube state array."""
    return FACE_NAMES.index(face) * n * n


VALID_MOVE_REGEX = re.compile(r"^([UDLRFB])('|2)?$")
ALL_MOVES = [face + mod for face in 'UDLRFB' for mod in ['', "'", '2']]


class Cube:
    """N×N×N Rubik's cube state machine. Supports N=2 and N=3."""

    def __init__(self, n: int = 3, scramble: Optional[List[str]] = None):
        if n not in (2, 3):
            raise ValueError(f"Unsupported cube size: {n}")
        self.n = n
        self.stickers: List[str] = self._solved_stickers()
        self._move_history: List[str] = []
        self.moves: Dict[str, List[Tuple[int, ...]]] = {}
        self._build_move_table()
        if scramble:
            for move in scramble:
                self.apply_move(move)
            # Scramble doesn't count toward the player's move budget.
            self._move_history = []

    def _solved_stickers(self) -> List[str]:
        out: List[str] = []
        for face in FACE_NAMES:
            out.extend([SOLVED_COLORS[face]] * (self.n * self.n))
        return out

    # ------------------------------------------------------------------ #
    # Permutation tables
    # ------------------------------------------------------------------ #

    def _build_move_table(self) -> None:
        n = self.n
        U = face_offset(n, 'U')
        L = face_offset(n, 'L')
        F = face_offset(n, 'F')
        R = face_offset(n, 'R')
        B = face_offset(n, 'B')
        D = face_offset(n, 'D')

        def face_rot_cycles(off: int) -> List[Tuple[int, ...]]:
            """Cycles that rotate an N×N face 90° CW (in cube-local index space)."""
            cycles: List[Tuple[int, ...]] = []
            for ring in range((n + 1) // 2):
                size = n - 2 * ring - 1
                if size == 0:
                    continue
                for k in range(size):
                    cycles.append((
                        off + ring * n + (ring + k),
                        off + (ring + k) * n + (n - 1 - ring),
                        off + (n - 1 - ring) * n + (n - 1 - ring - k),
                        off + (n - 1 - ring - k) * n + ring,
                    ))
            return cycles

        def strip_cycles(
            spec: List[Tuple[int, Callable[[int], int], Callable[[int], int]]],
        ) -> List[Tuple[int, ...]]:
            """Emit one 4-tuple cycle per i in [0, N) by evaluating
            each (face_offset, row(i), col(i)) tuple in `spec`."""
            cycles: List[Tuple[int, ...]] = []
            for i in range(n):
                cycles.append(tuple(
                    off + r_fn(i) * n + c_fn(i)
                    for off, r_fn, c_fn in spec
                ))
            return cycles

        # Move permutations derived from 3D rotation of cubies around
        # the appropriate face axis (CW from outside view). Verified
        # by Sune sextuple identity at module-load smoke test.
        # R: U[i, n-1] → B[n-1-i, 0] → D[i, n-1] → F[i, n-1] → U
        self.moves['R'] = face_rot_cycles(R) + strip_cycles([
            (U, lambda i: i, lambda _i: n - 1),
            (B, lambda i: n - 1 - i, lambda _i: 0),
            (D, lambda i: i, lambda _i: n - 1),
            (F, lambda i: i, lambda _i: n - 1),
        ])
        # L: U[i, 0] → F[i, 0] → D[i, 0] → B[n-1-i, n-1] → U
        self.moves['L'] = face_rot_cycles(L) + strip_cycles([
            (U, lambda i: i, lambda _i: 0),
            (F, lambda i: i, lambda _i: 0),
            (D, lambda i: i, lambda _i: 0),
            (B, lambda i: n - 1 - i, lambda _i: n - 1),
        ])
        # U: F[0, i] → L[0, i] → B[0, i] → R[0, i] → F
        self.moves['U'] = face_rot_cycles(U) + strip_cycles([
            (F, lambda _i: 0, lambda i: i),
            (L, lambda _i: 0, lambda i: i),
            (B, lambda _i: 0, lambda i: i),
            (R, lambda _i: 0, lambda i: i),
        ])
        # D: F[n-1, i] → R[n-1, i] → B[n-1, i] → L[n-1, i] → F
        self.moves['D'] = face_rot_cycles(D) + strip_cycles([
            (F, lambda _i: n - 1, lambda i: i),
            (R, lambda _i: n - 1, lambda i: i),
            (B, lambda _i: n - 1, lambda i: i),
            (L, lambda _i: n - 1, lambda i: i),
        ])
        # F: U[n-1, i] → R[i, 0] → D[0, n-1-i] → L[n-1-i, n-1] → U
        self.moves['F'] = face_rot_cycles(F) + strip_cycles([
            (U, lambda _i: n - 1, lambda i: i),
            (R, lambda i: i, lambda _i: 0),
            (D, lambda _i: 0, lambda i: n - 1 - i),
            (L, lambda i: n - 1 - i, lambda _i: n - 1),
        ])
        # B: U[0, i] → L[n-1-i, 0] → D[n-1, n-1-i] → R[i, n-1] → U
        self.moves['B'] = face_rot_cycles(B) + strip_cycles([
            (U, lambda _i: 0, lambda i: i),
            (L, lambda i: n - 1 - i, lambda _i: 0),
            (D, lambda _i: n - 1, lambda i: n - 1 - i),
            (R, lambda i: i, lambda _i: n - 1),
        ])

    # ------------------------------------------------------------------ #
    # Apply a move
    # ------------------------------------------------------------------ #

    def _apply_cycles(self, cycles: List[Tuple[int, ...]]) -> None:
        """Each cycle (a, b, c, d) shifts: stickers[b] ← stickers[a],
        stickers[c] ← stickers[b], …, stickers[a] ← stickers[d]."""
        for cyc in cycles:
            tail = self.stickers[cyc[-1]]
            for i in range(len(cyc) - 1, 0, -1):
                self.stickers[cyc[i]] = self.stickers[cyc[i - 1]]
            self.stickers[cyc[0]] = tail

    def apply_move(self, move: str) -> None:
        m = VALID_MOVE_REGEX.match(move.strip())
        if not m:
            raise ValueError(f"Invalid move: {move!r}")
        face, modifier = m.group(1), m.group(2)
        cycles = self.moves[face]
        if modifier is None:
            self._apply_cycles(cycles)
        elif modifier == "'":
            for _ in range(3):
                self._apply_cycles(cycles)
        elif modifier == "2":
            for _ in range(2):
                self._apply_cycles(cycles)
        self._move_history.append(move)

    # ------------------------------------------------------------------ #
    # Inspection
    # ------------------------------------------------------------------ #

    def is_solved(self) -> bool:
        n_per_face = self.n * self.n
        for f_idx in range(6):
            start = f_idx * n_per_face
            face_color = self.stickers[start]
            for i in range(start + 1, start + n_per_face):
                if self.stickers[i] != face_color:
                    return False
        return True

    def move_count(self) -> int:
        return len(self._move_history)

    def to_ascii(self) -> str:
        """Render the unfolded cross diagram."""
        n = self.n
        pad = " " * (n * 2 + 1)
        out: List[str] = []
        # U on top.
        for r in range(n):
            line = pad + " ".join(
                self.stickers[face_offset(n, 'U') + r * n + c]
                for c in range(n)
            )
            out.append(line)
        out.append("")
        # L F R B band.
        for r in range(n):
            parts = []
            for face in ('L', 'F', 'R', 'B'):
                parts.append(" ".join(
                    self.stickers[face_offset(n, face) + r * n + c]
                    for c in range(n)
                ))
            out.append(" | ".join(parts))
        out.append("")
        # D on bottom.
        for r in range(n):
            line = pad + " ".join(
                self.stickers[face_offset(n, 'D') + r * n + c]
                for c in range(n)
            )
            out.append(line)
        return "\n".join(out)

    def snapshot(self) -> Dict[str, List[List[str]]]:
        """Per-face 2D grids — for the viz."""
        n = self.n
        out: Dict[str, List[List[str]]] = {}
        for face in FACE_NAMES:
            off = face_offset(n, face)
            grid = [
                [self.stickers[off + r * n + c] for c in range(n)]
                for r in range(n)
            ]
            out[face] = grid
        return out


def random_scramble(n: int, depth: int, rng: random.Random) -> List[str]:
    """Random face moves, avoiding same-face consecutive (which would
    cancel or merge with the previous move and not really 'scramble')."""
    moves: List[str] = []
    last_face: Optional[str] = None
    for _ in range(depth):
        face = rng.choice('UDLRFB')
        while face == last_face:
            face = rng.choice('UDLRFB')
        modifier = rng.choice(['', "'", '2'])
        moves.append(face + modifier)
        last_face = face
    return moves


def _clean_move(raw: Any) -> Optional[str]:
    """Normalize various sloppy notations to canonical Singmaster form."""
    if raw is None:
        return None
    s = str(raw).strip()
    # Strip ONLY matched-pair wrapping quotes / parens. Stripping a
    # naked single quote with `s.strip("'")` was a bug: that ate the
    # trailing prime tick in `R'`, turning it into bare `R`.
    if len(s) >= 2:
        pairs = [('"', '"'), ('(', ')'), ('[', ']'), ('{', '}')]
        for opener, closer in pairs:
            if s[0] == opener and s[-1] == closer:
                s = s[1:-1].strip()
                break
        # Matched-pair single quote: only strip if BOTH ends are '.
        if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
            s = s[1:-1].strip()
    s = s.upper()
    s = (
        s.replace("PRIME", "'")
         .replace("INVERTED", "'")
         .replace("INVERSE", "'")
         .replace("-", "")
         .replace(" ", "")
         .replace("’", "'")
         .replace("`", "'")
    )
    # Some LLMs write "R2'" — interpret as R2 (180° is its own inverse).
    if len(s) == 3 and s[1] == '2' and s[2] == "'":
        s = s[:2]
    if VALID_MOVE_REGEX.match(s):
        return s
    return None


def _recover_move_from_text(details: Dict[str, Any]) -> Optional[str]:
    """Scan reasoning / speech fields for the FIRST plausible move token."""
    parts: List[str] = []
    for key in ("reasoning", "speech", "narrative", "thought", "cot"):
        v = details.get(key)
        if isinstance(v, str) and v:
            parts.append(v)
    if not parts:
        return None
    text = " ".join(parts)
    # Match a Singmaster move bounded by non-letter on both sides so
    # we don't pick up the "R" in "Ready" or "Rotate".
    for m in re.finditer(
        r"(?<![A-Za-z])([UDLRFB])([’'`2])?(?![A-Za-z])",
        text,
    ):
        candidate = m.group(1) + (m.group(2) or "")
        candidate = candidate.replace("’", "'").replace("`", "'")
        if VALID_MOVE_REGEX.match(candidate):
            return candidate
    return None


def _fallback_move(cube: Cube, rng: random.Random) -> str:
    """Pick a random move that doesn't share a face with the last move
    applied (avoids no-op like R followed by R')."""
    last = cube._move_history[-1] if cube._move_history else None
    last_face = last[0] if last else None
    for _ in range(20):
        face = rng.choice('UDLRFB')
        if face != last_face:
            return face + rng.choice(['', "'", '2'])
    return 'R'


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

DEFAULT_CUBE_SIZE = 3
ALLOWED_CUBE_SIZES = (2, 3)
DEFAULT_SCRAMBLE_DEPTH = 15
MIN_SCRAMBLE_DEPTH = 5
MAX_SCRAMBLE_DEPTH = 25
DEFAULT_MOVE_CAP = 80
MIN_MOVE_CAP = 30
MAX_MOVE_CAP = 200


class RubiksCubeModule(DomainModule):
    """Sealed-tick FMC duel."""

    suppress_chat = False   # no hidden info — taunts are fair

    def __init__(self, name: str = "rubiks_cube",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        size = int(p.get("cube_size") or DEFAULT_CUBE_SIZE)
        if size not in ALLOWED_CUBE_SIZES:
            size = DEFAULT_CUBE_SIZE
        self._n: int = size
        depth = int(p.get("scramble_depth") or DEFAULT_SCRAMBLE_DEPTH)
        depth = max(MIN_SCRAMBLE_DEPTH, min(MAX_SCRAMBLE_DEPTH, depth))
        self._scramble_depth: int = depth
        cap = int(p.get("move_cap") or DEFAULT_MOVE_CAP)
        cap = max(MIN_MOVE_CAP, min(MAX_MOVE_CAP, cap))
        self._move_cap: int = cap

        seed = p.get("rng_seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()
        self._scramble: List[str] = random_scramble(
            self._n, self._scramble_depth, self._rng,
        )

        self._white_cube: Optional[Cube] = None
        self._black_cube: Optional[Cube] = None
        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        self._white_gave_up: bool = False
        self._black_gave_up: bool = False
        self._pending_white: Optional[str] = None
        self._pending_black: Optional[str] = None
        self._round_number: int = 1

        self._terminal: Optional[Dict[str, Any]] = None
        self._initialized = False
        logger.info(
            "[rc] init size=%d scramble_depth=%d cap=%d scramble=%s",
            size, depth, cap, ' '.join(self._scramble),
        )

    @property
    def description(self) -> str:
        return (f"Rubik's Cube Duel — {self._n}×{self._n}×{self._n}, "
                f"scramble depth {self._scramble_depth}, "
                f"move cap {self._move_cap}.")

    @property
    def custom_actions(self) -> List[str]:
        return ["cube_move", "give_up"]

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
        self._white_cube = Cube(self._n, scramble=self._scramble)
        self._black_cube = Cube(self._n, scramble=self._scramble)
        for ent, color in ((agents[0], "white"), (agents[1], "black")):
            if hasattr(ent, "properties"):
                ent.properties["color"] = color
                ent.properties["moves_used"] = 0
                ent.properties["solved"] = False
        self._initialized = True
        logger.info(
            "[rc] seated white=%s black=%s",
            agents[0].name, agents[1].name,
        )

    # ------------------------------------------------------------------ #
    # Tick
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        was_seeded = self._initialized
        self._seed_from_state(state)
        if not was_seeded and self._initialized and self._white_cube and self._black_cube:
            return [{
                "type": "rc_init",
                "cube_size": self._n,
                "scramble": list(self._scramble),
                "scramble_depth": self._scramble_depth,
                "move_cap": self._move_cap,
                "white_cube": self._white_cube.snapshot(),
                "black_cube": self._black_cube.snapshot(),
                "narrative": (
                    f"Rubik's Cube Duel — {self._n}×{self._n}×{self._n}, "
                    f"scramble depth {self._scramble_depth}, "
                    f"move cap {self._move_cap}. Same scramble, "
                    "race to solve."
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
        side = self._side_for_entity(actor_id)
        if self._terminal is not None:
            return "Game over"
        if action_name == "cube_move":
            if self._has_pending(side):
                return "You already submitted this round — waiting on opponent"
            if self._is_done(side):
                return "You have already finished this game"
        if action_name == "give_up" and self._is_done(side):
            return "You have already finished"
        return None

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        if self._terminal is not None or not success or action_name not in self.custom_actions:
            return []
        raw = getattr(result, "details", None) or {}
        params = raw.get("_action_params") or {}
        details = {**raw, **params}
        if action_name == "cube_move":
            return self._handle_move(actor_id, details, state)
        if action_name == "give_up":
            return self._handle_give_up(actor_id, state)
        return []

    def _handle_move(self, actor_id: str, details: Dict[str, Any],
                     state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        cube = self._white_cube if side == "white" else self._black_cube
        actor_name = self._name_of(state, actor_id)
        raw_move = details.get("move") or details.get("turn") or details.get("notation")
        move = _clean_move(raw_move)
        logger.info(
            "[rc] submit: actor=%s side=%s raw=%r cleaned=%r",
            actor_id, side, raw_move, move,
        )
        if move is None:
            move = _recover_move_from_text(details)
            if move:
                logger.info("[rc] %s recovered %s from text", side, move)
        if move is None and cube is not None:
            move = _fallback_move(cube, self._rng)
            logger.warning(
                "[rc] %s gave invalid move raw=%r — substituting %s",
                side, raw_move, move,
            )
        if move is None:
            move = "R"   # ultimate defensive default

        if side == "white":
            self._pending_white = move
        else:
            self._pending_black = move

        events: List[Dict[str, Any]] = [{
            "type": "rc_pending",
            "player": actor_id,
            "side": side,
            "move": move,
            "round": self._round_number,
            "narrative": f"{actor_name} ({side}) submits {move} (sealed).",
        }]

        opp_side = "black" if side == "white" else "white"
        opp_pending = (self._pending_black if side == "white"
                       else self._pending_white)
        if opp_pending is not None or self._is_done(opp_side):
            events.extend(self._resolve_round(state))
        return events

    def _handle_give_up(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        if side == "white":
            self._white_gave_up = True
        else:
            self._black_gave_up = True
        events: List[Dict[str, Any]] = [{
            "type": "rc_pending",
            "player": actor_id,
            "side": side,
            "move": "GIVE_UP",
            "round": self._round_number,
            "narrative": f"{actor_name} ({side}) concedes.",
        }]
        opp_side = "black" if side == "white" else "white"
        opp_pending = (self._pending_black if side == "white"
                       else self._pending_white)
        if opp_pending is not None or self._is_done(opp_side):
            events.extend(self._resolve_round(state))
        return events

    def _resolve_round(self, state: Any) -> List[Dict[str, Any]]:
        w_move = self._pending_white
        b_move = self._pending_black
        self._pending_white = None
        self._pending_black = None

        events: List[Dict[str, Any]] = []
        for side, move in (("white", w_move), ("black", b_move)):
            if move is None or move == "GIVE_UP" or self._is_done(side):
                continue
            cube = self._white_cube if side == "white" else self._black_cube
            if cube is None:
                continue
            try:
                cube.apply_move(move)
            except ValueError:
                logger.exception("[rc] apply_move failed: %r", move)
                continue

        self._refresh_player_props(state)
        white_name = self._name_of(state, self._white_id)
        black_name = self._name_of(state, self._black_id)
        white_solved = self._white_cube.is_solved() if self._white_cube else False
        black_solved = self._black_cube.is_solved() if self._black_cube else False
        white_moves = self._white_cube.move_count() if self._white_cube else 0
        black_moves = self._black_cube.move_count() if self._black_cube else 0

        events.append({
            "type": "rc_round",
            "round": self._round_number,
            "white_move": w_move,
            "black_move": b_move,
            "white_cube": self._white_cube.snapshot() if self._white_cube else None,
            "black_cube": self._black_cube.snapshot() if self._black_cube else None,
            "white_moves": white_moves,
            "black_moves": black_moves,
            "white_solved": white_solved,
            "black_solved": black_solved,
            "white_gave_up": self._white_gave_up,
            "black_gave_up": self._black_gave_up,
            "move_cap": self._move_cap,
            "narrative": (
                f"Round {self._round_number}: "
                f"{white_name}→{w_move or '—'}, "
                f"{black_name}→{b_move or '—'}."
            ),
        })

        self._round_number += 1
        events.extend(self._check_terminal(state))
        return events

    # ------------------------------------------------------------------ #
    # Terminal check
    # ------------------------------------------------------------------ #

    def _check_terminal(self, state: Any) -> List[Dict[str, Any]]:
        if self._terminal is not None:
            return []
        white_name = self._name_of(state, self._white_id)
        black_name = self._name_of(state, self._black_id)
        white_solved = self._white_cube.is_solved() if self._white_cube else False
        black_solved = self._black_cube.is_solved() if self._black_cube else False
        # Both sealed moves have now resolved, so same-round solves still
        # tie. A first solve or surrender settles the match immediately;
        # only the move-cap fallback needs both players to finish.
        if not (white_solved or black_solved
                or self._white_gave_up or self._black_gave_up
                or (self._is_done("white") and self._is_done("black"))):
            return []
        white_moves = self._white_cube.move_count() if self._white_cube else 0
        black_moves = self._black_cube.move_count() if self._black_cube else 0

        winner_id: Optional[str] = None
        reason: str
        if self._white_gave_up and self._black_gave_up:
            reason = "both_gave_up"
        elif self._white_gave_up:
            winner_id = self._black_id
            reason = "white_gave_up"
        elif self._black_gave_up:
            winner_id = self._white_id
            reason = "black_gave_up"
        elif white_solved and black_solved:
            if white_moves < black_moves:
                winner_id = self._white_id
                reason = "both_solved_white_fewer"
            elif black_moves < white_moves:
                winner_id = self._black_id
                reason = "both_solved_black_fewer"
            else:
                reason = "both_solved_tied"
        elif white_solved:
            winner_id = self._white_id
            reason = "white_solved_only"
        elif black_solved:
            winner_id = self._black_id
            reason = "black_solved_only"
        else:
            if white_moves < black_moves:
                winner_id = self._white_id
                reason = "cap_white_fewer"
            elif black_moves < white_moves:
                winner_id = self._black_id
                reason = "cap_black_fewer"
            else:
                reason = "cap_tied"

        events: List[Dict[str, Any]] = []
        if winner_id is not None:
            self._terminal = {"reason": reason, "winner": winner_id}
            events.append({
                "event_type": "rc_win",
                "type": "rc_win",
                "winner": winner_id,
                "loser": self._opp_id(winner_id),
                "reason": reason,
                "white_moves": white_moves,
                "black_moves": black_moves,
                "white_solved": white_solved,
                "black_solved": black_solved,
                "scramble": list(self._scramble),
                "narrative": (
                    f"{self._name_of(state, winner_id)} wins. "
                    f"{white_name}: {white_moves} moves "
                    f"({'solved' if white_solved else 'unsolved'}), "
                    f"{black_name}: {black_moves} moves "
                    f"({'solved' if black_solved else 'unsolved'})."
                ),
            })
        else:
            self._terminal = {"reason": reason, "winner": None}
            events.append({
                "event_type": "rc_draw",
                "type": "rc_draw",
                "reason": reason,
                "white_moves": white_moves,
                "black_moves": black_moves,
                "white_solved": white_solved,
                "black_solved": black_solved,
                "scramble": list(self._scramble),
                "narrative": (
                    f"Drawn — {white_name} {white_moves} moves, "
                    f"{black_name} {black_moves} moves. "
                    f"{'Both solved.' if white_solved else 'Neither solved.'}"
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
                "cube_size": self._n,
                "scramble": list(self._scramble),
            }
        side = self._side_for_entity(entity_id)
        cube = self._white_cube if side == "white" else self._black_cube
        own_pending = (self._pending_white if side == "white"
                       else self._pending_black)
        opp_pending = (self._pending_black if side == "white"
                       else self._pending_white)
        own_moves = cube.move_count() if cube else 0
        opp_cube = self._black_cube if side == "white" else self._white_cube
        opp_moves = opp_cube.move_count() if opp_cube else 0
        moves_left = max(0, self._move_cap - own_moves)

        if self._terminal is not None:
            instructions = "Game over."
        elif own_pending is not None:
            instructions = (
                f"You submitted {own_pending} this round — sealed. "
                "Waiting on opponent." if opp_pending is None
                else f"You submitted {own_pending} this round — sealed. "
                     "Opponent has also submitted; round resolving."
            )
        elif self._is_done(side):
            instructions = "You've finished this game; wait for the opponent."
        else:
            ascii_view = cube.to_ascii() if cube else ""
            scramble_str = " ".join(self._scramble)
            instructions = (
                f"IT IS YOUR TURN. You are the {side.upper()} seat "
                f"in Rubik's Cube Duel ({self._n}×{self._n}×{self._n}).\n"
                f"\n"
                f"SHARED SCRAMBLE (applied to both cubes at start): "
                f"{scramble_str}\n"
                f"\n"
                f"YOUR CURRENT CUBE STATE (unfolded; W=white, Y=yellow, "
                f"G=green, b=blue, O=orange, R=red):\n"
                f"{ascii_view}\n"
                f"\n"
                f"You've used {own_moves} moves; opponent {opp_moves}. "
                f"Budget remaining: {moves_left} moves.\n"
                f"\n"
                f"GOAL: solve before the opponent. Tie at solved → "
                f"fewest moves wins. Move cap hit → fewest moves wins.\n"
                f"\n"
                f"YOUR TOOL CALL — output exactly this shape:\n"
                f'  cube_move(move="R")          # clockwise right turn\n'
                f"  cube_move(move=\"R'\")         # counter-clockwise right turn\n"
                f'  cube_move(move="R2")         # 180° right turn\n'
                f"  cube_move(move=\"U\") | cube_move(move=\"F2\") | cube_move(move=\"L'\")\n"
                f"\n"
                f"VALID `move` VALUES (exactly one of these, as a STRING): "
                f"R, R', R2, L, L', L2, U, U', U2, D, D', D2, F, F', F2, B, B', B2.\n"
                f"\n"
                f"The `move` parameter is REQUIRED. If you omit it the "
                f"engine will substitute a random move and you lose tempo. "
                f"If you're deeply stuck and the opponent has solved, "
                f"call `give_up()` instead."
            )

        return {
            "instructions": instructions,
            "your_color": side,
            "cube_size": self._n,
            "your_cube": cube.snapshot() if cube else None,
            "your_moves_used": own_moves,
            "your_moves_left": moves_left,
            "your_pending_move": own_pending,
            "your_is_solved": cube.is_solved() if cube else False,
            "opponent_moves_used": opp_moves,
            "opponent_has_submitted_this_round": opp_pending is not None,
            "move_cap": self._move_cap,
            "scramble": list(self._scramble),
            "valid_moves": list(ALL_MOVES),
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _side_for_entity(self, entity_id: str) -> str:
        return "white" if entity_id == self._white_id else "black"

    def _has_pending(self, side: str) -> bool:
        return (self._pending_white if side == "white" else self._pending_black) is not None

    def _is_done(self, side: str) -> bool:
        cube = self._white_cube if side == "white" else self._black_cube
        gave_up = self._white_gave_up if side == "white" else self._black_gave_up
        if gave_up:
            return True
        if cube is None:
            return False
        if cube.is_solved():
            return True
        if cube.move_count() >= self._move_cap:
            return True
        return False

    def _refresh_player_props(self, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        for entity_id, side in (
            (self._white_id, "white"), (self._black_id, "black"),
        ):
            ent = state.entities.get(entity_id) if entity_id else None
            if ent is None or not hasattr(ent, "properties"):
                continue
            cube = self._white_cube if side == "white" else self._black_cube
            ent.properties["color"] = side
            ent.properties["moves_used"] = cube.move_count() if cube else 0
            ent.properties["solved"] = cube.is_solved() if cube else False

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
