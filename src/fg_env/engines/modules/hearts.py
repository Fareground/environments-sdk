"""Hearts (4-player) domain module.

Standard Hearts:
  - 4 players, single 52-card deck, 13 cards each.
  - GOAL: avoid penalty points. Each ♥ = 1 point, Q♠ = 13 points.
  - LOWEST cumulative score when someone hits the target wins.
  - Shoot the Moon: a player who takes ALL 26 penalty points in a
    single hand gives 26 points to EVERY other player instead.

Hand structure:
  1. PASSING: each player picks 3 cards to pass. Direction rotates per
     hand: left → right → across → keep (no pass) → repeat.
  2. TRICK PLAY: holder of 2♣ leads the first trick with the 2♣.
     Players follow clockwise. Must follow suit if possible.
     Constraints:
       - On the FIRST TRICK no hearts or Q♠ may be played.
       - Hearts cannot be LED until broken (someone played a heart on a
         trick where they couldn't follow the led suit).
     Trick winner = highest card of the led suit. Winner leads next.
  3. Score: count penalty points; check for moon shot.
  4. Next hand. Game ends when any player reaches `target_score`.
     Player with the LOWEST total wins.

Engine wiring:
  * Single phase "playing" with resolution_mode=simultaneous.
  * Module cycles stages internally:
      STAGE_PASS  → all 4 pick 3 cards (sealed simultaneous)
      STAGE_PLAY  → one active player plays a card per round; the
                    other seats have no action until their turn
      STAGE_OVER
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule
from fg_env.roles import Role

logger = logging.getLogger(__name__)


STAGE_PASS = "pass"
STAGE_PLAY = "play"
STAGE_OVER = "over"

SUITS = ["♣", "♦", "♥", "♠"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_ORDER = {r: i for i, r in enumerate(RANKS)}
DECK: List[str] = [r + s for s in SUITS for r in RANKS]
PASS_DIRECTIONS = ["left", "right", "across", "keep"]


def card_rank(c: str) -> int:
    return RANK_ORDER[c[:-1]]


def card_suit(c: str) -> str:
    return c[-1]


def card_points(c: str) -> int:
    if card_suit(c) == "♥":
        return 1
    if c == "Q♠":
        return 13
    return 0


class HeartsModule(DomainModule):
    """Drives a Hearts match."""

    def __init__(self, name: str = "hearts", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Player")
        self._rng = random.Random(p.get("seed", 0x4E47))
        self._target_score: int = int(p.get("target_score", 100))
        self._bootstrapped = False
        self._game_over = False

        # Match-level state.
        self._seat_order: List[str] = []
        self._scores: Dict[str, int] = {}            # cumulative penalty totals
        # Hand-level state.
        self._hand_num: int = 0
        self._pass_direction: str = "left"
        self._hands: Dict[str, List[str]] = {}       # player_id → card list
        self._hand_scores: Dict[str, int] = {}       # this hand's penalty pickup
        self._hearts_broken: bool = False
        # Pass stage.
        self._stage: str = STAGE_PASS
        # Trick state.
        self._trick_idx: int = 0                     # 0..12
        self._trick_leader_id: str = ""
        self._trick_plays: List[Tuple[str, str]] = []  # [(player_id, card)]
        self._active_player_id: str = ""
        # Round bookkeeping.
        self._last_round_resolved: int = 0

    @property
    def description(self) -> str:
        return ("Hearts: 4-player trick-taking, avoid the queen and the "
                "hearts; lowest score wins.")

    @property
    def custom_actions(self) -> List[str]:
        return ["pass_cards", "play_card", "discuss"]

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _alive_players(self, state: Any) -> List[Any]:
        return [e for e in state.entities.values()
                if e.entity_type == self._player_type and e.alive]

    def _ensure_setup(self, state: Any) -> None:
        if self._bootstrapped:
            return

        state.roles.define_role(Role(
            name="player", team="solo", sees_teammates=False,
            description=("You're playing Hearts. Avoid taking tricks "
                         "with hearts (1 point each) and the Queen of "
                         "Spades (13 points). Lowest total wins."),
        ))

        players = self._alive_players(state)
        n = len(players)
        if n != 4:
            logger.warning(f"Hearts: needs exactly 4 players, got {n}.")
            self._bootstrapped = True
            self._game_over = True
            return

        for p in players:
            state.roles.assign(p.id, "player")
            self._scores[p.id] = 0

        self._seat_order = [p.id for p in players]
        self._start_hand(state)
        self._bootstrapped = True

    # ------------------------------------------------------------------
    # Hand lifecycle
    # ------------------------------------------------------------------

    def _start_hand(self, state: Any) -> None:
        self._hand_num += 1
        self._pass_direction = PASS_DIRECTIONS[(self._hand_num - 1) % 4]
        self._hand_scores = {pid: 0 for pid in self._seat_order}
        self._hearts_broken = False
        self._trick_idx = 0
        self._trick_plays = []
        # Deal.
        deck = list(DECK)
        self._rng.shuffle(deck)
        for i, pid in enumerate(self._seat_order):
            self._hands[pid] = sorted(deck[i * 13:(i + 1) * 13], key=self._sort_key)
            ent = state.get_entity(pid)
            if ent:
                ent.set("hand", list(self._hands[pid]))
                ent.set("score", self._scores.get(pid, 0))
        if self._pass_direction == "keep":
            # No pass — go straight to play. 2♣ holder leads.
            self._stage = STAGE_PLAY
            self._set_first_leader_to_2_clubs()
        else:
            self._stage = STAGE_PASS

    def _sort_key(self, c: str) -> Tuple[int, int]:
        # Sort by suit then rank for a stable hand display.
        suit_order = {"♣": 0, "♦": 1, "♠": 2, "♥": 3}
        return (suit_order.get(card_suit(c), 4), card_rank(c))

    def _set_first_leader_to_2_clubs(self) -> None:
        for pid, hand in self._hands.items():
            if "2♣" in hand:
                self._trick_leader_id = pid
                self._active_player_id = pid
                return
        # Shouldn't happen, but fall back to seat 0.
        self._trick_leader_id = self._seat_order[0]
        self._active_player_id = self._seat_order[0]

    def _pass_target(self, sender_id: str) -> str:
        if self._pass_direction == "keep":
            return sender_id
        idx = self._seat_order.index(sender_id)
        n = len(self._seat_order)
        if self._pass_direction == "left":
            return self._seat_order[(idx + 1) % n]
        if self._pass_direction == "right":
            return self._seat_order[(idx - 1) % n]
        # across
        return self._seat_order[(idx + 2) % n]

    # ------------------------------------------------------------------
    # Action gating
    # ------------------------------------------------------------------

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str], state: Any) -> List[str]:
        if self._game_over:
            return []
        ent = state.get_entity(entity_id)
        if not ent or not ent.alive:
            return []

        if self._stage == STAGE_PASS:
            return [a for a in valid_actions if a in ("pass_cards", "discuss")]

        if self._stage == STAGE_PLAY:
            # Only the seat on turn acts. Waiting seats would otherwise each
            # spend a model call per card on table talk nobody asked for.
            if entity_id == self._active_player_id:
                return [a for a in valid_actions if a in ("play_card", "discuss")]
            return []

        return []

    # ------------------------------------------------------------------
    # Tick — drive the hand forward
    # ------------------------------------------------------------------

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._ensure_setup(state)
        if self._game_over:
            return []

        events: List[Dict[str, Any]] = []

        if round_number > 1 and round_number - 1 > self._last_round_resolved:
            prev = round_number - 1
            played = self._stage
            if played == STAGE_PASS:
                events.extend(self._resolve_pass(state, prev))
            elif played == STAGE_PLAY:
                events.extend(self._resolve_play(state, prev))
            self._last_round_resolved = prev
            if self._game_over:
                return events

        events.append({
            "event_type": "hearts_stage",
            "narrative": self._stage_narrative(state),
            "data": {
                "stage": self._stage,
                "hand": self._hand_num,
                "pass_direction": self._pass_direction,
                "trick_idx": self._trick_idx,
                "trick_leader_id": self._trick_leader_id,
                "active_player_id": self._active_player_id,
                "hearts_broken": self._hearts_broken,
                "scores": dict(self._scores),
                "round": round_number,
            },
        })
        return events

    def _stage_narrative(self, state: Any) -> str:
        if self._stage == STAGE_PASS:
            return (f"Hand {self._hand_num} — pass 3 cards "
                    f"({self._pass_direction}).")
        if self._stage == STAGE_PLAY:
            ent = state.get_entity(self._active_player_id)
            active = ent.name if ent else self._active_player_id
            return (f"Hand {self._hand_num}, trick "
                    f"{self._trick_idx + 1}/13 — {active}'s turn.")
        return ""

    # ------------------------------------------------------------------
    # Pass resolution
    # ------------------------------------------------------------------

    def _resolve_pass(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        # Collect passes; fill missing ones with a deterministic default
        # (first three cards in the player's hand).
        passes: Dict[str, List[str]] = {}
        for pid in self._seat_order:
            p = state.get_entity(pid)
            chosen = p.get(f"_pass_r{round_number}") if p else None
            if not isinstance(chosen, list):
                chosen = []
            chosen = [c for c in chosen if isinstance(c, str) and c in self._hands.get(pid, [])]
            if len(chosen) < 3:
                # Auto-fill from sorted hand (last 3 — usually high cards
                # which is the default heuristic in Hearts).
                hand = list(self._hands.get(pid, []))
                for c in reversed(hand):
                    if c not in chosen:
                        chosen.append(c)
                    if len(chosen) >= 3:
                        break
            chosen = chosen[:3]
            passes[pid] = chosen

        # Apply: remove from senders, give to recipients.
        # First collect the transfers, then mutate hands atomically.
        incoming: Dict[str, List[str]] = {pid: [] for pid in self._seat_order}
        for sender, cards in passes.items():
            target = self._pass_target(sender)
            for c in cards:
                if c in self._hands[sender]:
                    self._hands[sender].remove(c)
                incoming[target].append(c)
        for pid in self._seat_order:
            self._hands[pid].extend(incoming[pid])
            self._hands[pid].sort(key=self._sort_key)
            ent = state.get_entity(pid)
            if ent:
                ent.set("hand", list(self._hands[pid]))

        # Public reveal — keep specific cards private (sealed), just
        # announce direction + count for the timeline. Each recipient
        # privately sees their new hand via the entity prop.
        events.append({
            "event_type": "hearts_pass_resolved",
            "data": {
                "round": round_number,
                "direction": self._pass_direction,
                "hand": self._hand_num,
            },
            "narrative": f"Pass complete ({self._pass_direction}).",
        })

        # Transition to play; 2♣ holder leads.
        self._set_first_leader_to_2_clubs()
        self._stage = STAGE_PLAY
        return events

    # ------------------------------------------------------------------
    # Play resolution
    # ------------------------------------------------------------------

    def _resolve_play(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        actor = state.get_entity(self._active_player_id)
        if not actor:
            return events
        raw = actor.get(f"_play_r{round_number}")
        valid_choices = self._legal_plays(self._active_player_id)
        chosen = raw if (isinstance(raw, str) and raw in valid_choices) else None
        if chosen is None:
            # Auto-pick the first legal play (lowest by sort).
            chosen = valid_choices[0] if valid_choices else None
        if chosen is None:
            # Can't play — shouldn't happen unless hand is empty.
            self._active_player_id = self._next_player(self._active_player_id)
            return events

        # Remove from hand; record in trick.
        self._hands[self._active_player_id].remove(chosen)
        actor.set("hand", list(self._hands[self._active_player_id]))
        self._trick_plays.append((self._active_player_id, chosen))
        if card_suit(chosen) == "♥" and not self._hearts_broken:
            # Hearts get broken when a player CAN'T follow led suit and
            # discards a heart. We approximate by flagging any heart
            # played that wasn't the lead card.
            if self._trick_plays and self._trick_plays[0][1] != chosen:
                self._hearts_broken = True
            elif card_suit(self._trick_plays[0][1]) == "♥":
                self._hearts_broken = True
        events.append({
            "event_type": "hearts_play",
            "actor_id": self._active_player_id,
            "data": {
                "round": round_number,
                "card": chosen,
                "trick_idx": self._trick_idx,
                "hand": self._hand_num,
                "trick_so_far": [
                    {"player": pid, "card": c} for pid, c in self._trick_plays
                ],
                "hearts_broken": self._hearts_broken,
            },
            "narrative": f"{actor.name} plays {chosen}.",
        })

        # Trick complete? (4 cards played)
        if len(self._trick_plays) >= 4:
            winner_id, winner_card = self._trick_winner()
            trick_points = sum(card_points(c) for _, c in self._trick_plays)
            self._hand_scores[winner_id] = self._hand_scores.get(winner_id, 0) + trick_points
            events.append({
                "event_type": "hearts_trick_resolved",
                "data": {
                    "round": round_number,
                    "trick_idx": self._trick_idx,
                    "hand": self._hand_num,
                    "winner_id": winner_id,
                    "winning_card": winner_card,
                    "plays": [{"player": pid, "card": c} for pid, c in self._trick_plays],
                    "points": trick_points,
                    "hand_scores": dict(self._hand_scores),
                },
                "narrative": (
                    f"Trick {self._trick_idx + 1}: "
                    f"{state.get_entity(winner_id).name if state.get_entity(winner_id) else winner_id} "
                    f"wins with {winner_card} (+{trick_points} pts)."
                ),
            })
            self._trick_idx += 1
            self._trick_plays = []
            self._trick_leader_id = winner_id
            self._active_player_id = winner_id
            # All 13 tricks done → score hand.
            if self._trick_idx >= 13:
                events.extend(self._end_hand(state))
                return events
        else:
            self._active_player_id = self._next_player(self._active_player_id)
        return events

    def _legal_plays(self, pid: str) -> List[str]:
        hand = list(self._hands.get(pid, []))
        if not hand:
            return []
        is_leading = len(self._trick_plays) == 0
        is_first_trick = self._trick_idx == 0
        if is_leading:
            if is_first_trick:
                # Must lead 2♣.
                return ["2♣"] if "2♣" in hand else hand[:1]
            # Otherwise: can't lead hearts until broken (unless only
            # hearts left).
            if not self._hearts_broken:
                non_hearts = [c for c in hand if card_suit(c) != "♥"]
                if non_hearts:
                    return sorted(non_hearts, key=self._sort_key)
            return sorted(hand, key=self._sort_key)
        # Following a led suit.
        led_suit = card_suit(self._trick_plays[0][1])
        same_suit = [c for c in hand if card_suit(c) == led_suit]
        if same_suit:
            return sorted(same_suit, key=self._sort_key)
        # Can't follow — discard. First trick: no hearts or Q♠.
        if is_first_trick:
            safe = [c for c in hand if card_suit(c) != "♥" and c != "Q♠"]
            if safe:
                return sorted(safe, key=self._sort_key)
        return sorted(hand, key=self._sort_key)

    def _trick_winner(self) -> Tuple[str, str]:
        led_suit = card_suit(self._trick_plays[0][1])
        winning_pid, winning_card = self._trick_plays[0]
        for pid, c in self._trick_plays[1:]:
            if card_suit(c) == led_suit and card_rank(c) > card_rank(winning_card):
                winning_pid, winning_card = pid, c
        return winning_pid, winning_card

    def _next_player(self, pid: str) -> str:
        idx = self._seat_order.index(pid)
        return self._seat_order[(idx + 1) % len(self._seat_order)]

    # ------------------------------------------------------------------
    # End of hand / game
    # ------------------------------------------------------------------

    def _end_hand(self, state: Any) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        # Moon shot: did one player take all 26 penalty points?
        moon_id: Optional[str] = None
        for pid, pts in self._hand_scores.items():
            if pts == 26:
                moon_id = pid
                break
        if moon_id is not None:
            # Shooter gets 0; others get 26.
            for pid in self._seat_order:
                if pid == moon_id:
                    pass  # 0 points this hand
                else:
                    self._scores[pid] += 26
            events.append({
                "event_type": "hearts_moon_shot",
                "actor_id": moon_id,
                "data": {
                    "shooter_id": moon_id,
                    "hand": self._hand_num,
                    "hand_scores": dict(self._hand_scores),
                },
                "narrative": (
                    f"{state.get_entity(moon_id).name if state.get_entity(moon_id) else moon_id} "
                    "SHOT THE MOON — all other players take 26 points."
                ),
            })
        else:
            for pid, pts in self._hand_scores.items():
                self._scores[pid] += pts

        # Persist scores to entities.
        for pid in self._seat_order:
            ent = state.get_entity(pid)
            if ent:
                ent.set("score", self._scores[pid])

        events.append({
            "event_type": "hearts_hand_resolved",
            "data": {
                "hand": self._hand_num,
                "hand_scores": dict(self._hand_scores),
                "totals": dict(self._scores),
                "moon_id": moon_id,
            },
            "narrative": (f"Hand {self._hand_num} done. "
                          f"Totals: {self._format_totals(state)}"),
        })

        # Game over?
        max_score = max(self._scores.values())
        if max_score >= self._target_score:
            winners = [pid for pid, s in self._scores.items()
                       if s == min(self._scores.values())]
            events.append({
                "event_type": "hearts_match_over",
                "data": {
                    "winning_team": "solo",
                    "winners": winners,
                    "scores": dict(self._scores),
                    "target_score": self._target_score,
                },
                "narrative": (f"Match over — "
                              f"{state.get_entity(winners[0]).name if winners and state.get_entity(winners[0]) else '?'} "
                              f"wins with {self._scores.get(winners[0], 0) if winners else '?'} points."),
            })
            self._game_over = True
            self._stage = STAGE_OVER
            return events

        # Next hand.
        self._start_hand(state)
        return events

    def _format_totals(self, state: Any) -> str:
        parts = []
        for pid in self._seat_order:
            ent = state.get_entity(pid)
            parts.append(f"{ent.name if ent else pid}={self._scores[pid]}")
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Action recording
    # ------------------------------------------------------------------

    def validate_action(self, action_name: str, actor: Any, target: Any, state: Any) -> Optional[str]:
        if not actor or not getattr(actor, "alive", True):
            return "Dead/missing actor."
        if self._game_over:
            return "Game over."
        return None

    def post_resolution(
        self, actor_id: str, action_name: str, success: bool, result: Any, state: Any,
    ) -> List[Dict[str, Any]]:
        if action_name not in self.custom_actions:
            return []
        actor = state.get_entity(actor_id)
        if actor is None:
            return []
        round_number = state.temporal.current_round
        details = (result.details or {}) if result and getattr(result, "details", None) else {}
        params = details.get("_action_params", {}) or {}
        events: List[Dict[str, Any]] = []

        if action_name == "pass_cards":
            raw = params.get("cards") or details.get("cards") or []
            if isinstance(raw, str):
                # comma- or space-separated string fallback.
                raw = [c.strip() for c in raw.replace(",", " ").split() if c.strip()]
            if isinstance(raw, list):
                normalized = []
                hand = self._hands.get(actor_id, [])
                for c in raw:
                    if isinstance(c, str) and c in hand and c not in normalized:
                        normalized.append(c)
                    if len(normalized) >= 3:
                        break
                if len(normalized) == 3:
                    actor.set(f"_pass_r{round_number}", normalized)
                    events.append({
                        "event_type": "hearts_pass_intent",
                        "actor_id": actor_id,
                        "data": {"round": round_number, "private_to": actor_id},
                        "narrative": f"{actor.name} locks in 3 cards to pass.",
                    })

        elif action_name == "play_card":
            raw = params.get("card") or details.get("card")
            if isinstance(raw, str):
                raw = raw.strip()
                hand = self._hands.get(actor_id, [])
                # Tolerate slight variants ("10S" → "10♠").
                normalized = self._normalize_card(raw, hand)
                if normalized:
                    actor.set(f"_play_r{round_number}", normalized)
                    events.append({
                        "event_type": "hearts_play_intent",
                        "actor_id": actor_id,
                        "data": {"round": round_number, "card": normalized},
                        "narrative": f"{actor.name} commits a card.",
                    })

        return events

    def _normalize_card(self, raw: str, hand: List[str]) -> Optional[str]:
        if raw in hand:
            return raw
        # Try suit-letter variants.
        suit_letter_map = {"C": "♣", "D": "♦", "H": "♥", "S": "♠"}
        # Strip parens / quotes.
        cleaned = raw.strip(" \"'()[]{}")
        if len(cleaned) >= 2:
            tail = cleaned[-1].upper()
            head = cleaned[:-1].upper()
            if tail in suit_letter_map:
                # Normalize rank ("T" → "10", "J/Q/K/A" stays).
                if head == "T":
                    head = "10"
                cand = f"{head}{suit_letter_map[tail]}"
                if cand in hand:
                    return cand
        return None

    # ------------------------------------------------------------------
    # Perception — surface the player's hand + legal plays
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        ent = state.get_entity(entity_id)
        if ent is None:
            return {}
        hand = list(self._hands.get(entity_id, []))
        seat_idx = self._seat_order.index(entity_id) if entity_id in self._seat_order else -1
        is_active = entity_id == self._active_player_id

        if self._stage == STAGE_PASS:
            stage_hint = (
                f'Pass 3 cards ({self._pass_direction}). Use '
                f'pass_cards(cards=["X","Y","Z"]) — exact strings from your hand. '
                "Convention: pass high spades + your worst suit, or set up a moon shot."
            )
        elif self._stage == STAGE_PLAY and is_active:
            legal = self._legal_plays(entity_id)
            example = legal[0] if legal else ""
            stage_hint = (
                f'Your turn. Play one card with play_card(card="{example}"). '
                'Legal plays only — must follow led suit if possible. '
                f'Hearts {"BROKEN" if self._hearts_broken else "not yet broken"}.'
            )
        else:
            stage_hint = ""

        out: Dict[str, Any] = {
            "stage": self._stage,
            "hand_number": self._hand_num,
            "pass_direction": self._pass_direction,
            "trick_idx": self._trick_idx,
            "trick_plays": [{"player": pid, "card": c} for pid, c in self._trick_plays],
            "trick_leader_id": self._trick_leader_id,
            "active_player_id": self._active_player_id,
            "i_am_active": is_active,
            "my_seat": seat_idx,
            "my_hand": hand,
            "hearts_broken": self._hearts_broken,
            "scores": dict(self._scores),
            "hand_scores": dict(self._hand_scores),
            "seat_order": list(self._seat_order),
            "target_score": self._target_score,
            "stage_hint": stage_hint,
        }
        if self._stage == STAGE_PLAY and is_active:
            out["legal_plays"] = self._legal_plays(entity_id)
        return out
