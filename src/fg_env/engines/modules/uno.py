"""Uno domain module.

Card encoding (compact strings):
  Colors:   R (red), G (green), B (blue), Y (yellow), W (wild)
  Values:   0-9, S (skip), V (reverse), D (draw two), W (wild), F (wild +4)
  Examples: "R7", "BS", "GV", "YD", "WW", "WF"

Deck (108 cards):
  - 4 colors × (one 0 + two each of 1-9 + two each S/V/D) = 25 × 4 = 100
  - 4 Wild (WW) + 4 Wild Draw Four (WF) = 8

Stacking (optional house rule, off by default):
  When `stacking=True`, a player hit with a Draw Two or Wild Draw Four
  may respond with their own matching draw card to pass the penalty
  to the next player. The penalty count accumulates (+2 → +4 → +6 → …
  for D-chains, +4 → +8 → +12 → … for F-chains). When the chain
  breaks (target can't or won't stack), the target draws the total
  and is skipped. Strict house rule: D stacks on D only, F stacks on
  F only — no cross-stacking.

Rules (official Mattel, no stacking):
  - Deal 7 cards each. Flip top of deck to start discard.
    If flip is Wild: re-flip until a colored card. If flip is action:
    apply effect to first player (Skip/Reverse/Draw 2 immediately).
  - Turn order: clockwise; Reverse flips direction.
  - To play: card must match current COLOR or VALUE, or be a Wild.
  - Wild Draw Four (WF) is only legal if the player holds NO card
    matching the current color. (Bluffing & challenges are skipped
    for simplicity — we just enforce the legality check.)
  - Action effects:
      Skip (S):       next player skipped
      Reverse (V):    direction flips. 2p: acts like Skip.
      Draw Two (D):   next player draws 2 + skipped
      Wild (W):       player declares new color
      Wild +4 (F):    next player draws 4 + skipped; player declares color
  - If a player can't play: they draw 1 from the deck. If the drawn
    card is playable, they MAY play it immediately. Otherwise the
    turn ends.
  - Hand ends when someone plays their last card.

Scoring:
  - Number cards: face value (0-9)
  - Skip/Reverse/Draw Two: 20 each
  - Wild / Wild Draw Four: 50 each
  - Winner of the hand scores the SUM of all OTHER players'
    remaining card values.

Match termination:
  - Single hand only. First player to empty their hand wins the match.
    No point race, no best-of-N — one game, one winner.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


STAGE_PLAY = "play"
STAGE_COLOR = "color"   # waiting for a Wild player to declare color
STAGE_OVER = "over"

COLORS = ["R", "G", "B", "Y"]
NUMBERS = [str(n) for n in range(10)]
ACTIONS = ["S", "V", "D"]   # Skip, reVerse, Draw 2

# Build the standard 108-card deck.
def _build_deck() -> List[str]:
    deck: List[str] = []
    for c in COLORS:
        deck.append(c + "0")  # one zero per color
        for n in range(1, 10):
            deck.append(c + str(n))
            deck.append(c + str(n))
        for a in ACTIONS:
            deck.append(c + a)
            deck.append(c + a)
    deck.extend(["WW"] * 4)
    deck.extend(["WF"] * 4)
    return deck


def card_color(card: str) -> str:
    return card[0]


def card_value(card: str) -> str:
    return card[1:]


def is_wild(card: str) -> bool:
    return card[0] == "W"


def card_score(card: str) -> int:
    v = card_value(card)
    if v.isdigit():
        return int(v)
    if v in ("S", "V", "D"):
        return 20
    return 50  # W or F


class UnoModule(DomainModule):
    """Drives an Uno match."""

    @property
    def description(self) -> str:
        return ("Uno: 2-6 players race to empty their hand. Match the "
                "top card's color or value, play action cards (Skip, "
                "Reverse, Draw Two), or play a Wild to dictate the "
                "color. First to play their last card wins.")

    @property
    def custom_actions(self) -> List[str]:
        return ["play_card", "draw_card", "choose_color", "discuss"]

    def __init__(self, name: str = "uno", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Player")
        self._rng = random.Random(p.get("seed", 0x0571A1))
        self._stacking_enabled: bool = bool(p.get("stacking", False))
        self._bootstrapped = False
        self._game_over = False
        # Active stacking chain (only meaningful when stacking_enabled):
        #   _stack_value: "D" or "F" — the card type being stacked
        #   _stack_count: cumulative cards the chain-ender will draw
        self._stack_value: Optional[str] = None
        self._stack_count: int = 0

        # Match-level
        self._seat_order: List[str] = []
        self._scores: Dict[str, int] = {}
        self._hands_won: Dict[str, int] = {}
        # Hand-level
        self._hand_num: int = 0
        self._hands: Dict[str, List[str]] = {}
        self._draw_pile: List[str] = []
        self._discard_top: Optional[str] = None
        self._discard_color: str = "R"   # effective color (Wilds resolve to a chosen color)
        self._direction: int = 1          # +1 = clockwise (forward in seat_order), -1 = ccw
        self._active_idx: int = 0
        self._stage: str = STAGE_PLAY
        self._pending_color_pid: Optional[str] = None  # who needs to pick color
        self._last_round_resolved: int = 0
        # When the player drew a card this turn already (so they can't draw twice).
        self._has_drawn_this_turn: bool = False
        # Set True by _resolve_round whenever it actually processed an
        # action. tick() reads it to decide whether to advance the
        # resolved-watermark.
        self._round_did_resolve: bool = False

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _alive_players(self, state: Any) -> List[Any]:
        return [e for e in state.entities.values()
                if e.entity_type == self._player_type and e.alive]

    def _ensure_setup(self, state: Any) -> None:
        if self._bootstrapped:
            return
        players = self._alive_players(state)
        n = len(players)
        if n < 2 or n > 6:
            logger.warning(f"Uno: needs 2-6 players, got {n}.")
            self._bootstrapped = True
            self._game_over = True
            return
        self._seat_order = [p.id for p in players]
        self._scores = {pid: 0 for pid in self._seat_order}
        self._hands_won = {pid: 0 for pid in self._seat_order}
        self._start_hand(state)
        self._bootstrapped = True

    # ------------------------------------------------------------------
    # Hand lifecycle
    # ------------------------------------------------------------------

    def _start_hand(self, state: Any) -> None:
        self._hand_num += 1
        self._hands = {pid: [] for pid in self._seat_order}
        self._direction = 1
        self._active_idx = 0
        self._stage = STAGE_PLAY
        self._pending_color_pid = None
        self._has_drawn_this_turn = False

        # Shuffle + deal
        deck = _build_deck()
        self._rng.shuffle(deck)
        for pid in self._seat_order:
            self._hands[pid] = [deck.pop() for _ in range(7)]

        # Flip a starter card. Re-flip wilds; honor first-card action effect.
        while True:
            top = deck.pop()
            if not is_wild(top):
                break
            deck.insert(0, top)  # bury it at bottom and try again
        self._discard_top = top
        self._discard_color = card_color(top)
        self._draw_pile = deck

        # First-card action effect (per official rules).
        v = card_value(top)
        if v == "S":
            self._advance(skip=True)
        elif v == "V":
            self._direction *= -1
            if len(self._seat_order) == 2:
                # 2-player reverse = skip
                self._advance(skip=True)
        elif v == "D":
            self._force_draw(self._seat_order[self._active_idx], 2)
            self._advance(skip=True)

        # Update public entity properties.
        self._sync_entities(state)

    def _sync_entities(self, state: Any) -> None:
        for pid in self._seat_order:
            ent = state.get_entity(pid)
            if not ent:
                continue
            hand = self._hands.get(pid, [])
            ent.set("hand", list(hand))
            ent.set("hand_count", len(hand))
            ent.set("score", self._scores.get(pid, 0))
            ent.set("hands_won", self._hands_won.get(pid, 0))
            ent.set("called_uno", len(hand) == 1)

    # ------------------------------------------------------------------
    # Turn flow helpers
    # ------------------------------------------------------------------

    def _next_idx(self, idx: int, steps: int = 1) -> int:
        n = len(self._seat_order)
        return (idx + self._direction * steps) % n

    def _advance(self, skip: bool = False) -> None:
        self._active_idx = self._next_idx(self._active_idx, 2 if skip else 1)
        self._has_drawn_this_turn = False

    def _force_draw(self, pid: str, n: int) -> None:
        for _ in range(n):
            card = self._draw_one()
            if card is None:
                return
            self._hands[pid].append(card)

    def _draw_one(self) -> Optional[str]:
        if not self._draw_pile:
            # Reshuffle the discard pile (minus top) back in.
            if not self._discard_top:
                return None
            # No history of all discards beyond top in this simplified
            # impl — if we run out, the hand just ends in a draw (rare).
            return None
        return self._draw_pile.pop()

    @property
    def _active_pid(self) -> Optional[str]:
        if not self._seat_order:
            return None
        return self._seat_order[self._active_idx]

    def _pname(self, state: Any, pid: Optional[str]) -> str:
        """Resolve an entity id to its human-readable name for narratives.
        Falls back to the id itself if the entity isn't found / no name."""
        if not pid or state is None:
            return pid or "?"
        ent = state.get_entity(pid)
        return getattr(ent, "name", None) or pid

    # ------------------------------------------------------------------
    # Legality
    # ------------------------------------------------------------------

    def _legal_plays(self, pid: str) -> List[str]:
        if self._stage != STAGE_PLAY:
            return []
        hand = self._hands.get(pid, [])
        # If a stacking chain is active, the ONLY legal play is a
        # matching stacker. Anything else means the player must draw
        # the accumulated penalty.
        if self._stack_value is not None:
            return [c for c in hand if card_value(c) == self._stack_value]
        legal: List[str] = []
        has_color_match = any(
            (not is_wild(c)) and card_color(c) == self._discard_color
            for c in hand
        )
        for c in hand:
            if is_wild(c):
                # Wild Draw Four only legal if no color match exists.
                if card_value(c) == "F" and has_color_match:
                    continue
                legal.append(c)
            elif card_color(c) == self._discard_color:
                legal.append(c)
            elif self._discard_top is not None and card_value(c) == card_value(self._discard_top) and not is_wild(self._discard_top):
                # Same value/symbol play allowed across colors.
                legal.append(c)
        return legal

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._ensure_setup(state)
        if self._game_over:
            return []

        events: List[Dict[str, Any]] = []
        if round_number > 1 and round_number - 1 > self._last_round_resolved:
            prev = round_number - 1
            self._round_did_resolve = False
            events.extend(self._resolve_round(state, prev))
            # Only advance the resolved-watermark when we actually
            # processed something. If the active player hadn't committed
            # an action yet, leave `prev` un-resolved so the next tick
            # tries again instead of silently skipping ahead.
            if self._round_did_resolve:
                self._last_round_resolved = prev
            if self._game_over:
                return events

        events.append({
            "event_type": "uno_stage",
            "narrative": self._stage_narrative(state),
            "data": {
                "stage": self._stage,
                "hand": self._hand_num,
                "active_player_id": self._active_pid,
                "discard_top": self._discard_top,
                "discard_color": self._discard_color,
                "direction": self._direction,
                "draw_pile_size": len(self._draw_pile),
                "hand_counts": {pid: len(self._hands.get(pid, [])) for pid in self._seat_order},
                "scores": dict(self._scores),
                "hands_won": dict(self._hands_won),
                "pending_color_pid": self._pending_color_pid,
                "stacking_enabled": self._stacking_enabled,
                "stack_value": self._stack_value,
                "stack_count": self._stack_count,
                "round": round_number,
            },
        })
        return events

    def _stage_narrative(self, state: Any) -> str:
        if self._stage == STAGE_PLAY:
            return (f"Hand {self._hand_num} — {self._pname(state, self._active_pid)}'s turn. "
                    f"Top: {self._discard_top} (color {self._discard_color}).")
        if self._stage == STAGE_COLOR:
            return f"{self._pname(state, self._pending_color_pid)} declares the new color."
        return ""

    # ------------------------------------------------------------------
    # Action resolution
    # ------------------------------------------------------------------

    def _resolve_round(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        # Whose turn was it last round? — the active player, or the
        # pending-color picker if we were mid-Wild.
        actor_pid = (self._pending_color_pid
                     if self._stage == STAGE_COLOR
                     else self._active_pid)
        if not actor_pid:
            return events
        actor = state.get_entity(actor_pid)
        if actor is None:
            return events

        # Read whatever the engine stashed for this player last round.
        # `post_resolution` writes one of these three keys:
        #   _play_r{N}  → string card to play
        #   _draw_r{N}  → truthy when the player chose to draw
        #   _color_r{N} → string color for the pending Wild
        play_card = actor.get(f"_play_r{round_number}")
        drew = actor.get(f"_draw_r{round_number}")
        color_pick = actor.get(f"_color_r{round_number}")

        if self._stage == STAGE_COLOR:
            # Only proceed if the picker actually committed a color.
            # Otherwise wait, same as in STAGE_PLAY below.
            if color_pick:
                color = str(color_pick).upper()
                events.extend(self._do_choose_color(state, actor_pid, color))
                self._round_did_resolve = True
            return events

        if isinstance(play_card, str) and play_card:
            events.extend(self._do_play(state, actor_pid, play_card.upper()))
            self._round_did_resolve = True
        elif drew:
            # ONLY draw when the player explicitly chose draw_card.
            # Previously we also auto-drew when no action was committed,
            # which meant a slow-to-respond agent (or a human still
            # thinking) got a card every cycle through the table — a
            # player with UNO could end up with 9 cards. Now we wait.
            events.extend(self._do_draw(state, actor_pid))
            self._round_did_resolve = True
        # Otherwise: no-op. `_round_did_resolve` stays False so the
        # caller leaves `_last_round_resolved` un-advanced and we'll
        # re-examine this round on the next tick.
        return events

    # ------------------------------------------------------------------
    # Engine action hooks
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
        """Stash each player's committed action as a per-round entity
        property so `_resolve_round` (next tick) can act on it. Mirrors
        Spades/Hearts' approach.
        """
        if action_name not in self.custom_actions:
            return []
        actor = state.get_entity(actor_id)
        if actor is None:
            return []
        round_number = state.temporal.current_round
        details = (result.details or {}) if result and getattr(result, "details", None) else {}
        params = details.get("_action_params", {}) or {}
        events: List[Dict[str, Any]] = []

        if action_name == "play_card":
            raw = params.get("card") or details.get("card")
            if isinstance(raw, str) and raw.strip():
                actor.set(f"_play_r{round_number}", raw.strip().upper())
                events.append({
                    "event_type": "uno_play_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "card": raw.strip().upper()},
                    "narrative": f"{actor.name} commits a card.",
                })
        elif action_name == "draw_card":
            actor.set(f"_draw_r{round_number}", True)
            events.append({
                "event_type": "uno_draw_intent",
                "actor_id": actor_id,
                "data": {"round": round_number},
                "narrative": f"{actor.name} will draw.",
            })
        elif action_name == "choose_color":
            raw = params.get("color") or details.get("color")
            if isinstance(raw, str) and raw.strip():
                color = raw.strip().upper()[:1]
                if color not in COLORS:
                    color = "R"
                actor.set(f"_color_r{round_number}", color)
                events.append({
                    "event_type": "uno_color_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "color": color},
                    "narrative": f"{actor.name} picks color {color}.",
                })
        # `discuss` is a chat-only action — no state to stash.
        return events

    def _do_play(self, state: Any, pid: str, card: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        legal = self._legal_plays(pid)
        if card not in legal or card not in self._hands.get(pid, []):
            # Illegal — force a draw instead.
            return self._do_draw(state, pid)

        self._hands[pid].remove(card)
        self._discard_top = card
        if not is_wild(card):
            self._discard_color = card_color(card)
        events.append({
            "event_type": "uno_play",
            "actor_id": pid,
            "narrative": f"{self._pname(state, pid)} plays {card}.",
            "data": {
                "card": card,
                "remaining_in_hand": len(self._hands[pid]),
                "is_uno_call": len(self._hands[pid]) == 1,
            },
        })

        # Check hand-end immediately.
        if not self._hands[pid]:
            events.extend(self._end_hand(state, winner_pid=pid))
            return events

        # Apply effects.
        v = card_value(card)
        n = len(self._seat_order)
        if v == "S":
            self._advance(skip=True)
        elif v == "V":
            self._direction *= -1
            if n == 2:
                self._advance(skip=True)
            else:
                self._advance(skip=False)
        elif v == "D":
            if self._stacking_enabled:
                # Open or extend a +2 stacking chain. Hand passes to the
                # next player, who must stack or draw the running total.
                self._stack_value = "D"
                self._stack_count += 2
                self._advance(skip=False)
            else:
                target_pid = self._seat_order[self._next_idx(self._active_idx, 1)]
                self._force_draw(target_pid, 2)
                self._advance(skip=True)
        elif v == "W":
            # Wait for choose_color.
            self._stage = STAGE_COLOR
            self._pending_color_pid = pid
        elif v == "F":
            if self._stacking_enabled:
                # Open or extend a +4 stacking chain. Player still
                # declares the color via choose_color. The next player
                # gets the option to stack another +4 OR eat the chain.
                self._stack_value = "F"
                self._stack_count += 4
                self._stage = STAGE_COLOR
                self._pending_color_pid = pid
            else:
                target_pid = self._seat_order[self._next_idx(self._active_idx, 1)]
                self._force_draw(target_pid, 4)
                self._stage = STAGE_COLOR
                self._pending_color_pid = pid
                # The +4 also skips the next player, but we apply the skip
                # AFTER color is declared (in _do_choose_color).
        else:
            # Number card — just advance.
            self._advance(skip=False)
        self._sync_entities(state)
        return events

    def _do_draw(self, state: Any, pid: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        # Stacking chain is breaking on this player — they eat the
        # accumulated draw total. Eating IS their turn (they don't get
        # to play a card after), so control passes to the NEXT player
        # via a normal advance (skip=False). Earlier this used
        # skip=True, which incorrectly skipped the player after the
        # eater too — letting someone else slip an extra turn.
        if self._stack_value is not None and self._stack_count > 0:
            n = self._stack_count
            self._force_draw(pid, n)
            events.append({
                "event_type": "uno_stack_eaten",
                "actor_id": pid,
                "narrative": f"{self._pname(state, pid)} eats the {self._stack_value}-chain: draws {n}.",
                "data": {"drew": n, "stack_value": self._stack_value},
            })
            self._stack_value = None
            self._stack_count = 0
            self._advance(skip=False)
            self._sync_entities(state)
            return events
        card = self._draw_one()
        if card is None:
            # Empty deck — hand stalls into a stalemate.
            events.extend(self._end_hand(state, winner_pid=None))
            return events
        self._hands[pid].append(card)
        self._has_drawn_this_turn = True
        events.append({
            "event_type": "uno_draw",
            "actor_id": pid,
            "narrative": f"{self._pname(state, pid)} draws a card.",
            "data": {"hand_count": len(self._hands[pid])},
        })
        # Official rules: if drawn card is playable, may play it immediately.
        # We auto-play it for simplicity (keeps the engine moving without
        # an extra turn-within-turn).
        legal_after = self._legal_plays(pid)
        if card in legal_after:
            events.extend(self._do_play(state, pid, card))
        else:
            self._advance(skip=False)
            self._sync_entities(state)
        return events

    def _do_choose_color(self, state: Any, pid: str, color: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if color not in COLORS:
            color = "R"
        self._discard_color = color
        was_plus_four = (self._discard_top and card_value(self._discard_top) == "F")
        in_stack_chain = self._stack_value == "F" and self._stacking_enabled
        self._stage = STAGE_PLAY
        self._pending_color_pid = None
        events.append({
            "event_type": "uno_color",
            "actor_id": pid,
            "narrative": f"{self._pname(state, pid)} declares the color: {color}.",
            "data": {"color": color},
        })
        # Stacking mode F-chain: pass turn to next player WITHOUT skip —
        # they get to stack or eat.
        # Non-stacking +4: advance with skip (draw was already applied).
        # Plain Wild: advance without skip.
        if in_stack_chain:
            self._advance(skip=False)
        else:
            self._advance(skip=bool(was_plus_four))
        self._sync_entities(state)
        return events

    # ------------------------------------------------------------------
    # Hand end / match end
    # ------------------------------------------------------------------

    def _end_hand(self, state: Any, winner_pid: Optional[str]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if winner_pid is None:
            # Stalemate — no scoring this hand.
            events.append({
                "event_type": "uno_hand_resolved",
                "narrative": f"Hand {self._hand_num} stalemated (empty deck).",
                "data": {"winner_id": None, "scores": dict(self._scores)},
            })
        else:
            # Single-hand match — record the residual card values for
            # the post-match scoreboard, but no cumulative scoring.
            per_player: Dict[str, int] = {}
            for pid in self._seat_order:
                if pid == winner_pid:
                    continue
                per_player[pid] = sum(card_score(c) for c in self._hands.get(pid, []))
            self._hands_won[winner_pid] = self._hands_won.get(winner_pid, 0) + 1
            events.append({
                "event_type": "uno_hand_resolved",
                "narrative": f"{self._pname(state, winner_pid)} emptied their hand and wins!",
                "data": {
                    "winner_id": winner_pid,
                    "opponent_residuals": per_player,
                    "scores": dict(self._scores),
                    "hands_won": dict(self._hands_won),
                },
            })

        # One hand = one match. Game over.
        self._game_over = True
        self._stage = STAGE_OVER
        # Build score map for the arena resolver — winner = 1, losers = 0.
        score_map = {pid: (1 if pid == winner_pid else 0) for pid in self._seat_order}
        events.append({
            "event_type": "uno_match_over",
            "narrative": (f"{self._pname(state, winner_pid)} wins the match!"
                          if winner_pid else "Match ended in a stalemate."),
            "data": {
                # `winner` is the key the arena resolver reads
                # (match_service `VERDICT_EVENT_TYPES` path). Keep the
                # legacy `winner_id` too so the viz / event log don't
                # break.
                "winner": winner_pid,
                "winner_id": winner_pid,
                "winner_name": self._pname(state, winner_pid) if winner_pid else None,
                "score": score_map,
                "scores": dict(self._scores),
                "hands_won": dict(self._hands_won),
            },
        })
        self._sync_entities(state)
        return events

    # ------------------------------------------------------------------
    # Perception
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        ent = state.get_entity(entity_id)
        if ent is None:
            return {}
        hand = list(self._hands.get(entity_id, []))
        is_active = entity_id == self._active_pid
        needs_color = (self._stage == STAGE_COLOR
                       and entity_id == self._pending_color_pid)

        if needs_color:
            stage_hint = (
                "You just played a Wild. Declare the new color: "
                'choose_color(color="R"), "G", "B", or "Y". Pick the '
                "color you hold the most of."
            )
        elif is_active and self._stage == STAGE_PLAY:
            legal = self._legal_plays(entity_id)
            if self._stack_value is not None and self._stack_count > 0:
                if legal:
                    example = legal[0]
                    stage_hint = (
                        f"STACKING — a {self._stack_value}-chain is open "
                        f"({self._stack_count} cards pending). Stack a "
                        f"matching {self._stack_value} with play_card("
                        f'card="{example}") to pass it on, OR draw_card() '
                        f"to eat the chain and be skipped."
                    )
                else:
                    stage_hint = (
                        f"STACKING — {self._stack_value}-chain has "
                        f"{self._stack_count} cards pending. You can't "
                        f"stack — use draw_card() to eat and be skipped."
                    )
            elif legal:
                example = legal[0]
                stage_hint = (
                    f"Your turn. Top discard: {self._discard_top} "
                    f"(color {self._discard_color}). Play one legal card "
                    f'with play_card(card="{example}"). Wild = WW, Wild +4 = WF. '
                    f"If you can't, use draw_card()."
                )
            else:
                stage_hint = (
                    f"Your turn but no legal play. Use draw_card() — "
                    f"the drawn card auto-plays if legal."
                )
        else:
            stage_hint = (
                f"Not your turn. Use discuss to react. Top: "
                f"{self._discard_top} ({self._discard_color}). "
                f"Active: {self._pname(state, self._active_pid)}."
            )

        out: Dict[str, Any] = {
            "stage": self._stage,
            "hand_number": self._hand_num,
            "my_hand": hand,
            "discard_top": self._discard_top,
            "discard_color": self._discard_color,
            "direction": self._direction,
            "active_player_id": self._active_pid,
            "i_am_active": is_active,
            "hand_counts": {pid: len(self._hands.get(pid, [])) for pid in self._seat_order},
            "scores": dict(self._scores),
            "hands_won": dict(self._hands_won),
            "seat_order": list(self._seat_order),
            "stacking_enabled": self._stacking_enabled,
            "stack_value": self._stack_value,
            "stack_count": self._stack_count,
            "stage_hint": stage_hint,
        }
        if is_active and self._stage == STAGE_PLAY:
            out["legal_plays"] = self._legal_plays(entity_id)
        if needs_color:
            out["needs_color_choice"] = True
        return out

    @property
    def is_terminal(self) -> bool:
        return self._game_over
