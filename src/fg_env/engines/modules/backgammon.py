"""Backgammon domain module — standard 2-player rules.

Board representation
====================
Points 1..24 indexed by integer. Each point holds {color, count} or is
empty. White moves toward point 1 (their home is points 1–6); Black moves
toward point 24 (home is 19–24). The bar is point 0 for white re-entry
and 25 for black re-entry; convention here: `bar_white` and `bar_black`
counters are tracked separately. Borne-off checkers live in `off_white`
and `off_black`.

Standard starting position (each player has 15 checkers):
    Point  24:  2 white                 Point  1:   2 black
    Point  13:  5 white                 Point 12:   5 black
    Point   8:  3 white                 Point 17:   3 black
    Point   6:  5 white                 Point 19:   5 black

Turn flow
=========
1. The engine auto-rolls 2d6 at the start of each player's turn.
2. Doubles → 4 moves at that pip value; non-doubles → 2 moves.
3. The player issues `move_checker(from, to)` actions one at a time
   until all pip values are consumed OR no legal moves remain.
4. `end_turn` flushes any unused pips and passes to the opponent.

Custom actions
==============
- `move_checker(from, to)`   — move ONE checker. `from` is a point
   index (1–24) or the string `"bar"` if you have checkers on the bar.
   `to` is a point index or `"off"` to bear off.
- `end_turn`                 — relinquish remaining pips (auto-fired
   when the player has no legal moves).

Events emitted
==============
- `backgammon_roll`         {player, d1, d2, pips}
- `backgammon_move`         {player, from, to, hit, board, bar, off}
- `backgammon_no_moves`     {player, dice}
- `backgammon_turn_end`     {player, used_pips}
- `backgammon_win`          {winner, loser}   ← terminal
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WHITE = "white"
BLACK = "black"


def opponent(side: str) -> str:
    return BLACK if side == WHITE else WHITE


def home_range(side: str) -> range:
    """Home board points (inclusive) for a side."""
    return range(1, 7) if side == WHITE else range(19, 25)


def direction(side: str) -> int:
    """Sign of motion in point-index space. White moves down, black up."""
    return -1 if side == WHITE else 1


def _empty_board() -> Dict[int, Dict[str, Any]]:
    return {i: {"color": None, "count": 0} for i in range(1, 25)}


def _starting_position() -> Dict[int, Dict[str, Any]]:
    """Standard starting position. 15 checkers per side."""
    b = _empty_board()
    b[24] = {"color": WHITE, "count": 2}
    b[13] = {"color": WHITE, "count": 5}
    b[8] = {"color": WHITE, "count": 3}
    b[6] = {"color": WHITE, "count": 5}
    b[1] = {"color": BLACK, "count": 2}
    b[12] = {"color": BLACK, "count": 5}
    b[17] = {"color": BLACK, "count": 3}
    b[19] = {"color": BLACK, "count": 5}
    return b


def _board_to_dict(b: Dict[int, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Stringified-key snapshot for events / serialization."""
    return {str(k): dict(v) for k, v in b.items()}


