"""Dominoes domain module — classic 2-player draw dominoes (double-six set).

Drives a real game of dominoes on top of the generic action engine:
  - Standard 28-tile double-six set, shuffled and dealt into two hands.
  - One open line; tiles attach at either end matching pip values.
  - Draw rules: a player who cannot play draws from the boneyard until
    they can; if the boneyard is empty they pass.
  - Win by emptying your hand; if the game is blocked, the lighter hand
    (lowest pip total) wins.

Custom actions:
  - `play_tile`: params `tile` (e.g. "3-5") and `end` ("left"|"right").
  - `draw_tile`: draw one tile from the boneyard.
  - `pass`: pass the turn (only when blocked and the boneyard is empty).

Events emitted (consumed by DominoViz + UI):
  - `domino_setup` { ...snapshot }
  - `domino_play`  { player, tile, end, ...snapshot }
  - `domino_drew`  { player, ...snapshot }
  - `domino_pass`  { player, ...snapshot }
  - `domino_win`   { winner, loser, reason, ...snapshot }   (terminal)
  - `domino_draw`  { ...snapshot }                          (terminal, blocked tie)

Hands are concealed — a player's perception never reveals the opponent's
tiles, only how many they hold.
"""
from __future__ import annotations

import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)

PIP_MAX = 6  # double-six set


def tile_str(tile: Tuple[int, int]) -> str:
    return f"{tile[0]}-{tile[1]}"


def parse_tile(raw: Any) -> Optional[Tuple[int, int]]:
    """Parse '3-5' / '3,5' / '3 5' into a canonical (low, high) tuple."""
    if raw is None:
        return None
    m = re.match(r"^\s*(\d)\s*[-,\s]\s*(\d)\s*$", str(raw))
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if 0 <= a <= PIP_MAX and 0 <= b <= PIP_MAX:
        return (min(a, b), max(a, b))
    return None


def full_set() -> List[Tuple[int, int]]:
    return [(a, b) for a in range(PIP_MAX + 1) for b in range(a, PIP_MAX + 1)]


