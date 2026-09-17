"""Hangman Duel — sealed-tick competitive Hangman.

Two players race to reveal the SAME secret word. Each round both submit
ONE letter sealed; once both have submitted, the engine reveals each
letter against the target word INDEPENDENTLY for each player. A correct
letter fills every matching position on that player's mask. A wrong
letter increments that player's strike count. A player wins the inner
game by FULLY revealing the word; a player loses by hitting
`max_strikes` first.

Match-level wrapper: best-of-N (configurable via `wins_needed`). Tied
best-of-N goes to sudden-death extra games until someone clinches.

Public events
~~~~~~~~~~~~~
  - `hm_init`        { word_length, max_strikes, best_of, wins_to_clinch,
                       match_score, game_number }                    one-shot
  - `hm_pending`     { player, side, letter, round }                 guess accepted, waiting on opponent
  - `hm_round`       { round, game_number, match_score,
                       white_letter, white_correct, white_mask,
                       white_strikes, white_solved, white_eliminated,
                       black_letter, black_correct, black_mask,
                       black_strikes, black_solved, black_eliminated } both submitted; reveal
  - `hm_game_end`    { game_number, target_word, winner, reason,
                       match_score, rounds_used }                    per-game terminal
  - `hm_next_game`   { game_number, match_score, sudden_death }      reset for next game
  - `hm_invalid`     { actor_id, reason, message }                   bad submit (defensive)
  - `hm_win`         { winner, loser, match_score, best_of }         match terminal

Engine-level chat suppression
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Same model as Wordle Duel — opponents' reasoning + speech is wiped
from all broadcast events. Otherwise the LLMs would leak each other's
deduction state.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


DEFAULT_MAX_STRIKES = 6
MIN_MAX_STRIKES = 4
MAX_MAX_STRIKES = 10

DEFAULT_WINS_NEEDED = 2
MIN_WINS_NEEDED = 1
MAX_WINS_NEEDED = 5

MASK_CHAR = "_"

# Frequency-ordered alphabet for fallback / opener-pool selection.
# Source: standard English-letter frequency (E, T, A, O, I, N, S, H, R,
# D, L, U, C, M, W, F, G, Y, P, B, V, K, J, X, Q, Z).
FREQ_ORDER = "etaoinshrdlucmwfgypbvkjxqz"


# ---------------------------------------------------------------------------
# Word pool — curated, medium-length English words biased toward common
# vocabulary. Length 5-10. The target is picked once per game.
# ---------------------------------------------------------------------------

_RAW_POOL: List[str] = [
    # 5-7 letter common words
    "apple", "bench", "cargo", "chair", "cloud", "diary", "dwarf", "eagle",
    "ember", "fence", "field", "flute", "globe", "grape", "honey", "horse",
    "ivory", "judge", "knife", "lemon", "lunar", "magic", "month", "novel",
    "ocean", "olive", "panic", "piano", "pivot", "queen", "quiet", "river",
    "robin", "sauce", "shore", "smile", "spark", "stage", "storm", "tiger",
    "tower", "ultra", "vapor", "vivid", "whale", "wheel", "world", "young",
    # 6 letters
    "anchor", "branch", "bridge", "castle", "circle", "danger", "dragon",
    "engine", "forest", "garden", "gravel", "harbor", "icicle", "island",
    "jacket", "jungle", "kingdom", "ladder", "marble", "meadow", "nephew",
    "outlaw", "palace", "pencil", "rabbit", "rocket", "saddle", "shadow",
    "silver", "spider", "squash", "stream", "tunnel", "umbrella", "valley",
    "violet", "willow", "winter", "wisdom", "wonder", "yellow", "zigzag",
    # 7 letters
    "acrobat", "balcony", "biscuit", "blanket", "boulder", "captain",
    "cleaner", "compass", "country", "courage", "diamond", "echelon",
    "factory", "feather", "freedom", "gallery", "harvest", "horizon",
    "iceberg", "journey", "kingdom", "library", "magenta", "mariner",
    "monarch", "octagon", "octopus", "olympic", "panther", "pyramid",
    "rainbow", "scratch", "skipper", "snorkel", "stamina", "thunder",
    "treason", "trolley", "twister", "venture", "warrior", "whisper",
    # 8 letters
    "absolute", "balloons", "calendar", "champion", "crystal", "daylight",
    "elephant", "festival", "flagship", "graceful", "handheld", "innovate",
    "jubilant", "keynotes", "language", "majestic", "mountain", "nautical",
    "obstacle", "operator", "panorama", "platinum", "quantity", "remarkable",
    "savoring", "starfish", "tropical", "vineyard", "watchful", "yearling",
    # 9 letters
    "adventure", "blueprint", "champagne", "chemistry", "discovery",
    "elephants", "extension", "factories", "guarantee", "harvested",
    "ignorance", "jubilance", "knowledge", "landscape", "marvelous",
    "newsstand", "objection", "patchwork", "quarterly", "raspberry",
    "saxophone", "telescope", "underwater", "vibration", "workshop",
]


def _validated_pool() -> Dict[int, List[str]]:
    """Group the raw pool by length, deduplicated and length-validated."""
    by_length: Dict[int, Set[str]] = {}
    for w in _RAW_POOL:
        wl = w.strip().lower()
        if not wl or not wl.isalpha():
            continue
        n = len(wl)
        if 4 <= n <= 10:
            by_length.setdefault(n, set()).add(wl)
    return {k: sorted(v) for k, v in sorted(by_length.items())}


WORD_POOLS: Dict[int, List[str]] = _validated_pool()
# Flat list across all lengths — used when no length constraint applies.
WORD_POOL_ALL: List[str] = sorted({w for ws in WORD_POOLS.values() for w in ws})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_letter(raw: Any) -> Optional[str]:
    """Coerce LLM input to a single a-z character. Returns None on failure."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    # Strip surrounding quotes the LLM may have added.
    if s and s[0] in "\"'" and s[-1] in "\"'":
        s = s[1:-1].strip()
    # If the LLM submitted a whole word, take the FIRST alphabetic char.
    alpha = "".join(ch for ch in s if ch.isalpha())
    if not alpha:
        return None
    return alpha[0]


