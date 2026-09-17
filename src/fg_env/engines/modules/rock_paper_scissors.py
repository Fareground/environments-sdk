"""Rock Paper Scissors — sealed-tick iterated best-of-N match.

Two players each round secretly pick rock / paper / scissors. Both
reveal simultaneously; standard win rules apply (rock crushes
scissors, scissors cuts paper, paper covers rock). First player to
reach ceil(best_of / 2) round wins takes the match.

Public events
~~~~~~~~~~~~~
  - `rps_init`     { best_of, wins_to_clinch, match_score }            one-shot
  - `rps_pending`  { player, side, move, round }                       sealed
  - `rps_round`    { round, white_move, black_move, winner, score,
                     wins_to_clinch }                                  both submitted
  - `rps_win`      { winner, loser, score, best_of }                   terminal

Chat is ENABLED. RPS is sealed-tick simultaneous — speech is bound
to the same action that's sealed, so it reveals together with the
move and can't leak the pick. Trash-talk + mind games are part of
the fun.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


VALID_MOVES = ("rock", "paper", "scissors")
# Winner table: BEATS[move] = move that this move beats.
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}

DEFAULT_BEST_OF = 9
MIN_BEST_OF = 1
MAX_BEST_OF = 99


def _clean_move(raw: Any) -> Optional[str]:
    """Normalize LLM input to one of 'rock' / 'paper' / 'scissors'."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s and s[0] in "\"'" and s[-1] in "\"'":
        s = s[1:-1].strip()
    # Accept single-letter shortcuts.
    if s in ("r", "ro"):
        return "rock"
    if s in ("p", "pa"):
        return "paper"
    if s in ("s", "sc", "sci"):
        return "scissors"
    if s in VALID_MOVES:
        return s
    # Match anything containing one of the words as a substring (e.g.
    # the LLM wrote "I throw rock"). First-match wins.
    for m in VALID_MOVES:
        if m in s:
            return m
    return None