def _checkers_on(b: Dict[int, Dict[str, Any]], side: str) -> int:
    """Count of `side`'s checkers visible on the board (not bar / off)."""
    return sum(p["count"] for p in b.values() if p["color"] == side)


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class BackgammonModule(DomainModule):
    """Full 2-player backgammon — roll, move, hit, bar, bear-off, win."""

    def __init__(self, name: str = "backgammon",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        seed = p.get("seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()

        # Board state
        self._board: Dict[int, Dict[str, Any]] = _starting_position()
        self._bar: Dict[str, int] = {WHITE: 0, BLACK: 0}
        self._off: Dict[str, int] = {WHITE: 0, BLACK: 0}

        # Seat assignment (decided on first tick).
        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        self._initialized = False

        # Turn state
        self._side_to_move: str = WHITE   # white moves first by convention
        self._dice: Tuple[int, int] = (0, 0)
        self._pips_remaining: List[int] = []   # die values still available this turn
        self._turn_log: List[str] = []
        self._terminal: Optional[Dict[str, Any]] = None
        self._auto_roll_on_next_tick: bool = True

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return "Standard backgammon. Roll dice, move 15 checkers off the board first."

    @property
    def custom_actions(self) -> List[str]:
        # `play_turn` is the BATCHED action — the agent submits the full
        # sequence of moves for their dice in ONE LLM call. Cuts the
        # round-trip count in half (or 4x on doubles) which is the
        # difference between a watchable game and a slog.
        # `move_checker` remains for single-move fallback / debugging.
        return ["play_turn", "move_checker", "end_turn"]

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
        for ent, color in ((agents[0], WHITE), (agents[1], BLACK)):
            if hasattr(ent, "properties"):
                ent.properties["color"] = color
                ent.properties["checkers_off"] = 0
                ent.properties["checkers_on_bar"] = 0
        self._initialized = True
        logger.info("Backgammon seated: white=%s, black=%s",
                    agents[0].name, agents[1].name)

    # ------------------------------------------------------------------ #
    # tick + auto-roll
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        if not self._initialized or self._terminal is not None:
            return []
        events: List[Dict[str, Any]] = []
        # Auto-roll at the start of each player's turn.
        if self._auto_roll_on_next_tick and not self._pips_remaining:
            self._auto_roll_on_next_tick = False
            events.extend(self._roll_for_active(state))
        return events

    def _roll_for_active(self, state: Any) -> List[Dict[str, Any]]:
        d1 = self._rng.randint(1, 6)
        d2 = self._rng.randint(1, 6)
        self._dice = (d1, d2)
        # Doubles → use the pip value 4 times.
        self._pips_remaining = [d1] * 4 if d1 == d2 else [d1, d2]
        player_id = self._active_player_id()
        player_name = self._name_of(state, player_id)
        evt = {
            "event_type": "backgammon_roll",
            "type": "backgammon_roll",
            "player": player_id,
            "player_name": player_name,
            "d1": d1, "d2": d2,
            "pips": list(self._pips_remaining),
            "side": self._side_to_move,
            "narrative": (
                f"{player_name} rolls {d1} + {d2}"
                + (" (doubles!)" if d1 == d2 else "")
            ),
        }
        # If the player has no legal moves at all, auto-pass the turn.
        if not self._any_legal_move(self._side_to_move):
            self._log(f"{player_name} has no legal moves with {d1}-{d2}.")
            evt2 = {
                "event_type": "backgammon_no_moves",
                "type": "backgammon_no_moves",
                "player": player_id,
                "player_name": player_name,
                "dice": [d1, d2],
                "narrative": f"{player_name} cannot move with {d1}-{d2} — turn skipped.",
            }
            self._end_turn_internal()
            return [evt, evt2]
        return [evt]

    # ------------------------------------------------------------------ #
    # Action filtering
    # ------------------------------------------------------------------ #

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        if not self._initialized or self._terminal is not None:
            return []
        out = [a for a in valid_actions if a not in self.custom_actions]
        active = self._active_player_id()
        if entity_id != active:
            return out
        # Active player with pips + at least one legal move: prefer the
        # batched `play_turn` (one LLM call covers all pips). Single-move
        # `move_checker` stays available but isn't the recommended path.
        if self._pips_remaining and self._any_legal_move(self._side_to_move):
            out.append("play_turn")
            out.append("move_checker")
        out.append("end_turn")
        return out

    def _active_player_id(self) -> Optional[str]:
        return self._white_id if self._side_to_move == WHITE else self._black_id

    # ------------------------------------------------------------------ #
    # Move validation
    # ------------------------------------------------------------------ #

    def _all_in_home(self, side: str) -> bool:
        """True if every one of `side`'s checkers (board + bar=0) is in
        their home board, prerequisite to bearing off."""
        if self._bar[side] > 0:
            return False
        home = home_range(side)
        for idx, p in self._board.items():
            if p["color"] == side and p["count"] > 0 and idx not in home:
                return False
        return True

    def _is_legal_move(self, side: str, frm: Any, to: Any, pip: int) -> bool:
        """Validate a single proposed move with the given pip value."""
        # 1) Bar entry has highest priority.
        bar_has = self._bar[side] > 0
        if bar_has and frm != "bar":
            return False
        if frm == "bar" and not bar_has:
            return False

        if frm == "bar":
            # Entry point is determined by pip. White enters at 25-pip
            # (i.e. point 25-pip). Black enters at 0+pip = pip.
            entry = 25 - pip if side == WHITE else pip
            if not (1 <= entry <= 24):
                return False
            return self._can_land(side, entry)

        # 2) From must be a valid source point with at least one of our checkers.
        if not isinstance(frm, int) or not (1 <= frm <= 24):
            return False
        src = self._board[frm]
        if src["color"] != side or src["count"] < 1:
            return False

        # 3) Bearing off: `to == "off"`.
        if to == "off":
            if not self._all_in_home(side):
                return False
            home = home_range(side)
            if frm not in home:
                return False
            # The pip must match the distance to "off" exactly OR exceed
            # it iff there are no checkers on higher points (for white)
            # / lower points (for black).
            distance = frm if side == WHITE else (25 - frm)
            if pip == distance:
                return True
            if pip > distance:
                # Allowed only when there's no checker on a higher (farther
                # from home) point in our home board.
                higher_points = (
                    range(frm + 1, 7) if side == WHITE
                    else range(19, frm)
                )
                for pi in higher_points:
                    p = self._board[pi]
                    if p["color"] == side and p["count"] > 0:
                        return False
                return True
            return False

        # 4) Regular point-to-point move.
        if not isinstance(to, int) or not (1 <= to <= 24):
            return False
        # Direction check.
        if (to - frm) * direction(side) <= 0:
            return False
        # Pip distance must match.
        if abs(to - frm) != pip:
            return False
        return self._can_land(side, to)

    def _can_land(self, side: str, point_idx: int) -> bool:
        """Can `side` land on `point_idx`?"""
        p = self._board.get(point_idx)
        if p is None:
            return False
        if p["color"] is None or p["color"] == side:
            return True
        # Opponent's point — landing legal only if it's a blot (1 checker).
        return p["count"] == 1

    def _any_legal_move(self, side: str) -> bool:
        """Is at least one move legal with any remaining pip?"""
        pips = set(self._pips_remaining)
        if not pips:
            return False
        # Bar-first rule.
        if self._bar[side] > 0:
            for pip in pips:
                if self._is_legal_move(side, "bar", None, pip):
                    return True
            return False
        # Try each pip from each owned point.
        for frm, p in self._board.items():
            if p["color"] != side or p["count"] < 1:
                continue
            for pip in pips:
                # Try landing at frm + dir * pip
                target = frm + direction(side) * pip
                if 1 <= target <= 24 and self._is_legal_move(side, frm, target, pip):
                    return True
                # Try bear-off
                if self._is_legal_move(side, frm, "off", pip):
                    return True
        return False

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                         state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        actor_id = getattr(actor, "id", None)
        if actor_id != self._active_player_id():
            return "Not your turn."
        return None

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        if not success or action_name not in self.custom_actions:
            return []
        raw = getattr(result, "details", {}) if hasattr(result, "details") else {}
        params = raw.get("_action_params") or {}
        details = {**raw, **params}
        if action_name == "play_turn":
            events = self._handle_play_turn(actor_id, details, state)
        elif action_name == "move_checker":
            events = self._handle_move(actor_id, details, state)
        elif action_name == "end_turn":
            events = self._handle_end_turn(actor_id, state)
        else:
            return []
        invalid = next((e for e in events if e.get("type") == "backgammon_invalid"), None)
        if invalid:
            # The kernel publishes this same result after the module hook.
            # An illegal board move is a failed action, even when the generic
            # deterministic resolver initially accepted its envelope.
            result.success = False
            result.narrative = invalid.get("narrative", "Invalid Backgammon move")
            result.details["domain_error"] = invalid.get("reason")
            if action_name == "play_turn" and not any(e.get("type") == "backgammon_turn_end" for e in events):
                events.extend(self._end_turn_internal(state, actor_id))
        return events

    def _handle_play_turn(self, actor_id: str, details: Dict[str, Any],
                          state: Any) -> List[Dict[str, Any]]:
        """Execute a SEQUENCE of moves in one shot. Reject the entire
        batch if any step is illegal — the agent retries via the standard
        validation loop and resubmits a corrected plan."""
        raw_moves = details.get("moves")
        # Accept stringified JSON too (LLMs sometimes do that).
        if isinstance(raw_moves, str):
            import json as _json
            try:
                raw_moves = _json.loads(raw_moves)
            except Exception:
                return [self._invalid(actor_id, "invalid_moves",
                    "`moves` must be a list of {from, to} objects (or a "
                    "JSON-string of one). Could not parse.")]
        if not isinstance(raw_moves, list) or not raw_moves:
            return [self._invalid(actor_id, "no_moves",
                "`moves` must be a non-empty list. Each item is "
                "{from: <1-24 or 'bar'>, to: <1-24 or 'off'>}. Pull "
                "candidates from `legal_moves` in your perception.")]

        # Snapshot state for atomic rollback if any step is invalid.
        import copy
        snap_board = copy.deepcopy(self._board)
        snap_bar = dict(self._bar)
        snap_off = dict(self._off)
        snap_pips = list(self._pips_remaining)
        snap_log_len = len(self._turn_log)

        all_events: List[Dict[str, Any]] = []
        for i, mv in enumerate(raw_moves):
            if not isinstance(mv, dict):
                self._rollback(snap_board, snap_bar, snap_off, snap_pips, snap_log_len, state)
                return [self._invalid(actor_id, "bad_move_shape",
                    f"Move #{i+1} is not a dict. Each move must be "
                    f"{{from, to}}. Got: {mv!r}")]
            try:
                evts = self._handle_move(actor_id, mv, state)
            except Exception:
                self._rollback(snap_board, snap_bar, snap_off, snap_pips, snap_log_len, state)
                return [self._invalid(actor_id, "batch_failed_step",
                    f"Move #{i+1} could not be applied. Whole batch rolled back. Turn forfeited.")] + self._end_turn_internal(state, actor_id)
            # _handle_move returns either [invalid] or [move (+ optional turn-end / win)].
            invalid = next((e for e in evts if e.get("type") == "backgammon_invalid"), None)
            if invalid:
                self._rollback(snap_board, snap_bar, snap_off, snap_pips, snap_log_len, state)
                msg = invalid.get("narrative", "")
                # Forfeit the turn after a failed batch so the engine
                # doesn't stall on dead rounds. The kernel doesn't loop
                # within a round to give the same entity another shot;
                # the agent's 3-attempt validator (and the perception's
                # `legal_moves`) already gave the LLM its chances pre-
                # submission. End the turn here so dice re-roll and the
                # opponent can move.
                err_evt = self._invalid(actor_id, "batch_failed_step",
                    f"Move #{i+1} ({mv.get('from')}→{mv.get('to')}) is "
                    f"invalid: {msg} Whole batch rolled back. Turn forfeited.")
                end_evts = self._end_turn_internal(state, actor_id)
                return [err_evt] + end_evts
            all_events.extend(evts)
            # _handle_move may have auto-ended the turn (no pips left or
            # no legal continuation). Stop processing further submitted
            # moves in that case — extras are silently ignored.
            if any(e.get("type") in ("backgammon_turn_end", "backgammon_win") for e in evts):
                break
        return all_events

    def _rollback(self, board, bar, off, pips, log_len, state):
        import copy
        self._board = copy.deepcopy(board)
        self._bar = dict(bar)
        self._off = dict(off)
        self._pips_remaining = list(pips)
        self._turn_log = self._turn_log[:log_len]
        self._sync_entity_props(state)

    # ------------------------------------------------------------------ #
    # move_checker
    # ------------------------------------------------------------------ #

    def _parse_point(self, raw: Any) -> Any:
        """Coerce a from/to param into 'bar' | 'off' | int(1..24) | None."""
        if raw is None:
            return None
        if isinstance(raw, int):
            return raw if 1 <= raw <= 24 else None
        s = str(raw).strip().lower()
        if s in ("bar", "b"):
            return "bar"
        if s in ("off", "home", "bear"):
            return "off"
        # Numeric string?
        try:
            n = int(s)
            return n if 1 <= n <= 24 else None
        except (ValueError, TypeError):
            return None

    def _handle_move(self, actor_id: str, details: Dict[str, Any],
                     state: Any) -> List[Dict[str, Any]]:
        side = WHITE if actor_id == self._white_id else BLACK
        frm = self._parse_point(details.get("from"))
        to = self._parse_point(details.get("to"))
        if frm is None or to is None or frm == "off" or to == "bar" or (frm == "bar" and to == "off"):
            return [self._invalid(actor_id, "invalid_points",
                f"move_checker requires `from` (1-24 or 'bar') and `to` "
                f"(1-24 or 'off'). Got from={details.get('from')!r}, "
                f"to={details.get('to')!r}.")]

        # Only moves that use the maximum possible number of dice are
        # admissible. If only one die can be used, it must be the larger.
        choices = [move for move in self._enumerate_legal_moves(side)
                   if move["from"] == frm and move["to"] == to]
        if not choices:
            return [self._invalid(actor_id, "illegal_move",
                f"Move {frm}→{to} cannot use the available dice legally. "
                "Use as many dice as possible; when only one can be played, use the larger die.")]
        pip = choices[0]["pip"]

        # Execute the move.
        hit = False
        opp = opponent(side)
        events: List[Dict[str, Any]] = []
        if frm == "bar":
            self._bar[side] -= 1
            dest = (25 - pip) if side == WHITE else pip
            dest_point = self._board[dest]
            if dest_point["color"] == opp and dest_point["count"] == 1:
                # Hit!
                self._bar[opp] += 1
                dest_point["color"] = None
                dest_point["count"] = 0
                hit = True
            dest_point["color"] = side
            dest_point["count"] += 1
            to = dest
        else:
            src = self._board[frm]
            src["count"] -= 1
            if src["count"] == 0:
                src["color"] = None
            if to == "off":
                self._off[side] += 1
            else:
                dest_point = self._board[to]
                if dest_point["color"] == opp and dest_point["count"] == 1:
                    self._bar[opp] += 1
                    dest_point["color"] = None
                    dest_point["count"] = 0
                    hit = True
                dest_point["color"] = side
                dest_point["count"] += 1

        # Consume the pip.
        self._pips_remaining.remove(pip)
        actor_name = self._name_of(state, actor_id)
        san = self._move_san(frm, to, hit, side)
        self._log(f"{actor_name}: {san} (pip {pip})")
        self._sync_entity_props(state)
        events.append({
            "event_type": "backgammon_move",
            "type": "backgammon_move",
            "player": actor_id,
            "player_name": actor_name,
            "from": frm if frm == "bar" else int(frm),
            "to": to if to == "off" else int(to),
            "pip": pip,
            "hit": hit,
            "side": side,
            "san": san,
            "board": _board_to_dict(self._board),
            "bar": dict(self._bar),
            "off": dict(self._off),
            "pips_remaining": list(self._pips_remaining),
            "narrative": f"{actor_name} plays {san}." + (" (HIT!)" if hit else ""),
        })

        # Win check.
        if self._off[side] >= 15:
            self._terminal = {"winner": side, "winner_id": actor_id}
            loser_id = self._white_id if side == BLACK else self._black_id
            events.append({
                "event_type": "backgammon_win",
                "type": "backgammon_win",
                "winner": actor_id,
                "winner_name": actor_name,
                "loser": loser_id,
                "side": side,
                "narrative": f"{actor_name} wins — all 15 checkers borne off.",
            })
            return events

        # Out of pips OR no legal continuation → end the turn automatically.
        if not self._pips_remaining or not self._any_legal_move(side):
            events.extend(self._end_turn_internal(state, actor_id))
        return events

    def _handle_end_turn(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        return self._end_turn_internal(state, actor_id)

    def _end_turn_internal(self, state: Any = None,
                           actor_id: Optional[str] = None) -> List[Dict[str, Any]]:
        unused = list(self._pips_remaining)
        side_ending = self._side_to_move
        player_id = actor_id or self._active_player_id()
        player_name = self._name_of(state, player_id) if state else (player_id or "?")
        self._pips_remaining = []
        self._side_to_move = opponent(self._side_to_move)
        self._auto_roll_on_next_tick = True
        return [{
            "event_type": "backgammon_turn_end",
            "type": "backgammon_turn_end",
            "player": player_id,
            "player_name": player_name,
            "side": side_ending,
            "used_pips": [] if unused == list(self._dice) else None,
            "unused_pips": unused,
            "narrative": (
                f"{player_name} ends the turn."
                + (f" Unused pips: {unused}" if unused else "")
            ),
        }]

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def _render_board(self, perspective: str) -> str:
        """Render the board as a multi-line ASCII string.

        Standard backgammon layout: 24 points in two halves with the BAR in
        the middle. Each point shows a vertical stack of checkers (one symbol
        per checker) with a count. Rendered from `perspective`'s point of
        view: that player's HOME quadrant sits in the BOTTOM-RIGHT, and their
        checkers travel toward it, so direction of movement is unambiguous.

        Symbols: 'O' = white checkers, 'X' = black checkers.
        """
        you = perspective
        opp = opponent(you)
        # Symbol for a given side, from this perspective.
        sym = {WHITE: "O", BLACK: "X"}

        def col(point: int, row: int) -> str:
            """Cell text (4 chars) for `point` at stack depth `row`."""
            p = self._board.get(point, {"color": None, "count": 0})
            cnt = p["count"]
            if cnt == 0:
                return " .  "
            s = sym[p["color"]]
            if row < 5:
                return f" {s}  " if row < cnt else "    "
            # Row 5 is the overflow row: show a count if the stack is tall.
            return f"({cnt:>2})" if cnt > 5 else "    "

        # From `you`'s perspective: bottom-right quadrant is YOUR home.
        # White home = points 1-6, Black home = points 19-24.
        # White travels 24->1, Black travels 1->24.
        if you == WHITE:
            top = [13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]
            bot = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        else:
            top = [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
            bot = [13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24]

        def hdr(points: List[int]) -> str:
            cells = "".join(f"{p:^4}" for p in points)
            return " " + cells[:24] + "  BAR  " + cells[24:]

        border = "+" + "-" * 24 + "+" + "-" * 7 + "+" + "-" * 24 + "+"

        def bar_cell(side_sym: str, count: int, show: bool) -> str:
            return f"{side_sym}x{count}".center(7) if (show and count) else " " * 7

        lines: List[str] = []
        lines.append("     OPPONENT'S OUTER  (entry / far side)")
        lines.append(hdr(top))
        lines.append(border)
        # Top points stack downward (row 0 nearest the top edge).
        for row in range(6):
            cells = [col(p, row) for p in top]
            mid = bar_cell(sym[opp], self._bar[opp], row == 0)
            lines.append("|" + "".join(cells[:6]) + "|" + mid + "|"
                         + "".join(cells[6:]) + "|")
        lines.append("|" + " " * 24 + "| BAR ->|" + " " * 24 + "|")
        # Bottom points stack upward (row 0 nearest the bottom edge).
        for row in reversed(range(6)):
            cells = [col(p, row) for p in bot]
            mid = bar_cell(sym[you], self._bar[you], row == 0)
            lines.append("|" + "".join(cells[:6]) + "|" + mid + "|"
                         + "".join(cells[6:]) + "|")
        lines.append(border)
        lines.append(hdr(bot))
        lines.append("    YOUR HOME BOARD  (bear off from here)")
        lines.append("")
        lines.append(
            f"Borne off:  YOU ({sym[you]}) = {self._off[you]}/15"
            f"   |   OPPONENT ({sym[opp]}) = {self._off[opp]}/15"
        )
        lines.append(
            f"On the BAR: YOU ({sym[you]}) = {self._bar[you]}"
            f"   |   OPPONENT ({sym[opp]}) = {self._bar[opp]}"
        )
        return "\n".join(lines)

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if not self._initialized:
            return {}
        if entity_id not in (self._white_id, self._black_id):
            return {
                "role": "spectator",
                "board": _board_to_dict(self._board),
                "bar": dict(self._bar),
                "off": dict(self._off),
            }
        side = WHITE if entity_id == self._white_id else BLACK
        is_your_turn = (entity_id == self._active_player_id()) and not self._terminal
        legal = self._enumerate_legal_moves(side) if is_your_turn else []
        # Format legal moves as human-readable "from-to" strings.
        legal_str = sorted({f"{m['from']}-{m['to']}" for m in legal})
        if is_your_turn and self._bar[side] > 0:
            instructions = (
                f"IT IS YOUR TURN. You are {side.upper()}. You have "
                f"{self._bar[side]} checker(s) on the BAR — you MUST re-enter "
                f"them BEFORE any other move. Roll: {list(self._dice)}, "
                f"pips remaining: {self._pips_remaining}. "
                f"Submit your ENTIRE turn in one call: "
                f"`play_turn(moves=[{{'from':'bar','to':<point>}}, ...])` — "
                f"the first entry must be from `legal_moves` below. The engine "
                f"plays them in order and stops when pips run out."
            )
        elif is_your_turn:
            instructions = (
                f"IT IS YOUR TURN. You are {side.upper()}. Roll: "
                f"{list(self._dice)}, pips remaining: {self._pips_remaining}. "
                f"Submit your ENTIRE turn in ONE call: "
                f"`play_turn(moves=[{{'from':<point>,'to':<point>}}, ...])` "
                f"with one entry per pip ({len(self._pips_remaining)} moves "
                f"expected). The first entry must appear in `legal_moves` below "
                f"— illegal batches are rolled back. Use to='off' to bear "
                f"off (legal only when ALL your checkers are in your home: "
                f"{'1-6' if side == WHITE else '19-24'}). After your first "
                f"move, later moves' legality depends on the resulting "
                f"board; plan accordingly."
            )
        elif self._terminal:
            instructions = "Game over."
        else:
            instructions = (
                f"Opponent ({opponent(side).upper()}) is on move. Wait."
            )
        sym = "O" if side == WHITE else "X"
        opp_sym = "X" if side == WHITE else "O"
        direction_text = (
            "You move from HIGH-numbered points toward LOW-numbered points "
            "(24 -> 1); your home board is points 1-6. Bear off once all your "
            "checkers are in points 1-6."
            if side == WHITE else
            "You move from LOW-numbered points toward HIGH-numbered points "
            "(1 -> 24); your home board is points 19-24. Bear off once all "
            "your checkers are in points 19-24."
        )
        board_help = (
            "Read `board_ascii` below: it is a real 2D backgammon board drawn "
            "from YOUR perspective. Your HOME quadrant is the bottom-right; "
            f"your checkers ('{sym}') travel toward it. Each point shows a "
            "stack of checker symbols; tall stacks show a number. The BAR "
            "column is in the middle. Choose every move from `legal_moves` — "
            "do not invent point numbers."
        )
        instructions = f"{instructions} {direction_text} {board_help}"
        return {
            "instructions": instructions,
            "board_ascii": self._render_board(side),
            "legend": {
                "O": "white checker",
                "X": "black checker",
                ".": "empty point",
                f"you ({sym})": f"your checkers — you are {side.upper()}",
                f"opponent ({opp_sym})": f"opponent's checkers ({opponent(side).upper()})",
                "BAR": "hit checkers wait here and must re-enter before any "
                       "other move",
                "1-24": "point numbers; numbers print above and below the board",
                "stack number": "shown on a point when more than 5 checkers "
                                 "are stacked there",
                "Borne off": "checkers removed from the board; first to 15 wins",
            },
            "your_color": side,
            "is_your_turn": is_your_turn,
            "board": _board_to_dict(self._board),
            "bar": dict(self._bar),
            "off": dict(self._off),
            "dice": list(self._dice),
            "pips_remaining": list(self._pips_remaining),
            "your_home": (
                "points 1-6 (white moves toward 1)" if side == WHITE
                else "points 19-24 (black moves toward 24)"
            ),
            "legal_moves": legal_str,
            "legal_moves_detailed": legal,
            "recent_log": self._turn_log[-20:],
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    def _enumerate_legal_moves(self, side: str) -> List[Dict[str, Any]]:
        """Legal first steps of a complete turn, including mandatory dice use."""
        import copy
        from functools import lru_cache

        def key(game):
            return (tuple((game._board[i]["color"], game._board[i]["count"]) for i in range(1, 25)),
                    game._bar[side], tuple(sorted(game._pips_remaining)))

        def probe(position):
            board, bar, pips = position
            game = copy.copy(self)
            game._board = {i + 1: {"color": color, "count": count} for i, (color, count) in enumerate(board)}
            game._bar = {**self._bar, side: bar}
            game._pips_remaining = list(pips)
            return game

        def after(game, move):
            game = probe(key(game))
            frm, to = move["from"], move["to"]
            if frm == "bar":
                game._bar[side] -= 1
            else:
                game._board[frm]["count"] -= 1
                if not game._board[frm]["count"]:
                    game._board[frm]["color"] = None
            if to != "off":
                point = game._board[to]
                point["count"] = point["count"] + 1 if point["color"] == side else 1
                point["color"] = side
            game._pips_remaining.remove(move["pip"])
            return key(game)

        @lru_cache(maxsize=None)
        def remaining(position):
            game = probe(position)
            return max((1 + remaining(after(game, move)) for move in game._raw_legal_moves(side)), default=0)

        candidates = self._raw_legal_moves(side)
        scored = [(move, 1 + remaining(after(self, move))) for move in candidates]
        maximum = max((score for _, score in scored), default=0)
        legal = [move for move, score in scored if score == maximum]
        if maximum == 1 and legal:
            largest = max(move["pip"] for move in legal)
            legal = [move for move in legal if move["pip"] == largest]
        return legal

    def _raw_legal_moves(self, side: str) -> List[Dict[str, Any]]:
        moves: List[Dict[str, Any]] = []
        pips = set(self._pips_remaining)
        if self._bar[side] > 0:
            for pip in pips:
                entry = 25 - pip if side == WHITE else pip
                if 1 <= entry <= 24 and self._is_legal_move(side, "bar", entry, pip):
                    moves.append({"from": "bar", "to": entry, "pip": pip,
                                  "hit": self._board[entry]["color"] == opponent(side)
                                         and self._board[entry]["count"] == 1})
            return moves
        for frm, p in self._board.items():
            if p["color"] != side or p["count"] < 1:
                continue
            for pip in pips:
                target = frm + direction(side) * pip
                if 1 <= target <= 24 and self._is_legal_move(side, frm, target, pip):
                    moves.append({
                        "from": frm, "to": target, "pip": pip,
                        "hit": self._board[target]["color"] == opponent(side)
                               and self._board[target]["count"] == 1,
                    })
                if self._is_legal_move(side, frm, "off", pip):
                    moves.append({"from": frm, "to": "off", "pip": pip, "hit": False})
        return moves

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _invalid(self, actor_id: str, reason: str, msg: str) -> Dict[str, Any]:
        return {
            "type": "backgammon_invalid",
            "player": actor_id,
            "reason": reason,
            "narrative": msg,
        }

    def _move_san(self, frm: Any, to: Any, hit: bool, side: str) -> str:
        f = "bar" if frm == "bar" else str(frm)
        t = "off" if to == "off" else str(to)
        s = f"{f}/{t}"
        return s + "*" if hit else s

    def _name_of(self, state: Any, entity_id: Optional[str]) -> str:
        if not entity_id:
            return "?"
        if hasattr(state, "entities"):
            ent = state.entities.get(entity_id)
            if ent is not None:
                return getattr(ent, "name", entity_id)
        return entity_id

    def _log(self, msg: str) -> None:
        self._turn_log.append(msg)
        if len(self._turn_log) > 200:
            self._turn_log = self._turn_log[-200:]

    def _sync_entity_props(self, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        for ent_id, side in (
            (self._white_id, WHITE), (self._black_id, BLACK),
        ):
            if not ent_id:
                continue
            ent = state.entities.get(ent_id)
            if ent and hasattr(ent, "properties"):
                ent.properties["checkers_off"] = self._off[side]
                ent.properties["checkers_on_bar"] = self._bar[side]

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "board": _board_to_dict(self._board),
            "bar": dict(self._bar),
            "off": dict(self._off),
            "white_id": self._white_id,
            "black_id": self._black_id,
            "side_to_move": self._side_to_move,
            "dice": list(self._dice),
            "pips_remaining": list(self._pips_remaining),
            "turn_log": list(self._turn_log),
            "terminal": dict(self._terminal) if self._terminal else None,
            "auto_roll_on_next_tick": self._auto_roll_on_next_tick,
            "initialized": self._initialized,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "BackgammonModule":
        mod = cls(name=data.get("name", "backgammon"), params=data.get("params", {}))
        s = data.get("state") or {}
        if s.get("board"):
            mod._board = {int(k): dict(v) for k, v in s["board"].items()}
        if s.get("bar"):
            mod._bar = {k: int(v) for k, v in s["bar"].items()}
        if s.get("off"):
            mod._off = {k: int(v) for k, v in s["off"].items()}
        mod._white_id = s.get("white_id")
        mod._black_id = s.get("black_id")
        mod._side_to_move = s.get("side_to_move", WHITE)
        d = s.get("dice") or [0, 0]
        mod._dice = (int(d[0]), int(d[1]))
        mod._pips_remaining = list(s.get("pips_remaining") or [])
        mod._turn_log = list(s.get("turn_log") or [])
        mod._terminal = dict(s["terminal"]) if s.get("terminal") else None
        mod._auto_roll_on_next_tick = bool(s.get("auto_roll_on_next_tick", True))
        mod._initialized = bool(s.get("initialized", False))
        return mod