def _build_mask(target: str, revealed: Set[str]) -> str:
    """Return target with unrevealed letters replaced by `_`."""
    return "".join(ch if ch in revealed else MASK_CHAR for ch in target)


def _render_mask_display(mask: str) -> str:
    """Format the mask with spaces between characters for readability."""
    return " ".join(mask)


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------


class HangmanDuelModule(DomainModule):
    """Sealed-tick competitive Hangman. Both players race to reveal the
    SAME target word; first to fully reveal wins. Hitting max_strikes
    first = loss. Match wraps the inner game as best-of-N."""

    # Engine-level flag: actions in this module's `custom_actions` MUST
    # NOT broadcast speech / reasoning to other agents or the chat
    # panel. Otherwise an LLM's reasoning ("E is too common, trying Z")
    # would leak to the opponent and ruin the deduction race.
    suppress_chat = True

    # Per-seat mandatory opener. Same idea as Wordle Duel — both seats
    # run the same LLM with the same empty-mask perception and reliably
    # converge on the same letter (almost always 'E'). Force divergence.
    _OPENER_LETTERS: Dict[str, List[str]] = {
        "white": ["e", "t", "a", "o", "s"],
        "black": ["i", "n", "r", "l", "u"],
    }

    def __init__(self, name: str = "hangman_duel",
                 params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        # Configurable: max strikes per game.
        ms = int(p.get("max_strikes") or DEFAULT_MAX_STRIKES)
        self._max_strikes: int = max(MIN_MAX_STRIKES, min(MAX_MAX_STRIKES, ms))
        # Configurable: match length (wins-to-clinch).
        wn = int(p.get("wins_needed") or DEFAULT_WINS_NEEDED)
        wn = max(MIN_WINS_NEEDED, min(MAX_WINS_NEEDED, wn))
        self._wins_to_clinch: int = wn
        self._best_of: int = wn * 2 - 1
        seed = p.get("rng_seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()
        # Optional length constraint — if not provided, sample across
        # the entire pool.
        wl = p.get("word_length")
        self._fixed_word_length: Optional[int] = (
            int(wl) if isinstance(wl, (int, float)) and int(wl) in WORD_POOLS else None
        )
        self._target: str = self._pick_target()

        self._white_id: Optional[str] = None
        self._black_id: Optional[str] = None
        # Match-level score.
        self._white_games: int = 0
        self._black_games: int = 0
        self._game_number: int = 1
        self._games_log: List[Dict[str, Any]] = []
        # Per-game state (resets between games).
        self._white_revealed: Set[str] = set()
        self._black_revealed: Set[str] = set()
        self._white_wrong: List[str] = []
        self._black_wrong: List[str] = []
        # Sealed pending letters this round.
        self._pending_white: Optional[str] = None
        self._pending_black: Optional[str] = None
        self._round_number: int = 1

        self._terminal: Optional[Dict[str, Any]] = None
        self._initialized = False
        logger.info(
            "[hm] init max_strikes=%d wins_needed=%d (best-of-%d) target_len=%d",
            self._max_strikes, self._wins_to_clinch, self._best_of,
            len(self._target),
        )

    # ------------------------------------------------------------------ #
    @property
    def description(self) -> str:
        return (f"Hangman Duel — letter-by-letter race, "
                f"{self._max_strikes} strikes max. Sealed-tick rounds: "
                "both submit one letter, both reveal.")

    @property
    def custom_actions(self) -> List[str]:
        return ["guess_letter"]

    @property
    def required_properties(self) -> List[str]:
        return ["color"]

    # ------------------------------------------------------------------ #
    # Target selection
    # ------------------------------------------------------------------ #

    def _pick_target(self) -> str:
        if self._fixed_word_length is not None:
            pool = WORD_POOLS.get(self._fixed_word_length) or WORD_POOL_ALL
        else:
            pool = WORD_POOL_ALL
        return self._rng.choice(pool) if pool else "hangman"

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
                ent.properties["strikes"] = 0
                ent.properties["letters_revealed"] = 0
                ent.properties["solved"] = False
        self._initialized = True
        logger.info(
            "[hm] seated white=%s black=%s target=%s",
            agents[0].name, agents[1].name, self._target,
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
                "type": "hm_init",
                "word_length": len(self._target),
                "max_strikes": self._max_strikes,
                "best_of": self._best_of,
                "wins_to_clinch": self._wins_to_clinch,
                "match_score": {"white": 0, "black": 0},
                "game_number": 1,
                "narrative": (
                    f"Hangman Duel — {bo_text}, both players race to "
                    f"reveal the same {len(self._target)}-letter word; "
                    f"{self._max_strikes} strikes per game."
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
        # Players who already submitted this round get an empty action
        # set — they wait for the opponent.
        if self._has_pending(side):
            return []
        # Players who've already solved or been eliminated this game
        # can't keep guessing. The inner-game resolution will fire when
        # the OTHER player resolves their own state.
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
        if action_name == "guess_letter":
            side = self._side_for_entity(actor_id)
            if self._has_pending(side):
                return "You already submitted this round — waiting on opponent"
            if self._is_done(side):
                return "You have already finished this game"
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
        # Wipe speech so opponents NEVER see this player's CoT.
        if isinstance(raw, dict):
            raw["speech"] = None
        params = raw.get("_action_params") or {}
        details = {**raw, **params}
        if action_name == "guess_letter":
            return self._handle_guess(actor_id, details, state)
        return []

    def _handle_guess(self, actor_id: str, details: Dict[str, Any],
                      state: Any) -> List[Dict[str, Any]]:
        side = self._side_for_entity(actor_id)
        actor_name = self._name_of(state, actor_id)
        raw_letter = details.get("letter") or details.get("char") or details.get("guess")
        letter = _clean_letter(raw_letter)
        logger.info(
            "[hm] submit: actor=%s side=%s raw=%r cleaned=%r details_keys=%s",
            actor_id, side, raw_letter, letter, list(details.keys())[:8],
        )

        # Hard-enforce a per-seat mandated opener on round 1 (no
        # previous guesses) so the two players don't both pick 'E'.
        already_guessed = self._all_guessed(side)
        if not already_guessed:
            mandated = self._mandated_opener(side)
            if mandated and letter != mandated:
                logger.info(
                    "[hm] overriding opener for %s: %r → %r (mandate)",
                    side, letter, mandated,
                )
                letter = mandated

        # Defensive fallback — LLM omitted / mangled the letter param.
        if letter is None or letter not in "abcdefghijklmnopqrstuvwxyz":
            fallback = self._fallback_letter(side)
            logger.warning(
                "[hm] %s gave invalid letter raw=%r — substituting %r (fallback)",
                side, raw_letter, fallback,
            )
            letter = fallback

        # Already-guessed defense — pick something new.
        if letter in already_guessed:
            replacement = self._fallback_letter(side, avoid_extra={letter})
            logger.warning(
                "[hm] %s replayed letter %r — substituting %r (fallback)",
                side, letter, replacement,
            )
            letter = replacement

        if side == "white":
            self._pending_white = letter
        else:
            self._pending_black = letter

        events: List[Dict[str, Any]] = [{
            "type": "hm_pending",
            "player": actor_id,
            "side": side,
            # Spectator viz uses this; opponent never sees events.
            "letter": letter,
            "round": self._round_number,
            "narrative": (
                f"{actor_name} guessed a letter (sealed — waiting on opponent)."
            ),
        }]

        # If both submitted (or the opponent is already done and so can
        # never pending), resolve.
        opp_side = "black" if side == "white" else "white"
        opp_pending = self._pending_black if side == "white" else self._pending_white
        if opp_pending is not None or self._is_done(opp_side):
            events.extend(self._resolve_round(state))
        return events

    # ------------------------------------------------------------------ #
    # Round resolution
    # ------------------------------------------------------------------ #

    def _apply_letter(self, side: str, letter: str) -> Tuple[bool, str]:
        """Apply a guessed letter to this player's mask. Returns
        (was_correct, narrative_fragment)."""
        revealed = (self._white_revealed if side == "white"
                    else self._black_revealed)
        wrong = self._white_wrong if side == "white" else self._black_wrong
        if letter in self._target:
            revealed.add(letter)
            return True, "hit"
        wrong.append(letter)
        return False, "miss"

    def _resolve_round(self, state: Any) -> List[Dict[str, Any]]:
        w_letter = self._pending_white
        b_letter = self._pending_black

        # Apply each player's letter independently.
        w_correct: Optional[bool] = None
        b_correct: Optional[bool] = None
        if w_letter is not None and not self._is_done("white"):
            w_correct, _ = self._apply_letter("white", w_letter)
        if b_letter is not None and not self._is_done("black"):
            b_correct, _ = self._apply_letter("black", b_letter)

        self._pending_white = None
        self._pending_black = None

        # Refresh derived state (mask, solved flags).
        w_mask = _build_mask(self._target, self._white_revealed)
        b_mask = _build_mask(self._target, self._black_revealed)
        w_solved = MASK_CHAR not in w_mask
        b_solved = MASK_CHAR not in b_mask
        w_eliminated = len(self._white_wrong) >= self._max_strikes
        b_eliminated = len(self._black_wrong) >= self._max_strikes
        self._refresh_player_props(state, w_mask, b_mask)

        white_name = self._name_of(state, self._white_id)
        black_name = self._name_of(state, self._black_id)

        events: List[Dict[str, Any]] = [{
            "type": "hm_round",
            "round": self._round_number,
            "game_number": self._game_number,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "white_letter": w_letter,
            "white_correct": w_correct,
            "white_mask": w_mask,
            "white_strikes": len(self._white_wrong),
            "white_solved": w_solved,
            "white_eliminated": w_eliminated and not w_solved,
            "black_letter": b_letter,
            "black_correct": b_correct,
            "black_mask": b_mask,
            "black_strikes": len(self._black_wrong),
            "black_solved": b_solved,
            "black_eliminated": b_eliminated and not b_solved,
            "narrative": (
                f"Round {self._round_number}: "
                f"{white_name} → '{(w_letter or '-').upper()}' "
                f"({'hit' if w_correct else 'miss' if w_correct is not None else 'done'}); "
                f"{black_name} → '{(b_letter or '-').upper()}' "
                f"({'hit' if b_correct else 'miss' if b_correct is not None else 'done'})."
            ),
        }]

        self._round_number += 1

        # Per-game terminal check. The inner game ends as soon as:
        #   - someone solves (their mask is fully revealed),
        #   - someone gets eliminated (max strikes),
        #   - OR both are simultaneously done.
        # The match-level wrapper turns these into wins/losses below.
        game_winner: Optional[str] = None
        game_reason: Optional[str] = None
        if w_solved and b_solved:
            # Tie-break on strike count — fewer strikes = the win.
            if len(self._white_wrong) < len(self._black_wrong):
                game_winner = self._white_id
                game_reason = "double_solve_fewer_strikes"
            elif len(self._black_wrong) < len(self._white_wrong):
                game_winner = self._black_id
                game_reason = "double_solve_fewer_strikes"
            else:
                game_reason = "double_solve"   # genuine tie
        elif w_solved and (b_eliminated or self._is_done("black")):
            game_winner = self._white_id
            game_reason = "solved_only"
        elif b_solved and (w_eliminated or self._is_done("white")):
            game_winner = self._black_id
            game_reason = "solved_only"
        elif w_solved:
            game_winner = self._white_id
            game_reason = "solved"
        elif b_solved:
            game_winner = self._black_id
            game_reason = "solved"
        elif w_eliminated and b_eliminated:
            game_reason = "double_elimination"
        elif w_eliminated:
            game_winner = self._black_id
            game_reason = "white_eliminated"
        elif b_eliminated:
            game_winner = self._white_id
            game_reason = "black_eliminated"
        else:
            return events  # game continues — next round

        # Inner game ended.
        if game_winner == self._white_id:
            self._white_games += 1
        elif game_winner == self._black_id:
            self._black_games += 1
        self._games_log.append({
            "game_number": self._game_number,
            "target": self._target,
            "winner": game_winner,
            "reason": game_reason,
            "rounds": self._round_number - 1,
        })
        events.append({
            "type": "hm_game_end",
            "game_number": self._game_number,
            "target_word": self._target,
            "winner": game_winner,
            "reason": game_reason,
            "rounds_used": self._round_number - 1,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "narrative": (
                f"Game {self._game_number} complete (target '{self._target}'). "
                + (f"{self._name_of(state, game_winner)} wins the game."
                   if game_winner else
                   f"Game tied — {game_reason}.")
                + f" Match score: {white_name} {self._white_games}–"
                f"{self._black_games} {black_name}."
            ),
        })

        # Match-level terminal check.
        match_winner: Optional[str] = None
        if self._white_games >= self._wins_to_clinch:
            match_winner = self._white_id
        elif self._black_games >= self._wins_to_clinch:
            match_winner = self._black_id

        if match_winner is not None:
            self._terminal = {"reason": "match_won", "winner": match_winner}
            events.append({
                "event_type": "hm_win",
                "type": "hm_win",
                "winner": match_winner,
                "loser": self._opp_id(match_winner),
                "match_score": {"white": self._white_games, "black": self._black_games},
                "best_of": self._best_of,
                "narrative": (
                    f"{self._name_of(state, match_winner)} clinches the "
                    f"best-of-{self._best_of} match {self._white_games}–"
                    f"{self._black_games}."
                ),
            })
            return events

        # Otherwise reset for the next game (with sudden-death extension
        # if cap reached on a tie).
        self._game_number += 1
        is_sudden_death = self._game_number > self._best_of
        # New target word; reset per-game state.
        self._target = self._pick_target()
        self._white_revealed = set()
        self._black_revealed = set()
        self._white_wrong = []
        self._black_wrong = []
        self._pending_white = None
        self._pending_black = None
        self._round_number = 1
        logger.info(
            "[hm] next game #%d (best_of=%d, score=%d–%d, target_len=%d, sd=%s)",
            self._game_number, self._best_of, self._white_games,
            self._black_games, len(self._target), is_sudden_death,
        )
        narrative = (
            f"Sudden-death game {self._game_number} — match score "
            f"{self._white_games}–{self._black_games} (best-of-"
            f"{self._best_of} ran tied; play continues until a "
            f"winner emerges)."
            if is_sudden_death else
            f"Starting game {self._game_number} of {self._best_of}. "
            "(New target word; histories reset.)"
        )
        events.append({
            "type": "hm_next_game",
            "game_number": self._game_number,
            "match_score": {"white": self._white_games, "black": self._black_games},
            "best_of": self._best_of,
            "sudden_death": is_sudden_death,
            "narrative": narrative,
        })
        return events

    # ------------------------------------------------------------------ #
    # Perception
    # ------------------------------------------------------------------ #

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        if entity_id not in (self._white_id, self._black_id):
            return {
                "role": "spectator",
                "target_word": self._target if self._terminal else None,
                "max_strikes": self._max_strikes,
            }
        side = self._side_for_entity(entity_id)
        own_revealed = (self._white_revealed if side == "white"
                        else self._black_revealed)
        own_wrong = self._white_wrong if side == "white" else self._black_wrong
        own_pending = (self._pending_white if side == "white"
                       else self._pending_black)
        opp_pending = (self._pending_black if side == "white"
                       else self._pending_white)
        opp_side = "black" if side == "white" else "white"

        mask = _build_mask(self._target, own_revealed)
        strikes_left = max(0, self._max_strikes - len(own_wrong))
        all_guessed = sorted(self._all_guessed(side))

        if self._terminal is not None:
            instructions = "Game over."
        elif own_pending is not None:
            instructions = (
                f"You guessed '{own_pending.upper()}' this round — "
                f"sealed. Waiting on opponent."
                if opp_pending is None else
                f"You guessed '{own_pending.upper()}' this round — "
                f"sealed. Opponent has also submitted; round about to "
                f"resolve."
            )
        else:
            opening_hint = ""
            if not all_guessed:
                pick = (self._mandated_opener(side) or "").upper()
                opp_pick = (self._mandated_opener(opp_side) or "").upper()
                if pick:
                    opening_hint = (
                        f"OPENING — FIRST GUESS IS MANDATED. To prevent "
                        f"both AI players from picking the same opening "
                        f"letter, each seat gets a different mandate.\n"
                        f"  YOU ({side.upper()}) MUST guess: '{pick}'\n"
                        f"  Your opponent ({opp_side.upper()}) is mandated: "
                        f"'{opp_pick}'.\n"
                        f"Call guess_letter with letter='{pick}'. The "
                        f"engine will OVERRIDE any other letter you "
                        f"submit on round 1.\n\n"
                    )

            mask_display = _render_mask_display(mask)
            confirmed_letters = sorted(own_revealed)
            wrong_letters = sorted(own_wrong)
            unguessed = "".join(
                ch for ch in FREQ_ORDER if ch not in self._all_guessed(side)
            ).upper()
            instructions = (
                f"IT IS YOUR TURN. You are {side.upper()} in Hangman Duel.\n"
                f"\n"
                f"Your goal: reveal the entire target word before "
                f"hitting {self._max_strikes} wrong-letter strikes. "
                f"You have {strikes_left} strike{'s' if strikes_left != 1 else ''} "
                f"remaining.\n"
                f"\n"
                f"{opening_hint}"
                f"YOUR BOARD:\n"
                f"  Word ({len(self._target)} letters): {mask_display}\n"
                f"  Revealed letters: "
                f"{', '.join(c.upper() for c in confirmed_letters) or '(none yet)'}\n"
                f"  Wrong letters: "
                f"{', '.join(c.upper() for c in wrong_letters) or '(none)'}\n"
                f"  Strikes: {len(own_wrong)} of {self._max_strikes}\n"
                f"\n"
                f"Pick ONE new letter (a single character, a-z) to "
                f"guess this round. Do NOT pick a letter you've "
                f"already guessed.\n"
                f"\n"
                f"Letters you HAVEN'T tried yet (frequency-ordered): "
                f"{unguessed}\n"
                f"\n"
                f"YOUR TOOL CALL — output exactly this shape:\n"
                f'  guess_letter(letter="E")        # any single a-z letter\n'
                f'  guess_letter(letter="t")        # case-insensitive\n'
                f"\n"
                f"The `letter` parameter is REQUIRED — exactly one "
                f"a-z character. If you omit it the engine will "
                f"substitute the next-best frequency letter and you "
                f"lose tempo. Both players submit sealed; both reveals "
                f"happen simultaneously."
            )

        return {
            "instructions": instructions,
            "your_color": side,
            "match_best_of": self._best_of,
            "match_wins_to_clinch": self._wins_to_clinch,
            "match_score": {
                "white": self._white_games,
                "black": self._black_games,
            },
            "game_number": self._game_number,
            "your_mask": mask,
            "your_revealed_letters": sorted(c.upper() for c in own_revealed),
            "your_wrong_letters": sorted(c.upper() for c in own_wrong),
            "your_strikes": len(own_wrong),
            "your_strikes_left": strikes_left,
            "your_pending_letter": (own_pending.upper() if own_pending else None),
            "word_length": len(self._target),
            "max_strikes": self._max_strikes,
            "opponent_strikes": len(self._black_wrong if side == "white" else self._white_wrong),
            "opponent_letters_revealed": len(
                self._black_revealed if side == "white" else self._white_revealed
            ),
            "opponent_has_submitted_this_round": opp_pending is not None,
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

    def _is_done(self, side: str) -> bool:
        """A side is done THIS GAME once they've solved or hit max
        strikes. The other player still finishes their own race."""
        revealed = (self._white_revealed if side == "white"
                    else self._black_revealed)
        wrong = self._white_wrong if side == "white" else self._black_wrong
        if MASK_CHAR not in _build_mask(self._target, revealed):
            return True
        if len(wrong) >= self._max_strikes:
            return True
        return False

    def _all_guessed(self, side: str) -> Set[str]:
        revealed = (self._white_revealed if side == "white"
                    else self._black_revealed)
        wrong = self._white_wrong if side == "white" else self._black_wrong
        return set(revealed) | set(wrong)

    def _mandated_opener(self, side: str) -> Optional[str]:
        """Per-seat opening letter for the current game."""
        pool = self._OPENER_LETTERS.get(side) or []
        if not pool:
            return None
        return pool[(self._game_number - 1) % len(pool)]

    def _fallback_letter(self, side: str,
                         avoid_extra: Optional[Set[str]] = None) -> str:
        """Pick a frequency-ordered letter the player hasn't tried yet.
        Used when the LLM submits an unparseable / duplicate letter."""
        used = self._all_guessed(side)
        if avoid_extra:
            used = used | avoid_extra
        for ch in FREQ_ORDER:
            if ch not in used:
                return ch
        # Pathological exhaustion — pick any letter.
        for ch in "abcdefghijklmnopqrstuvwxyz":
            if ch not in used:
                return ch
        return "e"

    def _refresh_player_props(self, state: Any, w_mask: str, b_mask: str) -> None:
        if not hasattr(state, "entities"):
            return
        for entity_id, side, mask, wrong, revealed in (
            (self._white_id, "white", w_mask, self._white_wrong, self._white_revealed),
            (self._black_id, "black", b_mask, self._black_wrong, self._black_revealed),
        ):
            ent = state.entities.get(entity_id) if entity_id else None
            if ent is None or not hasattr(ent, "properties"):
                continue
            ent.properties["color"] = side
            ent.properties["strikes"] = len(wrong)
            ent.properties["letters_revealed"] = len(revealed)
            ent.properties["solved"] = (MASK_CHAR not in mask)

    def _invalid(self, actor_id: str, actor_name: str, reason: str,
                 message: str) -> Dict[str, Any]:
        return {
            "type": "hm_invalid",
            "actor_id": actor_id,
            "actor_name": actor_name,
            "reason": reason,
            "message": message,
            "narrative": f"{actor_name}: {message}",
        }

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
