"""Battleship domain module — classic 2-player naval grid duel.

Drives a real game of Battleship on top of the generic action engine:
  - Two players, one hidden fleet each on a 10x10 grid.
  - Standard fleet: Carrier(5), Battleship(4), Cruiser(3), Submarine(3),
    Destroyer(2) — 17 cells total.
  - Fleets are deployed automatically (random, legal, non-overlapping) at
    game start. The competitive game is the firing duel.
  - Players alternate strikes; first to sink all five enemy ships wins.

Custom actions:
  - `fire`: parameter `coord` (e.g. "F7"). Strikes one enemy square.
    Invalid / repeated squares are auto-redirected to a legal square so
    the game always makes forward progress.

Events emitted (consumed by BattleshipViz + UI):
  - `battleship_setup`   { boards }           — fleets deployed
  - `battleship_fire`    { player, coord, result, ship?, sunk?, boards }
  - `battleship_victory` { winner, loser, boards }   (terminal)

Coordinates: row letter A-J, column number 1-10 (e.g. "A1", "J10").
Internally cells are 0-indexed (row, col) tuples.
"""
from __future__ import annotations

import logging
from functools import lru_cache
import random
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)

# Row letters — supports square boards up to 14x14.
ROW_LETTERS = "ABCDEFGHIJKLMN"

MIN_GRID = 8
MAX_GRID = 14

# Standard Milton-Bradley fleet expressed as {ship_length: count}.
DEFAULT_SHIP_COUNTS: Dict[int, int] = {5: 1, 4: 1, 3: 2, 2: 1, 1: 0}

# Base display name for a ship of a given length.
SHIP_BASE_NAME: Dict[int, str] = {
    5: "Carrier",
    4: "Battleship",
    3: "Cruiser",
    2: "Destroyer",
    1: "Patrol Boat",
}


def build_fleet_spec(params: Dict[str, Any]) -> List[Tuple[str, int]]:
    """Build the ordered (name, length) fleet spec from match params.

    Reads `ships_len_<N>` for N in 5..1; any length not supplied falls
    back to the standard fleet count. Ships are ordered largest-first so
    they are easier to place. Empty fleets are rejected.
    """
    spec: List[Tuple[str, int]] = []
    for length in (5, 4, 3, 2, 1):
        raw = params.get(f"ships_len_{length}")
        count = DEFAULT_SHIP_COUNTS[length] if raw is None else raw
        if isinstance(count, bool) or not isinstance(count, (int, float)) or int(count) != count or not 0 <= count <= 40:
            raise ValueError("Ship counts must be whole numbers from 0 to 40.")
        count = int(count)
        base = SHIP_BASE_NAME[length]
        for i in range(count):
            spec.append((base if count == 1 else f"{base} {i + 1}", length))
    if not spec:
        raise ValueError("Add at least one ship before starting the match.")
    return spec


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def cell_to_coord(row: int, col: int) -> str:
    """(0,0) -> 'A1'."""
    return f"{ROW_LETTERS[row]}{col + 1}"


def parse_coord(raw: Any, size: int) -> Optional[Tuple[int, int]]:
    """Parse a coordinate string like 'F7' / 'f-7' / 'F 7' into (row, col).

    Returns None if the string is malformed or out of bounds for `size`.
    """
    if raw is None:
        return None
    text = str(raw).strip().upper()
    m = re.match(r"^([A-Z])\s*[-,]?\s*(\d{1,2})$", text)
    if not m:
        return None
    letter = m.group(1)
    valid_rows = ROW_LETTERS[:size]
    if letter not in valid_rows:
        return None
    row = valid_rows.index(letter)
    col = int(m.group(2)) - 1
    if 0 <= col < size:
        return (row, col)
    return None


# ---------------------------------------------------------------------------
# Fleet deployment
# ---------------------------------------------------------------------------

