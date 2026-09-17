"""Spades (4-player partnership) domain module.

Standard Spades:
  - 4 players in 2 fixed partnerships: N+S vs E+W.
  - Single 52-card deck, 13 cards each.
  - SPADES are ALWAYS trump.

Hand structure:
  1. BIDDING: each player bids the number of tricks they expect to
     take (0-13). Bids reveal simultaneously. Combined team bid =
     sum of partner bids. A bid of 0 is a NIL (special: ±100 pts
     depending on whether the bidder takes 0 tricks).
  2. TRICK PLAY: holder of 2♣ leads the first trick. Must follow led
     suit if possible. Spades can only be LED once they've been
     "broken" (someone played a spade as a discard on a non-spade
     trick) unless the leader has only spades left. Highest card of
     the led suit wins UNLESS a spade was played, in which case the
     highest spade wins.
  3. SCORING per team:
       - Combined team bid `B`, combined tricks taken `T`.
       - If T >= B: score = 10*B + (T - B) bags
       - If T <  B: score = -10*B (set)
       - Nil bid by a player: +100 if that player took 0 tricks,
         -100 otherwise. Their personal tricks still count toward
         partner's bid? Standard: NO — nil tricks count as bags for
         the team.
       - Every 10 cumulative bags = -100 penalty (and bags -= 10).
  4. Game ends when any team hits `target_score` (default 500).
     Highest-scoring team wins; ties split the pot.

Engine wiring:
  * Single phase "playing" with resolution_mode=simultaneous.
  * Module cycles stages internally:
      STAGE_BID   → all 4 bid (sealed simultaneous)
      STAGE_PLAY  → one active player plays a card per round
      STAGE_OVER
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule
from fg_env.roles import Role

logger = logging.getLogger(__name__)


STAGE_BID = "bid"
STAGE_PLAY = "play"
STAGE_OVER = "over"

SUITS = ["♣", "♦", "♥", "♠"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
RANK_ORDER = {r: i for i, r in enumerate(RANKS)}
DECK: List[str] = [r + s for s in SUITS for r in RANKS]


def card_rank(c: str) -> int:
    return RANK_ORDER[c[:-1]]


def card_suit(c: str) -> str:
    return c[-1]


class SpadesModule(DomainModule):
    """Drives a Spades match."""

    def __init__(self, name: str = "spades", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._player_type: str = p.get("player_type", "Player")
        self._rng = random.Random(p.get("seed", 0x59AD35))
        self._target_score: int = int(p.get("target_score", 500))
        self._bootstrapped = False
        self._game_over = False

        # Match-level.
        self._seat_order: List[str] = []      # [N, E, S, W]
        self._teams: Dict[str, str] = {}      # pid → "NS"|"EW"
        self._team_scores: Dict[str, int] = {"NS": 0, "EW": 0}
        self._team_bags: Dict[str, int] = {"NS": 0, "EW": 0}
        # Hand-level.
        self._hand_num: int = 0
        self._hands: Dict[str, List[str]] = {}
        self._bids: Dict[str, int] = {}       # pid → bid this hand
        self._tricks_taken: Dict[str, int] = {}  # pid → tricks this hand
        self._spades_broken: bool = False
        # Stage / trick.
        self._stage: str = STAGE_BID
        self._trick_idx: int = 0
        self._trick_leader_id: str = ""
        self._trick_plays: List[Tuple[str, str]] = []
        self._active_player_id: str = ""
        self._last_round_resolved: int = 0

    @property
    def description(self) -> str:
        return ("Spades: 4-player partnership trick-taking. Bid your "
                "tricks, then take exactly that many (or more for bags). "
                "Spades are always trump. First team to the target wins.")

    @property
    def custom_actions(self) -> List[str]:
        return ["bid", "play_card", "discuss"]

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
            name="ns", team="NS", sees_teammates=True,
            description="You're on Team NS (North + South). Hit your team's combined bid to score.",
        ))
        state.roles.define_role(Role(
            name="ew", team="EW", sees_teammates=True,
            description="You're on Team EW (East + West). Hit your team's combined bid to score.",
        ))

        players = self._alive_players(state)
        if len(players) != 4:
            logger.warning(f"Spades: needs exactly 4 players, got {len(players)}.")
            self._bootstrapped = True
            self._game_over = True
            return

        # Seat order: [N, E, S, W]. NS = north+south, EW = east+west.
        # If players carry a preset `team` property (set at match-create
        # time from the lobby's team picker), arrange them so NS players
        # sit at indices [0, 2] and EW at [1, 3]. Otherwise fall back to
        # alternating by arrival order.
        pool = list(players[:4])
        ns_pref = [p for p in pool if (p.get("team") or "").upper() == "NS"]
        ew_pref = [p for p in pool if (p.get("team") or "").upper() == "EW"]
        if len(ns_pref) == 2 and len(ew_pref) == 2:
            ordered = [ns_pref[0], ew_pref[0], ns_pref[1], ew_pref[1]]
        else:
            ordered = pool
        self._seat_order = [p.id for p in ordered]
        team_keys = ["NS", "EW", "NS", "EW"]
        for pid, tk in zip(self._seat_order, team_keys):
            self._teams[pid] = tk
            state.roles.assign(pid, "ns" if tk == "NS" else "ew")
            ent = state.get_entity(pid)
            if ent:
                ent.set("team", tk)
                ent.set("score", 0)
                ent.set("bid", 0)
                ent.set("tricks", 0)
        self._start_hand(state)
        self._bootstrapped = True

    # ------------------------------------------------------------------
    # Hand lifecycle
    # ------------------------------------------------------------------

    def _start_hand(self, state: Any) -> None:
        self._hand_num += 1
        self._bids = {pid: -1 for pid in self._seat_order}  # -1 = no bid yet
        self._tricks_taken = {pid: 0 for pid in self._seat_order}
        self._spades_broken = False
        self._trick_idx = 0
        self._trick_plays = []
        self._stage = STAGE_BID

        # Deal.
        deck = list(DECK)
        self._rng.shuffle(deck)
        for i, pid in enumerate(self._seat_order):
            self._hands[pid] = sorted(deck[i * 13:(i + 1) * 13], key=self._sort_key)
            ent = state.get_entity(pid)
            if ent:
                ent.set("hand", list(self._hands[pid]))
                ent.set("bid", 0)
                ent.set("tricks", 0)
                ent.set("score", self._team_scores.get(self._teams[pid], 0))

    def _sort_key(self, c: str) -> Tuple[int, int]:
        suit_order = {"♣": 0, "♦": 1, "♥": 2, "♠": 3}
        return (suit_order.get(card_suit(c), 4), card_rank(c))

    def _set_first_leader_to_2_clubs(self) -> None:
        for pid, hand in self._hands.items():
            if "2♣" in hand:
                self._trick_leader_id = pid
                self._active_player_id = pid
                return
        self._trick_leader_id = self._seat_order[0]
        self._active_player_id = self._seat_order[0]

    # ------------------------------------------------------------------
    # Action gating
    # ------------------------------------------------------------------

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str], state: Any) -> List[str]:
        if self._game_over:
            return []
        ent = state.get_entity(entity_id)
        if not ent or not ent.alive:
            return []

        if self._stage == STAGE_BID:
            return [a for a in valid_actions if a in ("bid", "discuss")]
        if self._stage == STAGE_PLAY:
            # Only the seat on turn acts. Waiting seats would otherwise each
            # spend a model call per card on table talk nobody asked for.
            if entity_id == self._active_player_id:
                return [a for a in valid_actions if a in ("play_card", "discuss")]
            return []
        return []

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
            played = self._stage
            if played == STAGE_BID:
                events.extend(self._resolve_bid(state, prev))
            elif played == STAGE_PLAY:
                events.extend(self._resolve_play(state, prev))
            self._last_round_resolved = prev
            if self._game_over:
                return events

        events.append({
            "event_type": "spades_stage",
            "narrative": self._stage_narrative(state),
            "data": {
                "stage": self._stage,
                "hand": self._hand_num,
                "trick_idx": self._trick_idx,
                "trick_leader_id": self._trick_leader_id,
                "active_player_id": self._active_player_id,
                "spades_broken": self._spades_broken,
                "bids": dict(self._bids),
                "tricks_taken": dict(self._tricks_taken),
                "team_scores": dict(self._team_scores),
                "team_bags": dict(self._team_bags),
                "teams": dict(self._teams),
                "round": round_number,
            },
        })
        return events

    def _stage_narrative(self, state: Any) -> str:
        if self._stage == STAGE_BID:
            return f"Hand {self._hand_num} — everyone bids how many tricks they'll take."
        if self._stage == STAGE_PLAY:
            ent = state.get_entity(self._active_player_id)
            actor = ent.name if ent else self._active_player_id
            return (f"Hand {self._hand_num}, trick "
                    f"{self._trick_idx + 1}/13 — {actor}'s turn.")
        return ""

    # ------------------------------------------------------------------
    # Bid resolution
    # ------------------------------------------------------------------

    def _resolve_bid(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for pid in self._seat_order:
            ent = state.get_entity(pid)
            if not ent:
                continue
            raw = ent.get(f"_bid_r{round_number}")
            try:
                b = int(raw)
            except Exception:
                b = -1
            if b < 0 or b > 13:
                # Auto-fill missing/invalid bids — average-ish 3 is the
                # neutral default; nil opt-in must be explicit.
                b = 3
            self._bids[pid] = b
            ent.set("bid", b)

        team_bids: Dict[str, int] = {"NS": 0, "EW": 0}
        for pid, b in self._bids.items():
            team_bids[self._teams[pid]] += b

        events.append({
            "event_type": "spades_bids_resolved",
            "data": {
                "hand": self._hand_num,
                "bids": dict(self._bids),
                "team_bids": team_bids,
            },
            "narrative": "Bids: " + ", ".join(
                f"{state.get_entity(pid).name if state.get_entity(pid) else pid}={b}"
                for pid, b in self._bids.items()
            ),
        })
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
            chosen = valid_choices[0] if valid_choices else None
        if chosen is None:
            self._active_player_id = self._next_player(self._active_player_id)
            return events

        self._hands[self._active_player_id].remove(chosen)
        actor.set("hand", list(self._hands[self._active_player_id]))
        self._trick_plays.append((self._active_player_id, chosen))

        # Spades broken? — a spade was played on a non-spade-led trick.
        if not self._spades_broken and self._trick_plays:
            led_suit = card_suit(self._trick_plays[0][1])
            for _, c in self._trick_plays:
                if card_suit(c) == "♠" and led_suit != "♠":
                    self._spades_broken = True
                    break

        events.append({
            "event_type": "spades_play",
            "actor_id": self._active_player_id,
            "data": {
                "round": round_number,
                "card": chosen,
                "trick_idx": self._trick_idx,
                "hand": self._hand_num,
                "trick_so_far": [
                    {"player": pid, "card": c} for pid, c in self._trick_plays
                ],
                "spades_broken": self._spades_broken,
            },
            "narrative": f"{actor.name} plays {chosen}.",
        })

        if len(self._trick_plays) >= 4:
            winner_id, winner_card = self._trick_winner()
            self._tricks_taken[winner_id] = self._tricks_taken.get(winner_id, 0) + 1
            winner_ent = state.get_entity(winner_id)
            if winner_ent:
                winner_ent.set("tricks", self._tricks_taken[winner_id])
            events.append({
                "event_type": "spades_trick_resolved",
                "data": {
                    "round": round_number,
                    "trick_idx": self._trick_idx,
                    "hand": self._hand_num,
                    "winner_id": winner_id,
                    "winning_card": winner_card,
                    "plays": [{"player": pid, "card": c} for pid, c in self._trick_plays],
                    "tricks_taken": dict(self._tricks_taken),
                },
                "narrative": (
                    f"Trick {self._trick_idx + 1}: "
                    f"{winner_ent.name if winner_ent else winner_id} "
                    f"takes with {winner_card}."
                ),
            })
            self._trick_idx += 1
            self._trick_plays = []
            self._trick_leader_id = winner_id
            self._active_player_id = winner_id
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
                return ["2♣"] if "2♣" in hand else hand[:1]
            # Can't LEAD spades until broken (unless only spades left).
            if not self._spades_broken:
                non_spades = [c for c in hand if card_suit(c) != "♠"]
                if non_spades:
                    return sorted(non_spades, key=self._sort_key)
            return sorted(hand, key=self._sort_key)
        # Following.
        led_suit = card_suit(self._trick_plays[0][1])
        same_suit = [c for c in hand if card_suit(c) == led_suit]
        if same_suit:
            return sorted(same_suit, key=self._sort_key)
        # Can't follow — any card (spades count as trump).
        return sorted(hand, key=self._sort_key)

    def _trick_winner(self) -> Tuple[str, str]:
        # Highest spade wins if any was played; otherwise highest card of
        # the led suit wins.
        spades = [(pid, c) for pid, c in self._trick_plays if card_suit(c) == "♠"]
        if spades:
            winner_pid, winner_card = max(spades, key=lambda kv: card_rank(kv[1]))
            return winner_pid, winner_card
        led_suit = card_suit(self._trick_plays[0][1])
        in_suit = [(pid, c) for pid, c in self._trick_plays if card_suit(c) == led_suit]
        winner_pid, winner_card = max(in_suit, key=lambda kv: card_rank(kv[1]))
        return winner_pid, winner_card

    def _next_player(self, pid: str) -> str:
        idx = self._seat_order.index(pid)
        return self._seat_order[(idx + 1) % len(self._seat_order)]

    # ------------------------------------------------------------------
    # End of hand / game
    # ------------------------------------------------------------------

    def _end_hand(self, state: Any) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        # Per-team scoring.
        team_delta: Dict[str, int] = {"NS": 0, "EW": 0}
        team_bid: Dict[str, int] = {"NS": 0, "EW": 0}
        team_tricks: Dict[str, int] = {"NS": 0, "EW": 0}
        nil_results: List[Dict[str, Any]] = []

        for pid in self._seat_order:
            t = self._teams[pid]
            b = self._bids[pid]
            tk = self._tricks_taken.get(pid, 0)
            # Nil handling: bid == 0 is a Nil. Scores ±100 independently
            # of partner. Tricks taken by a niller still count as
            # OVERTRICKS (bags) for the team — they DON'T count toward
            # partner's bid total.
            if b == 0:
                if tk == 0:
                    team_delta[t] += 100
                    nil_results.append({"player_id": pid, "success": True})
                else:
                    team_delta[t] -= 100
                    nil_results.append({"player_id": pid, "success": False, "tricks": tk})
            else:
                team_bid[t] += b
            # Niller's tricks → bags. Non-niller's tricks count toward
            # partner-bid + overtricks.
            team_tricks[t] += tk

        # Score combined non-nil bids per team.
        for t in ("NS", "EW"):
            b = team_bid[t]
            if b <= 0:
                # Whole team bid nil — nothing more to score from bid.
                continue
            # Effective tricks for bid-matching = team tricks MINUS the
            # nillers' tricks (they're bags either way).
            niller_tricks = sum(self._tricks_taken.get(pid, 0)
                                for pid in self._seat_order
                                if self._teams[pid] == t and self._bids[pid] == 0)
            effective = team_tricks[t] - niller_tricks
            if effective >= b:
                team_delta[t] += 10 * b
                bags = (effective - b) + niller_tricks
                self._team_bags[t] += bags
                # Add bags as +1 each into points.
                team_delta[t] += bags
            else:
                team_delta[t] -= 10 * b
                # Niller tricks still go to bag pile.
                self._team_bags[t] += niller_tricks

            # Bag overflow: every 10 bags → -100 and bags %= 10.
            if self._team_bags[t] >= 10:
                penalties = self._team_bags[t] // 10
                team_delta[t] -= 100 * penalties
                self._team_bags[t] %= 10

        for t in ("NS", "EW"):
            self._team_scores[t] += team_delta[t]

        # Persist to entities.
        for pid in self._seat_order:
            ent = state.get_entity(pid)
            if ent:
                ent.set("score", self._team_scores[self._teams[pid]])

        events.append({
            "event_type": "spades_hand_resolved",
            "data": {
                "hand": self._hand_num,
                "bids": dict(self._bids),
                "tricks_taken": dict(self._tricks_taken),
                "team_bid": team_bid,
                "team_tricks": team_tricks,
                "team_delta": team_delta,
                "team_scores": dict(self._team_scores),
                "team_bags": dict(self._team_bags),
                "nil_results": nil_results,
            },
            "narrative": (f"Hand {self._hand_num} done. "
                          f"NS {self._team_scores['NS']} (bags {self._team_bags['NS']}), "
                          f"EW {self._team_scores['EW']} (bags {self._team_bags['EW']})."),
        })

        # Game-over check.
        if max(self._team_scores.values()) >= self._target_score:
            best = max(self._team_scores.values())
            winners_team = [t for t, s in self._team_scores.items() if s == best]
            winner_pids = [pid for pid in self._seat_order
                           if self._teams[pid] in winners_team]
            events.append({
                "event_type": "spades_match_over",
                "data": {
                    "winning_team": winners_team[0] if len(winners_team) == 1 else "tied",
                    "winners": winner_pids,
                    "team_scores": dict(self._team_scores),
                    "target_score": self._target_score,
                },
                "narrative": (
                    f"Match over — Team {winners_team[0]} wins with "
                    f"{best} points." if len(winners_team) == 1
                    else f"Match over — teams tied at {best}."
                ),
            })
            self._game_over = True
            self._stage = STAGE_OVER
            return events

        self._start_hand(state)
        return events

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

        if action_name == "bid":
            raw = params.get("amount") or params.get("bid")
            try:
                b = int(raw)
            except Exception:
                b = -1
            if 0 <= b <= 13:
                actor.set(f"_bid_r{round_number}", b)
                events.append({
                    "event_type": "spades_bid_intent",
                    "actor_id": actor_id,
                    "data": {"round": round_number, "private_to": actor_id},
                    "narrative": f"{actor.name} locks in a sealed bid.",
                })

        elif action_name == "play_card":
            raw = params.get("card") or details.get("card")
            if isinstance(raw, str):
                normalized = self._normalize_card(raw.strip(), self._hands.get(actor_id, []))
                if normalized:
                    actor.set(f"_play_r{round_number}", normalized)
                    events.append({
                        "event_type": "spades_play_intent",
                        "actor_id": actor_id,
                        "data": {"round": round_number, "card": normalized},
                        "narrative": f"{actor.name} commits a card.",
                    })

        return events

    def _normalize_card(self, raw: str, hand: List[str]) -> Optional[str]:
        if raw in hand:
            return raw
        suit_letter_map = {"C": "♣", "D": "♦", "H": "♥", "S": "♠"}
        cleaned = raw.strip(" \"'()[]{}")
        if len(cleaned) >= 2:
            tail = cleaned[-1].upper()
            head = cleaned[:-1].upper()
            if tail in suit_letter_map:
                if head == "T":
                    head = "10"
                cand = f"{head}{suit_letter_map[tail]}"
                if cand in hand:
                    return cand
        return None

    # ------------------------------------------------------------------
    # Perception
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        ent = state.get_entity(entity_id)
        if ent is None:
            return {}
        hand = list(self._hands.get(entity_id, []))
        my_team = self._teams.get(entity_id, "")
        partner_id = next(
            (pid for pid in self._seat_order
             if self._teams.get(pid) == my_team and pid != entity_id),
            None,
        )
        is_active = entity_id == self._active_player_id

        if self._stage == STAGE_BID:
            stage_hint = (
                "BIDDING. Estimate how many of the 13 tricks you'll take. "
                'Use bid(amount=N) where 0 ≤ N ≤ 13. Bid 0 for a NIL '
                "(brave: ±100 to your team). Spades count as trump. "
                "Aces of any suit + high spades are likely winners."
            )
        elif self._stage == STAGE_PLAY and is_active:
            legal = self._legal_plays(entity_id)
            example = legal[0] if legal else ""
            spades_note = "BROKEN" if self._spades_broken else "not yet broken — can't lead them"
            stage_hint = (
                f'Your turn. Play one card with play_card(card="{example}"). '
                f'Must follow led suit if you can. Spades are trump. '
                f'Spades {spades_note}.'
            )
        else:
            stage_hint = ""

        out: Dict[str, Any] = {
            "stage": self._stage,
            "hand_number": self._hand_num,
            "trick_idx": self._trick_idx,
            "trick_plays": [{"player": pid, "card": c} for pid, c in self._trick_plays],
            "trick_leader_id": self._trick_leader_id,
            "active_player_id": self._active_player_id,
            "i_am_active": is_active,
            "my_hand": hand,
            "my_team": my_team,
            "partner_id": partner_id,
            "spades_broken": self._spades_broken,
            "bids": dict(self._bids),
            "tricks_taken": dict(self._tricks_taken),
            "team_scores": dict(self._team_scores),
            "team_bags": dict(self._team_bags),
            "teams": dict(self._teams),
            "seat_order": list(self._seat_order),
            "target_score": self._target_score,
            "stage_hint": stage_hint,
        }
        if self._stage == STAGE_PLAY and is_active:
            out["legal_plays"] = self._legal_plays(entity_id)
        return out
