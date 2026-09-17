"""Monopoly domain module — classic US-board property game.

Mechanics included (v1):
  - 40-space board: properties, railroads, utilities, GO, Jail, Chance,
    Community Chest, Free Parking, Income Tax, Luxury Tax, Go-To-Jail.
  - Roll 2d6 (with doubles → extra turn; 3 doubles in a row → jail).
  - Move + automatic landing action handling.
  - Buy-or-pass on unowned property (LLM decision).
  - Auto rent payment; monopolies double base rent on undeveloped property.
  - Houses/hotels (1–4 houses then hotel); rent scales per Monopoly's
    standard schedule.
  - Chance + Community Chest decks (16 cards each, classic effects).
  - Jail: pay $50, use a Get-Out-of-Jail card, or roll doubles. Forced
    leave after 3 turns.
  - Mortgaging (automatic) when a player can't cover a debt; bankruptcy
    when even mortgages can't cover.
  - Last solvent player wins.

Player-to-player trading: propose_trade / accept_trade / reject_trade.
Offers list properties + cash on each side; only unimproved, unmortgaged
properties are tradeable. The recipient sees the offer and must
accept/reject before their next normal action.

NOT in v1:
  - Auctions when a player declines to buy (auto-skip, property stays
    on the bank).
  - Free Parking pot variants.
  - "Even build" rule across a colour group.
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, List, Optional, Tuple

from fg_env.domain_module import DomainModule

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static board data
# ---------------------------------------------------------------------------

# Standard 40-space US Monopoly board. Index 0 = GO.
#
# Each space dict:
#   type: 'go' | 'property' | 'railroad' | 'utility' | 'chance' |
#         'community_chest' | 'tax' | 'jail' | 'free_parking' |
#         'go_to_jail'
#   name: display name
#   group: colour group for properties (None for non-properties)
#   price: purchase price ($) — None if not purchasable
#   rent: base rent ($) — for property: [base, 1h, 2h, 3h, 4h, hotel]
#         for railroad: [1rr, 2rr, 3rr, 4rr] = [25, 50, 100, 200]
#         for utility: multiplier on dice, depends on # owned
#   house_cost: $ to add a house (also hotel cost). Property only.
#   mortgage: $ received when mortgaged (half of price).

BOARD: List[Dict[str, Any]] = [
    {"type": "go", "name": "GO"},
    {"type": "property", "name": "Mediterranean Ave", "group": "brown",
     "price": 60, "rent": [2, 10, 30, 90, 160, 250], "house_cost": 50, "mortgage": 30},
    {"type": "community_chest", "name": "Community Chest"},
    {"type": "property", "name": "Baltic Ave", "group": "brown",
     "price": 60, "rent": [4, 20, 60, 180, 320, 450], "house_cost": 50, "mortgage": 30},
    {"type": "tax", "name": "Income Tax", "amount": 200},
    {"type": "railroad", "name": "Reading Railroad", "price": 200, "mortgage": 100},
    {"type": "property", "name": "Oriental Ave", "group": "light_blue",
     "price": 100, "rent": [6, 30, 90, 270, 400, 550], "house_cost": 50, "mortgage": 50},
    {"type": "chance", "name": "Chance"},
    {"type": "property", "name": "Vermont Ave", "group": "light_blue",
     "price": 100, "rent": [6, 30, 90, 270, 400, 550], "house_cost": 50, "mortgage": 50},
    {"type": "property", "name": "Connecticut Ave", "group": "light_blue",
     "price": 120, "rent": [8, 40, 100, 300, 450, 600], "house_cost": 50, "mortgage": 60},
    {"type": "jail", "name": "Jail / Just Visiting"},
    {"type": "property", "name": "St. Charles Place", "group": "pink",
     "price": 140, "rent": [10, 50, 150, 450, 625, 750], "house_cost": 100, "mortgage": 70},
    {"type": "utility", "name": "Electric Company", "price": 150, "mortgage": 75},
    {"type": "property", "name": "States Ave", "group": "pink",
     "price": 140, "rent": [10, 50, 150, 450, 625, 750], "house_cost": 100, "mortgage": 70},
    {"type": "property", "name": "Virginia Ave", "group": "pink",
     "price": 160, "rent": [12, 60, 180, 500, 700, 900], "house_cost": 100, "mortgage": 80},
    {"type": "railroad", "name": "Pennsylvania Railroad", "price": 200, "mortgage": 100},
    {"type": "property", "name": "St. James Place", "group": "orange",
     "price": 180, "rent": [14, 70, 200, 550, 750, 950], "house_cost": 100, "mortgage": 90},
    {"type": "community_chest", "name": "Community Chest"},
    {"type": "property", "name": "Tennessee Ave", "group": "orange",
     "price": 180, "rent": [14, 70, 200, 550, 750, 950], "house_cost": 100, "mortgage": 90},
    {"type": "property", "name": "New York Ave", "group": "orange",
     "price": 200, "rent": [16, 80, 220, 600, 800, 1000], "house_cost": 100, "mortgage": 100},
    {"type": "free_parking", "name": "Free Parking"},
    {"type": "property", "name": "Kentucky Ave", "group": "red",
     "price": 220, "rent": [18, 90, 250, 700, 875, 1050], "house_cost": 150, "mortgage": 110},
    {"type": "chance", "name": "Chance"},
    {"type": "property", "name": "Indiana Ave", "group": "red",
     "price": 220, "rent": [18, 90, 250, 700, 875, 1050], "house_cost": 150, "mortgage": 110},
    {"type": "property", "name": "Illinois Ave", "group": "red",
     "price": 240, "rent": [20, 100, 300, 750, 925, 1100], "house_cost": 150, "mortgage": 120},
    {"type": "railroad", "name": "B & O Railroad", "price": 200, "mortgage": 100},
    {"type": "property", "name": "Atlantic Ave", "group": "yellow",
     "price": 260, "rent": [22, 110, 330, 800, 975, 1150], "house_cost": 150, "mortgage": 130},
    {"type": "property", "name": "Ventnor Ave", "group": "yellow",
     "price": 260, "rent": [22, 110, 330, 800, 975, 1150], "house_cost": 150, "mortgage": 130},
    {"type": "utility", "name": "Water Works", "price": 150, "mortgage": 75},
    {"type": "property", "name": "Marvin Gardens", "group": "yellow",
     "price": 280, "rent": [24, 120, 360, 850, 1025, 1200], "house_cost": 150, "mortgage": 140},
    {"type": "go_to_jail", "name": "Go To Jail"},
    {"type": "property", "name": "Pacific Ave", "group": "green",
     "price": 300, "rent": [26, 130, 390, 900, 1100, 1275], "house_cost": 200, "mortgage": 150},
    {"type": "property", "name": "North Carolina Ave", "group": "green",
     "price": 300, "rent": [26, 130, 390, 900, 1100, 1275], "house_cost": 200, "mortgage": 150},
    {"type": "community_chest", "name": "Community Chest"},
    {"type": "property", "name": "Pennsylvania Ave", "group": "green",
     "price": 320, "rent": [28, 150, 450, 1000, 1200, 1400], "house_cost": 200, "mortgage": 160},
    {"type": "railroad", "name": "Short Line", "price": 200, "mortgage": 100},
    {"type": "chance", "name": "Chance"},
    {"type": "property", "name": "Park Place", "group": "dark_blue",
     "price": 350, "rent": [35, 175, 500, 1100, 1300, 1500], "house_cost": 200, "mortgage": 175},
    {"type": "tax", "name": "Luxury Tax", "amount": 100},
    {"type": "property", "name": "Boardwalk", "group": "dark_blue",
     "price": 400, "rent": [50, 200, 600, 1400, 1700, 2000], "house_cost": 200, "mortgage": 200},
]

JAIL_SPACE = 10
GO_SALARY = 200
JAIL_FINE = 50
MAX_HOUSES_PER_PROPERTY = 4  # then a hotel

# Pre-compute group → property indices for monopoly checks.
GROUP_TO_INDICES: Dict[str, List[int]] = {}
for _i, _s in enumerate(BOARD):
    if _s["type"] == "property":
        GROUP_TO_INDICES.setdefault(_s["group"], []).append(_i)

RAILROAD_INDICES = [i for i, s in enumerate(BOARD) if s["type"] == "railroad"]
UTILITY_INDICES = [i for i, s in enumerate(BOARD) if s["type"] == "utility"]


# ---------------------------------------------------------------------------
# Card decks (classic Monopoly Chance + Community Chest)
# ---------------------------------------------------------------------------
#
# Each card dict has a `text` (for display) and an `effect` callback name.
# The module dispatches the effect; we keep them as strings + params so
# cards remain serializable.

def _chance_deck() -> List[Dict[str, Any]]:
    return [
        {"text": "Advance to GO. Collect $200.", "effect": "advance_to", "space": 0},
        {"text": "Advance to Illinois Ave. Collect $200 if you pass GO.", "effect": "advance_to", "space": 24},
        {"text": "Advance to St. Charles Place. Collect $200 if you pass GO.", "effect": "advance_to", "space": 11},
        {"text": "Advance to nearest Utility. Pay 10× dice roll if owned.", "effect": "advance_to_nearest", "kind": "utility"},
        {"text": "Advance to nearest Railroad. Pay double rent if owned.", "effect": "advance_to_nearest", "kind": "railroad"},
        {"text": "Bank pays you a dividend of $50.", "effect": "collect", "amount": 50},
        {"text": "Get Out of Jail Free.", "effect": "get_out_of_jail_card"},
        {"text": "Go back 3 spaces.", "effect": "move_relative", "delta": -3},
        {"text": "Go directly to Jail. Do not pass GO.", "effect": "go_to_jail"},
        {"text": "Make general repairs: $25/house, $100/hotel.", "effect": "repairs", "house": 25, "hotel": 100},
        {"text": "Pay poor tax of $15.", "effect": "pay", "amount": 15},
        {"text": "Take a trip to Reading Railroad. Collect $200 if you pass GO.", "effect": "advance_to", "space": 5},
        {"text": "Take a walk on the Boardwalk.", "effect": "advance_to", "space": 39},
        {"text": "You have been elected Chairman of the Board. Pay each player $50.", "effect": "pay_each_player", "amount": 50},
        {"text": "Your building loan matures. Collect $150.", "effect": "collect", "amount": 150},
        {"text": "You have won a crossword competition. Collect $100.", "effect": "collect", "amount": 100},
    ]


def _community_chest_deck() -> List[Dict[str, Any]]:
    return [
        {"text": "Advance to GO. Collect $200.", "effect": "advance_to", "space": 0},
        {"text": "Bank error in your favor. Collect $200.", "effect": "collect", "amount": 200},
        {"text": "Doctor's fee. Pay $50.", "effect": "pay", "amount": 50},
        {"text": "From sale of stock you get $50.", "effect": "collect", "amount": 50},
        {"text": "Get Out of Jail Free.", "effect": "get_out_of_jail_card"},
        {"text": "Go to Jail. Do not pass GO.", "effect": "go_to_jail"},
        {"text": "Holiday fund matures. Receive $100.", "effect": "collect", "amount": 100},
        {"text": "Income tax refund. Collect $20.", "effect": "collect", "amount": 20},
        {"text": "It is your birthday. Collect $10 from every player.", "effect": "collect_from_each", "amount": 10},
        {"text": "Life insurance matures. Collect $100.", "effect": "collect", "amount": 100},
        {"text": "Pay hospital fees of $100.", "effect": "pay", "amount": 100},
        {"text": "Pay school fees of $50.", "effect": "pay", "amount": 50},
        {"text": "Receive $25 consultancy fee.", "effect": "collect", "amount": 25},
        {"text": "You are assessed for street repair: $40/house, $115/hotel.", "effect": "repairs", "house": 40, "hotel": 115},
        {"text": "You have won second prize in a beauty contest. Collect $10.", "effect": "collect", "amount": 10},
        {"text": "You inherit $100.", "effect": "collect", "amount": 100},
    ]


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class MonopolyModule(DomainModule):
    """Classic Monopoly engine."""

    def __init__(self, name: str = "monopoly", params: Optional[Dict[str, Any]] = None):
        super().__init__(name=name, params=params)
        seed = self._params.get("seed")
        self._rng = random.Random(seed) if seed is not None else random.Random()

        # Property state: space_index → {owner_entity_id, houses, hotel, mortgaged}
        self._properties: Dict[int, Dict[str, Any]] = {}
        # Players: entity_id → {position, money, in_jail, jail_turns, jail_cards,
        #                       doubles_streak, bankrupt}
        self._players: Dict[str, Dict[str, Any]] = {}
        # Turn order — set on first tick from state.
        self._turn_order: List[str] = []
        self._turn_index: int = 0
        # Shuffled decks; pop from end, append to front on use.
        self._chance: List[Dict[str, Any]] = list(_chance_deck())
        self._community: List[Dict[str, Any]] = list(_community_chest_deck())
        self._rng.shuffle(self._chance)
        self._rng.shuffle(self._community)
        # Last roll (for utility rent calc + UI).
        self._last_roll: Tuple[int, int] = (0, 0)
        # Event log of turn-by-turn narration (compact strings for the UI).
        self._turn_log: List[str] = []

        # Pending decision: when set, the active player owes the engine
        # an action of this kind before their turn can advance.
        # Shape: {kind: 'buy'|'build'|'leave_jail', space?: int, ...}
        self._pending: Optional[Dict[str, Any]] = None
        # Open trade offers keyed by recipient_id. One slot per recipient —
        # a new offer to the same player overwrites the previous one.
        # Shape: {proposer, give_properties: [int], give_money: int,
        #         want_properties: [int], want_money: int, note: str}
        self._pending_trades: Dict[str, Dict[str, Any]] = {}
        # Structured trade history — every accept/reject/counter outcome.
        # Used by perception so agents see precise prior terms and can
        # avoid re-proposing identical losing deals.
        self._trade_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Domain contract
    # ------------------------------------------------------------------ #

    @property
    def description(self) -> str:
        return "Classic Monopoly: dice → move → buy/rent/build → bankrupt the rest"

    @property
    def custom_actions(self) -> List[str]:
        # buy_property / pass_property — landing on unowned space
        # build_house / sell_house — between-turn improvements
        # pay_jail_fine / use_jail_card / roll_in_jail — jail decisions
        # end_turn — explicit advance
        return [
            "buy_property", "pass_property",
            "build_house", "sell_house",
            "pay_jail_fine", "use_jail_card", "roll_in_jail",
            "propose_trade", "accept_trade", "reject_trade", "counter_trade",
            "end_turn",
        ]

    @property
    def required_properties(self) -> List[str]:
        return ["money"]

    # ------------------------------------------------------------------ #
    # Setup
    # ------------------------------------------------------------------ #

    def _ensure_player(self, entity_id: str, starting_money: int) -> Dict[str, Any]:
        p = self._players.get(entity_id)
        if p is None:
            p = {
                "position": 0,
                "money": starting_money,
                "in_jail": False,
                "jail_turns": 0,
                "jail_cards": 0,
                "doubles_streak": 0,
                "bankrupt": False,
            }
            self._players[entity_id] = p
        return p

    def _seed_from_state(self, state: Any) -> None:
        """Initialize players + turn order from world state."""
        if self._turn_order:
            return
        starting_money = int(self._params.get("starting_money", 1500))
        agents = list(state.get_agent_entities()) if hasattr(state, "get_agent_entities") else []
        if not agents:
            return
        for ent in agents:
            self._ensure_player(ent.id, starting_money)
            # Mirror money onto the entity property so the engine + UI can see it.
            if hasattr(ent, "properties"):
                ent.properties["money"] = starting_money
                ent.properties["position"] = 0
        self._turn_order = [a.id for a in agents]
        self._turn_index = 0
        logger.info(
            "Monopoly initialized: %d players, $%d starting money",
            len(self._turn_order), starting_money,
        )

    # ------------------------------------------------------------------ #
    # tick — runs at start of every round; we use it to seed initial state
    # and to advance the dice for the active player at the START of their
    # turn. Per-decision actions (buy/build) are handled via custom actions.
    # ------------------------------------------------------------------ #

    def tick(self, state: Any, round_number: int) -> List[Dict[str, Any]]:
        self._seed_from_state(state)
        # Solvent player count check — if only one remains, the engine's
        # termination_condition handles ending the sim.
        solvent = [pid for pid, p in self._players.items() if not p["bankrupt"]]
        if len(solvent) <= 1:
            return [{"type": "monopoly_game_over", "winner": solvent[0] if solvent else None}]
        return []

    # ------------------------------------------------------------------ #
    # Action handling — we drive the game loop here.
    # The engine calls validate_action + post_resolution. We use
    # post_resolution to update internal state.
    # ------------------------------------------------------------------ #

    def filter_valid_actions(self, entity_id: str, valid_actions: List[str], state: Any) -> List[str]:
        """Constrain the action set based on game state."""
        p = self._players.get(entity_id)
        if not p or p["bankrupt"]:
            return []

        # Only the active player can act on Monopoly-specific actions —
        # EXCEPT for accept_trade / reject_trade, which any player with an
        # open offer addressed to them can take at any time. Trades are
        # asynchronous: A proposes on their turn, B accepts/rejects on B's
        # turn (or the next chance B gets to act).
        active = self._active_player_id()
        base_out: List[str] = [a for a in valid_actions if a not in self.custom_actions]

        if entity_id != active:
            # Non-active player: only trade-response actions are available.
            if entity_id in self._pending_trades:
                base_out.extend(["accept_trade", "reject_trade", "counter_trade"])
            return base_out

        out = base_out

        if p["in_jail"]:
            # While in jail: pay, use card, or roll. After 3 turns must pay.
            out.extend(["pay_jail_fine", "use_jail_card", "roll_in_jail"])
            return out

        # Pending offer addressed to the active player — they MUST resolve
        # it before continuing the normal flow (otherwise it'd sit forever).
        if entity_id in self._pending_trades:
            out.extend(["accept_trade", "reject_trade", "counter_trade"])
            return out

        # Active player has an OUTGOING offer that hasn't been answered
        # yet. They cannot end their turn (or buy/build their way past it)
        # until the recipient accepts or rejects — turn is pinned until
        # the negotiation settles. They can still propose AMENDED terms
        # (re-proposing to the same recipient overwrites the open offer)
        # or to a different player.
        has_outgoing = any(o.get("proposer") == entity_id for o in self._pending_trades.values())

        if self._pending is None:
            # Beginning of turn — must roll. The "roll" itself isn't an LLM
            # decision; we trigger it via the implicit `end_turn` flow.
            # For simplicity, we let the player optionally build first, then
            # end_turn advances to the next player after rolling.
            out.extend(["build_house", "sell_house", "propose_trade"])
            if not has_outgoing:
                out.append("end_turn")
            return out

        if self._pending.get("kind") == "buy":
            # Buy-or-pass is forced; trade can't preempt a landed-on
            # purchase decision.
            out.extend(["buy_property", "pass_property"])
            return out

        # Building decisions allowed between resolved landing & end_turn
        out.extend(["build_house", "sell_house", "propose_trade"])
        if not has_outgoing:
            out.append("end_turn")
        return out

    def post_resolution(
        self,
        actor_id: str,
        action_name: str,
        success: bool,
        result: Any,
        state: Any,
    ) -> List[Dict[str, Any]]:
        if not success or action_name not in self.custom_actions:
            return []

        details = getattr(result, "details", {}) if hasattr(result, "details") else {}
        changes: List[Dict[str, Any]] = []

        if action_name == "end_turn":
            changes.extend(self._handle_end_turn(actor_id, state))
        elif action_name == "buy_property":
            changes.extend(self._handle_buy(actor_id, state, accept=True))
            self._pending = None
        elif action_name == "pass_property":
            changes.extend(self._handle_buy(actor_id, state, accept=False))
            self._pending = None
        elif action_name == "build_house":
            changes.extend(self._handle_build(actor_id, state, details.get("space")))
        elif action_name == "sell_house":
            changes.extend(self._handle_sell(actor_id, state, details.get("space")))
        elif action_name == "pay_jail_fine":
            changes.extend(self._handle_pay_jail(actor_id, state))
        elif action_name == "use_jail_card":
            changes.extend(self._handle_use_jail_card(actor_id, state))
        elif action_name == "roll_in_jail":
            changes.extend(self._handle_roll_in_jail(actor_id, state))
        elif action_name == "propose_trade":
            params = details.get("_action_params", {}) or {}
            target = details.get("target_id") or params.get("target_id")
            changes.extend(self._handle_propose_trade(actor_id, target, params, state))
        elif action_name == "accept_trade":
            changes.extend(self._handle_accept_trade(actor_id, state))
        elif action_name == "reject_trade":
            changes.extend(self._handle_reject_trade(actor_id, state))
        elif action_name == "counter_trade":
            params = details.get("_action_params", {}) or {}
            changes.extend(self._handle_counter_trade(actor_id, params, state))

        return changes

    # ------------------------------------------------------------------ #
    # Core game-loop handlers
    # ------------------------------------------------------------------ #

    def _active_player_id(self) -> Optional[str]:
        if not self._turn_order:
            return None
        # Skip bankrupt players
        for _ in range(len(self._turn_order)):
            pid = self._turn_order[self._turn_index]
            if not self._players[pid]["bankrupt"]:
                return pid
            self._turn_index = (self._turn_index + 1) % len(self._turn_order)
        return None

    def _advance_turn(self) -> None:
        if not self._turn_order:
            return
        self._turn_index = (self._turn_index + 1) % len(self._turn_order)
        # Reset doubles streak for the player who *just* ended their turn.
        # (We reset on entry to next turn below.)

    def _handle_end_turn(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        """End-of-turn ⇒ roll dice for the NEXT active player and process landing."""
        changes: List[Dict[str, Any]] = []
        # Move to next player.
        self._advance_turn()
        next_id = self._active_player_id()
        if next_id is None:
            return changes
        # Reset rolling player's doubles streak at start of their turn.
        self._players[next_id]["doubles_streak"] = 0
        changes.extend(self._roll_and_resolve(next_id, state))
        return changes

    def _roll_and_resolve(self, player_id: str, state: Any) -> List[Dict[str, Any]]:
        """Roll dice for `player_id`, move, and run landing logic."""
        p = self._players[player_id]
        if p["bankrupt"]:
            return []
        d1, d2 = self._rng.randint(1, 6), self._rng.randint(1, 6)
        self._last_roll = (d1, d2)
        doubles = d1 == d2
        if doubles:
            p["doubles_streak"] += 1
        else:
            p["doubles_streak"] = 0
        self._log(f"{self._name_of(state, player_id)} rolled {d1}+{d2}={d1+d2}{' (doubles)' if doubles else ''}")
        if p["doubles_streak"] >= 3:
            self._log(f"{self._name_of(state, player_id)} sent to jail (3 doubles)")
            return self._send_to_jail(player_id, state, [
                {"type": "monopoly_dice", "player": player_id, "d1": d1, "d2": d2, "doubles": True}
            ])
        old_pos = p["position"]
        p["position"] = (p["position"] + d1 + d2) % 40
        if p["position"] < old_pos:
            self._credit(player_id, GO_SALARY, state)
            self._log(f"{self._name_of(state, player_id)} passed GO (+${GO_SALARY})")
        self._sync_position_property(player_id, state)
        events: List[Dict[str, Any]] = [{
            "type": "monopoly_dice",
            "player": player_id,
            "d1": d1, "d2": d2, "doubles": doubles,
            "new_position": p["position"],
        }]
        events.extend(self._resolve_landing(player_id, state))
        return events

    def _resolve_landing(self, player_id: str, state: Any) -> List[Dict[str, Any]]:
        """Run landing logic for the player's current space."""
        p = self._players[player_id]
        space = BOARD[p["position"]]
        events: List[Dict[str, Any]] = [{
            "type": "monopoly_landed",
            "player": player_id,
            "space": p["position"],
            "name": space["name"],
        }]
        kind = space["type"]
        if kind == "go":
            pass  # Salary already credited via pass-GO
        elif kind == "property" or kind == "railroad" or kind == "utility":
            events.extend(self._resolve_purchasable(player_id, p["position"], state))
        elif kind == "tax":
            events.extend(self._resolve_tax(player_id, space, state))
        elif kind == "chance":
            events.extend(self._draw_card(player_id, "chance", state))
        elif kind == "community_chest":
            events.extend(self._draw_card(player_id, "community_chest", state))
        elif kind == "go_to_jail":
            events.extend(self._send_to_jail(player_id, state, []))
        # free_parking, jail (just visiting) → nothing
        return events

    def _resolve_purchasable(self, player_id: str, space_idx: int, state: Any) -> List[Dict[str, Any]]:
        """Buy if unowned, pay rent if owned by someone else."""
        space = BOARD[space_idx]
        prop = self._properties.get(space_idx)
        if prop is None:
            # Unowned → ask LLM to buy or pass
            self._pending = {"kind": "buy", "space": space_idx}
            return [{
                "type": "monopoly_unowned",
                "player": player_id,
                "space": space_idx,
                "price": space["price"],
            }]
        if prop["owner"] == player_id:
            return []  # Own it — no action
        if prop["mortgaged"]:
            return [{"type": "monopoly_mortgaged_visit", "player": player_id, "space": space_idx}]
        rent = self._calc_rent(space_idx)
        return self._pay_rent(player_id, prop["owner"], rent, space_idx, state)

    def _calc_rent(self, space_idx: int) -> int:
        space = BOARD[space_idx]
        prop = self._properties.get(space_idx)
        if not prop:
            return 0
        owner = prop["owner"]
        if space["type"] == "property":
            base = int(space["rent"][0])
            if prop["hotel"]:
                return int(space["rent"][5])
            houses = int(prop["houses"])
            if houses > 0:
                return int(space["rent"][houses])
            # No buildings — monopoly check doubles base rent
            group = space["group"]
            if self._owns_full_group(owner, group):
                return base * 2
            return base
        if space["type"] == "railroad":
            owned = sum(1 for i in RAILROAD_INDICES
                        if self._properties.get(i, {}).get("owner") == owner)
            rents = [25, 50, 100, 200]
            return rents[owned - 1] if 1 <= owned <= 4 else 25
        if space["type"] == "utility":
            owned = sum(1 for i in UTILITY_INDICES
                        if self._properties.get(i, {}).get("owner") == owner)
            multiplier = 10 if owned >= 2 else 4
            d1, d2 = self._last_roll
            return (d1 + d2) * multiplier
        return 0

    def _pay_rent(self, payer: str, receiver: str, amount: int, space_idx: int, state: Any) -> List[Dict[str, Any]]:
        actually_paid = self._charge(payer, amount, state, creditor=receiver)
        self._credit(receiver, actually_paid, state)
        self._log(f"{self._name_of(state, payer)} paid ${actually_paid} rent to {self._name_of(state, receiver)}")
        events = [{
            "type": "monopoly_rent",
            "payer": payer,
            "receiver": receiver,
            "amount": actually_paid,
            "space": space_idx,
        }]
        if self._players[payer]["bankrupt"]:
            events.extend(self._bankrupt(payer, receiver, state))
        return events

    def _resolve_tax(self, player_id: str, space: Dict[str, Any], state: Any) -> List[Dict[str, Any]]:
        amt = int(space.get("amount", 0))
        self._charge(player_id, amt, state)
        self._log(f"{self._name_of(state, player_id)} paid ${amt} ({space['name']})")
        events = [{"type": "monopoly_tax", "player": player_id, "amount": amt}]
        if self._players[player_id]["bankrupt"]:
            events.extend(self._bankrupt(player_id, None, state))
        return events

    # ------------------------------------------------------------------ #
    # Buy / build / sell
    # ------------------------------------------------------------------ #

    def _handle_buy(self, actor_id: str, state: Any, accept: bool) -> List[Dict[str, Any]]:
        pending = self._pending
        if not pending or pending.get("kind") != "buy":
            return []
        space_idx = pending["space"]
        space = BOARD[space_idx]
        if not accept:
            self._log(f"{self._name_of(state, actor_id)} declined {space['name']}")
            return [{"type": "monopoly_declined", "player": actor_id, "space": space_idx}]
        price = int(space["price"])
        if self._players[actor_id]["money"] < price:
            return [{"type": "monopoly_buy_failed", "player": actor_id, "reason": "insufficient_funds"}]
        self._charge(actor_id, price, state)
        self._properties[space_idx] = {
            "owner": actor_id, "houses": 0, "hotel": False, "mortgaged": False,
        }
        self._log(f"{self._name_of(state, actor_id)} bought {space['name']} (${price})")
        return [{"type": "monopoly_bought", "player": actor_id, "space": space_idx, "price": price}]

    def _handle_build(self, actor_id: str, state: Any, space_hint: Optional[int]) -> List[Dict[str, Any]]:
        # Pick which property to build on — agent may specify; otherwise
        # auto-select the first eligible.
        candidates = self._buildable_spaces(actor_id)
        if not candidates:
            return [{"type": "monopoly_build_failed", "player": actor_id, "reason": "no_eligible_property"}]
        target = space_hint if (space_hint in candidates) else candidates[0]
        space = BOARD[target]
        prop = self._properties[target]
        cost = int(space["house_cost"])
        if self._players[actor_id]["money"] < cost:
            return [{"type": "monopoly_build_failed", "player": actor_id, "reason": "insufficient_funds"}]
        self._charge(actor_id, cost, state)
        if prop["houses"] >= MAX_HOUSES_PER_PROPERTY:
            prop["hotel"] = True
            prop["houses"] = 0  # houses replaced by hotel in classic rules
            self._log(f"{self._name_of(state, actor_id)} built a hotel on {space['name']}")
            return [{"type": "monopoly_hotel", "player": actor_id, "space": target, "cost": cost}]
        prop["houses"] += 1
        self._log(f"{self._name_of(state, actor_id)} built a house on {space['name']} (now {prop['houses']})")
        return [{"type": "monopoly_house", "player": actor_id, "space": target, "houses": prop["houses"], "cost": cost}]

    def _handle_sell(self, actor_id: str, state: Any, space_hint: Optional[int]) -> List[Dict[str, Any]]:
        # Sell a house/hotel for half cost.
        candidates = [i for i, prop in self._properties.items()
                      if prop["owner"] == actor_id and (prop["houses"] > 0 or prop["hotel"])]
        if not candidates:
            return [{"type": "monopoly_sell_failed", "player": actor_id, "reason": "no_buildings"}]
        target = space_hint if (space_hint in candidates) else candidates[0]
        space = BOARD[target]
        prop = self._properties[target]
        refund = int(space["house_cost"]) // 2
        if prop["hotel"]:
            prop["hotel"] = False
            prop["houses"] = MAX_HOUSES_PER_PROPERTY
            self._credit(actor_id, refund, state)
            self._log(f"{self._name_of(state, actor_id)} sold hotel on {space['name']} (+${refund})")
            return [{"type": "monopoly_sold_hotel", "player": actor_id, "space": target, "refund": refund}]
        prop["houses"] -= 1
        self._credit(actor_id, refund, state)
        return [{"type": "monopoly_sold_house", "player": actor_id, "space": target,
                 "houses": prop["houses"], "refund": refund}]

    def _buildable_spaces(self, actor_id: str) -> List[int]:
        out: List[int] = []
        for group, indices in GROUP_TO_INDICES.items():
            if not self._owns_full_group(actor_id, group):
                continue
            # Any property in a full group with no mortgages → buildable
            if any(self._properties.get(i, {}).get("mortgaged") for i in indices):
                continue
            for i in indices:
                prop = self._properties.get(i, {})
                if prop.get("owner") == actor_id and not prop.get("hotel", False):
                    out.append(i)
        return out

    def _owns_full_group(self, player_id: str, group: str) -> bool:
        idxs = GROUP_TO_INDICES.get(group, [])
        return all(self._properties.get(i, {}).get("owner") == player_id for i in idxs)

    # ------------------------------------------------------------------ #
    # Trading (player-to-player)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _coerce_int_list(raw: Any) -> List[int]:
        if raw is None:
            return []
        if isinstance(raw, (int, float)):
            return [int(raw)]
        if isinstance(raw, str):
            # Comma-separated string fallback ("12,15")
            try:
                return [int(x.strip()) for x in raw.split(",") if x.strip()]
            except ValueError:
                return []
        if isinstance(raw, list):
            out: List[int] = []
            for v in raw:
                try:
                    out.append(int(v))
                except (TypeError, ValueError):
                    continue
            return out
        return []

    def _owned_unimproved(self, player_id: str, space_idx: int) -> bool:
        """A property must be owned by `player_id` AND have no buildings
        AND not be mortgaged to be tradeable in v1."""
        prop = self._properties.get(space_idx)
        if not prop or prop.get("owner") != player_id:
            return False
        if prop.get("mortgaged"):
            return False
        if prop.get("houses", 0) > 0 or prop.get("hotel"):
            return False
        return True

    def _handle_propose_trade(
        self, proposer_id: str, target_id: Optional[str],
        params: Dict[str, Any], state: Any,
    ) -> List[Dict[str, Any]]:
        if not target_id or target_id == proposer_id:
            return [{"type": "monopoly_trade_failed",
                     "player": proposer_id, "reason": "no_target"}]
        if target_id not in self._players or self._players[target_id]["bankrupt"]:
            return [{"type": "monopoly_trade_failed",
                     "player": proposer_id, "reason": "target_invalid"}]

        give_props = self._coerce_int_list(params.get("give_properties"))
        want_props = self._coerce_int_list(params.get("want_properties"))
        try:
            give_money = max(0, int(params.get("give_money") or 0))
            want_money = max(0, int(params.get("want_money") or 0))
        except (TypeError, ValueError):
            give_money, want_money = 0, 0
        note = str(params.get("note") or "").strip()[:200]

        # Must offer SOMETHING in each direction (any combination of
        # money or properties on either side counts).
        if (not give_props and give_money == 0) and (not want_props and want_money == 0):
            return [{"type": "monopoly_trade_failed",
                     "player": proposer_id, "reason": "empty_offer"}]

        # Validate ownership at proposal time so junk offers don't sit
        # in the queue. We re-validate at accept time too.
        for sp in give_props:
            if not self._owned_unimproved(proposer_id, sp):
                return [{"type": "monopoly_trade_failed", "player": proposer_id,
                         "reason": "give_property_not_tradeable", "space": sp}]
        for sp in want_props:
            if not self._owned_unimproved(target_id, sp):
                return [{"type": "monopoly_trade_failed", "player": proposer_id,
                         "reason": "want_property_not_tradeable", "space": sp}]
        if self._players[proposer_id]["money"] < give_money:
            return [{"type": "monopoly_trade_failed", "player": proposer_id,
                     "reason": "insufficient_cash_to_offer"}]

        offer = {
            "proposer": proposer_id,
            "give_properties": give_props,
            "give_money": give_money,
            "want_properties": want_props,
            "want_money": want_money,
            "note": note,
        }
        self._pending_trades[target_id] = offer
        self._trade_log.append({
            "outcome": "proposed",
            "proposer": proposer_id,
            "target": target_id,
            "give_properties": give_props,
            "give_money": give_money,
            "want_properties": want_props,
            "want_money": want_money,
            "note": note,
        })
        self._log(
            f"{self._name_of(state, proposer_id)} offered "
            f"{self._name_of(state, target_id)} a trade"
        )
        return [{
            "type": "monopoly_trade_proposed",
            "player": proposer_id,
            "target": target_id,
            "give_properties": give_props,
            "give_money": give_money,
            "want_properties": want_props,
            "want_money": want_money,
            "note": note,
        }]

    def _handle_accept_trade(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        offer = self._pending_trades.pop(actor_id, None)
        if not offer:
            return [{"type": "monopoly_trade_failed",
                     "player": actor_id, "reason": "no_open_offer"}]
        proposer = offer["proposer"]
        if proposer not in self._players or self._players[proposer]["bankrupt"]:
            return [{"type": "monopoly_trade_cancelled",
                     "player": actor_id, "reason": "proposer_unavailable"}]

        # Re-validate ownership & cash NOW — state may have changed since
        # proposal (rent paid, bankruptcies, buildings added).
        for sp in offer["give_properties"]:
            if not self._owned_unimproved(proposer, sp):
                return [{"type": "monopoly_trade_cancelled",
                         "player": actor_id, "reason": "give_property_changed", "space": sp}]
        for sp in offer["want_properties"]:
            if not self._owned_unimproved(actor_id, sp):
                return [{"type": "monopoly_trade_cancelled",
                         "player": actor_id, "reason": "want_property_changed", "space": sp}]
        if self._players[proposer]["money"] < offer["give_money"]:
            return [{"type": "monopoly_trade_cancelled",
                     "player": actor_id, "reason": "proposer_short_on_cash"}]
        if self._players[actor_id]["money"] < offer["want_money"]:
            return [{"type": "monopoly_trade_cancelled",
                     "player": actor_id, "reason": "acceptor_short_on_cash"}]

        # Execute: swap properties, then settle cash.
        for sp in offer["give_properties"]:
            self._properties[sp]["owner"] = actor_id
        for sp in offer["want_properties"]:
            self._properties[sp]["owner"] = proposer
        if offer["give_money"] > 0:
            self._charge(proposer, offer["give_money"], state)
            self._credit(actor_id, offer["give_money"], state)
        if offer["want_money"] > 0:
            self._charge(actor_id, offer["want_money"], state)
            self._credit(proposer, offer["want_money"], state)

        self._trade_log.append({
            "outcome": "accepted",
            "proposer": proposer,
            "target": actor_id,
            "give_properties": offer["give_properties"],
            "give_money": offer["give_money"],
            "want_properties": offer["want_properties"],
            "want_money": offer["want_money"],
        })
        self._log(
            f"{self._name_of(state, actor_id)} accepted trade from "
            f"{self._name_of(state, proposer)}"
        )
        return [{
            "type": "monopoly_trade_accepted",
            "player": actor_id,
            "proposer": proposer,
            "give_properties": offer["give_properties"],
            "give_money": offer["give_money"],
            "want_properties": offer["want_properties"],
            "want_money": offer["want_money"],
        }]

    # Cap how many times an offer can ricochet between two players —
    # without this, two stubborn LLMs could counter each other forever
    # and pin the active player's turn indefinitely. After this many
    # round-trips the offer is auto-rejected.
    _MAX_COUNTER_DEPTH = 6

    def _handle_counter_trade(
        self, actor_id: str, params: Dict[str, Any], state: Any,
    ) -> List[Dict[str, Any]]:
        incoming = self._pending_trades.pop(actor_id, None)
        if not incoming:
            return [{"type": "monopoly_trade_failed",
                     "player": actor_id, "reason": "no_open_offer"}]
        original_proposer = incoming["proposer"]
        depth = int(incoming.get("counter_depth", 0)) + 1
        if depth > self._MAX_COUNTER_DEPTH:
            self._log(
                f"{self._name_of(state, actor_id)} declined "
                f"{self._name_of(state, original_proposer)}'s offer "
                f"(counter limit reached)"
            )
            return [{
                "type": "monopoly_trade_rejected",
                "player": actor_id,
                "proposer": original_proposer,
                "reason": "counter_limit",
            }]

        # The counter-offer is FROM the actor TO the original proposer.
        # Their roles flip: what actor gives is what the OLD recipient
        # (=actor) now offers up.
        give_props = self._coerce_int_list(params.get("give_properties"))
        want_props = self._coerce_int_list(params.get("want_properties"))
        try:
            give_money = max(0, int(params.get("give_money") or 0))
            want_money = max(0, int(params.get("want_money") or 0))
        except (TypeError, ValueError):
            give_money, want_money = 0, 0
        note = str(params.get("note") or "").strip()[:200]

        if (not give_props and give_money == 0) and (not want_props and want_money == 0):
            # Empty counter — treat as a rejection rather than letting the
            # game stall on a nothing-for-nothing offer.
            self._log(
                f"{self._name_of(state, actor_id)} rejected "
                f"{self._name_of(state, original_proposer)}'s offer "
                "(empty counter)"
            )
            return [{
                "type": "monopoly_trade_rejected",
                "player": actor_id,
                "proposer": original_proposer,
                "reason": "empty_counter",
            }]

        for sp in give_props:
            if not self._owned_unimproved(actor_id, sp):
                # Restore the original offer so they can try again — the
                # alternative is the offer vanishing on a malformed counter.
                self._pending_trades[actor_id] = incoming
                return [{"type": "monopoly_trade_failed", "player": actor_id,
                         "reason": "give_property_not_tradeable", "space": sp}]
        for sp in want_props:
            if not self._owned_unimproved(original_proposer, sp):
                self._pending_trades[actor_id] = incoming
                return [{"type": "monopoly_trade_failed", "player": actor_id,
                         "reason": "want_property_not_tradeable", "space": sp}]
        if self._players[actor_id]["money"] < give_money:
            self._pending_trades[actor_id] = incoming
            return [{"type": "monopoly_trade_failed", "player": actor_id,
                     "reason": "insufficient_cash_to_offer"}]

        new_offer = {
            "proposer": actor_id,
            "give_properties": give_props,
            "give_money": give_money,
            "want_properties": want_props,
            "want_money": want_money,
            "note": note,
            "counter_depth": depth,
        }
        self._pending_trades[original_proposer] = new_offer
        self._trade_log.append({
            "outcome": "countered",
            "proposer": actor_id,
            "target": original_proposer,
            "give_properties": give_props,
            "give_money": give_money,
            "want_properties": want_props,
            "want_money": want_money,
            "note": note,
            "counter_depth": depth,
        })
        self._log(
            f"{self._name_of(state, actor_id)} countered "
            f"{self._name_of(state, original_proposer)}'s offer"
        )
        return [{
            "type": "monopoly_trade_countered",
            "player": actor_id,
            "original_proposer": original_proposer,
            "give_properties": give_props,
            "give_money": give_money,
            "want_properties": want_props,
            "want_money": want_money,
            "note": note,
            "counter_depth": depth,
        }]

    def _handle_reject_trade(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        offer = self._pending_trades.pop(actor_id, None)
        if not offer:
            return [{"type": "monopoly_trade_failed",
                     "player": actor_id, "reason": "no_open_offer"}]
        self._trade_log.append({
            "outcome": "rejected",
            "proposer": offer["proposer"],
            "target": actor_id,
            "give_properties": offer["give_properties"],
            "give_money": offer["give_money"],
            "want_properties": offer["want_properties"],
            "want_money": offer["want_money"],
        })
        self._log(
            f"{self._name_of(state, actor_id)} rejected trade from "
            f"{self._name_of(state, offer['proposer'])}"
        )
        return [{
            "type": "monopoly_trade_rejected",
            "player": actor_id,
            "proposer": offer["proposer"],
        }]

    # ------------------------------------------------------------------ #
    # Jail
    # ------------------------------------------------------------------ #

    def _send_to_jail(self, player_id: str, state: Any, prior_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        p = self._players[player_id]
        p["position"] = JAIL_SPACE
        p["in_jail"] = True
        p["jail_turns"] = 0
        p["doubles_streak"] = 0
        self._sync_position_property(player_id, state)
        return prior_events + [{"type": "monopoly_to_jail", "player": player_id}]

    def _handle_pay_jail(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        p = self._players[actor_id]
        if not p["in_jail"]:
            return []
        if p["money"] < JAIL_FINE:
            return [{"type": "monopoly_jail_fine_failed", "player": actor_id}]
        self._charge(actor_id, JAIL_FINE, state)
        p["in_jail"] = False
        p["jail_turns"] = 0
        self._log(f"{self._name_of(state, actor_id)} paid $50 to leave jail")
        return [{"type": "monopoly_left_jail", "player": actor_id, "reason": "paid"}]

    def _handle_use_jail_card(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        p = self._players[actor_id]
        if not p["in_jail"] or p["jail_cards"] <= 0:
            return [{"type": "monopoly_no_jail_card", "player": actor_id}]
        p["jail_cards"] -= 1
        p["in_jail"] = False
        p["jail_turns"] = 0
        self._log(f"{self._name_of(state, actor_id)} used a Get Out of Jail card")
        return [{"type": "monopoly_left_jail", "player": actor_id, "reason": "card"}]

    def _handle_roll_in_jail(self, actor_id: str, state: Any) -> List[Dict[str, Any]]:
        p = self._players[actor_id]
        if not p["in_jail"]:
            return []
        d1, d2 = self._rng.randint(1, 6), self._rng.randint(1, 6)
        self._last_roll = (d1, d2)
        events: List[Dict[str, Any]] = [{
            "type": "monopoly_dice", "player": actor_id, "d1": d1, "d2": d2,
            "doubles": d1 == d2, "in_jail_attempt": True,
        }]
        if d1 == d2:
            p["in_jail"] = False
            p["jail_turns"] = 0
            old_pos = p["position"]
            p["position"] = (p["position"] + d1 + d2) % 40
            if p["position"] < old_pos:
                self._credit(actor_id, GO_SALARY, state)
            self._sync_position_property(actor_id, state)
            self._log(f"{self._name_of(state, actor_id)} rolled doubles and escaped jail")
            events.extend(self._resolve_landing(actor_id, state))
            return events
        p["jail_turns"] += 1
        if p["jail_turns"] >= 3:
            # Forced exit — must pay (auto) and move.
            if p["money"] >= JAIL_FINE:
                self._charge(actor_id, JAIL_FINE, state)
                p["in_jail"] = False
                p["jail_turns"] = 0
                old_pos = p["position"]
                p["position"] = (p["position"] + d1 + d2) % 40
                if p["position"] < old_pos:
                    self._credit(actor_id, GO_SALARY, state)
                self._sync_position_property(actor_id, state)
                self._log(f"{self._name_of(state, actor_id)} forced out of jail (paid $50)")
                events.extend(self._resolve_landing(actor_id, state))
            else:
                # Truly broke — bankrupt them out via the cash crunch path
                self._charge(actor_id, JAIL_FINE, state)
                if p["bankrupt"]:
                    events.extend(self._bankrupt(actor_id, None, state))
        return events

    # ------------------------------------------------------------------ #
    # Card draw
    # ------------------------------------------------------------------ #

    def _draw_card(self, player_id: str, deck_name: str, state: Any) -> List[Dict[str, Any]]:
        deck = self._chance if deck_name == "chance" else self._community
        if not deck:
            return []
        card = deck.pop()
        # Get-Out-of-Jail card is kept until used.
        keep = card["effect"] == "get_out_of_jail_card"
        if not keep:
            deck.insert(0, card)
        self._log(f"{self._name_of(state, player_id)} drew {deck_name}: {card['text']}")
        events: List[Dict[str, Any]] = [{
            "type": "monopoly_card",
            "deck": deck_name,
            "player": player_id,
            "text": card["text"],
        }]
        events.extend(self._apply_card_effect(player_id, card, state))
        return events

    def _apply_card_effect(self, player_id: str, card: Dict[str, Any], state: Any) -> List[Dict[str, Any]]:
        p = self._players[player_id]
        eff = card["effect"]
        if eff == "collect":
            self._credit(player_id, int(card["amount"]), state)
            return []
        if eff == "pay":
            self._charge(player_id, int(card["amount"]), state)
            if p["bankrupt"]:
                return self._bankrupt(player_id, None, state)
            return []
        if eff == "pay_each_player":
            amt = int(card["amount"])
            for other in self._turn_order:
                if other == player_id or self._players[other]["bankrupt"]:
                    continue
                paid = self._charge(player_id, amt, state, creditor=other)
                self._credit(other, paid, state)
                if p["bankrupt"]:
                    return self._bankrupt(player_id, other, state)
            return []
        if eff == "collect_from_each":
            amt = int(card["amount"])
            for other in self._turn_order:
                if other == player_id or self._players[other]["bankrupt"]:
                    continue
                paid = self._charge(other, amt, state, creditor=player_id)
                self._credit(player_id, paid, state)
                if self._players[other]["bankrupt"]:
                    return self._bankrupt(other, player_id, state)
            return []
        if eff == "advance_to":
            target = int(card["space"])
            self._move_to(player_id, target, state, pass_go_pays=True)
            return self._resolve_landing(player_id, state)
        if eff == "advance_to_nearest":
            kind = card["kind"]
            idxs = RAILROAD_INDICES if kind == "railroad" else UTILITY_INDICES
            curr = p["position"]
            target = min(idxs, key=lambda i: (i - curr) % 40 if (i - curr) % 40 > 0 else 40)
            self._move_to(player_id, target, state, pass_go_pays=True)
            return self._resolve_landing(player_id, state)
        if eff == "move_relative":
            delta = int(card["delta"])
            new_pos = (p["position"] + delta) % 40
            p["position"] = new_pos
            self._sync_position_property(player_id, state)
            return self._resolve_landing(player_id, state)
        if eff == "go_to_jail":
            return self._send_to_jail(player_id, state, [])
        if eff == "get_out_of_jail_card":
            p["jail_cards"] += 1
            return []
        if eff == "repairs":
            houses = sum(prop["houses"] for prop in self._properties.values()
                         if prop["owner"] == player_id)
            hotels = sum(1 for prop in self._properties.values()
                         if prop["owner"] == player_id and prop["hotel"])
            total = houses * int(card["house"]) + hotels * int(card["hotel"])
            if total > 0:
                self._charge(player_id, total, state)
                if p["bankrupt"]:
                    return self._bankrupt(player_id, None, state)
            return [{"type": "monopoly_repairs", "player": player_id, "amount": total}]
        return []

    def _move_to(self, player_id: str, target: int, state: Any, pass_go_pays: bool) -> None:
        p = self._players[player_id]
        if pass_go_pays and target < p["position"]:
            self._credit(player_id, GO_SALARY, state)
        p["position"] = target
        self._sync_position_property(player_id, state)

    # ------------------------------------------------------------------ #
    # Money + bankruptcy
    # ------------------------------------------------------------------ #

    def _credit(self, player_id: str, amount: int, state: Any) -> None:
        if amount <= 0 or player_id not in self._players:
            return
        p = self._players[player_id]
        p["money"] = int(p["money"]) + int(amount)
        self._sync_money_property(player_id, state)

    def _charge(self, player_id: str, amount: int, state: Any, creditor: Optional[str] = None) -> int:
        """Take `amount` from player. Tries cash; then mortgages owned
        un-mortgaged unbuilt properties. If still short, marks bankrupt
        and pays whatever's left. Returns the actual amount paid."""
        if amount <= 0 or player_id not in self._players:
            return 0
        p = self._players[player_id]
        # Stage 1: cash
        if p["money"] >= amount:
            p["money"] -= amount
            self._sync_money_property(player_id, state)
            return amount
        # Stage 2: mortgage properties to raise cash
        for idx, prop in self._properties.items():
            if prop["owner"] != player_id or prop["mortgaged"]:
                continue
            if prop["houses"] > 0 or prop["hotel"]:
                continue  # Must sell buildings first (auto-sell)
            mort = int(BOARD[idx]["mortgage"])
            prop["mortgaged"] = True
            p["money"] += mort
            self._log(f"{self._name_of(state, player_id)} mortgaged {BOARD[idx]['name']} (+${mort})")
            if p["money"] >= amount:
                break
        # Stage 3: sell all buildings if still short
        if p["money"] < amount:
            for idx, prop in self._properties.items():
                if prop["owner"] != player_id:
                    continue
                refund_each = int(BOARD[idx]["house_cost"]) // 2
                if prop["hotel"]:
                    prop["hotel"] = False
                    prop["houses"] = 0
                    p["money"] += refund_each * 5  # hotel = 5 houses worth
                while prop["houses"] > 0 and p["money"] < amount:
                    prop["houses"] -= 1
                    p["money"] += refund_each
            # Now try mortgaging anything left (newly unbuilt)
            for idx, prop in self._properties.items():
                if prop["owner"] != player_id or prop["mortgaged"]:
                    continue
                p["money"] += int(BOARD[idx]["mortgage"])
                prop["mortgaged"] = True
                if p["money"] >= amount:
                    break
        if p["money"] >= amount:
            p["money"] -= amount
            self._sync_money_property(player_id, state)
            return amount
        # Bankrupt — pay what we have
        paid = max(0, p["money"])
        p["money"] = 0
        p["bankrupt"] = True
        self._sync_money_property(player_id, state)
        return paid

    def _bankrupt(self, player_id: str, creditor: Optional[str], state: Any) -> List[Dict[str, Any]]:
        """Mark player bankrupt and transfer assets."""
        if player_id not in self._players:
            return []
        p = self._players[player_id]
        p["bankrupt"] = True
        if creditor and creditor in self._players:
            # Transfer all owned properties to creditor (un-mortgaged).
            # Houses already auto-sold during the failed charge.
            for idx, prop in self._properties.items():
                if prop["owner"] == player_id:
                    prop["owner"] = creditor
        else:
            # To the bank — properties become unowned.
            to_remove = [i for i, prop in self._properties.items() if prop["owner"] == player_id]
            for i in to_remove:
                del self._properties[i]
        # Clear any open offers involving this player — both incoming
        # (addressed to them) and outgoing (proposed by them).
        self._pending_trades.pop(player_id, None)
        stale = [r for r, o in self._pending_trades.items() if o.get("proposer") == player_id]
        for r in stale:
            self._pending_trades.pop(r, None)
        self._log(f"{self._name_of(state, player_id)} went BANKRUPT")
        return [{"type": "monopoly_bankrupt", "player": player_id, "creditor": creditor}]

    # ------------------------------------------------------------------ #
    # State sync (mirror to entity properties for engine + UI consumers)
    # ------------------------------------------------------------------ #

    def _sync_money_property(self, player_id: str, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        ent = state.entities.get(player_id)
        if ent and hasattr(ent, "properties"):
            ent.properties["money"] = int(self._players[player_id]["money"])
            ent.properties["bankrupt"] = bool(self._players[player_id]["bankrupt"])

    def _sync_position_property(self, player_id: str, state: Any) -> None:
        if not hasattr(state, "entities"):
            return
        ent = state.entities.get(player_id)
        if ent and hasattr(ent, "properties"):
            ent.properties["position"] = int(self._players[player_id]["position"])
            ent.properties["in_jail"] = bool(self._players[player_id]["in_jail"])

    # ------------------------------------------------------------------ #
    # Helpers + perception
    # ------------------------------------------------------------------ #

    def _name_of(self, state: Any, player_id: str) -> str:
        if hasattr(state, "entities"):
            ent = state.entities.get(player_id)
            if ent:
                return getattr(ent, "name", player_id)
        return player_id

    def _log(self, msg: str) -> None:
        self._turn_log.append(msg)
        if len(self._turn_log) > 200:
            self._turn_log = self._turn_log[-200:]

    # ------------------------------------------------------------------ #
    # Perception render helpers
    # ------------------------------------------------------------------ #

    def _building_str(self, prop: Optional[Dict[str, Any]]) -> str:
        """Compact building/mortgage marker for a property."""
        if not prop:
            return ""
        if prop.get("mortgaged"):
            return "MORTGAGED"
        if prop.get("hotel"):
            return "HOTEL"
        h = int(prop.get("houses", 0))
        if h > 0:
            return f"{h} house{'s' if h != 1 else ''}"
        return "no buildings"

    def _render_board_view(self, entity_id: str, state: Any) -> str:
        """Multi-line, human-readable view of the whole property landscape,
        grouped by colour set so monopolies are obvious."""
        names = {pid: self._name_of(state, pid) for pid in self._players}
        you = entity_id
        lines: List[str] = []
        lines.append("=== BOARD / PROPERTY LANDSCAPE ===")
        you_pos = self._players[you]["position"]
        lines.append(
            f"Your token is on space {you_pos}: {BOARD[you_pos]['name']}."
        )
        lines.append("")

        # --- colour-group property sets ---
        # Pretty group order following the board.
        seen_groups: List[str] = []
        for s in BOARD:
            if s["type"] == "property" and s["group"] not in seen_groups:
                seen_groups.append(s["group"])

        monopoly_holders: Dict[str, List[str]] = {}
        lines.append("-- Colour groups (complete a group = monopoly = build) --")
        for group in seen_groups:
            idxs = GROUP_TO_INDICES[group]
            # Who, if anyone, owns the whole group.
            full_owner = None
            for pid in self._players:
                if self._owns_full_group(pid, group):
                    full_owner = pid
                    break
            tag = ""
            if full_owner:
                monopoly_holders.setdefault(full_owner, []).append(group)
                who = "YOU" if full_owner == you else names.get(full_owner, full_owner)
                tag = f"  *** MONOPOLY: {who} ***"
            lines.append(f"[{group.replace('_', ' ').upper()}]{tag}")
            for i in idxs:
                sp = BOARD[i]
                prop = self._properties.get(i)
                if prop is None:
                    owner_str = "UNOWNED (buyable)"
                elif prop["owner"] == you:
                    owner_str = "YOU"
                else:
                    owner_str = names.get(prop["owner"], prop["owner"])
                bld = self._building_str(prop)
                bld_str = f" | {bld}" if bld else ""
                rent_now = self._calc_rent(i) if prop else 0
                rent_str = f" | rent now ${rent_now}" if prop and not prop["mortgaged"] else ""
                here = "  <-- YOU ARE HERE" if i == you_pos else ""
                lines.append(
                    f"  #{i:<2} {sp['name']:<20} ${sp['price']:<4} "
                    f"owner: {owner_str}{bld_str}{rent_str}{here}"
                )
            lines.append("")

        # --- railroads + utilities ---
        lines.append("-- Railroads --")
        for i in RAILROAD_INDICES:
            sp = BOARD[i]
            prop = self._properties.get(i)
            owner_str = ("UNOWNED (buyable)" if prop is None
                         else "YOU" if prop["owner"] == you
                         else names.get(prop["owner"], prop["owner"]))
            mort = " | MORTGAGED" if prop and prop["mortgaged"] else ""
            here = "  <-- YOU ARE HERE" if i == you_pos else ""
            lines.append(f"  #{i:<2} {sp['name']:<20} ${sp['price']:<4} "
                         f"owner: {owner_str}{mort}{here}")
        lines.append("")
        lines.append("-- Utilities --")
        for i in UTILITY_INDICES:
            sp = BOARD[i]
            prop = self._properties.get(i)
            owner_str = ("UNOWNED (buyable)" if prop is None
                         else "YOU" if prop["owner"] == you
                         else names.get(prop["owner"], prop["owner"]))
            mort = " | MORTGAGED" if prop and prop["mortgaged"] else ""
            here = "  <-- YOU ARE HERE" if i == you_pos else ""
            lines.append(f"  #{i:<2} {sp['name']:<20} ${sp['price']:<4} "
                         f"owner: {owner_str}{mort}{here}")
        lines.append("")

        # --- monopoly summary per player ---
        lines.append("-- Monopolies controlled --")
        any_mono = False
        for pid in self._players:
            groups = monopoly_holders.get(pid)
            if groups:
                any_mono = True
                who = "YOU" if pid == you else names.get(pid, pid)
                lines.append(f"  {who}: {', '.join(g.replace('_', ' ') for g in groups)}")
        if not any_mono:
            lines.append("  (none yet — no player owns a full colour group)")
        return "\n".join(lines)

    def _compute_legal_actions(self, entity_id: str, p: Dict[str, Any],
                               is_active: bool, state: Any) -> List[str]:
        """Plain-English list of exactly what this player can do right now."""
        actions: List[str] = []

        # Open trade offer overrides everything — must respond first.
        if entity_id in self._pending_trades:
            offer = self._pending_trades[entity_id]
            from_name = self._name_of(state, offer["proposer"])
            give_props = ", ".join(BOARD[sp]["name"] for sp in offer["give_properties"]) or "—"
            want_props = ", ".join(BOARD[sp]["name"] for sp in offer["want_properties"]) or "—"
            actions.append(
                f"accept_trade — take {from_name}'s offer "
                f"(they give: [{give_props}] + ${offer['give_money']}; "
                f"they want: [{want_props}] + ${offer['want_money']})."
            )
            actions.append(
                f"counter_trade — propose REVISED terms back to "
                f"{from_name}. Same params as propose_trade. Use this "
                "to haggle (ask for more cash, different property, etc)."
            )
            actions.append(
                f"reject_trade — flatly decline {from_name}'s offer."
            )
            return actions

        if p["bankrupt"]:
            actions.append("You are bankrupt — out of the game.")
            return actions
        if not is_active:
            actions.append("Wait — it is not your turn; you have no actions.")
            return actions

        # In jail.
        if p["in_jail"]:
            if p["money"] >= JAIL_FINE:
                actions.append(
                    f"pay_jail_fine — pay ${JAIL_FINE} to get out of jail now."
                )
            if p["jail_cards"] > 0:
                actions.append(
                    "use_jail_card — spend a Get Out of Jail Free card "
                    f"(you hold {p['jail_cards']})."
                )
            actions.append(
                "roll_in_jail — roll the dice; doubles frees you, "
                "otherwise you stay (forced out & charged $50 after 3 tries)."
            )
            return actions

        # Pending buy decision.
        if self._pending and self._pending.get("kind") == "buy":
            idx = self._pending["space"]
            sp = BOARD[idx]
            price = int(sp["price"])
            afford = p["money"] >= price
            if afford:
                actions.append(
                    f"buy_property — buy {sp['name']} (space #{idx}) for "
                    f"${price}. You can afford it (cash ${p['money']})."
                )
            else:
                actions.append(
                    f"buy_property — UNAFFORDABLE: {sp['name']} costs ${price} "
                    f"but you only have ${p['money']}."
                )
            actions.append(
                f"pass_property — decline {sp['name']}; it stays with the bank."
            )
            return actions

        # Normal turn (no pending) — build / sell / end.
        buildable = self._buildable_spaces(entity_id)
        for i in buildable:
            sp = BOARD[i]
            prop = self._properties[i]
            cost = int(sp["house_cost"])
            if prop["houses"] >= MAX_HOUSES_PER_PROPERTY:
                what = f"a HOTEL on {sp['name']}"
            else:
                what = f"a house on {sp['name']} (now {prop['houses']} -> {prop['houses'] + 1})"
            afford = "" if p["money"] >= cost else " [UNAFFORDABLE]"
            actions.append(
                f"build_house space={i} — build {what} for ${cost}.{afford}"
            )
        sellable = [i for i, pr in self._properties.items()
                    if pr["owner"] == entity_id and (pr["houses"] > 0 or pr["hotel"])]
        for i in sellable:
            sp = BOARD[i]
            refund = int(sp["house_cost"]) // 2
            actions.append(
                f"sell_house space={i} — sell a building on {sp['name']} "
                f"for ${refund} cash back."
            )
        # Trade hints — surface players who hold the missing piece of a
        # near-monopoly so the LLM has a concrete target to propose to.
        for group, idxs in GROUP_TO_INDICES.items():
            mine = [i for i in idxs if self._properties.get(i, {}).get("owner") == entity_id]
            if not mine or len(mine) == len(idxs):
                continue
            missing = [i for i in idxs if self._properties.get(i, {}).get("owner") not in (entity_id, None)]
            if not missing:
                continue
            for sp_idx in missing:
                other = self._properties[sp_idx]["owner"]
                actions.append(
                    f"propose_trade target_id={other} — you own "
                    f"{len(mine)}/{len(idxs)} of the {group} group; "
                    f"{self._name_of(state, other)} holds {BOARD[sp_idx]['name']} "
                    f"(space #{sp_idx}). Offering cash + a property they need "
                    "could complete your monopoly."
                )
        outgoing = [
            (r, o) for r, o in self._pending_trades.items()
            if o.get("proposer") == entity_id
        ]
        if outgoing:
            recipient_id, open_offer = outgoing[0]
            rname = self._name_of(state, recipient_id)
            give_props = ", ".join(BOARD[sp]["name"] for sp in open_offer["give_properties"]) or "—"
            want_props = ", ".join(BOARD[sp]["name"] for sp in open_offer["want_properties"]) or "—"
            actions.append(
                f"propose_trade target_id={recipient_id} — REVISE your "
                f"offer to {rname} (current terms: you give [{give_props}] "
                f"+ ${open_offer['give_money']}, you want [{want_props}] "
                f"+ ${open_offer['want_money']}). A new proposal overwrites."
            )
            actions.append(
                f"propose_trade target_id=<other_player> — start a "
                "separate offer with a different player."
            )
            actions.append(
                f"end_turn — LOCKED: waiting on {rname} to accept/reject. "
                "You cannot end your turn until the negotiation settles."
            )
            return actions

        actions.append(
            "propose_trade target_id=<player> — offer any other solvent "
            "player a swap. Params: give_properties (list of space indices "
            "you give), give_money (cash you pay), want_properties (their "
            "indices), want_money (cash you want). Properties must be "
            "unimproved & unmortgaged. NOTE: once you propose, your turn "
            "stays open until the recipient accepts or rejects."
        )
        actions.append(
            "end_turn — finish your turn; this rolls the dice for the "
            "next player."
        )
        return actions

    def get_perception_data(self, entity_id: str, state: Any) -> Dict[str, Any]:
        p = self._players.get(entity_id)
        if not p:
            return {}
        active = self._active_player_id()
        is_active = entity_id == active
        # Sanitised view of every player.
        roster = []
        for pid, ps in self._players.items():
            roster.append({
                "id": pid,
                "name": self._name_of(state, pid),
                "money": ps["money"],
                "position": ps["position"],
                "in_jail": ps["in_jail"],
                "bankrupt": ps["bankrupt"],
                "is_you": pid == entity_id,
                "is_active": pid == active,
            })
        # Property summary: only show ownership + buildings (public info).
        properties = []
        for i, prop in self._properties.items():
            properties.append({
                "space": i, "name": BOARD[i]["name"], "owner": prop["owner"],
                "houses": prop["houses"], "hotel": prop["hotel"],
                "mortgaged": prop["mortgaged"],
            })

        # Open offer this player has PROPOSED (still awaiting response).
        outgoing_view: Optional[Dict[str, Any]] = None
        for recipient_id, offer in self._pending_trades.items():
            if offer.get("proposer") == entity_id:
                outgoing_view = {
                    "to": recipient_id,
                    "to_name": self._name_of(state, recipient_id),
                    "you_give_properties": [
                        {"space": sp, "name": BOARD[sp]["name"]}
                        for sp in offer["give_properties"]
                    ],
                    "you_give_money": offer["give_money"],
                    "you_want_properties": [
                        {"space": sp, "name": BOARD[sp]["name"]}
                        for sp in offer["want_properties"]
                    ],
                    "you_want_money": offer["want_money"],
                    "note": offer.get("note", ""),
                    "counter_depth": offer.get("counter_depth", 0),
                }
                break

        # Recent structured trade history — last 12 events. Names resolved.
        recent_trades: List[Dict[str, Any]] = []
        for ev in self._trade_log[-12:]:
            recent_trades.append({
                "outcome": ev["outcome"],
                "proposer": ev["proposer"],
                "proposer_name": self._name_of(state, ev["proposer"]),
                "target": ev["target"],
                "target_name": self._name_of(state, ev["target"]),
                "give_properties": [BOARD[sp]["name"] for sp in ev.get("give_properties", [])],
                "give_money": ev.get("give_money", 0),
                "want_properties": [BOARD[sp]["name"] for sp in ev.get("want_properties", [])],
                "want_money": ev.get("want_money", 0),
                "note": ev.get("note", ""),
            })

        board_view = self._render_board_view(entity_id, state)
        legal_actions = self._compute_legal_actions(entity_id, p, is_active, state)

        # ---------- Per-player holdings & net-worth (strategic view) -----
        # An LLM scanning a flat property list to figure out "who is
        # weakest" or "who holds the orange property I need" is wasteful
        # and error-prone. Pre-compute the per-player picture so trade
        # targeting can lean on structured facts.
        holdings_by_player: Dict[str, Dict[str, Any]] = {}
        for pid, ps in self._players.items():
            props_owned = []
            for i, prop in self._properties.items():
                if prop["owner"] != pid:
                    continue
                sp = BOARD[i]
                props_owned.append({
                    "space": i,
                    "name": sp["name"],
                    "group": sp.get("group"),
                    "type": sp["type"],
                    "price": sp.get("price"),
                    "houses": prop["houses"],
                    "hotel": prop["hotel"],
                    "mortgaged": prop["mortgaged"],
                    "current_rent": self._calc_rent(i) if not prop["mortgaged"] else 0,
                })
            # Net worth = cash + face value of properties + buildings
            net_worth = ps["money"]
            monopolies: List[str] = []
            for pr in props_owned:
                price = int(pr.get("price") or 0)
                if pr["mortgaged"]:
                    net_worth += price // 2  # ~mortgage value
                else:
                    net_worth += price
                # Buildings (half-cost on sale)
                sp = BOARD[pr["space"]]
                hc = int(sp.get("house_cost") or 0)
                if pr["hotel"]:
                    net_worth += hc * 5 // 2  # 4 houses + hotel ~= 5x house cost / 2
                elif pr["houses"]:
                    net_worth += pr["houses"] * hc // 2
            for group in {pr["group"] for pr in props_owned if pr["group"]}:
                if self._owns_full_group(pid, group):
                    monopolies.append(group)
            holdings_by_player[pid] = {
                "name": self._name_of(state, pid),
                "is_you": pid == entity_id,
                "cash": ps["money"],
                "net_worth_estimate": net_worth,
                "properties": props_owned,
                "monopolies": monopolies,
                "bankrupt": ps["bankrupt"],
            }

        # ---------- Monopoly progress (viewer-centric) -------------------
        # "You own 2/3 of orange — need #18 St. James (Bob owns it)."
        # Lets the LLM jump straight to the right trade target without
        # parsing board_view.
        monopoly_progress: List[Dict[str, Any]] = []
        for group, idxs in GROUP_TO_INDICES.items():
            mine = [i for i in idxs if self._properties.get(i, {}).get("owner") == entity_id]
            if not mine:
                continue
            missing = []
            for i in idxs:
                prop = self._properties.get(i)
                if not prop:
                    missing.append({"space": i, "name": BOARD[i]["name"],
                                    "held_by": None, "held_by_name": "BANK (buyable)"})
                elif prop["owner"] != entity_id:
                    missing.append({"space": i, "name": BOARD[i]["name"],
                                    "held_by": prop["owner"],
                                    "held_by_name": self._name_of(state, prop["owner"])})
            monopoly_progress.append({
                "group": group,
                "owned": len(mine),
                "total": len(idxs),
                "complete": len(missing) == 0,
                "owned_spaces": mine,
                "missing": missing,
            })

        # Open trade offer addressed to this player (if any).
        incoming_offer = self._pending_trades.get(entity_id)
        offer_view: Optional[Dict[str, Any]] = None
        if incoming_offer:
            offer_view = {
                "from": incoming_offer["proposer"],
                "from_name": self._name_of(state, incoming_offer["proposer"]),
                "they_give_you_properties": [
                    {"space": sp, "name": BOARD[sp]["name"]}
                    for sp in incoming_offer["give_properties"]
                ],
                "they_give_you_money": incoming_offer["give_money"],
                "they_want_your_properties": [
                    {"space": sp, "name": BOARD[sp]["name"]}
                    for sp in incoming_offer["want_properties"]
                ],
                "they_want_your_money": incoming_offer["want_money"],
                "note": incoming_offer.get("note", ""),
            }

        # Build a clear call-to-action describing the current decision.
        if incoming_offer:
            instructions = (
                f"{offer_view['from_name']} has proposed a TRADE with you "
                "(see `incoming_offer`). You MUST respond before doing "
                "anything else: `accept_trade` to take their deal, "
                "`counter_trade` to propose REVISED terms back (haggle — "
                "ask for more cash, swap a different property, etc), or "
                "`reject_trade` to flatly decline. Evaluate: do the offered "
                "properties complete a colour group for you? Are you "
                "giving up a key piece of a monopoly? Is the cash fair?"
            )
        elif not is_active:
            instructions = (
                f"It is {self._name_of(state, active)}'s turn, not yours. "
                "You have no actions — wait."
            )
        elif p["in_jail"]:
            instructions = (
                "IT IS YOUR TURN AND YOU ARE IN JAIL. Choose ONE: "
                "pay_jail_fine ($50 cash), use_jail_card (if you hold one), "
                "or roll_in_jail (free only on doubles). See `legal_actions`."
            )
        elif self._pending and self._pending.get("kind") == "buy":
            idx = self._pending["space"]
            sp = BOARD[idx]
            instructions = (
                f"IT IS YOUR TURN. You landed on {sp['name']} (space #{idx}), "
                f"which is UNOWNED and costs ${int(sp['price'])}. You MUST "
                "decide now: call `buy_property` to purchase it, or "
                "`pass_property` to leave it with the bank. See `board_view` "
                "to judge whether this completes or blocks a colour group."
            )
        else:
            outgoing = [
                (r, o) for r, o in self._pending_trades.items()
                if o.get("proposer") == entity_id
            ]
            if outgoing:
                recipient_id, _open_offer = outgoing[0]
                rname = self._name_of(state, recipient_id)
                instructions = (
                    f"IT IS YOUR TURN, but you have an OPEN TRADE OFFER "
                    f"to {rname} that they haven't answered yet. You "
                    "CANNOT end your turn until they accept or reject. "
                    "You may build, sell, or send a REVISED offer to "
                    f"{rname} (proposing again to them overwrites the "
                    "current offer with new terms — useful if they seem "
                    "likely to refuse). Or propose to a different player. "
                    "Stay in the negotiation."
                )
            else:
                instructions = (
                    "IT IS YOUR TURN. You have already moved. You may now "
                    "`build_house` on any colour group you fully own, "
                    "`sell_house` to raise cash, `propose_trade` to swap "
                    "properties/cash with another player (target_id + "
                    "give_properties/give_money/want_properties/want_money "
                    "params), or `end_turn` to finish. Trading is often the "
                    "fastest way to complete a colour group — look at "
                    "`properties_owned` and find a player who holds the one "
                    "property you need to monopolise a group. Pick ONE action."
                )

        return {
            "instructions": instructions,
            "board_view": board_view,
            "legal_actions": legal_actions,
            "your_money": p["money"],
            "your_position": p["position"],
            "your_position_name": BOARD[p["position"]]["name"],
            "in_jail": p["in_jail"],
            "jail_cards": p["jail_cards"],
            "active_player": active,
            "is_your_turn": is_active,
            "pending": self._pending if is_active else None,
            "roster": roster,
            "properties_owned": properties,
            "holdings_by_player": holdings_by_player,
            "monopoly_progress": monopoly_progress,
            "incoming_offer": offer_view,
            "outgoing_offer": outgoing_view,
            "recent_trades": recent_trades,
            "recent_log": self._turn_log[-40:],
            "last_roll": list(self._last_roll),
            "rules_summary": (
                "Roll dice on your turn (end_turn triggers next player's roll). "
                "Buy unowned property to collect rent. Complete a colour group "
                "to build houses (1-4) then a hotel. Force opponents to bankruptcy."
            ),
        }

    # ------------------------------------------------------------------ #
    # Serialisation
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["state"] = {
            "properties": {str(k): v for k, v in self._properties.items()},
            "players": dict(self._players),
            "turn_order": list(self._turn_order),
            "turn_index": self._turn_index,
            "chance": list(self._chance),
            "community": list(self._community),
            "last_roll": list(self._last_roll),
            "turn_log": list(self._turn_log),
            "pending": dict(self._pending) if self._pending else None,
            "pending_trades": {k: dict(v) for k, v in self._pending_trades.items()},
            "trade_log": list(self._trade_log),
        }
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "MonopolyModule":
        mod = cls(name=data.get("name", "monopoly"), params=data.get("params", {}))
        s = data.get("state", {})
        mod._properties = {int(k): v for k, v in (s.get("properties") or {}).items()}
        mod._players = dict(s.get("players") or {})
        mod._turn_order = list(s.get("turn_order") or [])
        mod._turn_index = int(s.get("turn_index") or 0)
        mod._chance = list(s.get("chance") or _chance_deck())
        mod._community = list(s.get("community") or _community_chest_deck())
        lr = s.get("last_roll") or [0, 0]
        mod._last_roll = (int(lr[0]), int(lr[1]))
        mod._turn_log = list(s.get("turn_log") or [])
        mod._pending = dict(s["pending"]) if s.get("pending") else None
        mod._pending_trades = {
            k: dict(v) for k, v in (s.get("pending_trades") or {}).items()
        }
        mod._trade_log = list(s.get("trade_log") or [])
        return mod