@lru_cache(maxsize=128)
def fleet_layout(size: int, lengths: Tuple[int, ...]) -> Tuple[Tuple[int, ...], ...]:
    """Find a complete layout before accepting a match; never drop a ship.

    Fill the first free cell with a ship or reserve it as water. A bounded
    search handles dense fleets without blocking admission indefinitely.
    """
    total = sum(lengths)
    if not lengths or total > size * size or max(lengths) > size:
        raise ValueError("The complete fleet does not fit. Reduce ship counts or enlarge the board.")
    counts = [lengths.count(n) for n in range(1, 6)]
    occupied = [False] * (size * size)
    nodes = 0
    def search(start, remaining, water):
        nonlocal nodes
        nodes += 1
        if nodes > 50000:
            return None
        if remaining == 0:
            return []
        while start < len(occupied) and occupied[start]:
            start += 1
        if start == len(occupied):
            return None
        row, col = divmod(start, size)
        for length in range(5, 0, -1):
            if not counts[length - 1]:
                continue
            for vertical in (False, True):
                if (row if vertical else col) + length > size:
                    continue
                cells = tuple(start + i * (size if vertical else 1) for i in range(length))
                if any(occupied[cell] for cell in cells):
                    continue
                for cell in cells:
                    occupied[cell] = True
                counts[length - 1] -= 1
                result = search(start + 1, remaining - length, water)
                counts[length - 1] += 1
                for cell in cells:
                    occupied[cell] = False
                if result is not None:
                    return [cells] + result
        if water:
            return search(start + 1, remaining, water - 1)
        return None
    result = search(0, total, size * size - total)
    if result is None:
        raise ValueError("A complete fleet layout could not be found. Reduce ship counts or enlarge the board.")
    return tuple(result)


