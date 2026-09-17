"""Fool — 2-to-4 player Slavic shedding game (Podkidnoy variant).

36-card deck: ranks 6,7,8,9,10,J,Q,K,A in 4 suits (♣ ♦ ♥ ♠).
Trump suit revealed from the BOTTOM of the draw pile and stays
visible until that card is drawn (always the last drawn).

Round flow:
  1. Attacker plays an initial attack card → STAGE_DEFEND.
     Defender = the next still-in-the-match player to the attacker's
     LEFT (clockwise in seat order).
  2. Defender either:
       a) defend(card) — must beat the most recent unbeaten attack
          (same suit higher rank, OR any trump if attack isn't trump).
          On success → STAGE_ADD.
       b) take() — picks up ALL table cards. Round ends, refill;
          attacker stays, turn shifts PAST the defender to the next
          live player (defender loses one rotation).
  3. STAGE_ADD: attacker may pile on a card of any rank already on
     the table (up to min(6, defender_hand_size) attack cards total).
     attack(card) → STAGE_DEFEND.
     pass_round() → table → discard, defender becomes attacker,
     refill.

Match end:
  After each round, players refill to 6 from the deck (attacker first,
  then the rest in turn order). Once the deck is empty, players who
  finish their hand drop out of the rotation. The LAST player still
  holding cards is the FOOL and loses. If everyone goes out
  simultaneously → draw.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


STAGE_ATTACK = "attack"
STAGE_DEFEND = "defend"
STAGE_ADD = "add"
STAGE_OVER = "over"

SUITS = ["♣", "♦", "♥", "♠"]
RANKS = ["6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_ORDER = {r: i for i, r in enumerate(RANKS)}
MAX_ATTACKS_PER_ROUND = 6


def _build_deck() -> List[str]:
    return [r + s for s in SUITS for r in RANKS]


def card_rank(c: str) -> str:
    # All ranks are 1 char except "10"
    return c[:-1]


def card_suit(c: str) -> str:
    return c[-1]


def rank_idx(c: str) -> int:
    return RANK_ORDER[card_rank(c)]


def beats(attack: str, defense: str, trump_suit: str) -> bool:
    """Does `defense` beat `attack` given the trump suit?"""
    a_suit, d_suit = card_suit(attack), card_suit(defense)
    a_trump, d_trump = a_suit == trump_suit, d_suit == trump_suit
    if d_trump and not a_trump:
        return True
    if a_trump and not d_trump:
        return False
    # Same trump-ness — must match suit and beat by rank.
    if a_suit != d_suit:
        return False
    return rank_idx(defense) > rank_idx(attack)


class DurakModule(DomainModule):
    """Drives a Fool (Slavic shedding card game) match."""

    @property
    def description(self) -> str:
        return ("Fool: 2-4 player Slavic shedding game. 36-card "
                "deck with one trump suit. Attack, defend, or pick up. "
                "Empty your hand — the last player holding cards is the "
                "Fool.")

    @property
    def custom_actions(self) -> List[str]:
        return ["attack", "defend", "take", "pass_round", "discuss"]

    def __init__(self, name: str = "durak", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Player")
        self._rng = random.Random(p.get("seed", 0xD08AC))
        # `wins_needed` is the slider value the user picks on the New
        # Match page: how many games they must win to take the match.
        # We derive `best_of = 2 * wins - 1` for display ("best of 3"
        # etc.). 1 win = single game (legacy default).
        self._wins_to_clinch: int = max(1, int(p.get("wins_needed", p.get("best_of_wins", 1))))
        self._best_of: int = max(1, 2 * self._wins_to_clinch - 1)
        self._bootstrapped = False
        self._game_over = False        # True only when the whole MATCH is over
        self._round_did_resolve = False

        # Match-level
        self._seat_order: List[str] = []
        self._rounds_won: Dict[str, int] = {}
        self._game_wins: Dict[str, int] = {}   # how many games each player has won
        self._game_num: int = 0                # which game inside the match
        self._trump_suit: str = "♠"
        self._trump_card: Optional[str] = None  # bottom-of-deck card; None once drawn

        # Round-level
        self._attacker_pid: Optional[str] = None
        self._defender_pid: Optional[str] = None
        # Table is a list of (attack_card, defense_card_or_None) pairs in
        # the order played.
        self._table: List[Tuple[str, Optional[str]]] = []
        self._stage: str = STAGE_ATTACK
        self._round_num: int = 0
        self._last_round_resolved: int = 0
        self._winner_id: Optional[str] = None  # the non-Durak (or None for draw)
        self._durak_id: Optional[str] = None   # the loser

        # Hands & deck
        self._hands: Dict[str, List[str]] = {}
        self._draw_pile: List[str] = []
        self._discard: List[str] = []

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
        if n < 2 or n > 4:
            logger.warning(f"Fool: needs 2-4 players, got {n}.")
            self._bootstrapped = True
            self._game_over = True
            return
        self._seat_order = [p.id for p in players]
        self._rounds_won = {pid: 0 for pid in self._seat_order}
        self._game_wins = {pid: 0 for pid in self._seat_order}

        self._start_new_game(state, first_attacker_pid=None)
        self._sync_entities(state)
        self._bootstrapped = True

    def _start_new_game(self, state: Any, first_attacker_pid: Optional[str]) -> None:
        """Set up the next game inside a best-of-N match. Shuffles, deals
        a fresh deck, resets all per-game state. `first_attacker_pid`
        forces a starting attacker (typically last game's Fool); if None
        we fall back to lowest-trump rule."""
        self._game_num += 1
        self._round_num = 1
        self._stage = STAGE_ATTACK
        self._table = []
        self._discard = []
        self._winner_id = None
        self._durak_id = None
        self._rounds_won = {pid: 0 for pid in self._seat_order}

        deck = _build_deck()
        self._rng.shuffle(deck)
        # Trump = bottom card. It's still part of the deck — drawn last.
        self._trump_card = deck[0]
        self._trump_suit = card_suit(self._trump_card)
        self._draw_pile = deck  # last item drawn is index 0 (the trump)

        # Deal 6 cards each from the TOP of the deck.
        self._hands = {pid: [] for pid in self._seat_order}
        for _ in range(6):
            for pid in self._seat_order:
                if self._draw_pile:
                    self._hands[pid].append(self._draw_pile.pop())

        # First attacker: caller-specified (e.g. previous Fool), else
        # lowest trump in hand. Ties broken by seat order.
        if first_attacker_pid and first_attacker_pid in self._seat_order:
            self._attacker_pid = first_attacker_pid
        else:
            def lowest_trump(hand: List[str]) -> int:
                trumps = [c for c in hand if card_suit(c) == self._trump_suit]
                return min((rank_idx(c) for c in trumps), default=99)
            scored = [(lowest_trump(self._hands[pid]), i, pid)
                      for i, pid in enumerate(self._seat_order)]
            scored.sort()
            self._attacker_pid = scored[0][2]
        self._defender_pid = self._next_live(self._attacker_pid)

    # ------------------------------------------------------------------
    # Seat rotation helpers
    # ------------------------------------------------------------------

    def _is_in_game(self, pid: str) -> bool:
        """A player is still 'in' if they hold cards OR the deck still
        has cards (they could be dealt back in next refill)."""
        if not pid:
            return False
        return bool(self._hands.get(pid)) or bool(self._draw_pile)

    def _next_live(self, pid: str, skip: int = 1) -> Optional[str]:
        """Return the seat `skip` live players clockwise from `pid`.
        Skips anyone who has already gone out (empty hand + empty deck).
        Returns None if no other live player exists."""
        if not self._seat_order or pid not in self._seat_order:
            return None
        n = len(self._seat_order)
        idx = self._seat_order.index(pid)
        remaining = skip
        for step in range(1, n + 1):
            cand = self._seat_order[(idx + step) % n]
            if cand == pid:
                continue
            if self._is_in_game(cand):
                remaining -= 1
                if remaining <= 0:
                    return cand
        return None

    def _sync_entities(self, state: Any) -> None:
        for pid in self._seat_order:
            ent = state.get_entity(pid)
            if not ent:
                continue
            hand = self._hands.get(pid, [])
            ent.set("hand", list(hand))
            ent.set("hand_count", len(hand))
            ent.set("rounds_won", self._rounds_won.get(pid, 0))
            ent.set("is_durak", pid == self._durak_id)
            if pid == self._attacker_pid:
                ent.set("role", "attacker")
            elif pid == self._defender_pid:
                ent.set("role", "defender")
            else:
                ent.set("role", "")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _attack_cards(self) -> List[str]:
        return [a for a, _ in self._table]

    def _unbeaten_attacks(self) -> List[str]:
        return [a for a, d in self._table if d is None]

    def _table_ranks(self) -> List[str]:
        ranks: List[str] = []
        for a, d in self._table:
            ranks.append(card_rank(a))
            if d:
                ranks.append(card_rank(d))
        return ranks

    def _legal_attacks(self, pid: str) -> List[str]:
        hand = self._hands.get(pid, [])
        if not self._table:
            # Initial attack — any card legal.
            return list(hand)
        # Add stage — only cards matching a rank already on the table,
        # bounded by attack-cards-so-far cap (min(6, defender hand size)).
        max_attacks = min(MAX_ATTACKS_PER_ROUND,
                          len(self._hands.get(self._defender_pid or pid, [])) + sum(1 for _, d in self._table if d is None))
        if sum(1 for _ in self._table) >= max_attacks:
            return []
        ranks_on_table = set(self._table_ranks())
        return [c for c in hand if card_rank(c) in ranks_on_table]

    def _legal_defenses(self, pid: str) -> List[str]:
        unbeaten = self._unbeaten_attacks()
        if not unbeaten:
            return []
        target = unbeaten[0]
        hand = self._hands.get(pid, [])
        return [c for c in hand if beats(target, c, self._trump_suit)]

    @property
    def _active_pid(self) -> Optional[str]:
        if self._stage == STAGE_DEFEND:
            return self._defender_pid
        if self._stage in (STAGE_ATTACK, STAGE_ADD):
            return self._attacker_pid
        return None

    def _pname(self, state: Any, pid: Optional[str]) -> str:
        if not pid or state is None:
            return pid or "?"
        ent = state.get_entity(pid)
        return getattr(ent, "name", None) or pid

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
            if self._round_did_resolve:
                self._last_round_resolved = prev
            if self._game_over:
                return events

        events.append({
            "event_type": "durak_stage",
            "narrative": self._stage_narrative(state),
            "data": {
                "stage": self._stage,
                "round": self._round_num,
                "attacker_id": self._attacker_pid,
                "defender_id": self._defender_pid,
                "active_player_id": self._active_pid,
                "table": [{"attack": a, "defense": d} for a, d in self._table],
                "trump_suit": self._trump_suit,
                "trump_card": self._trump_card,  # None once drawn
                "deck_size": len(self._draw_pile),
                "discard_size": len(self._discard),
                "hand_counts": {pid: len(self._hands.get(pid, [])) for pid in self._seat_order},
                "rounds_won": dict(self._rounds_won),
                "game": self._game_num,
                "best_of": self._best_of,
                "wins_to_clinch": self._wins_to_clinch,
                "game_wins": dict(self._game_wins),
            },
        })
        return events

    def _stage_narrative(self, state: Any) -> str:
        a = self._pname(state, self._attacker_pid)
        d = self._pname(state, self._defender_pid)
        if self._stage == STAGE_ATTACK:
            return f"Round {self._round_num} — {a} attacks {d}. Trump: {self._trump_suit}."
        if self._stage == STAGE_DEFEND:
            return f"{d} must beat or take. Trump: {self._trump_suit}."
        if self._stage == STAGE_ADD:
            return f"{a} may pile on another card, or pass."
        return ""

    # ------------------------------------------------------------------
    # Action resolution
    # ------------------------------------------------------------------

    def _resolve_round(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        actor_pid = self._active_pid
        if not actor_pid:
            return events
        actor = state.get_entity(actor_pid)
        if actor is None:
            return events

        attack_card = actor.get(f"_attack_r{round_number}")
        defend_card = actor.get(f"_defend_r{round_number}")
        take = actor.get(f"_take_r{round_number}")
        passed = actor.get(f"_pass_r{round_number}")

        if self._stage == STAGE_ATTACK and isinstance(attack_card, str) and attack_card:
            events.extend(self._do_attack(state, actor_pid, attack_card))
            self._round_did_resolve = True
        elif self._stage == STAGE_DEFEND:
            if take:
                events.extend(self._do_take(state, actor_pid))
                self._round_did_resolve = True
            elif isinstance(defend_card, str) and defend_card:
                events.extend(self._do_defend(state, actor_pid, defend_card))
                self._round_did_resolve = True
        elif self._stage == STAGE_ADD:
            if isinstance(attack_card, str) and attack_card:
                events.extend(self._do_attack(state, actor_pid, attack_card))
                self._round_did_resolve = True
            elif passed:
                events.extend(self._do_pass_round(state, actor_pid))
                self._round_did_resolve = True
        # Otherwise: wait silently for the active player's action.
        return events

    def _do_attack(self, state: Any, pid: str, card: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if pid != self._attacker_pid:
            return events
        legal = self._legal_attacks(pid)
        if card not in legal:
            # Illegal — silently treat as "pass" if we're in ADD stage;
            # if it's the initial attack and the card was bad, also pass
            # the round to avoid deadlock.
            if self._stage == STAGE_ADD:
                return self._do_pass_round(state, pid)
            return events
        self._hands[pid].remove(card)
        self._table.append((card, None))
        self._stage = STAGE_DEFEND
        events.append({
            "event_type": "durak_attack",
            "actor_id": pid,
            "narrative": f"{self._pname(state, pid)} attacks with {card}.",
            "data": {"card": card, "table": [{"attack": a, "defense": d} for a, d in self._table]},
        })
        self._sync_entities(state)
        # Check end conditions immediately — attacker might have just
        # emptied their hand on the final-card play.
        self._maybe_end_match(state, events)
        return events

    def _do_defend(self, state: Any, pid: str, card: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if pid != self._defender_pid:
            return events
        unbeaten = self._unbeaten_attacks()
        if not unbeaten:
            return events
        target = unbeaten[0]
        if card not in self._hands.get(pid, []) or not beats(target, card, self._trump_suit):
            # Illegal defense — treat as take (safer than deadlock).
            return self._do_take(state, pid)
        # Attach the defense to the first unbeaten attack.
        for i, (a, d) in enumerate(self._table):
            if d is None:
                self._table[i] = (a, card)
                break
        self._hands[pid].remove(card)
        events.append({
            "event_type": "durak_defend",
            "actor_id": pid,
            "narrative": f"{self._pname(state, pid)} beats {target} with {card}.",
            "data": {"attack": target, "defense": card,
                     "table": [{"attack": a, "defense": d} for a, d in self._table]},
        })
        # After a successful defense, attacker gets a chance to add more
        # — unless cap reached or defender is out of cards (can't defend
        # more anyway). In either case → STAGE_ADD; attacker can pass.
        self._stage = STAGE_ADD
        self._sync_entities(state)
        self._maybe_end_match(state, events)
        return events

    def _do_take(self, state: Any, pid: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if pid != self._defender_pid:
            return events
        picked: List[str] = []
        for a, d in self._table:
            picked.append(a)
            if d:
                picked.append(d)
        self._hands[pid].extend(picked)
        self._table = []
        self._rounds_won[self._attacker_pid] = self._rounds_won.get(self._attacker_pid, 0) + 1
        events.append({
            "event_type": "durak_take",
            "actor_id": pid,
            "narrative": f"{self._pname(state, pid)} picks up {len(picked)} card(s).",
            "data": {"picked": picked, "n": len(picked)},
        })
        # Refill in turn order: attacker first, then everyone else
        # clockwise. Skips anyone already out.
        self._refill_all()
        # When the defender TAKES, the turn moves cleanly +1 around the
        # table — the defender BECOMES the next attacker, and the next
        # live player after them is the new defender. (Simple rotation
        # variant — the standard Russian Podkidnoy rule "skip the
        # defender" felt unintuitive in playtesting: from the table's
        # POV the attack just hops one seat past the player who lost.)
        # In 2-player this still collapses to "attacker keeps attacking
        # the same defender" because there's only one other player.
        old_defender = self._defender_pid
        new_attacker = old_defender
        new_defender = self._next_live(new_attacker) if new_attacker else None
        if new_attacker and new_defender:
            self._attacker_pid = new_attacker
            self._defender_pid = new_defender
        self._round_num += 1
        self._stage = STAGE_ATTACK
        self._sync_entities(state)
        self._maybe_end_match(state, events)
        return events

    def _do_pass_round(self, state: Any, pid: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if pid != self._attacker_pid:
            return events
        # Only valid in STAGE_ADD (or as a safety fallback after initial
        # attack with no legal continuation).
        # Cards go to discard.
        cards_cleared = []
        for a, d in self._table:
            cards_cleared.append(a)
            if d:
                cards_cleared.append(d)
        self._discard.extend(cards_cleared)
        self._table = []
        self._rounds_won[self._defender_pid] = self._rounds_won.get(self._defender_pid, 0) + 1
        events.append({
            "event_type": "durak_round_resolved",
            "actor_id": pid,
            "narrative": f"{self._pname(state, self._defender_pid)} successfully defended the round.",
            "data": {"discarded": cards_cleared, "n": len(cards_cleared)},
        })
        # Refill in turn order before rotating roles.
        self._refill_all()
        # Defender successfully defended → becomes the new attacker.
        # New defender = next live seat past them.
        new_attacker = self._defender_pid
        new_defender = self._next_live(new_attacker) if new_attacker else None
        if new_attacker and new_defender:
            self._attacker_pid = new_attacker
            self._defender_pid = new_defender
        self._round_num += 1
        self._stage = STAGE_ATTACK
        self._sync_entities(state)
        self._maybe_end_match(state, events)
        return events

    def _refill(self, pid: str) -> None:
        """Draw from the top of the deck until the player has 6 cards
        (or the deck is empty). The trump card sits at index 0 and is
        the LAST card drawn."""
        if not pid:
            return
        while len(self._hands.get(pid, [])) < 6 and self._draw_pile:
            card = self._draw_pile.pop()
            self._hands[pid].append(card)
            if card == self._trump_card:
                self._trump_card = None  # trump card is now in someone's hand

    def _refill_all(self) -> None:
        """Refill every player to 6, starting with the attacker and
        proceeding clockwise. Standard refill order."""
        if not self._attacker_pid:
            return
        order: List[str] = [self._attacker_pid]
        n = len(self._seat_order)
        idx = self._seat_order.index(self._attacker_pid)
        for step in range(1, n):
            order.append(self._seat_order[(idx + step) % n])
        for pid in order:
            self._refill(pid)

    # ------------------------------------------------------------------
    # Match-end detection
    # ------------------------------------------------------------------

    def _maybe_end_match(self, state: Any, events: List[Dict[str, Any]]) -> None:
        """Detect end-of-GAME (deck empty, ≤1 player holding cards),
        score it (+1 game-win to each non-Fool), and decide whether the
        MATCH has clinched. If yes → emit `durak_match_over` and stop.
        If no → emit `durak_game_resolved` and start the next game in
        the best-of-N series."""
        if self._game_over:
            return
        if self._draw_pile:
            return  # still drawable
        if self._table:
            return  # let the current round resolve first
        with_cards = [pid for pid in self._seat_order if self._hands.get(pid)]
        if len(with_cards) > 1:
            return  # game not over yet — multiple players still in

        # --- Resolve THIS game ---
        if len(with_cards) == 1:
            game_durak_id: Optional[str] = with_cards[0]
            game_winners = [pid for pid in self._seat_order if pid != game_durak_id]
            is_game_draw = False
            narrative_game = (
                f"Game {self._game_num} — "
                f"{self._pname(state, game_durak_id)} is the Fool. "
                f"{', '.join(self._pname(state, w) for w in game_winners)} wins the game."
            )
        else:
            game_durak_id = None
            game_winners = []
            is_game_draw = True
            narrative_game = (
                f"Game {self._game_num} — everyone empties simultaneously, no Fool."
            )

        for pid in game_winners:
            self._game_wins[pid] = self._game_wins.get(pid, 0) + 1

        events.append({
            "event_type": "durak_game_resolved",
            "narrative": narrative_game,
            "data": {
                "game": self._game_num,
                "game_durak_id": game_durak_id,
                "game_winners": game_winners,
                "is_draw": is_game_draw,
                "game_wins": dict(self._game_wins),
                "wins_to_clinch": self._wins_to_clinch,
                "best_of": self._best_of,
            },
        })

        # --- Has the MATCH been clinched? ---
        clinched = [pid for pid, w in self._game_wins.items()
                    if w >= self._wins_to_clinch]
        # Single-game mode: this game IS the match.
        match_clinched = (self._best_of <= 1) or bool(clinched)

        if not match_clinched:
            # No winner yet — re-deal and start the next game inside the
            # match. The previous Fool leads the next game (Slavic
            # convention).
            self._start_new_game(state, first_attacker_pid=game_durak_id)
            self._sync_entities(state)
            return

        # --- Match over. Pick the match-level Fool/winner. ---
        self._game_over = True
        self._stage = STAGE_OVER
        if self._best_of <= 1:
            # Single game: that game's Fool IS the match Fool.
            self._durak_id = game_durak_id
            winners_list = [pid for pid in self._seat_order if pid != self._durak_id] \
                if self._durak_id else []
        else:
            # Best-of-N: leader is the player with the most game-wins;
            # match Fool is whoever has the fewest (only if unambiguous).
            top_wins = max(self._game_wins.values()) if self._game_wins else 0
            low_wins = min(self._game_wins.values()) if self._game_wins else 0
            top_players = [pid for pid, w in self._game_wins.items() if w == top_wins]
            low_players = [pid for pid, w in self._game_wins.items() if w == low_wins]
            winners_list = top_players
            self._durak_id = low_players[0] if len(low_players) == 1 else None

        self._winner_id = winners_list[0] if winners_list else None

        if self._durak_id is None and not winners_list:
            score_map = {pid: 0.5 for pid in self._seat_order}
            narrative = "Match ends in a draw."
        else:
            score_map = {pid: (0 if pid == self._durak_id else 1)
                         for pid in self._seat_order}
            if self._durak_id:
                narrative = (
                    f"Match over — {self._pname(state, self._durak_id)} is the Fool. "
                    f"{', '.join(self._pname(state, w) for w in winners_list)} wins the match."
                )
            else:
                narrative = (
                    f"Match over — "
                    f"{', '.join(self._pname(state, w) for w in winners_list)} wins."
                )

        events.append({
            "event_type": "durak_match_over",
            "narrative": narrative,
            "data": {
                "winner": self._winner_id,
                "winner_id": self._winner_id,
                "winner_name": self._pname(state, self._winner_id) if self._winner_id else None,
                "winners": winners_list,
                "durak_id": self._durak_id,
                "score": score_map,
                "rounds_won": dict(self._rounds_won),
                "game_wins": dict(self._game_wins),
                "best_of": self._best_of,
                "games_played": self._game_num,
                "is_draw": self._durak_id is None and not winners_list,
            },
        })
        self._sync_entities(state)

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
        if action_name not in self.custom_actions:
            return []
        actor = state.get_entity(actor_id)
        if actor is None:
            return []
        round_number = state.temporal.current_round
        details = (result.details or {}) if result and getattr(result, "details", None) else {}
        params = details.get("_action_params", {}) or {}
        events: List[Dict[str, Any]] = []

        if action_name == "attack":
            raw = params.get("card") or details.get("card")
            if isinstance(raw, str) and raw.strip():
                actor.set(f"_attack_r{round_number}", raw.strip())
                events.append({
                    "event_type": "durak_attack_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "card": raw.strip()},
                    "narrative": f"{actor.name} commits an attack.",
                })
        elif action_name == "defend":
            raw = params.get("card") or details.get("card")
            if isinstance(raw, str) and raw.strip():
                actor.set(f"_defend_r{round_number}", raw.strip())
                events.append({
                    "event_type": "durak_defend_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "card": raw.strip()},
                    "narrative": f"{actor.name} commits a defense.",
                })
        elif action_name == "take":
            actor.set(f"_take_r{round_number}", True)
            events.append({
                "event_type": "durak_take_intent",
                "actor_id": actor_id,
                "data": {"round": round_number},
                "narrative": f"{actor.name} will pick up.",
            })
        elif action_name == "pass_round":
            actor.set(f"_pass_r{round_number}", True)
            events.append({
                "event_type": "durak_pass_intent",
                "actor_id": actor_id,
                "data": {"round": round_number},
                "narrative": f"{actor.name} passes the round.",
            })
        # `discuss` has no state to stash.
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
        my_role = ("attacker" if entity_id == self._attacker_pid
                   else "defender" if entity_id == self._defender_pid
                   else "")

        stage_hint = ""
        legal: List[str] = []
        if is_active and self._stage == STAGE_ATTACK:
            legal = self._legal_attacks(entity_id)
            example = legal[0] if legal else ""
            stage_hint = (f'Your initial attack. Play any card from your hand with '
                          f'attack(card="{example}"). Lead a LOW non-trump to '
                          f'bleed the defender; save high trumps for late.')
        elif is_active and self._stage == STAGE_DEFEND:
            legal = self._legal_defenses(entity_id)
            if legal:
                example = legal[0]
                stage_hint = (f"Defend. Top unbeaten attack: {self._unbeaten_attacks()[0]}. "
                              f'Play a beating card with defend(card="{example}") — '
                              f"same suit higher rank, or a trump. Or take() to pick "
                              f"up ALL table cards.")
            else:
                stage_hint = "No legal defense. Use take() — you must pick up."
        elif is_active and self._stage == STAGE_ADD:
            legal = self._legal_attacks(entity_id)
            if legal:
                example = legal[0]
                stage_hint = (f'You may pile on another card matching a rank on '
                              f'the table: attack(card="{example}"). Or '
                              f"pass_round() to end the round — defender's "
                              f"successful defense.")
            else:
                stage_hint = ("No legal add-on. Use pass_round() to end the round.")
        else:
            stage_hint = f"Not your turn. Use discuss to react. Active: {self._pname(state, self._active_pid)}."

        out: Dict[str, Any] = {
            "stage": self._stage,
            "round": self._round_num,
            "my_hand": hand,
            "my_role": my_role,
            "i_am_active": is_active,
            "trump_suit": self._trump_suit,
            "trump_card": self._trump_card,
            "deck_size": len(self._draw_pile),
            "discard_size": len(self._discard),
            "table": [{"attack": a, "defense": d} for a, d in self._table],
            "attacker_id": self._attacker_pid,
            "defender_id": self._defender_pid,
            "active_player_id": self._active_pid,
            "hand_counts": {pid: len(self._hands.get(pid, [])) for pid in self._seat_order},
            "game": self._game_num,
            "best_of": self._best_of,
            "wins_to_clinch": self._wins_to_clinch,
            "game_wins": dict(self._game_wins),
            "stage_hint": stage_hint,
        }
        if is_active and legal:
            if self._stage == STAGE_DEFEND:
                out["legal_defenses"] = legal
            else:
                out["legal_attacks"] = legal
        return out

    @property
    def is_terminal(self) -> bool:
        return self._game_over