class DominoesModule(DomainModule):
    """Classic 2-player draw dominoes with the double-six set."""

    def __init__(self, name: str = "dominoes", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._hand_size: int = max(2, min(12, int(p.get("hand_size", 7) or 7)))
        self._players: List[str] = []
        self._hands: Dict[str, List[Tuple[int, int]]] = {}
        self._line: List[List[int]] = []   # oriented chain: line[i][1] == line[i+1][0]
        self._boneyard: List[Tuple[int, int]] = []
        self._turn: Optional[str] = None
        self._consecutive_passes: int = 0
        self._result: Optional[Dict[str, Any]] = None
        self._terminal: bool = False
        self._initialized: bool = False
        self._setup_emitted: bool = False
        self._log: List[str] = []
        self._rng = random.Random()

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return "Classic 2-player draw dominoes with the double-six set."

    @property
    def custom_actions(self) -> List[str]:
        return ["play_tile", "draw_tile", "pass"]

    @property
    def required_properties(self) -> List[str]:
        return ["tiles_in_hand"]

    # ------------------------------------------------------------------ #
    # Deal
    # ------------------------------------------------------------------ #

    def _seed_from_state(self, state: Any) -> None:
        if self._initialized:
            return
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        if len(agents) < 2:
            return
        self._players = [agents[0].id, agents[1].id]
        deck = full_set()
        self._rng.shuffle(deck)
        hand_size = min(self._hand_size, len(deck) // 2)
        for i, ent in enumerate(agents[:2]):
            hand = deck[i * hand_size:(i + 1) * hand_size]
            self._hands[ent.id] = list(hand)
            if hasattr(ent, "properties"):
                ent.properties["tiles_in_hand"] = len(hand)
        self._boneyard = deck[2 * hand_size:]
        self._turn = self._players[0]
        self._initialized = True
        logger.info("Dominoes dealt: %d tiles each, %d in boneyard",
                    hand_size, len(self._boneyard))

    # ------------------------------------------------------------------ #
    # tick / floor control
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        if not self._initialized or self._setup_emitted:
            return []
        self._setup_emitted = True
        return [{
            "type": "domino_setup",
            **self._snapshot(state),
            "narrative": "Tiles dealt. The line is open — play begins.",
        }]

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        out = [a for a in valid_actions if a not in self.custom_actions]
        if self._terminal or entity_id not in self._players:
            return out if entity_id not in self._players else []
        if entity_id != self._turn:
            return []
        if self._playable_moves(entity_id):
            out.append("play_tile")
        elif self._boneyard:
            out.append("draw_tile")
        else:
            out.append("pass")
        return out

    def _ends(self) -> Tuple[Optional[int], Optional[int]]:
        if not self._line:
            return (None, None)
        return (self._line[0][0], self._line[-1][1])

    def _playable_moves(self, player_id: str) -> List[Tuple[str, str]]:
        """Legal (tile, end) moves for this player given the current line."""
        hand = self._hands.get(player_id, [])
        left, right = self._ends()
        if not self._line:
            # Opening move — any tile, end is irrelevant.
            return [(tile_str(t), "right") for t in hand]
        moves: List[Tuple[str, str]] = []
        for t in hand:
            if left in t:
                moves.append((tile_str(t), "left"))
            if right in t:
                moves.append((tile_str(t), "right"))
        return moves

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                        state: Any) -> Optional[str]:
        if action_name not in self.custom_actions:
            return None
        if self._terminal:
            return "The game is over"
        if getattr(actor, "id", None) != self._turn:
            return "Not your turn"
        return None

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        if not success or action_name not in self.custom_actions or self._terminal:
            return []
        if actor_id != self._turn or actor_id not in self._players:
            return []
        raw = getattr(result, "details", {}) if hasattr(result, "details") else {}
        params = raw.get("_action_params") or {}
        if action_name == "play_tile":
            return self._handle_play(actor_id, params, raw, state)
        if action_name == "draw_tile":
            return self._handle_draw(actor_id, state)
        if action_name == "pass":
            return self._handle_pass(actor_id, state)
        return []

    def _handle_play(self, actor_id: str, params: Dict[str, Any],
                     raw: Dict[str, Any], state: Any) -> List[Dict[str, Any]]:
        moves = self._playable_moves(actor_id)
        if not moves:
            return []
        tile = parse_tile(params.get("tile", raw.get("tile")))
        end = str(params.get("end", raw.get("end", "right"))).lower().strip()
        end = "left" if end == "left" else "right"
        # Auto-redirect an invalid / illegal choice to the first legal move.
        requested = (tile_str(tile) if tile else "", end)
        if tile is None or requested not in moves:
            tile = parse_tile(moves[0][0])
            end = moves[0][1]
        assert tile is not None

        self._place(tile, end)
        self._hands[actor_id].remove(tile)
        self._consecutive_passes = 0
        self._sync_props(state)
        name = self._name_of(state, actor_id)
        narrative = f"{name} plays {tile_str(tile)} on the {end}."
        self._log.append(narrative)
        events: List[Dict[str, Any]] = [{
            "type": "domino_play",
            "player": actor_id, "tile": tile_str(tile), "end": end,
            **self._snapshot(state),
            "narrative": narrative,
        }]

        if not self._hands[actor_id]:
            opponent = self._opponent(actor_id)
            self._result = {"winner": actor_id, "loser": opponent,
                            "reason": "emptied their hand"}
            self._terminal = True
            events.append({
                "event_type": "domino_win", "type": "domino_win",
                "winner": actor_id, "loser": opponent,
                "reason": "emptied their hand",
                **self._snapshot(state),
                "narrative": f"{name} plays their last tile and wins the game.",
            })
            return events

        self._turn = self._opponent(actor_id)
        return events

    def _handle_draw(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        if not self._boneyard:
            return []
        tile = self._boneyard.pop()
        self._hands[actor_id].append(tile)
        self._consecutive_passes = 0
        self._sync_props(state)
        name = self._name_of(state, actor_id)
        narrative = f"{name} draws a tile from the boneyard."
        self._log.append(narrative)
        # The turn stays with the drawer — they may now be able to play.
        return [{
            "type": "domino_drew",
            "player": actor_id,
            **self._snapshot(state),
            "narrative": narrative,
        }]

    def _handle_pass(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        self._consecutive_passes += 1
        name = self._name_of(state, actor_id)
        narrative = f"{name} cannot play and passes."
        self._log.append(narrative)
        events: List[Dict[str, Any]] = [{
            "type": "domino_pass",
            "player": actor_id,
            **self._snapshot(state),
            "narrative": narrative,
        }]
        if self._consecutive_passes >= 2:
            events.append(self._finish_blocked(state))
        else:
            self._turn = self._opponent(actor_id)
        return events

    def _place(self, tile: Tuple[int, int], end: str) -> None:
        a, b = tile
        if not self._line:
            self._line = [[a, b]]
            return
        if end == "left":
            left_val = self._line[0][0]
            other = a if b == left_val else b
            self._line.insert(0, [other, left_val])
        else:
            right_val = self._line[-1][1]
            other = b if a == right_val else a
            self._line.append([right_val, other])

    def _finish_blocked(self, state: Any) -> Dict[str, Any]:
        pips = {pid: self._pip_count(pid) for pid in self._players}
        a, b = self._players
        if pips[a] == pips[b]:
            self._result = {"winner": None, "loser": None, "reason": "blocked — tied on pips"}
            self._terminal = True
            narrative = (f"The game is blocked. Both hands total {pips[a]} pips "
                         f"— it is a draw.")
            self._log.append(narrative)
            return {
                "event_type": "domino_draw", "type": "domino_draw",
                **self._snapshot(state),
                "narrative": narrative,
            }
        winner = a if pips[a] < pips[b] else b
        loser = b if winner == a else a
        self._result = {"winner": winner, "loser": loser,
                        "reason": "blocked — lighter hand"}
        self._terminal = True
        narrative = (
            f"The game is blocked. {self._name_of(state, winner)} wins with the "
            f"lighter hand ({pips[winner]} pips vs {pips[loser]})."
        )
        self._log.append(narrative)
        return {
            "event_type": "domino_win", "type": "domino_win",
            "winner": winner, "loser": loser, "reason": "blocked — lighter hand",
            **self._snapshot(state),
            "narrative": narrative,
        }

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        # Fairness: a player only ever sees their OWN hand.
        if entity_id not in self._players:
            return {"role": "spectator"}
        opponent = self._opponent(entity_id)
        hand = self._hands.get(entity_id, [])
        is_your_turn = (entity_id == self._turn) and not self._terminal
        moves = self._playable_moves(entity_id) if is_your_turn else []
        left, right = self._ends()

        if not is_your_turn:
            instructions = "It is your opponent's turn. Wait for your move."
        elif moves:
            instructions = (
                "IT IS YOUR TURN. Play a tile: call `play_tile` with `tile` "
                "(one from `playable_moves` below) and `end` ('left' or "
                "'right'). A tile can attach to an end only if it shares "
                "that end's pip value. Strategy: shed your heaviest tiles, "
                "keep tiles for suits you still hold, and note which pip "
                "values your opponent has drawn or passed on."
            )
        elif self._boneyard:
            instructions = (
                "You have no playable tile. Call `draw_tile` to draw from "
                "the boneyard — you will be able to act again after drawing."
            )
        else:
            instructions = (
                "You have no playable tile and the boneyard is empty. "
                "Call `pass`."
            )

        return {
            "instructions": instructions,
            "is_your_turn": is_your_turn,
            "your_hand": [tile_str(t) for t in hand],
            "your_hand_pip_count": self._pip_count(entity_id),
            "line": self._render_line(),
            "left_end": left,
            "right_end": right,
            "tiles_on_line": len(self._line),
            "playable_moves": [{"tile": t, "end": e} for t, e in moves],
            "boneyard_count": len(self._boneyard),
            "opponent_tiles_remaining": len(self._hands.get(opponent or "", [])),
            "recent_log": list(self._log[-12:]),
            "terminal": dict(self._result) if self._result else None,
        }

    def _render_line(self) -> str:
        if not self._line:
            return "(the line is empty — you make the opening play; any tile is allowed)"
        chain = "".join(f"[{a}|{b}]" for a, b in self._line)
        left, right = self._ends()
        return f"left end = {left}   {chain}   right end = {right}"

    # ------------------------------------------------------------------ #
    # Snapshot for the visualization
    # ------------------------------------------------------------------ #

    def _snapshot(self, state: Any) -> Dict[str, Any]:
        left, right = self._ends()
        players = [
            {
                "id": pid,
                "name": self._name_of(state, pid),
                "hand": [tile_str(t) for t in self._hands.get(pid, [])],
                "hand_count": len(self._hands.get(pid, [])),
                "pip_count": self._pip_count(pid),
            }
            for pid in self._players
        ]
        snap: Dict[str, Any] = {
            "line": [list(t) for t in self._line],
            "left_end": left,
            "right_end": right,
            "boneyard_count": len(self._boneyard),
            "players": players,
            "turn": self._turn if not self._terminal else None,
        }
        if self._result is not None:
            snap["winner"] = self._result.get("winner")
            snap["result_reason"] = self._result.get("reason")
        else:
            snap["winner"] = None
            snap["result_reason"] = None
        return snap

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _opponent(self, pid: Optional[str]) -> Optional[str]:
        for other in self._players:
            if other != pid:
                return other
        return None

    def _pip_count(self, pid: str) -> int:
        return sum(a + b for a, b in self._hands.get(pid, []))

    def _name_of(self, state: Any, pid: Optional[str]) -> str:
        if not pid:
            return "?"
        if hasattr(state, "entities"):
            ent = state.entities.get(pid)
            if ent is not None:
                return getattr(ent, "name", pid)
        return pid

    def _sync_props(self, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        for pid in self._players:
            ent = state.entities.get(pid)
            if ent is not None and hasattr(ent, "properties"):
                ent.properties["tiles_in_hand"] = len(self._hands.get(pid, []))

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "hand_size": self._hand_size,
            "players": list(self._players),
            "hands": {pid: [list(t) for t in h] for pid, h in self._hands.items()},
            "line": [list(t) for t in self._line],
            "boneyard": [list(t) for t in self._boneyard],
            "turn": self._turn,
            "consecutive_passes": self._consecutive_passes,
            "result": dict(self._result) if self._result else None,
            "terminal": self._terminal,
            "initialized": self._initialized,
            "setup_emitted": self._setup_emitted,
            "log": list(self._log),
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "DominoesModule":
        mod = cls(name=data.get("name", "dominoes"), params=data.get("params", {}))
        s = data.get("state", {})
        mod._hand_size = int(s.get("hand_size", mod._hand_size) or mod._hand_size)
        mod._players = list(s.get("players") or [])
        mod._hands = {
            pid: [tuple(t) for t in hand]
            for pid, hand in (s.get("hands") or {}).items()
        }
        mod._line = [list(t) for t in (s.get("line") or [])]
        mod._boneyard = [tuple(t) for t in (s.get("boneyard") or [])]
        mod._turn = s.get("turn")
        mod._consecutive_passes = int(s.get("consecutive_passes") or 0)
        mod._result = dict(s["result"]) if s.get("result") else None
        mod._terminal = bool(s.get("terminal", False))
        mod._initialized = bool(s.get("initialized", False))
        mod._setup_emitted = bool(s.get("setup_emitted", False))
        mod._log = list(s.get("log") or [])
        return mod