def deploy_fleet(size: int, rng: random.Random,
                 fleet_spec: List[Tuple[str, int]]) -> List[Dict[str, Any]]:
    fallback = fleet_layout(size, tuple(length for _, length in fleet_spec))
    # Try complete random layouts; a validated layout handles crowded boards.
    for _ in range(32):
        occupied = set()
        placements = []
        for _, length in fleet_spec:
            candidates = []
            for vertical in (False, True):
                for row in range(size - length + 1 if vertical else size):
                    for col in range(size if vertical else size - length + 1):
                        cells = tuple((row + (i if vertical else 0), col + (0 if vertical else i)) for i in range(length))
                        if not occupied.intersection(cells):
                            candidates.append(cells)
            if not candidates:
                break
            cells = rng.choice(candidates)
            occupied.update(cells)
            placements.append(cells)
        if len(placements) == len(fleet_spec):
            break
    else:
        # Random rotations/reflections retain every ship in the proven layout.
        flip_row, flip_col, transpose = (rng.choice((True, False)) for _ in range(3))
        def transform(cell):
            row, col = divmod(cell, size)
            row, col = (size - 1 - row if flip_row else row), (size - 1 - col if flip_col else col)
            return (col, row) if transpose else (row, col)
        groups = {length: [] for length in range(1, 6)}
        for cells in fallback:
            groups[len(cells)].append(tuple(transform(cell) for cell in cells))
        placements = [groups[length].pop() for _, length in fleet_spec]
    return [{"name": name, "length": length,
             "cells": [cell_to_coord(*cell) for cell in cells], "hits": set(),
             "orientation": "H" if len(cells) < 2 or cells[0][0] == cells[1][0] else "V"}
            for (name, length), cells in zip(fleet_spec, placements)]


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class BattleshipModule(DomainModule):
    """Classic 2-player Battleship. Hidden fleets, alternating salvos."""

    def __init__(self, name: str = "battleship", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        size = p.get("grid_size", 10)
        if isinstance(size, bool) or not isinstance(size, (int, float)) or int(size) != size or not MIN_GRID <= size <= MAX_GRID:
            raise ValueError("Board size must be a whole number from 8 to 14.")
        self._size = int(size)
        self._fleet_spec = build_fleet_spec(p)
        fleet_layout(self._size, tuple(length for _, length in self._fleet_spec))
        # Player ids assigned on first tick (seat order).
        self._players: List[str] = []
        # fleets[pid] -> list of ship dicts (this player's own ships).
        self._fleets: Dict[str, List[Dict[str, Any]]] = {}
        # shots[pid] -> {coord: "hit"|"miss"} — shots RECEIVED on pid's grid.
        self._shots: Dict[str, Dict[str, str]] = {}
        self._turn: Optional[str] = None
        self._terminal: Optional[Dict[str, Any]] = None
        self._initialized: bool = False
        self._log: List[str] = []
        self._setup_emitted: bool = False

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return "Classic 2-player Battleship — hidden fleets, alternating salvos."

    @property
    def custom_actions(self) -> List[str]:
        return ["fire"]

    @property
    def required_properties(self) -> List[str]:
        return ["ships_remaining"]

    # ------------------------------------------------------------------ #
    # Seat assignment / fleet deployment
    # ------------------------------------------------------------------ #

    def _seed_from_state(self, state: Any) -> None:
        if self._initialized:
            return
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        if len(agents) < 2:
            return
        rng = random.Random(self._params.get("seed"))
        self._players = [agents[0].id, agents[1].id]
        for ent in agents[:2]:
            fleet = deploy_fleet(self._size, rng, self._fleet_spec)
            self._fleets[ent.id] = fleet
            self._shots[ent.id] = {}
            if hasattr(ent, "properties"):
                ent.properties["ships_remaining"] = len(fleet)
                ent.properties["hits_landed"] = 0
        self._turn = self._players[0]
        self._initialized = True
        logger.info("Battleship deployed: %s vs %s",
                    agents[0].name, agents[1].name)

    # ------------------------------------------------------------------ #
    # tick / filter_valid_actions
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        if self._initialized and not self._setup_emitted:
            self._setup_emitted = True
            return [{
                "type": "battleship_setup",
                "grid_size": self._size,
                "boards": self._boards_snapshot(state),
                "narrative": "Fleets deployed. The duel begins.",
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
            out.append("fire")
        return out

    # ------------------------------------------------------------------ #
    # Action handling
    # ------------------------------------------------------------------ #

    def validate_action(self, action_name: str, actor: Any, target: Any,
                        state: Any) -> Optional[str]:
        if action_name != "fire":
            return None
        if self._terminal is not None:
            return "The game is over"
        if getattr(actor, "id", None) != self._turn:
            return "Not your turn"
        return None

    def post_resolution(self, actor_id: str, action_name: str, success: bool,
                        result: Any, state: Any) -> List[Dict[str, Any]]:
        if not success or action_name != "fire":
            return []
        if self._terminal is not None or actor_id not in self._players:
            return []
        raw = getattr(result, "details", {}) if hasattr(result, "details") else {}
        params = raw.get("_action_params") or {}
        coord_raw = params.get("coord", raw.get("coord", ""))
        return self._handle_fire(actor_id, coord_raw, state)

    def _handle_fire(self, actor_id: str, coord_raw: Any,
                     state: Any) -> List[Dict[str, Any]]:
        opponent_id = self._opponent(actor_id)
        if opponent_id is None:
            return []
        actor_name = self._name_of(state, actor_id)
        size = self._size
        shots = self._shots[opponent_id]

        # Resolve the target square — auto-redirect invalid / repeated shots
        # to the first legal square so the game always progresses.
        auto_corrected = False
        cell = parse_coord(coord_raw, size)
        coord = cell_to_coord(*cell) if cell else None
        if coord is None or coord in shots:
            fallback = self._first_open_target(opponent_id)
            if fallback is None:
                return []
            auto_corrected = coord is not None or bool(str(coord_raw).strip())
            coord = fallback

        # Resolve hit / miss against the opponent's fleet.
        hit_ship: Optional[Dict[str, Any]] = None
        for ship in self._fleets[opponent_id]:
            if coord in ship["cells"]:
                hit_ship = ship
                break

        events: List[Dict[str, Any]] = []
        if hit_ship is not None:
            shots[coord] = "hit"
            hit_ship["hits"].add(coord)
            result = "hit"
        else:
            shots[coord] = "miss"
            result = "miss"

        sunk_ship: Optional[str] = None
        if hit_ship is not None and len(hit_ship["hits"]) >= hit_ship["length"]:
            sunk_ship = hit_ship["name"]

        # Sync UI-facing entity properties.
        self._sync_props(state, actor_id, opponent_id)

        # Narrative.
        if sunk_ship:
            narrative = f"{actor_name} fires at {coord} — HIT. Enemy {sunk_ship} SUNK!"
        elif result == "hit":
            narrative = f"{actor_name} fires at {coord} — HIT."
        else:
            narrative = f"{actor_name} fires at {coord} — miss."
        if auto_corrected:
            narrative += f" (salvo auto-redirected to {coord})"
        self._log.append(narrative)

        # Check for victory — all opponent ships sunk.
        opponent_fleet = self._fleets[opponent_id]
        opponent_sunk = sum(
            1 for s in opponent_fleet
            if len(s["hits"]) >= s["length"]
        )
        game_over = bool(opponent_fleet) and opponent_sunk >= len(opponent_fleet)
        if game_over:
            self._terminal = {"winner": actor_id, "loser": opponent_id}
        else:
            self._turn = opponent_id

        events.append({
            "type": "battleship_fire",
            "player": actor_id,
            "coord": coord,
            "result": result,
            "ship": sunk_ship,
            "sunk": bool(sunk_ship),
            "auto_corrected": auto_corrected,
            "grid_size": self._size,
            "boards": self._boards_snapshot(state),
            "turn": self._turn if not game_over else None,
            "narrative": narrative,
        })

        if game_over:
            winner_name = self._name_of(state, actor_id)
            loser_name = self._name_of(state, opponent_id)
            events.append({
                "event_type": "battleship_victory",
                "type": "battleship_victory",
                "winner": actor_id,
                "loser": opponent_id,
                "grid_size": self._size,
                "boards": self._boards_snapshot(state),
                "narrative": f"All enemy ships sunk. {winner_name} defeats {loser_name}.",
            })
        return events

    def _first_open_target(self, opponent_id: str) -> Optional[str]:
        shots = self._shots[opponent_id]
        for r in range(self._size):
            for c in range(self._size):
                coord = cell_to_coord(r, c)
                if coord not in shots:
                    return coord
        return None

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        # IMPORTANT — fairness: a player NEVER receives the opponent's ship
        # locations. They only see their own fleet and the results of the
        # salvos they have personally fired.
        if entity_id not in self._players:
            return {"role": "spectator"}
        opponent_id = self._opponent(entity_id)
        if opponent_id is None:
            return {"role": "spectator"}

        is_your_turn = (entity_id == self._turn) and self._terminal is None
        my_shots = self._shots.get(opponent_id, {})       # shots I fired
        incoming = self._shots.get(entity_id, {})         # shots fired at me

        legal_targets = [
            cell_to_coord(r, c)
            for r in range(self._size)
            for c in range(self._size)
            if cell_to_coord(r, c) not in my_shots
        ]

        # Enemy ships I have fully sunk (the only enemy-ship info I'm
        # allowed to know) and the cells those sunk ships occupy.
        sunk_enemy_cells: Set[str] = set()
        enemy_sunk: List[str] = []
        for s in self._fleets.get(opponent_id, []):
            if len(s["hits"]) >= s["length"]:
                enemy_sunk.append(s["name"])
                sunk_enemy_cells.update(s["cells"])
        enemy_afloat = len(self._fleets.get(opponent_id, [])) - len(enemy_sunk)

        # Unresolved hits: squares I have HIT that do not yet belong to a
        # sunk ship — these are wounded enemy ships I should finish.
        unresolved_hits = sorted(
            coord for coord, res in my_shots.items()
            if res == "hit" and coord not in sunk_enemy_cells
        )
        # Squares orthogonally adjacent to an unresolved hit that I have
        # not fired on yet — the highest-value follow-up shots.
        suggested: Set[str] = set()
        for coord in unresolved_hits:
            for nb in self._neighbors(coord):
                if nb not in my_shots:
                    suggested.add(nb)
        suggested_targets = sorted(suggested)

        # My own fleet status.
        my_fleet = [
            {
                "name": s["name"],
                "length": s["length"],
                "sunk": len(s["hits"]) >= s["length"],
                "hits_taken": len(s["hits"]),
            }
            for s in self._fleets.get(entity_id, [])
        ]

        coordinate_system = (
            f"The grid is {self._size}x{self._size}. ROWS are letters A (top) to {ROW_LETTERS[self._size - 1]} (bottom). "
            f"COLUMNS are numbers 1 (left) to {self._size} (right). A coordinate is "
            "the row letter followed by the column number, e.g. 'C7' is "
            "row C, column 7. Read the grids below row by row."
        )
        if is_your_turn:
            instructions = (
                "IT IS YOUR TURN TO FIRE. Study TARGETING GRID below — it "
                "shows every salvo you have fired at the enemy. Then call "
                "`fire` with parameter `coord` set to ONE square from "
                "`legal_targets` (e.g. coord='F7'). DO NOT set a target — "
                "Battleship has no target entity. STRATEGY: if "
                "`unresolved_hits` is non-empty you have a wounded enemy "
                "ship — fire a square from `suggested_targets` to finish "
                "it. Two adjacent hits reveal the ship's orientation: "
                "continue firing along that line. If there are no "
                "unresolved hits, hunt with a checkerboard (parity) "
                "pattern to cover the board efficiently."
            )
        else:
            instructions = (
                "The enemy admiral is firing. You have no legal action "
                "this turn — wait for your next salvo."
            )

        return {
            "instructions": instructions,
            "coordinate_system": coordinate_system,
            "is_your_turn": is_your_turn,
            "grid_size": self._size,
            "legend": {
                "targeting_grid": {
                    ".": "unknown water — never fired here",
                    "o": "MISS — you fired here, no ship",
                    "X": "HIT — you hit an enemy ship here (not yet sunk)",
                    "S": "SUNK — part of an enemy ship you have sunk",
                },
                "your_fleet_grid": {
                    ".": "open water",
                    "#": "your ship — intact",
                    "X": "your ship — HIT by the enemy",
                    "o": "enemy MISS — they fired here, no ship",
                },
            },
            "targeting_grid": self._render_targeting_grid(my_shots, sunk_enemy_cells),
            "your_fleet_grid": self._render_fleet_grid(entity_id, incoming),
            "legal_targets": legal_targets,
            "legal_targets_count": len(legal_targets),
            "unresolved_hits": unresolved_hits,
            "suggested_targets": suggested_targets,
            "your_fleet": my_fleet,
            "your_ships_afloat": sum(1 for s in my_fleet if not s["sunk"]),
            "enemy_ships_afloat": enemy_afloat,
            "enemy_ships_sunk": enemy_sunk,
            "shots_fired": len(my_shots),
            "your_hits": sum(1 for v in my_shots.values() if v == "hit"),
            "your_misses": sum(1 for v in my_shots.values() if v == "miss"),
            "recent_log": list(self._log[-12:]),
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    def _neighbors(self, coord: str) -> List[str]:
        """Orthogonal in-bounds neighbours of a coordinate."""
        cell = parse_coord(coord, self._size)
        if cell is None:
            return []
        r, c = cell
        out: List[str] = []
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < self._size and 0 <= nc < self._size:
                out.append(cell_to_coord(nr, nc))
        return out

    def _render_targeting_grid(self, my_shots: Dict[str, str],
                               sunk_enemy_cells: Set[str]) -> str:
        """Offensive view of MY shots on the enemy. Enemy ship positions
        are NEVER shown — only my own hits, misses and sunk ships."""
        def cellchar(coord: str) -> str:
            if coord in sunk_enemy_cells:
                return "S"
            v = my_shots.get(coord)
            if v == "hit":
                return "X"
            if v == "miss":
                return "o"
            return "."
        return self._render_grid(cellchar)

    def _render_fleet_grid(self, entity_id: str,
                           incoming: Dict[str, str]) -> str:
        """Defensive view of MY OWN fleet and the enemy's salvos on it."""
        ship_cells: Set[str] = set()
        for s in self._fleets.get(entity_id, []):
            ship_cells.update(s["cells"])

        def cellchar(coord: str) -> str:
            v = incoming.get(coord)
            if coord in ship_cells:
                return "X" if v == "hit" else "#"
            return "o" if v == "miss" else "."
        return self._render_grid(cellchar)

    def _render_grid(self, cellchar) -> str:
        """Render a labelled 10x10 ASCII grid. Columns 1-10 across the
        top, rows A-J down the side, each cell padded to width 2 so the
        grid stays aligned (including the two-digit column 10)."""
        header = "   " + "".join(f"{c + 1:>3}" for c in range(self._size))
        lines = [header]
        for r in range(self._size):
            row = "".join(f"{cellchar(cell_to_coord(r, c)):>3}"
                          for c in range(self._size))
            lines.append(f" {ROW_LETTERS[r]} {row}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Snapshot for the visualization
    # ------------------------------------------------------------------ #

    def _boards_snapshot(self, state: Any) -> List[Dict[str, Any]]:
        """Full ground-truth board state — spectators see both fleets."""
        boards: List[Dict[str, Any]] = []
        for pid in self._players:
            ships = []
            for s in self._fleets.get(pid, []):
                ships.append({
                    "name": s["name"],
                    "length": s["length"],
                    "cells": list(s["cells"]),
                    "hits": sorted(s["hits"]),
                    "sunk": len(s["hits"]) >= s["length"],
                    "orientation": s.get("orientation", "H"),
                })
            boards.append({
                "player_id": pid,
                "name": self._name_of(state, pid),
                "ships": ships,
                "shots": dict(self._shots.get(pid, {})),
                "ships_afloat": sum(1 for s in ships if not s["sunk"]),
            })
        return boards

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _opponent(self, pid: str) -> Optional[str]:
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

    def _sync_props(self, state: Any, actor_id: str, opponent_id: str) -> None:
        """Mirror score state onto entity properties for the UI."""
        if not hasattr(state, "entities"):
            return
        actor = state.entities.get(actor_id)
        if actor is not None and hasattr(actor, "properties"):
            actor.properties["hits_landed"] = sum(
                1 for v in self._shots[opponent_id].values() if v == "hit"
            )
        opp = state.entities.get(opponent_id)
        if opp is not None and hasattr(opp, "properties"):
            afloat = sum(
                1 for s in self._fleets[opponent_id]
                if len(s["hits"]) < s["length"]
            )
            opp.properties["ships_remaining"] = afloat

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "size": self._size,
            "players": list(self._players),
            "fleets": {
                pid: [
                    {
                        "name": s["name"],
                        "length": s["length"],
                        "cells": list(s["cells"]),
                        "hits": sorted(s["hits"]),
                        "orientation": s.get("orientation", "H"),
                    }
                    for s in ships
                ]
                for pid, ships in self._fleets.items()
            },
            "shots": {pid: dict(sh) for pid, sh in self._shots.items()},
            "turn": self._turn,
            "terminal": dict(self._terminal) if self._terminal else None,
            "log": list(self._log),
            "initialized": self._initialized,
            "setup_emitted": self._setup_emitted,
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "BattleshipModule":
        mod = cls(name=data.get("name", "battleship"), params=data.get("params", {}))
        s = data.get("state", {})
        mod._size = int(s.get("size", 10) or 10)
        mod._players = list(s.get("players") or [])
        mod._fleets = {
            pid: [
                {
                    "name": sh["name"],
                    "length": sh["length"],
                    "cells": list(sh["cells"]),
                    "hits": set(sh.get("hits") or []),
                    "orientation": sh.get("orientation", "H"),
                }
                for sh in ships
            ]
            for pid, ships in (s.get("fleets") or {}).items()
        }
        mod._shots = {pid: dict(sh) for pid, sh in (s.get("shots") or {}).items()}
        mod._turn = s.get("turn")
        mod._terminal = dict(s["terminal"]) if s.get("terminal") else None
        mod._log = list(s.get("log") or [])
        mod._initialized = bool(s.get("initialized", False))
        mod._setup_emitted = bool(s.get("setup_emitted", False))
        return mod
