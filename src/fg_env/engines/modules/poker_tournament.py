"""Texas Hold'em poker domain module.

Owns the table state (pot, community cards, current bet, blinds, dealer
button, street) and applies chip movement / status updates in response to
agent actions (bet, raise_bet, call, fold, check).

The module is intentionally self-contained: it spawns the Table + Deck
entities on first tick if the template didn't ship them, so an existing
poker template only needs to set ``"domain_modules": [{"name": "poker"}]``
to get full mechanics.

Hand cycle: deal and post blinds, then complete betting on each street
before revealing the next cards. Settle main and side pots at showdown;
no player acts between settlement and the next deal.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional

from fg_env.domain_module import DomainModule
from fg_env.entity import Entity, EntityType
from fg_env.phase_handlers import STANDARD_DECK, _score_hand

logger = logging.getLogger(__name__)

# Streets in advancement order. "showdown" is the resolution step that
# happens at the end of a hand before we loop back to "deal".
_STREETS = ["deal", "preflop", "flop", "turn", "river", "showdown"]


class PokerModule(DomainModule):
    """Drives Texas Hold'em mechanics on top of the generic action engine."""

    def __init__(self, name: str = "poker", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        p = params or {}
        self._chip_resource: str = p.get("chip_resource", "chips")
        def integer(key, default, minimum, maximum):
            value = p.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum or int(value) != value:
                raise ValueError(f"{key.replace('_', ' ').title()} must be a whole number from {minimum} to {maximum}.")
            return int(value)
        self._big_blind = integer("blind_level", p.get("big_blind", 100), 1, 1000000)
        self._small_blind = max(1, self._big_blind // 2) if "blind_level" in p else integer("small_blind", 50, 1, self._big_blind)
        self._ante = integer("ante", 0, 0, 1000000)
        if p.get("game_variant", "no_limit_holdem") != "no_limit_holdem":
            raise ValueError("This environment supports no-limit Texas Hold'em.")
        if "blind_increase_speed" in p or "payout_structure" in p:
            raise ValueError("This environment uses fixed blinds and chip standings. Remove unsupported blind-speed or payout settings.")
        self._player_type: str = p.get("player_type", "PokerPlayer")
        self._table_id: str = p.get("table_id", "table_main")
        self._deck_id: str = p.get("deck_id", "deck_main")
        self._rng = random.Random(p.get("seed", 0xC0FFEE))
        self._bootstrapped = False
        self._pending = []
        self._acted = set()
        self._hand_seats = []
        self._last_full_raise = self._big_blind
        # Lightweight transcript of betting actions, used purely to build
        # the `action_history` perception key. Each entry:
        #   {"hand": int, "phase": str, "actor_id": str, "actor": str,
        #    "action": str, "amount": int}
        # This is read-only bookkeeping — it does not influence betting
        # logic, hand evaluation, or event emission.
        self._action_log: List[Dict[str, Any]] = []

    @property
    def description(self) -> str:
        return "Texas Hold'em poker mechanics: blinds, betting rounds, community cards, showdown."

    @property
    def custom_actions(self) -> List[str]:
        return ["bet", "raise_bet", "call", "fold", "check"]

    def filter_valid_actions(self, entity_id: str, valid_actions, state):
        table = state.get_entity(self._table_id)
        actor = state.get_entity(entity_id)
        if table is None:
            return []
        legal = {action.split()[0] for action in self._legal_actions(state, table, actor)}
        return [action for action in valid_actions if action in legal]

    def validate_action(self, action_name, actor, target, state):
        if action_name in self.custom_actions and action_name not in self.filter_valid_actions(actor.id, self.custom_actions, state):
            return "That poker action is not legal on this turn."
        return None

    def _begin_betting(self, state, table, preflop=False):
        seats = self._hand_seats
        dealer = int(table.get("dealer_idx", 0)) % max(1, len(seats))
        start = (dealer if len(seats) == 2 else dealer + 3) if preflop else dealer + 1
        order = seats[start % len(seats):] + seats[:start % len(seats)] if seats else []
        self._pending = [pid for pid in order if state.get_entity(pid).get("status") == "active" and self._chips(state, pid) > 0]
        self._acted = set()
        self._last_full_raise = self._big_blind
        if len(self._pending) == 1:
            actor = state.get_entity(self._pending[0])
            if actor.get("bet_this_street", 0) >= table.get("current_bet", 0):
                self._pending = []

    def _finish_action(self, state, table, actor_id, previous_bet):
        current = int(table.get("current_bet", 0))
        increase = current - previous_bet
        if increase >= self._last_full_raise:
            self._last_full_raise = increase
            self._acted = set()
        self._acted.add(actor_id)
        self._pending = [pid for pid in self._pending if pid != actor_id and state.get_entity(pid).get("status") == "active"]
        if increase > 0:
            seats = self._hand_seats
            start = (seats.index(actor_id) + 1) % len(seats)
            order = seats[start:] + seats[:start]
            self._pending = [pid for pid in order if pid != actor_id and state.get_entity(pid).get("status") == "active" and state.get_entity(pid).get("bet_this_street", 0) < current]
        if len(self._active_players(state)) <= 1:
            self._pending = []

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def _ensure_table(self, state: Any):
        """Create the Table + Deck entities on first run if they don't exist."""
        if self._bootstrapped:
            return

        # Register entity types if missing
        if "Table" not in state.entity_types:
            state.entity_types["Table"] = EntityType(
                name="Table",
                role="object",
                properties=[],
            )
        if "Deck" not in state.entity_types:
            state.entity_types["Deck"] = EntityType(
                name="Deck",
                role="object",
                properties=[],
            )

        # Create Table entity
        table = state.get_entity(self._table_id)
        if table is None:
            table = Entity(
                id=self._table_id,
                name="Table",
                entity_type="Table",
                properties={
                    "community_cards": [],
                    "pot": 0,
                    "current_bet": 0,
                    "small_blind": self._small_blind,
                    "big_blind": self._big_blind,
                    "ante": self._ante,
                    "phase": "deal",
                    "dealer_idx": 0,
                    "hand_number": 0,
                    "last_winner": None,
                    "last_winning_hand": None,
                },
            )
            state.entities[self._table_id] = table

        # Create Deck entity
        deck = state.get_entity(self._deck_id)
        if deck is None:
            cards = list(STANDARD_DECK)
            self._rng.shuffle(cards)
            deck = Entity(
                id=self._deck_id,
                name="Deck",
                entity_type="Deck",
                properties={"cards": cards},
            )
            state.entities[self._deck_id] = deck

        self._bootstrapped = True

    # ------------------------------------------------------------------
    # Players helpers
    # ------------------------------------------------------------------

    def _players(self, state: Any) -> List[Any]:
        out = []
        for e in state.entities.values():
            if e.entity_type == self._player_type and e.alive:
                out.append(e)
        return out

    def _chip_pool(self, state: Any):
        return state.resources.get(self._chip_resource)

    def _chips(self, state: Any, entity_id: str) -> int:
        pool = self._chip_pool(state)
        if not pool:
            return 0
        return int(pool.get(entity_id) or 0)

    def _pot_chips(self, state: Any) -> int:
        pool = self._chip_pool(state)
        if not pool:
            return 0
        return int(pool.holdings.get(self._table_id, 0) or 0)

    def _move_chips(self, state: Any, from_id: str, to_id: str, amount: int) -> int:
        """Move chips between holdings, clamped to what `from_id` has. Returns moved."""
        pool = self._chip_pool(state)
        if not pool or amount <= 0:
            return 0
        have = int(pool.get(from_id) or 0)
        moved = min(have, int(amount))
        if moved <= 0:
            return 0
        # pool.transfer respects conservation; both holdings get adjusted.
        ok = pool.transfer(from_id, to_id, float(moved))
        if ok:
            table = state.get_entity(self._table_id)
            if table is not None:
                table.set("pot", self._pot_chips(state))
            for entity_id in (from_id, to_id):
                entity = state.get_entity(entity_id)
                if entity is not None and entity.entity_type == self._player_type:
                    entity.set("chips", self._chips(state, entity_id))
        return moved if ok else 0

    def _active_players(self, state: Any) -> List[Any]:
        """Players still in the hand (not folded, still have chips or all-in)."""
        out = []
        for p in self._players(state):
            if p.get("status") in ("folded", "out"):
                continue
            out.append(p)
        return out

    def _reset_for_new_hand(self, state: Any, table: Any, deck: Any):
        # Fresh deck
        cards = list(STANDARD_DECK)
        self._rng.shuffle(cards)
        deck.set("cards", cards)

        # Clear table community + pot + bets
        table.set("community_cards", [])
        table.set("current_bet", 0)
        if self._pot_chips(state):
            raise ValueError("Cannot start a new hand with an undistributed pot.")

        # Reset player per-hand state
        for p in self._players(state):
            p.set("status", "active" if self._chips(state, p.id) > 0 else "out")
            p.set("hand", [])
            p.set("bet_this_street", 0)
            p.set("bet_this_hand", 0)

        # Advance dealer button across players who still have chips.
        players = [p for p in self._players(state) if self._chips(state, p.id) > 0]
        if players:
            dealer_idx = int(table.get("dealer_idx", 0) or 0)
            table.set("dealer_idx", (dealer_idx + 1) % len(players))

        table.set("hand_number", int(table.get("hand_number", 0) or 0) + 1)
        table.set("phase", "deal")

    def _deal_hole_cards(self, state: Any, deck: Any):
        cards = list(deck.get("cards", []) or [])
        for p in self._players(state):
            if self._chips(state, p.id) <= 0:
                p.set("status", "out")
                continue
            hand = cards[:2]
            cards = cards[2:]
            p.set("hand", hand)
        deck.set("cards", cards)

    def _post_blinds(self, state: Any, table: Any):
        players_with_chips = [p for p in self._players(state) if self._chips(state, p.id) > 0]
        self._hand_seats = [p.id for p in players_with_chips]
        n = len(players_with_chips)
        if n < 2:
            return
        dealer_idx = int(table.get("dealer_idx", 0) or 0) % n
        # Heads-up rule: the DEALER posts the small blind, the other
        # player posts the big blind. Dealer also acts first pre-flop
        # and last post-flop. For 3+ players use the standard SB = dealer+1,
        # BB = dealer+2.
        if n == 2:
            sb_idx = dealer_idx
            bb_idx = (dealer_idx + 1) % n
        else:
            sb_idx = (dealer_idx + 1) % n
            bb_idx = (dealer_idx + 2) % n
        sb = players_with_chips[sb_idx]
        bb = players_with_chips[bb_idx]
        for player in players_with_chips:
            amount = self._move_chips(state, player.id, self._table_id, self._ante)
            player.set("bet_this_hand", amount)
            if self._chips(state, player.id) == 0:
                player.set("status", "all_in")
        sb_amt = self._move_chips(state, sb.id, self._table_id, self._small_blind)
        bb_amt = self._move_chips(state, bb.id, self._table_id, self._big_blind)
        sb.set("bet_this_street", sb_amt)
        sb.set("bet_this_hand", int(sb.get("bet_this_hand", 0)) + sb_amt)
        bb.set("bet_this_street", bb_amt)
        bb.set("bet_this_hand", int(bb.get("bet_this_hand", 0)) + bb_amt)
        table.set("current_bet", self._big_blind)
        for player in (sb, bb):
            if self._chips(state, player.id) == 0:
                player.set("status", "all_in")

    def _reveal_community(self, state: Any, table: Any, deck: Any, count: int):
        cards = list(deck.get("cards", []) or [])
        revealed = cards[:count]
        deck.set("cards", cards[count:])
        current = list(table.get("community_cards", []) or [])
        table.set("community_cards", current + revealed)
        # Reset per-street bets so call/raise math is straightforward.
        for p in self._players(state):
            if p.get("status") != "folded":
                p.set("bet_this_street", 0)
        table.set("current_bet", 0)

    def _showdown(self, state: Any, table: Any):
        community = list(table.get("community_cards", []) or [])
        players = self._players(state)
        contributions = {p.id: int(p.get("bet_this_hand", 0)) for p in players}
        scores = {p.id: _score_hand(list(p.get("hand", [])), community) for p in self._active_players(state) if p.get("hand")}
        payouts = {p.id: 0 for p in players}
        contested_winners = set()
        previous = 0
        levels = sorted({amount for amount in contributions.values() if amount > 0})
        for level in levels:
            contributors = [p.id for p in players if contributions[p.id] >= level]
            amount = (level - previous) * len(contributors)
            eligible = [pid for pid in contributors if pid in scores]
            if len(contributors) == 1:
                winners = contributors  # uncalled excess is returned
            elif eligible:
                best = max(scores[pid] for pid in eligible)
                winners = [pid for pid in eligible if scores[pid] == best]
                contested_winners.update(winners)
            else:
                raise ValueError("Side pot has no eligible player.")
            # Odd chips go clockwise from the dealer among tied winners.
            seats = self._hand_seats or [p.id for p in players]
            start = (int(table.get("dealer_idx", 0)) + 1) % len(seats)
            order = seats[start:] + seats[:start]
            winners.sort(key=order.index)
            share, remainder = divmod(amount, len(winners))
            for index, pid in enumerate(winners):
                payouts[pid] += share + (index < remainder)
            previous = level
        if sum(payouts.values()) != self._pot_chips(state):
            raise ValueError("Pot does not match recorded contributions; settlement stopped.")
        for pid, amount in payouts.items():
            if amount and self._move_chips(state, self._table_id, pid, amount) != amount:
                raise ValueError("Could not transfer the complete poker payout.")
        winners = [p for p in players if p.id in contested_winners]
        table.set("last_winner", ", ".join(p.name for p in winners))
        table.set("last_winning_hand", list(winners[0].get("hand", [])) + community if winners else [])
        table.set("payouts", payouts)
        self._pending = []

    # ------------------------------------------------------------------
    # Tick — drives hand-by-hand phase progression
    # ------------------------------------------------------------------

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._ensure_table(state)
        table = state.get_entity(self._table_id)
        deck = state.get_entity(self._deck_id)
        if table is None or deck is None:
            return []

        phase = str(table.get("phase", "deal"))
        if phase == "tournament_over":
            return []
        if phase == "showdown":
            phase = "deal"
            table.set("phase", "deal")
        events: List[Dict[str, Any]] = []

        # Fold-out short-circuit: if everyone but one player has folded,
        # there's nothing left to play. Award the pot to the last
        # standing contender, reset to a new deal, and skip the rest of
        # the street/community/showdown progression. Without this the
        # engine would keep dealing flop/turn/river to a single player.
        if phase in ("preflop", "flop", "turn", "river"):
            still_in = self._active_players(state)
            if len(still_in) == 1:
                winner = still_in[0]
                pot_chips = self._pot_chips(state)
                if pot_chips > 0:
                    self._move_chips(state, self._table_id, winner.id, pot_chips)
                table.set("last_winner", winner.name)
                table.set("last_winning_hand", [])
                table.set("phase", "showdown")
                self._pending = []
                events.append({
                    "type": "poker_hand_won_by_fold",
                    "narrative": (
                        f"{winner.name} wins the pot uncontested — "
                        f"everyone else folded. Pot: {pot_chips}."
                    ),
                    "data": {
                        "phase": "showdown",
                        "winner": winner.name,
                        "winner_id": winner.id,
                        "pot": pot_chips,
                        "won_by": "fold",
                    },
                })
                return events

        if phase in ("preflop", "flop", "turn", "river") and self._pending:
            return []
        if phase == "deal":
            solvent = [p for p in self._players(state) if self._chips(state, p.id) > 0]
            if len(solvent) < 2:
                if len(solvent) != 1 or self._pot_chips(state) != 0:
                    raise ValueError('Poker tournament cannot finish with an unsettled pot or no solvent winner')
                winner = solvent[0]
                table.set("phase", "tournament_over")
                return [{"type": "poker_match_over", "narrative": f"{winner.name} wins the tournament.",
                         "data": {"winner": winner.id, "winner_name": winner.name,
                                  "score": {p.id: self._chips(state, p.id) for p in self._players(state)}}}]
            self._reset_for_new_hand(state, table, deck)
            self._deal_hole_cards(state, deck)
            self._post_blinds(state, table)
            table.set("phase", "preflop")
            self._begin_betting(state, table, preflop=True)
            events.append({
                "type": "poker_phase",
                "narrative": (
                    f"Hand #{int(table.get('hand_number', 0) or 0)} dealt. "
                    f"Blinds {self._small_blind}/{self._big_blind}."
                ),
                "data": {
                    "phase": "preflop",
                    "small_blind": self._small_blind,
                    "big_blind": self._big_blind,
                    "ante": self._ante,
                    "pot": self._pot_chips(state),
                    "community_cards": list(table.get("community_cards", []) or []),
                },
            })
        elif phase == "preflop":
            self._reveal_community(state, table, deck, 3)
            table.set("phase", "flop")
            self._begin_betting(state, table)
            events.append(self._phase_event("flop", state, table))
        elif phase == "flop":
            self._reveal_community(state, table, deck, 1)
            table.set("phase", "turn")
            self._begin_betting(state, table)
            events.append(self._phase_event("turn", state, table))
        elif phase == "turn":
            self._reveal_community(state, table, deck, 1)
            table.set("phase", "river")
            self._begin_betting(state, table)
            events.append(self._phase_event("river", state, table))
        elif phase == "river":
            self._showdown(state, table)
            table.set("phase", "showdown")
            events.append({
                "type": "poker_showdown",
                "narrative": (
                    f"Showdown. {table.get('last_winner') or 'No one'} wins the pot."
                ),
                "data": {
                    "phase": "showdown",
                    "winner": table.get("last_winner"),
                    "pot": 0,
                    "community_cards": list(table.get("community_cards", []) or []),
                },
            })

        return events

    def _phase_event(self, phase: str, state: Any, table: Any) -> Dict[str, Any]:
        cards = list(table.get("community_cards", []) or [])
        return {
            "type": "poker_phase",
            "narrative": f"{phase.title()}: {', '.join(cards) if cards else '(no cards)'}.",
            "data": {
                "phase": phase,
                "community_cards": cards,
                "pot": self._pot_chips(state),
                "current_bet": int(table.get("current_bet", 0) or 0),
            },
        }

    # ------------------------------------------------------------------
    # Post-resolution — chip + status bookkeeping for poker actions
    # ------------------------------------------------------------------

    def post_resolution(
        self,
        actor_id: str,
        action_name: str,
        success: bool,
        result: Any,
        state: Any,
    ) -> List[Dict[str, Any]]:
        self._ensure_table(state)
        if not success or action_name not in {"bet", "raise_bet", "call", "fold", "check"}:
            return []

        table = state.get_entity(self._table_id)
        actor = state.get_entity(actor_id)
        if table is None or actor is None:
            return []
        if action_name not in self.filter_valid_actions(actor_id, self.custom_actions, state):
            return [{"type": "poker_invalid", "narrative": f"{actor.name}: action rejected; it is not legal on this turn."}]

        params = {}
        if result and getattr(result, "details", None):
            params = result.details.get("_action_params") or {}

        changes: List[Dict[str, Any]] = []

        if actor.get("status") == "folded" or actor.get("status") == "out":
            return changes

        current_bet = int(table.get("current_bet", 0) or 0)
        actor_bet_this_street = int(actor.get("bet_this_street", 0) or 0)
        actor_chips = self._chips(state, actor_id)

        requested = None
        if action_name in ("bet", "raise_bet"):
            requested = params.get("amount")
            maximum = actor_chips + (actor_bet_this_street if action_name == "raise_bet" else 0)
            minimum = min(maximum, current_bet + self._last_full_raise if action_name == "raise_bet" else self._big_blind)
            if isinstance(requested, bool) or not isinstance(requested, (int, float)) or not minimum <= requested <= maximum or int(requested) != requested:
                return [{"type": "poker_invalid", "narrative": f"{actor.name}: amount rejected; choose a whole number from {minimum} to {maximum}."}]
            requested = int(requested)

        def push_to_pot(amount: int, label: str):
            moved = self._move_chips(state, actor_id, self._table_id, amount)
            if moved <= 0:
                return 0
            actor.set("bet_this_street", actor_bet_this_street + moved)
            actor.set("bet_this_hand", int(actor.get("bet_this_hand", 0) or 0) + moved)
            new_current = max(current_bet, actor_bet_this_street + moved)
            table.set("current_bet", new_current)
            if self._chips(state, actor_id) <= 0:
                actor.set("status", "all_in")
            changes.append({
                "actor": actor_id,
                "action": label,
                "amount": moved,
                "pot": self._pot_chips(state),
                "current_bet": int(table.get("current_bet", 0) or 0),
            })
            return moved

        if action_name == "bet":
            push_to_pot(requested, "bet")

        elif action_name == "raise_bet":
            push_to_pot(requested - actor_bet_this_street, "raise")

        elif action_name == "call":
            owed = max(0, current_bet - actor_bet_this_street)
            if owed == 0:
                # No outstanding bet — treat as a check.
                changes.append({"actor": actor_id, "action": "check", "amount": 0, "pot": self._pot_chips(state)})
            else:
                push_to_pot(min(owed, actor_chips), "call")

        elif action_name == "fold":
            actor.set("status", "folded")
            # Keep `hand` populated so the visualizer can still show the
            # user what cards they were holding when they folded. Showdown
            # logic filters by status="folded" rather than empty hand.
            changes.append({
                "actor": actor_id,
                "action": "fold",
                "amount": 0,
                "pot": self._pot_chips(state),
            })

        elif action_name == "check":
            changes.append({
                "actor": actor_id,
                "action": "check",
                "amount": 0,
                "pot": self._pot_chips(state),
            })

        self._finish_action(state, table, actor_id, current_bet)

        # Mirror the resolved actions into the read-only transcript so
        # get_perception_data() can show a per-hand action history. This
        # only records what already happened — it changes no game state.
        hand_no = int(table.get("hand_number", 0) or 0)
        phase = str(table.get("phase", "") or "")
        for ch in changes:
            self._action_log.append({
                "hand": hand_no,
                "phase": phase,
                "actor_id": ch.get("actor"),
                "actor": getattr(actor, "name", ch.get("actor")),
                "action": ch.get("action"),
                "amount": int(ch.get("amount", 0) or 0),
            })
        # Bound memory — keep only the last 200 entries.
        if len(self._action_log) > 200:
            self._action_log = self._action_log[-200:]

        return changes

    # ------------------------------------------------------------------
    # Perception — let agents see pot, blinds, board, their hand
    # ------------------------------------------------------------------

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        self._ensure_table(state)
        table = state.get_entity(self._table_id)
        actor = state.get_entity(entity_id)
        if table is None:
            return {}

        # --- existing scalar keys (KEPT, unchanged) -----------------------
        data: Dict[str, Any] = {
            "pot": self._pot_chips(state),
            "current_bet": int(table.get("current_bet", 0) or 0),
            "small_blind": int(table.get("small_blind", 0) or 0),
            "big_blind": int(table.get("big_blind", 0) or 0),
            "phase": table.get("phase"),
            "community_cards": list(table.get("community_cards", []) or []),
            "hand_number": int(table.get("hand_number", 0) or 0),
        }
        if actor is not None:
            data["my_hand"] = list(actor.get("hand", []) or [])
            data["my_chips"] = self._chips(state, entity_id)
            data["my_status"] = actor.get("status")
            data["my_bet_this_street"] = int(actor.get("bet_this_street", 0) or 0)
            maximum = data["my_chips"] + data["my_bet_this_street"]
            data["minimum_raise_total"] = min(maximum, data["current_bet"] + self._last_full_raise)
            data["maximum_raise_total"] = maximum
            data["minimum_bet"] = min(self._big_blind, data["my_chips"])

        # --- new enrichment keys ------------------------------------------
        positions = self._position_labels(state, table)
        data["table_view"] = self._render_table_view(
            state, table, actor, entity_id, positions
        )
        data["legal_actions"] = self._legal_actions(state, table, actor)
        data["action_history"] = self._render_action_history(table)
        data["instructions"] = self._render_instructions(state, table, actor)
        return data

    # ------------------------------------------------------------------
    # Perception render helpers (private — display only, no state change)
    # ------------------------------------------------------------------

    def _position_labels(self, state: Any, table: Any) -> Dict[str, str]:
        """Map player id -> 'Dealer'/'Small Blind'/'Big Blind'/'' .

        Mirrors the seat math used by _post_blinds so the labels shown to
        agents match where the blinds were actually posted.
        """
        labels: Dict[str, str] = {}
        players_with_chips = [state.get_entity(pid) for pid in self._hand_seats]
        n = len(players_with_chips)
        if n == 0:
            return labels
        dealer_idx = int(table.get("dealer_idx", 0) or 0) % n
        labels[players_with_chips[dealer_idx].id] = "Dealer"
        if n >= 2:
            labels[players_with_chips[(dealer_idx + 1) % n].id] = "Small Blind"
        if n >= 3:
            labels[players_with_chips[(dealer_idx + 2) % n].id] = "Big Blind"
        elif n == 2:
            # Heads-up: dealer is SB, the other is BB.
            labels[players_with_chips[dealer_idx].id] = "Dealer/Small Blind"
            labels[players_with_chips[(dealer_idx + 1) % n].id] = "Big Blind"
        return labels

    def _player_status_label(self, status: Any) -> str:
        return {
            "active": "active",
            "folded": "FOLDED",
            "all_in": "ALL-IN",
            "out": "out (no chips)",
        }.get(str(status or "active"), str(status or "active"))

    def _render_table_view(
        self, state: Any, table: Any, actor: Any,
        entity_id: str, positions: Dict[str, str],
    ) -> str:
        """Multi-line plain-text rendering of the whole table.

        Shows community cards, pot, the bet to call, and one line per
        player. NEVER reveals another player's hole cards or the deck.
        """
        community = list(table.get("community_cards", []) or [])
        pot = self._pot_chips(state)
        current_bet = int(table.get("current_bet", 0) or 0)
        phase = str(table.get("phase", "") or "")
        hand_no = int(table.get("hand_number", 0) or 0)

        lines: List[str] = []
        lines.append(f"=== POKER TABLE — Hand #{hand_no} | Street: {phase} ===")
        board = " ".join(community) if community else "(none dealt yet)"
        lines.append(f"Community cards : {board}")
        lines.append(f"Pot             : {pot}")
        lines.append(f"Current bet     : {current_bet}")
        lines.append("")
        lines.append("Players (seat order):")
        for p in self._players(state):
            is_me = p.id == entity_id
            chips = self._chips(state, p.id)
            bet_street = int(p.get("bet_this_street", 0) or 0)
            status = self._player_status_label(p.get("status"))
            pos = positions.get(p.id, "")
            pos_tag = f" [{pos}]" if pos else ""
            name = p.name + (" (YOU)" if is_me else "")
            if is_me:
                hole = list(p.get("hand", []) or [])
                cards = " ".join(hole) if hole else "(no cards)"
            else:
                # Fairness: opponents' hole cards are always hidden.
                cards = "XX XX (hidden)"
            lines.append(
                f"  - {name}{pos_tag}: stack={chips}, "
                f"bet_this_street={bet_street}, status={status}, "
                f"hole_cards={cards}"
            )
        return "\n".join(lines)

    def _legal_actions(
        self, state: Any, table: Any, actor: Any,
    ) -> List[str]:
        """Explicit list of the moves this player may make right now."""
        if actor is None or table.get("phase") not in ("preflop", "flop", "turn", "river") or not self._pending or self._pending[0] != actor.id:
            return []
        status = actor.get("status")
        if status in ("folded", "out", "all_in"):
            return []
        current_bet = int(table.get("current_bet", 0) or 0)
        bet_this_street = int(actor.get("bet_this_street", 0) or 0)
        chips = self._chips(state, actor.id)
        if chips <= 0:
            return []
        owed = max(0, current_bet - bet_this_street)
        big_blind = int(table.get("big_blind", 0) or 0)

        actions: List[str] = ["fold"]
        if owed == 0:
            actions.append("check")
        else:
            call_amt = min(owed, chips)
            actions.append(f"call {call_amt}")
        if owed >= chips or actor.id in self._acted:
            # Cannot raise beyond a call — calling would put you all-in.
            return actions
        if current_bet == 0:
            # No bet yet: opening bet allowed.
            bet_min = min(big_blind, chips)
            actions.append(f"bet — min {bet_min}, max {chips} (all-in)")
        else:
            # Raise: target total at least current_bet + big_blind.
            raise_min_total = min(current_bet + self._last_full_raise, bet_this_street + chips)
            raise_max_total = bet_this_street + chips
            actions.append(
                f"raise_bet — min total {raise_min_total}, "
                f"max total {raise_max_total} (all-in)"
            )
        return actions

    def _render_action_history(self, table: Any) -> List[str]:
        """Readable transcript of actions in the CURRENT hand."""
        hand_no = int(table.get("hand_number", 0) or 0)
        out: List[str] = []
        for e in self._action_log:
            if e.get("hand") != hand_no:
                continue
            action = e.get("action")
            amount = int(e.get("amount", 0) or 0)
            phase = e.get("phase") or "?"
            who = e.get("actor") or "?"
            if action in ("bet", "raise", "call") and amount > 0:
                out.append(f"[{phase}] {who} {action} {amount}")
            else:
                out.append(f"[{phase}] {who} {action}")
        return out

    def _render_instructions(
        self, state: Any, table: Any, actor: Any,
    ) -> str:
        """Hand-held call-to-action sentence for the agent on move."""
        if actor is None:
            return "You are observing the poker table."
        if not self._legal_actions(state, table, actor):
            return "Wait for your next betting turn."
        status = actor.get("status")
        if status == "folded":
            return "You have FOLDED this hand — wait for the next hand."
        if status == "out":
            return "You are OUT (no chips) — you cannot act."
        if status == "all_in":
            return "You are ALL-IN — no further action this hand; wait."
        current_bet = int(table.get("current_bet", 0) or 0)
        bet_this_street = int(actor.get("bet_this_street", 0) or 0)
        owed = max(0, current_bet - bet_this_street)
        my_chips = self._chips(state, actor.id)
        pot = self._pot_chips(state)
        situation = (
            f"It costs {owed} chips to call. " if owed > 0
            else "You can check for free. "
        )
        return (
            "IT IS YOUR TURN at the poker table. Study `table_view` for the "
            "full board, then read `action_history` to see what opponents "
            f"did this round. The pot is {pot} and you have {my_chips} "
            f"chips. {situation}Pick EXACTLY ONE option from `legal_actions` "
            "and call the matching action (fold / check / call / bet / "
            "raise_bet). For bet and raise_bet supply an `amount`. Do NOT "
            "choose an action that is not listed in `legal_actions`."
        )
