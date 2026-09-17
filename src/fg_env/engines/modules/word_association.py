"""Word Association domain module — Codenames-style 2v2 word puzzle.

Ruleset (2v2):
  * 25 random words form a 5x5 board.
  * Color split: 9 Blue, 8 Yellow, 7 Neutral, 1 Assassin.
  * Four players: BlueCluemaster + BlueGuesser vs YellowCluemaster +
    YellowGuesser. Information wall — the Cluemaster sees every tile's
    color; the Guesser only sees revealed tiles and the active clue.
  * Turn loop for the active team:
      1. CLUE stage  — that team's CLUEMASTER calls
                       give_clue(word, count). The clue word may not
                       appear on any UNREVEALED tile. count >= 1, <= 9.
      2. GUESS stage — that team's GUESSER calls guess(tile) one tile at
                       a time, up to count + 1 guesses total:
            - hit own color    → tile revealed, keep guessing.
            - hit neutral      → tile revealed, turn ends.
            - hit opponent     → opponent scores that tile, turn ends.
            - hit ASSASSIN     → active team LOSES instantly.
         The active guesser may also call pass_turn() to end voluntarily.
  * Inactive players (3 of 4 every round) can only call `wait` — no
    chat / no broadcast, this is a mechanical puzzle.
  * Win conditions:
      - First team to reveal ALL their color tiles wins.
      - Touching the assassin = instant loss for the guesser's team.
      - Safety: if max_turns hit, team ahead on tiles wins (ties broken
        by team that started second).

Engine wiring:
  * Single phase `playing` with `resolution_mode: simultaneous`.
  * Stage machine (CLUE / GUESS / OVER) advances inside tick() based on
    the previous round's recorded action intents.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule
from fg_env.roles import Role

logger = logging.getLogger(__name__)


# A curated pool of common, distinct, image-evocative English nouns.
# Avoid plurals, proper nouns, and words that share confusable
# substrings (CAT/CATS) — clue collision detection treats the clue word
# as a substring of any tile.
WORD_POOL: Tuple[str, ...] = (
    "AIRPLANE", "ANCHOR", "APPLE", "ARROW", "BADGE", "BANANA", "BANK",
    "BARREL", "BEACH", "BEAVER", "BENCH", "BISCUIT", "BLANKET", "BOOK",
    "BOOMERANG", "BOOT", "BOTTLE", "BRIDGE", "BROOM", "BUBBLE", "BUCKET",
    "BUOY", "BUTTON", "CACTUS", "CAMERA", "CANDLE", "CANOE", "CANYON",
    "CAPE", "CARD", "CASTLE", "CAVE", "CHAIR", "CHALK", "CHISEL",
    "CIRCUS", "CLIFF", "CLOCK", "CLOUD", "CLOVER", "COIN", "COMET",
    "COMPASS", "CONE", "CORAL", "CRADLE", "CRANE", "CRATER", "CROWN",
    "CRYSTAL", "CUP", "CURTAIN", "DESERT", "DIAL", "DIAMOND", "DITCH",
    "DOCK", "DRAGON", "DRUM", "EAGLE", "ECHO", "EGG", "EMERALD",
    "ENGINE", "FAN", "FARM", "FEATHER", "FENCE", "FERRY", "FIDDLE",
    "FIELD", "FIRE", "FLAG", "FLAME", "FLASK", "FLOWER", "FOREST",
    "FORK", "FOUNTAIN", "FOX", "FROST", "FUNGUS", "GALAXY", "GARDEN",
    "GHOST", "GIANT", "GLACIER", "GLOVE", "GOAT", "GRAPE", "GRAVEL",
    "GUITAR", "HAMMER", "HARBOR", "HARP", "HAT", "HEDGE", "HELMET",
    "HONEY", "HOOK", "HORSE", "ICE", "ISLAND", "JAR", "JAW",
    "JEWEL", "JOUST", "JUNGLE", "KEY", "KING", "KITE", "KNIGHT",
    "LADDER", "LAMP", "LANTERN", "LEAF", "LEMON", "LENS", "LICHEN",
    "LIGHTHOUSE", "LION", "LOG", "MAP", "MASK", "MAZE", "MEADOW",
    "MEDAL", "MIRROR", "MITTEN", "MONKEY", "MOON", "MOOSE", "MOP",
    "MOTH", "MOUNTAIN", "MUSEUM", "NEEDLE", "NEST", "NIGHT", "OAK",
    "OCEAN", "ONION", "ORCHARD", "OWL", "PALACE", "PANDA", "PARADE",
    "PARROT", "PEAR", "PEBBLE", "PENCIL", "PIANO", "PILLAR", "PIRATE",
    "PISTOL", "PLANET", "POND", "POPPY", "POTION", "PRINCE", "PUMPKIN",
    "PUZZLE", "PYRAMID", "QUARRY", "QUEEN", "RABBIT", "RACCOON", "RADAR",
    "RAIN", "RAINBOW", "RANCH", "RAVEN", "REEF", "RESERVOIR", "RING",
    "RIVER", "ROBIN", "ROCKET", "ROOT", "ROSE", "RUBY", "SADDLE",
    "SAIL", "SAND", "SAPPHIRE", "SAUCER", "SAW", "SCARF", "SCROLL",
    "SEAL", "SHELL", "SHIELD", "SHRINE", "SILK", "SKULL", "SLED",
    "SMOKE", "SNAIL", "SNAKE", "SNOW", "SPARK", "SPEAR", "SPIDER",
    "SPONGE", "SPOON", "STAGE", "STAIR", "STAR", "STATUE", "STEAM",
    "STONE", "STORM", "STRING", "SUGAR", "SUMMIT", "SUN", "SWAMP",
    "SWORD", "SYRUP", "TABLET", "TEAPOT", "TELESCOPE", "TEMPLE", "TENT",
    "THORN", "THRONE", "TIDE", "TIGER", "TIMBER", "TOMB", "TORCH",
    "TOWER", "TRAIN", "TRAP", "TREASURE", "TREE", "TROPHY", "TRUCK",
    "TRUMPET", "TUNNEL", "TURTLE", "TWINE", "UMBRELLA", "VALLEY", "VAULT",
    "VINE", "VIOLIN", "VOLCANO", "WAGON", "WALLET", "WAVE", "WHALE",
    "WHEEL", "WHISTLE", "WIDOW", "WILLOW", "WINDMILL", "WING", "WOLF",
    "WOOL", "WORM", "WRECK", "YARN", "ZEBRA",
)


STAGE_CLUE = "clue"
STAGE_GUESS = "guess"
STAGE_OVER = "over"

COLOR_BLUE = "blue"
COLOR_YELLOW = "yellow"
COLOR_NEUTRAL = "neutral"
COLOR_ASSASSIN = "assassin"

ROLE_CLUEMASTER = "cluemaster"
ROLE_GUESSER = "guesser"

BLUE_TILES = 9
YELLOW_TILES = 8
NEUTRAL_TILES = 7
ASSASSIN_TILES = 1
BOARD_SIZE = BLUE_TILES + YELLOW_TILES + NEUTRAL_TILES + ASSASSIN_TILES  # 25


class WordAssociationModule(DomainModule):
    """Drives a Word Association (Codenames-style 2v2) match."""

    def __init__(
        self, name: str = "word_association", params: Optional[Dict[str, Any]] = None
    ):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Player")
        self._rng = random.Random(p.get("seed", 0xC0DE2025))
        self._max_turns: int = int(p.get("max_turns", 40))
        self._blue_first: bool = bool(p.get("blue_first", True))

        # Per-match state.
        self._bootstrapped = False
        self._game_over = False
        self._board: List[Dict[str, Any]] = []  # [{word, color, revealed}]
        # entity-id lookups
        self._blue_cluemaster_id: str = ""
        self._blue_guesser_id: str = ""
        self._yellow_cluemaster_id: str = ""
        self._yellow_guesser_id: str = ""

        # Per-turn state.
        self._stage: str = STAGE_CLUE
        self._active_team: str = COLOR_BLUE
        self._turn_number: int = 0
        self._current_clue: Optional[Dict[str, Any]] = None  # {word, count, by}
        self._guesses_remaining: int = 0
        self._guesses_made_this_turn: int = 0
        self._last_round_resolved: int = 0

    @property
    def description(self) -> str:
        return (
            "Word Association (Codenames 2v2): cluemaster gives single-word "
            "clues + count; guesser picks tiles one by one. Avoid assassin."
        )

    @property
    def custom_actions(self) -> List[str]:
        return ["give_clue", "guess", "pass_turn", "wait"]

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _players(self, state: Any) -> List[Any]:
        return [
            e for e in state.entities.values()
            if e.entity_type == self._player_type and e.alive
        ]

    def _ensure_setup(self, state: Any) -> None:
        if self._bootstrapped:
            return

        # Define team roles (used by the perception filter to know team
        # membership; the per-role action gating is enforced here in the
        # module's filter_valid_actions).
        state.roles.define_role(Role(
            name="blue", team="blue", sees_teammates=True,
            description="You are on the Blue team. Cluemasters see all colors; Guessers see only revealed tiles + the active clue.",
        ))
        state.roles.define_role(Role(
            name="yellow", team="yellow", sees_teammates=True,
            description="You are on the Yellow team. Cluemasters see all colors; Guessers see only revealed tiles + the active clue.",
        ))

        players = self._players(state)
        if len(players) < 4:
            logger.warning("Word Association needs 4 players (2v2), got %s. Disabled.", len(players))
            self._bootstrapped = True
            self._game_over = True
            return

        # Pin seats by (team, role) properties from the template. Fall
        # back to seat order if any are missing.
        def pick(team: str, role: str) -> Optional[Any]:
            for p in players:
                if (p.get("team") or "").lower() == team and (p.get("role") or "").lower() == role:
                    return p
            return None

        bc = pick(COLOR_BLUE, ROLE_CLUEMASTER)
        bg = pick(COLOR_BLUE, ROLE_GUESSER)
        yc = pick(COLOR_YELLOW, ROLE_CLUEMASTER)
        yg = pick(COLOR_YELLOW, ROLE_GUESSER)
        if not (bc and bg and yc and yg):
            # Defensive — engine sometimes wipes properties when swapping
            # in real user agents. Assign by seat order in the canonical
            # template entity ID layout (blue clue, blue guess, yellow
            # clue, yellow guess).
            bc, bg, yc, yg = players[0], players[1], players[2], players[3]
            bc.set("team", COLOR_BLUE);   bc.set("role", ROLE_CLUEMASTER)
            bg.set("team", COLOR_BLUE);   bg.set("role", ROLE_GUESSER)
            yc.set("team", COLOR_YELLOW); yc.set("role", ROLE_CLUEMASTER)
            yg.set("team", COLOR_YELLOW); yg.set("role", ROLE_GUESSER)

        self._blue_cluemaster_id = bc.id
        self._blue_guesser_id = bg.id
        self._yellow_cluemaster_id = yc.id
        self._yellow_guesser_id = yg.id

        state.roles.assign(bc.id, "blue")
        state.roles.assign(bg.id, "blue")
        state.roles.assign(yc.id, "yellow")
        state.roles.assign(yg.id, "yellow")
        for p in (bc, bg, yc, yg):
            p.set("score", 0)

        # Build the 25-tile board.
        words = self._rng.sample(list(WORD_POOL), BOARD_SIZE)
        colors = (
            [COLOR_BLUE] * BLUE_TILES
            + [COLOR_YELLOW] * YELLOW_TILES
            + [COLOR_NEUTRAL] * NEUTRAL_TILES
            + [COLOR_ASSASSIN] * ASSASSIN_TILES
        )
        self._rng.shuffle(colors)
        self._board = [
            {"word": w, "color": c, "revealed": False}
            for w, c in zip(words, colors)
        ]

        self._active_team = COLOR_BLUE if self._blue_first else COLOR_YELLOW
        self._stage = STAGE_CLUE
        self._turn_number = 1
        self._bootstrapped = True

    # ------------------------------------------------------------------
    # Seat lookups
    # ------------------------------------------------------------------

    def _active_cluemaster_id(self) -> str:
        return self._blue_cluemaster_id if self._active_team == COLOR_BLUE else self._yellow_cluemaster_id

    def _active_guesser_id(self) -> str:
        return self._blue_guesser_id if self._active_team == COLOR_BLUE else self._yellow_guesser_id

    def _inactive_team(self) -> str:
        return COLOR_YELLOW if self._active_team == COLOR_BLUE else COLOR_BLUE

    def _seat_label(self, entity_id: str) -> str:
        if entity_id == self._blue_cluemaster_id: return "Blue Cluemaster"
        if entity_id == self._blue_guesser_id: return "Blue Guesser"
        if entity_id == self._yellow_cluemaster_id: return "Yellow Cluemaster"
        if entity_id == self._yellow_guesser_id: return "Yellow Guesser"
        return "Player"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tile_by_word(self, word: str) -> Optional[Dict[str, Any]]:
        if not word:
            return None
        target = word.strip().upper()
        for tile in self._board:
            if tile["word"] == target:
                return tile
        target_clean = "".join(ch for ch in target if ch.isalpha())
        if not target_clean:
            return None
        for tile in self._board:
            if tile["word"].startswith(target_clean) or target_clean in tile["word"]:
                return tile
        return None

    def _unrevealed_count(self, color: str) -> int:
        return sum(1 for t in self._board if t["color"] == color and not t["revealed"])

    def _clue_collides(self, clue: str) -> Optional[str]:
        if not clue:
            return None
        c = clue.strip().upper()
        if not c.isalpha():
            return None
        for tile in self._board:
            if tile["revealed"]:
                continue
            tw = tile["word"]
            if c == tw or c in tw or tw in c:
                return tw
        return None

    # ------------------------------------------------------------------
    # Action gating
    # ------------------------------------------------------------------

    def filter_valid_actions(
        self, entity_id: str, valid_actions: List[str], state: Any
    ) -> List[str]:
        # Bootstrap eagerly so seat IDs are populated before round 1.
        self._ensure_setup(state)
        if self._game_over:
            return [a for a in valid_actions if a == "wait"]
        ent = state.get_entity(entity_id)
        if not ent or not ent.alive:
            return []

        if self._stage == STAGE_CLUE:
            if entity_id == self._active_cluemaster_id():
                return [a for a in valid_actions if a == "give_clue"]
            return [a for a in valid_actions if a == "wait"]

        if self._stage == STAGE_GUESS:
            if entity_id == self._active_guesser_id():
                return [a for a in valid_actions if a in {"guess", "pass_turn"}]
            return [a for a in valid_actions if a == "wait"]

        return [a for a in valid_actions if a == "wait"]

    # ------------------------------------------------------------------
    # Tick — resolve previous round, advance stage, emit narrative
    # ------------------------------------------------------------------

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._ensure_setup(state)
        if self._game_over:
            return []

        events: List[Dict[str, Any]] = []

        if round_number > 1 and round_number - 1 > self._last_round_resolved:
            prev = round_number - 1
            stage_played = self._stage
            if stage_played == STAGE_CLUE:
                events.extend(self._resolve_clue(state, prev))
            elif stage_played == STAGE_GUESS:
                events.extend(self._resolve_guess(state, prev))
            self._last_round_resolved = prev
            if self._game_over:
                return events
            if self._turn_number > self._max_turns:
                events.append(self._timeout_victory(state))
                self._game_over = True
                self._stage = STAGE_OVER
                return events

        events.append({
            "event_type": "word_association_stage",
            "narrative": self._stage_narrative(state),
            "data": {
                "stage": self._stage,
                "round": round_number,
                "turn": self._turn_number,
                "active_team": self._active_team,
                "active_cluemaster_id": self._active_cluemaster_id(),
                "active_guesser_id": self._active_guesser_id(),
                "current_clue": dict(self._current_clue) if self._current_clue else None,
                "guesses_remaining": self._guesses_remaining,
                "board_public": self._public_board(),
                "scores": self._scores(state),
            },
        })
        return events

    def _stage_narrative(self, state: Any) -> str:
        if self._stage == STAGE_CLUE:
            cm = state.get_entity(self._active_cluemaster_id())
            name = cm.name if cm else f"{self._active_team.title()} Cluemaster"
            return f"Turn {self._turn_number} — {name} ({self._active_team.title()}) is thinking of a clue."
        if self._stage == STAGE_GUESS:
            gu = state.get_entity(self._active_guesser_id())
            name = gu.name if gu else f"{self._active_team.title()} Guesser"
            clue = self._current_clue or {}
            return (
                f"{name} ({self._active_team.title()}) guessing on clue "
                f"\"{clue.get('word', '?')}\" for {clue.get('count', '?')} — "
                f"{self._guesses_remaining} guess(es) left."
            )
        return ""

    # ------------------------------------------------------------------
    # Stage resolvers
    # ------------------------------------------------------------------

    def _resolve_clue(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        cm = state.get_entity(self._active_cluemaster_id())
        if cm is None:
            return events

        clue_word = cm.get(f"_clue_word_r{round_number}")
        clue_count = cm.get(f"_clue_count_r{round_number}")

        if not clue_word:
            # Fallback so a missing clue doesn't stall the match.
            clue_word = "HINT"
            clue_count = 1

        clue_word = str(clue_word).strip().upper()
        try:
            count = max(1, min(9, int(clue_count or 1)))
        except (TypeError, ValueError):
            count = 1

        collided = self._clue_collides(clue_word)

        self._current_clue = {
            "word": clue_word,
            "count": count,
            "by": self._active_team,
            "collision": collided,
        }
        self._guesses_remaining = count + 1
        self._guesses_made_this_turn = 0
        events.append({
            "event_type": "word_association_clue",
            "actor_id": cm.id,
            "data": {
                "round": round_number,
                "turn": self._turn_number,
                "team": self._active_team,
                "word": clue_word,
                "count": count,
                "guesses_allowed": count + 1,
                "collision_tile": collided,
            },
            "narrative": (
                f"{cm.name} ({self._active_team.title()} Cluemaster): "
                f"\"{clue_word}\" for {count}."
                + (f" [⚠ collides with {collided} — treated as 1]" if collided else "")
            ),
        })
        if collided:
            self._guesses_remaining = 2
            self._current_clue["count"] = 1
        self._stage = STAGE_GUESS
        return events

    def _resolve_guess(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        gu = state.get_entity(self._active_guesser_id())
        if gu is None:
            return events

        passed = bool(gu.get(f"_pass_turn_r{round_number}"))
        guess_word = gu.get(f"_guess_tile_r{round_number}")

        if passed and not guess_word:
            events.append({
                "event_type": "word_association_pass",
                "actor_id": gu.id,
                "data": {"round": round_number, "turn": self._turn_number, "team": self._active_team},
                "narrative": f"{gu.name} ({self._active_team.title()} Guesser) passes.",
            })
            self._end_turn(events, state)
            return events

        if not guess_word:
            events.append({
                "event_type": "word_association_pass",
                "actor_id": gu.id,
                "data": {"round": round_number, "turn": self._turn_number, "team": self._active_team, "reason": "no_guess"},
                "narrative": f"{gu.name} ({self._active_team.title()} Guesser) makes no guess — turn ends.",
            })
            self._end_turn(events, state)
            return events

        tile = self._tile_by_word(str(guess_word))
        if tile is None or tile["revealed"]:
            events.append({
                "event_type": "word_association_invalid_guess",
                "actor_id": gu.id,
                "data": {
                    "round": round_number, "turn": self._turn_number,
                    "team": self._active_team, "guess": str(guess_word),
                    "reason": "unknown_or_revealed",
                },
                "narrative": f"{gu.name} guesses \"{guess_word}\" — not on the board (or already revealed). Turn ends.",
            })
            self._end_turn(events, state)
            return events

        # Reveal the tile.
        tile["revealed"] = True
        self._guesses_made_this_turn += 1
        self._guesses_remaining -= 1
        color = tile["color"]
        word = tile["word"]

        # Mirror score across both teammates so the front-end / API can
        # read either entity's `score` and see the team total.
        if color == COLOR_BLUE:
            for pid in (self._blue_cluemaster_id, self._blue_guesser_id):
                p = state.get_entity(pid)
                if p:
                    p.set("score", int(p.get("score") or 0) + 1)
        elif color == COLOR_YELLOW:
            for pid in (self._yellow_cluemaster_id, self._yellow_guesser_id):
                p = state.get_entity(pid)
                if p:
                    p.set("score", int(p.get("score") or 0) + 1)

        events.append({
            "event_type": "word_association_reveal",
            "actor_id": gu.id,
            "data": {
                "round": round_number,
                "turn": self._turn_number,
                "team": self._active_team,
                "tile": word,
                "tile_color": color,
                "guesser_team": self._active_team,
                "guesses_remaining": self._guesses_remaining,
                "scores": self._scores(state),
            },
            "narrative": self._reveal_narrative(gu, word, color),
        })

        if color == COLOR_ASSASSIN:
            events.append(self._victory_event(state, self._inactive_team(), reason="assassin"))
            self._game_over = True
            self._stage = STAGE_OVER
            return events

        if self._unrevealed_count(COLOR_BLUE) == 0:
            events.append(self._victory_event(state, COLOR_BLUE, reason="all_tiles_revealed"))
            self._game_over = True
            self._stage = STAGE_OVER
            return events
        if self._unrevealed_count(COLOR_YELLOW) == 0:
            events.append(self._victory_event(state, COLOR_YELLOW, reason="all_tiles_revealed"))
            self._game_over = True
            self._stage = STAGE_OVER
            return events

        if color != self._active_team or self._guesses_remaining <= 0:
            self._end_turn(events, state)
        return events

    def _reveal_narrative(self, actor: Any, word: str, color: str) -> str:
        prefix = f"{actor.name} ({self._active_team.title()} Guesser) reveals \"{word}\""
        if color == COLOR_ASSASSIN:
            return f"{prefix} — 💀 ASSASSIN. {self._active_team.title()} LOSES."
        if color == self._active_team:
            return f"{prefix} — ✓ {color.title()}. Keep guessing."
        if color == COLOR_NEUTRAL:
            return f"{prefix} — ◯ neutral. Turn ends."
        return f"{prefix} — ✗ {color.title()} (opponent). Turn ends."

    def _end_turn(self, events: List[Dict[str, Any]], state: Any) -> None:
        events.append({
            "event_type": "word_association_turn_end",
            "data": {
                "turn": self._turn_number,
                "ended_team": self._active_team,
                "guesses_used": self._guesses_made_this_turn,
                "scores": self._scores(state),
            },
            "narrative": (
                f"End of turn {self._turn_number}. "
                f"Blue {self._unrevealed_count(COLOR_BLUE)} left, "
                f"Yellow {self._unrevealed_count(COLOR_YELLOW)} left."
            ),
        })
        self._active_team = self._inactive_team()
        self._stage = STAGE_CLUE
        self._current_clue = None
        self._guesses_remaining = 0
        self._guesses_made_this_turn = 0
        self._turn_number += 1

    # ------------------------------------------------------------------
    # Victory
    # ------------------------------------------------------------------

    def _victory_event(
        self, state: Any, team: str, reason: str = "all_tiles_revealed"
    ) -> Dict[str, Any]:
        if team == COLOR_BLUE:
            winners = [self._blue_cluemaster_id, self._blue_guesser_id]
            losers = [self._yellow_cluemaster_id, self._yellow_guesser_id]
        else:
            winners = [self._yellow_cluemaster_id, self._yellow_guesser_id]
            losers = [self._blue_cluemaster_id, self._blue_guesser_id]
        narrative_reason = {
            "all_tiles_revealed": "revealed all their tiles",
            "assassin": "the opposing guesser hit the assassin",
            "timeout": "ended ahead on tiles at the turn cap",
        }.get(reason, reason.replace("_", " "))
        return {
            "event_type": f"{team}_victory",
            "narrative": f"{team.title()} team WINS — {narrative_reason}.",
            "data": {
                "winning_team": team,
                "winners": winners,
                "losers": losers,
                "reason": reason,
                "final_scores": self._scores(state),
                "board": self._full_board(),
            },
        }

    def _timeout_victory(self, state: Any) -> Dict[str, Any]:
        blue_left = self._unrevealed_count(COLOR_BLUE)
        yellow_left = self._unrevealed_count(COLOR_YELLOW)
        if blue_left < yellow_left:
            return self._victory_event(state, COLOR_BLUE, reason="timeout")
        if yellow_left < blue_left:
            return self._victory_event(state, COLOR_YELLOW, reason="timeout")
        # Tie → the team that started second wins (they had fewer tiles
        # to begin with, so a tied tile count is a stronger result).
        return self._victory_event(
            state, COLOR_YELLOW if self._blue_first else COLOR_BLUE, reason="timeout"
        )

    # ------------------------------------------------------------------
    # Action recording
    # ------------------------------------------------------------------

    def validate_action(
        self, action_name: str, actor: Any, target: Any, state: Any
    ) -> Optional[str]:
        if not actor or not getattr(actor, "alive", True):
            return "Dead/missing actor."
        if self._game_over:
            return "Game over."
        return None

    def post_resolution(
        self, actor_id: str, action_name: str, success: bool, result: Any, state: Any,
    ) -> List[Dict[str, Any]]:
        if action_name not in {"give_clue", "guess", "pass_turn", "wait"}:
            return []
        actor = state.get_entity(actor_id)
        if actor is None:
            return []
        round_number = state.temporal.current_round
        details = (result.details or {}) if result and getattr(result, "details", None) else {}
        params = details.get("_action_params", {}) or {}
        events: List[Dict[str, Any]] = []

        if action_name == "give_clue" and actor_id == self._active_cluemaster_id():
            word = str(params.get("word") or "").strip()
            count_raw = params.get("count") or params.get("number") or 1
            try:
                count = int(count_raw)
            except (TypeError, ValueError):
                count = 1
            if word:
                actor.set(f"_clue_word_r{round_number}", word)
                actor.set(f"_clue_count_r{round_number}", count)
                events.append({
                    "event_type": "word_association_clue_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "private_to": actor_id},
                    "narrative": f"{actor.name} prepares a clue (sealed).",
                })

        elif action_name == "guess" and actor_id == self._active_guesser_id():
            tile = str(params.get("tile") or params.get("word") or "").strip()
            if tile:
                actor.set(f"_guess_tile_r{round_number}", tile)
                events.append({
                    "event_type": "word_association_guess_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "tile": tile},
                    "narrative": f"{actor.name} eyes \"{tile}\".",
                })

        elif action_name == "pass_turn" and actor_id == self._active_guesser_id():
            actor.set(f"_pass_turn_r{round_number}", True)
            events.append({
                "event_type": "word_association_pass_intent",
                "actor_id": actor_id,
                "data": {"round": round_number},
                "narrative": f"{actor.name} signals to pass.",
            })

        return events

    # ------------------------------------------------------------------
    # Perception — enforces the cluemaster/guesser information wall.
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        ent = state.get_entity(entity_id)
        if ent is None:
            return {}
        my_team = (ent.get("team") or "").lower()
        my_role = (ent.get("role") or "").lower()
        is_cluemaster = my_role == ROLE_CLUEMASTER
        is_guesser = my_role == ROLE_GUESSER
        is_active_cluemaster = entity_id == self._active_cluemaster_id()
        is_active_guesser = entity_id == self._active_guesser_id()
        is_my_team_turn = my_team == self._active_team

        public_board = self._public_board()

        # CRITICAL — the information wall. Cluemasters see all colors;
        # Guessers only see revealed tiles + (during their turn) the
        # current clue.
        if is_cluemaster:
            full_board = self._full_board()
        else:
            full_board = public_board

        # Stage hint — tell the agent exactly what action to call.
        if self._stage == STAGE_OVER:
            stage_hint = "Game over."
        elif is_active_cluemaster:
            own_left = self._unrevealed_count(my_team)
            opp_left = self._unrevealed_count(self._inactive_team())
            stage_hint = (
                f"YOUR TURN — CLUE STAGE. You are the {my_team.title()} Cluemaster. "
                f"Look at `board_full` to see every tile's color. Find a one-word clue "
                f"that links as many of your {own_left} remaining {my_team.title()} "
                f"tiles as possible WITHOUT matching neutral, the {opp_left} opponent "
                f"tiles, or the ASSASSIN. Call `give_clue(word=\"…\", count=N)`. "
                f"Clue word must NOT be a substring of any unrevealed tile."
            )
        elif is_active_guesser:
            clue = self._current_clue or {}
            stage_hint = (
                f"YOUR TURN — GUESS STAGE. You are the {my_team.title()} Guesser. "
                f"Your cluemaster's clue: \"{clue.get('word','?')}\" for "
                f"{clue.get('count','?')}. You have {self._guesses_remaining} "
                f"guess(es) left. Look at `board_public` (you DO NOT see tile colors) "
                f"and call `guess(tile=\"WORD\")` to reveal a tile, or `pass_turn()` "
                f"to end early. Hitting your own color continues; neutral/opponent "
                f"ends your turn; ASSASSIN = instant loss."
            )
        elif is_cluemaster and is_my_team_turn:
            stage_hint = (
                f"It's your team's GUESS stage now. Wait while your guesser picks "
                f"tiles based on your clue. Call `wait()`."
            )
        elif is_guesser and is_my_team_turn:
            stage_hint = (
                f"It's your team's CLUE stage. Wait for your cluemaster to give a "
                f"clue. Call `wait()`."
            )
        else:
            stage_hint = (
                f"Opponent's turn ({self._active_team.title()}). Wait. Call `wait()`."
            )

        out: Dict[str, Any] = {
            "stage": self._stage,
            "turn": self._turn_number,
            "max_turns": self._max_turns,
            "your_team": my_team,
            "your_role": my_role,
            "active_team": self._active_team,
            "active_cluemaster_id": self._active_cluemaster_id(),
            "active_guesser_id": self._active_guesser_id(),
            "is_your_turn": is_active_cluemaster or is_active_guesser,
            "current_clue": dict(self._current_clue) if self._current_clue else None,
            "guesses_remaining": self._guesses_remaining,
            "board_public": public_board,
            "board_full": full_board,
            "scores": self._scores(state),
            "tiles_left": {
                "blue": self._unrevealed_count(COLOR_BLUE),
                "yellow": self._unrevealed_count(COLOR_YELLOW),
                "neutral": self._unrevealed_count(COLOR_NEUTRAL),
                "assassin": self._unrevealed_count(COLOR_ASSASSIN),
            },
            "stage_hint": stage_hint,
            "teammate_id": (
                self._blue_guesser_id if entity_id == self._blue_cluemaster_id
                else self._blue_cluemaster_id if entity_id == self._blue_guesser_id
                else self._yellow_guesser_id if entity_id == self._yellow_cluemaster_id
                else self._yellow_cluemaster_id if entity_id == self._yellow_guesser_id
                else ""
            ),
        }
        return out

    # ------------------------------------------------------------------
    # Board projection helpers
    # ------------------------------------------------------------------

    def _public_board(self) -> List[Dict[str, Any]]:
        """Board as seen by guessers — colors only shown for revealed tiles."""
        out: List[Dict[str, Any]] = []
        for tile in self._board:
            out.append({
                "word": tile["word"],
                "revealed": tile["revealed"],
                "color": tile["color"] if tile["revealed"] else None,
            })
        return out

    def _full_board(self) -> List[Dict[str, Any]]:
        """Board with ALL colors — for cluemaster perception and the post-game reveal."""
        return [dict(tile) for tile in self._board]

    def _scores(self, state: Any) -> Dict[str, int]:
        bc = state.get_entity(self._blue_cluemaster_id)
        yc = state.get_entity(self._yellow_cluemaster_id)
        return {
            "blue": int(bc.get("score") or 0) if bc else 0,
            "yellow": int(yc.get("score") or 0) if yc else 0,
            "blue_left": self._unrevealed_count(COLOR_BLUE),
            "yellow_left": self._unrevealed_count(COLOR_YELLOW),
        }