class RockPaperScissorsModule(DomainModule):
    """Sealed-tick iterated RPS, best-of-N."""

    # Chat ENABLED. Unlike Wordle / Hangman / Sudoku where the
    # opponent's reasoning could be reused across rounds to crack the
    # sealed puzzle, RPS is round-by-round with no persistent secret —
    # speech reveals together with the move (sealed simultaneous
    # resolution), so trash-talk + mind games are fair play.
    suppress_chat = False

    def __init__(self, name: str = "rock_paper_scissors",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        bo = int(p.get("best_of") or DEFAULT_BEST_OF)
        bo = max(MIN_BEST_OF, min(MAX_BEST_OF, bo))
        # Force odd so there's always a clear majority winner.
        if bo % 2 == 0:
            bo = max(MIN_BEST_OF, bo - 1)
        self._best_of: int = bo
        self._wins_to_clinch: int = (bo + 1) // 2

        seed = p.get("rng_seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()

        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        self._white_score: int = 0
        self._black_score: int = 0
        self._ties: int = 0
        self._round_number: int = 1
        self._white_history: List[str] = []
        self._black_history: List[str] = []
        self._pending_white: Optional[str] = None
        self._pending_black: Optional[str] = None

        self._terminal: Optional[Dict[str, Any]] = None
        self._initialized = False
        logger.info(
            "[rps] init best_of=%d wins_to_clinch=%d",
            self._best_of, self._wins_to_clinch,
        )

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return (f"Rock Paper Scissors — best-of-{self._best_of}; first to "
                f"{self._wins_to_clinch} round wins takes the match.")

    @property
    def custom_actions(self) -> List[str]:
        return ["throw"]

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
                ent.properties["score"] = 0
        self._initialized = True
        logger.info(
            "[rps] seated white=%s black=%s best_of=%d",
            agents[0].name, agents[1].name, self._best_of,
        )

    # ------------------------------------------------------------------ #
    # tick — emit init event once
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        was_seeded = self._initialized
        self._seed_from_state(state)
        if not was_seeded and self._initialized:
            return [{
                "type": "rps_init",
                "best_of": self._best_of,
                "wins_to_clinch": self._wins_to_clinch,
                "match_score": {"white": 0, "black": 0},
                "narrative": (
                    f"Rock Paper Scissors — best-of-{self._best_of}, first "
                    f"to {self._wins_to_clinch} round wins takes the match."
                ),
            }]
        return []

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str],
                             state: Any) -> List[str]:
        self._seed_from_state(state)
        if self._terminal is not None:
            return []
        if entity_id not in (self._white_id, self._black_id):
            return []
        side = self._side_for_entity(entity_id)
        if self._has_pending(side):
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
        if action_name == "throw":
            side = self._side_for_entity(actor_id)
            if self._has_pending(side):
                return "You already submitted this round — waiting on opponent"
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
        # Speech intentionally NOT cleared — RPS chat is enabled, the
        # speech field flows through to the chat panel as normal.
        params = raw.get("_action_params") or {}
        details = {**raw, **params}
        if action_name == "throw":
            return self._handle_throw(actor_id, details, state)
        return []

    def _handle_throw(self, actor_id: str, details: Dict[str, Any],
                      state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        raw_move = details.get("move") or details.get("choice") or details.get("play")
        move = _clean_move(raw_move)
        logger.info(
            "[rps] submit: actor=%s side=%s raw=%r cleaned=%r",
            actor_id, side, raw_move, move,
        )

        if move is None:
            # Defensive fallback — pick a uniform random move so the
            # round still resolves.
            move = self._rng.choice(VALID_MOVES)
            logger.warning(
                "[rps] %s gave invalid move raw=%r — substituting %r",
                side, raw_move, move,
            )

        if side == "white":
            self._pending_white = move
        else:
            self._pending_black = move

        events: List[Dict[str, Any]] = [{
            "type": "rps_pending",
            "player": actor_id,
            "side": side,
            "move": move,
            "round": self._round_number,
            "narrative": (
                f"{actor_name} throws (sealed — waiting on opponent)."
            ),
        }]
        if self._pending_white is not None and self._pending_black is not None:
            events.extend(self._resolve_round(state))
        return events

    # ------------------------------------------------------------------ #
    # Round resolution
    # ------------------------------------------------------------------ #

    def _resolve_round(self, state: Any) -> List[Dict[str, Any]]:
        w_move = self._pending_white or "rock"
        b_move = self._pending_black or "rock"
        self._pending_white = None
        self._pending_black = None
        self._white_history.append(w_move)
        self._black_history.append(b_move)

        if w_move == b_move:
            winner_side: Optional[str] = None
            self._ties += 1
        elif BEATS[w_move] == b_move:
            winner_side = "white"
            self._white_score += 1
        else:
            winner_side = "black"
            self._black_score += 1

        self._refresh_player_props(state)
        white_name = self._name_of(state, self._white_id)
        black_name = self._name_of(state, self._black_id)

        events: List[Dict[str, Any]] = [{
            "type": "rps_round",
            "round": self._round_number,
            "white_move": w_move,
            "black_move": b_move,
            "winner": (self._white_id if winner_side == "white"
                       else self._black_id if winner_side == "black"
                       else None),
            "winner_side": winner_side,
            "score": {"white": self._white_score, "black": self._black_score},
            "ties": self._ties,
            "wins_to_clinch": self._wins_to_clinch,
            "best_of": self._best_of,
            "narrative": (
                f"Round {self._round_number}: "
                f"{white_name} → {w_move}; {black_name} → {b_move}. "
                + ("Tie." if winner_side is None else
                   f"{white_name if winner_side == 'white' else black_name} wins the round.")
                + f" Score {self._white_score}–{self._black_score}."
            ),
        }]
        self._round_number += 1

        # Match-level terminal: first to clinch wins.
        match_winner: Optional[str] = None
        if self._white_score >= self._wins_to_clinch:
            match_winner = self._white_id
        elif self._black_score >= self._wins_to_clinch:
            match_winner = self._black_id

        if match_winner is not None:
            self._terminal = {"reason": "match_won", "winner": match_winner}
            events.append({
                "event_type": "rps_win",
                "type": "rps_win",
                "winner": match_winner,
                "loser": self._opp_id(match_winner),
                "score": {"white": self._white_score, "black": self._black_score},
                "ties": self._ties,
                "best_of": self._best_of,
                "narrative": (
                    f"{self._name_of(state, match_winner)} clinches the "
                    f"best-of-{self._best_of} match "
                    f"{self._white_score}–{self._black_score}"
                    + (f" ({self._ties} ties)." if self._ties else ".")
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
                "best_of": self._best_of,
            }
        side = self._side_for_entity(entity_id)
        own_hist = self._white_history if side == "white" else self._black_history
        opp_hist = self._black_history if side == "white" else self._white_history
        own_score = self._white_score if side == "white" else self._black_score
        opp_score = self._black_score if side == "white" else self._white_score
        own_pending = (self._pending_white if side == "white"
                       else self._pending_black)
        opp_pending = (self._pending_black if side == "white"
                       else self._pending_white)
        opp_side = "black" if side == "white" else "white"

        # Compute opponent's frequency distribution (only over resolved
        # rounds — the current sealed move is never exposed).
        opp_freq = {m: 0 for m in VALID_MOVES}
        for m in opp_hist:
            opp_freq[m] = opp_freq.get(m, 0) + 1
        total_resolved = len(opp_hist)

        if self._terminal is not None:
            instructions = "Match over."
        elif own_pending is not None:
            instructions = (
                f"You threw {own_pending.upper()} this round — sealed. "
                f"Waiting on opponent."
                if opp_pending is None else
                f"You threw {own_pending.upper()} this round — sealed. "
                f"Opponent has also submitted; round about to resolve."
            )
        else:
            recent_opp = ", ".join(m.upper() for m in opp_hist[-10:]) or "(no rounds yet)"
            recent_own = ", ".join(m.upper() for m in own_hist[-10:]) or "(no rounds yet)"
            freq_lines = [
                f"  {m.upper()}: {opp_freq[m]}"
                f" ({(opp_freq[m] / total_resolved * 100):.0f}%)"
                if total_resolved > 0 else f"  {m.upper()}: 0 (—)"
                for m in VALID_MOVES
            ]
            instructions = (
                f"IT IS YOUR TURN. You are {side.upper()} in Rock Paper Scissors.\n"
                f"\n"
                f"Best-of-{self._best_of}. First to {self._wins_to_clinch} "
                f"round wins takes the match.\n"
                f"Current score — YOU: {own_score}, OPPONENT: {opp_score} "
                f"({self._ties} ties).\n"
                f"\n"
                f"OPPONENT MOVE FREQUENCY (across {total_resolved} resolved rounds):\n"
                + "\n".join(freq_lines) + "\n"
                f"\n"
                f"RECENT MOVES (oldest → newest, last 10):\n"
                f"  Opponent ({opp_side.upper()}): {recent_opp}\n"
                f"  You ({side.upper()}):         {recent_own}\n"
                f"\n"
                f"Pick ONE move: rock, paper, or scissors. Standard rules: "
                f"rock beats scissors, scissors beats paper, paper beats "
                f"rock. Identical moves tie (no point).\n"
                f"\n"
                f"YOUR TOOL CALL — output exactly this shape:\n"
                f'  throw(move="rock")        # or "paper", or "scissors"\n'
                f"\n"
                f"The `move` parameter is REQUIRED and must be exactly "
                f"one of: rock, paper, scissors. If you omit it the "
                f"engine substitutes a uniform-random pick and you "
                f"lose tempo. Both players submit sealed; reveal is "
                f"simultaneous."
            )

        return {
            "instructions": instructions,
            "your_color": side,
            "best_of": self._best_of,
            "wins_to_clinch": self._wins_to_clinch,
            "your_score": own_score,
            "opponent_score": opp_score,
            "ties": self._ties,
            "round": self._round_number,
            "your_history": list(own_hist),
            "opponent_history": list(opp_hist),
            "your_pending_move": own_pending,
            "opponent_has_submitted_this_round": opp_pending is not None,
            "valid_moves": list(VALID_MOVES),
            "terminal": dict(self._terminal) if self._terminal else None,
        }

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _side_for_entity(self, entity_id: str) -> str:
        return "white" if entity_id == self._white_id else "black"

    def _has_pending(self, side: str) -> bool:
        return (
            (self._pending_white if side == "white" else self._pending_black)
            is not None
        )

    def _refresh_player_props(self, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        for entity_id, side, score in (
            (self._white_id, "white", self._white_score),
            (self._black_id, "black", self._black_score),
        ):
            ent = state.entities.get(entity_id) if entity_id else None
            if ent is None or not hasattr(ent, "properties"):
                continue
            ent.properties["color"] = side
            ent.properties["score"] = score

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
