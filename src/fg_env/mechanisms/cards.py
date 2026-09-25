"""Cards as world state: the ``game.cards`` mode's decks, zones and engine-enforced visibility, its ``game`` op
actions and card functions.

A card is an entity of its deck's card type. Its journaled props say where it is:

* ``zone`` — ``deck`` (the draw pile), ``hand``, ``discard``, ``burn`` or a zone the deck declares,
* ``owner`` — the player whose zone it is in ('' for shared zones),
* ``order`` — its position in the zone (highest = top),
* ``face_up`` — shown to everyone wherever it lies; ``seen_by`` — players who peeked at it,
* ``played_by`` — the last player who held it in an owned zone (who played it to a trick).

Who may see a card is decided in one place, :func:`card_visible`: cards in public zones and
face-up cards are seen by everyone, cards in ``owner`` zones by their owner, cards in hidden
zones by nobody, and a peek adds single viewers. The card type's inspect rule and every
generated view use it, so a hidden card never reaches another player. All changes go through
the world's journaled API; randomness comes only from ``world.rng``.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..errors import RunError
from ..expr import Call, ExprError, function, is_expr
from ..expr.objects import Entity
from ..registry import config_data, family_action
from ._common import whole
from .card_scoring import RANK_LABELS, SUIT_LETTERS, SUIT_SYMBOLS, SUITS
from .contract_cache import parse_kind, per_contract
from .expressions import Expr

__all__ = ["CardsConfig", "CardEntry", "ZoneConfig", "CardActionConfig", "Deck", "Zone", "decks", "zones_for",
           "deck_cards", "cards_in", "card_visible", "card_names", "place", "shuffle", "deal", "collect", "KEY"]

KEY = "game.cards"

def _props(entity: Entity) -> dict[str, Any]:
    """An entity's properties, typed loosely: values are whatever the contract declared."""
    return cast(dict[str, Any], entity.properties)


Visibility = Literal["public", "owner", "hidden"]


# ---------------------------------------------------------------------------
# Config (shared by the mechanism expansion and the runtime)
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ZoneConfig(_Strict):
    """A place cards can be."""

    visible: Visibility = Field("public",
                                description="public (everyone sees the cards) | owner (only the zone's owner) | hidden "
                                            "(nobody).")
    owned: bool | None = Field(None, description="Every player has their own copy of this zone (like a hand).")
    show: Literal["all", "top", "count"] = Field("all",
                                                 description="What the table view shows: every card, the top card, or "
                                                             "the count.")
    title: str | None = Field(None, description="Name shown in views.")


class CardEntry(_Strict):
    """One card, or a family of cards (every suit × every rank), with copies."""

    name: str | None = Field(None, description="Card name; may use {suit} and {rank}. Default '<suit> <rank>'.")
    suit: str | None = None
    rank: Any = None
    suits: list[str] | None = Field(None, description="Make one card per suit (combined with ranks).")
    ranks: list[Any] | None = Field(None, description="Make one card per rank (combined with suits).")
    copies: int = Field(1, ge=1, le=100)
    per_player: bool = Field(False,
                             description="Every player gets their own copies (starting decks); created in round 1.")
    zone: str = Field("deck", description="Where the card starts.")
    props: dict[str, Any] = Field(default_factory=dict, description="Extra card properties: cost, text, effect …")


class CardActionConfig(_Strict):
    """A generated card tool. ``true`` takes every default."""

    description: str = ""
    to: str | None = Field(None, description="Zone the card goes to (play: default discard).")
    where: Expr | None = Field(None,
                              description="Which cards of your hand qualify ($it the card, $actor): only these are "
                                          "offered.")
    when: list[Any] = Field(default_factory=list, description="Extra requirements for the tool, as in actions.")
    params: dict[str, Any] = Field(default_factory=dict, description="Extra tool arguments.")
    do: list[Any] = Field(default_factory=list, description="Effects after the card moves ($params.card is the card).")
    qty: int | str = Field(1, description="Cards drawn (draw).")
    terminal: bool | str = True
    announce: str | None = None
    outcome: str | None = None


class CardsConfig(_Strict):
    """A deck of cards played by agents of one type."""

    who: str = Field(..., description="Agent type holding hands (subtypes included).")
    type: str = Field("card", description="Entity type of the cards.")
    deck: Literal["standard"] | list[CardEntry] = Field(
        "standard",
        description="'standard' (52 cards, ids like AS 10H, ranks 2–14, suits spades hearts diamonds clubs) or a list "
                    "of card entries.")
    jokers: int = Field(0, ge=0, le=8, description="Jokers added to a standard deck.")
    props: dict[str, Any] = Field(default_factory=dict, description="Extra card property specs.")
    zones: dict[str, Visibility | ZoneConfig] = Field(
        default_factory=dict,
        description="Extra zones (or changes to deck, hand, discard, burn): a visibility or "
                    "{visible, owned, show, title}.")
    personal: bool = Field(False, description="Every player has their own draw pile and discard pile (deck-builders).")
    hand_size: int | str = Field(0, description="Cards dealt to each player (number or expression).")
    deal: Literal["start", "round", "never"] = Field(
        "start",
        description="start: shuffle and deal once in round 1 | round: collect, shuffle and deal every round | never.")
    deal_to: Expr | None = Field(None, description="Which players are dealt in ($it), e.g. \"$it.chips > 0\".")
    after_deal: list[Any] = Field(default_factory=list,
                                  description="Effects right after each deal (flip a starting card …).")
    reshuffle: bool = Field(True, description="An empty draw pile is refilled by shuffling the discard pile.")
    keep_top: bool = Field(False, description="The discard pile's top card stays when it is reshuffled.")
    play: bool | CardActionConfig | None = Field(None,
                                                 description="Generate `<name>_play` (a card from your hand to a "
                                                             "zone).")
    discard: bool | CardActionConfig | None = Field(None, description="Generate `<name>_discard`.")
    draw: bool | CardActionConfig | None = Field(None, description="Generate `<name>_draw`.")
    give: bool | CardActionConfig | None = Field(None,
                                                 description="Generate `<name>_give` (give a card to another player, "
                                                             "privately).")
    views: bool = Field(True, description="Generate the hand and table views.")


@dataclass(frozen=True)
class Zone:
    name: str
    visible: str
    owned: bool
    show: str
    title: str


@dataclass(frozen=True)
class Deck:
    name: str
    type: str
    who: str
    zones: Mapping[str, Zone]
    config: CardsConfig


def zones_for(config: CardsConfig) -> dict[str, Zone]:
    """The deck's zones: the built-in ones changed or extended by the config."""
    zones = {"deck": Zone("deck", "hidden", config.personal, "count", "Draw pile"),
             "discard": Zone("discard", "public", config.personal, "top", "Discard pile"),
             "hand": Zone("hand", "owner", True, "all", "Hand"),
             "burn": Zone("burn", "hidden", False, "count", "Burned")}
    for name, raw in config.zones.items():
        spec = ZoneConfig(visible=raw) if isinstance(raw, str) else raw
        base = zones.get(name)
        owned = spec.owned if spec.owned is not None else (base.owned if base else False)
        show = (spec.show if isinstance(raw, ZoneConfig) and "show" in raw.model_fields_set
                else (base.show if base else "all"))
        zones[name] = Zone(name, spec.visible, owned, show,
                           spec.title or (base.title if base else name.replace("_", " ").capitalize()))
    return zones


def card_family(entry: CardEntry) -> list[tuple[str, Any, str]]:
    """``(name, rank, suit)`` of every distinct card an entry makes (before copies)."""
    suits = entry.suits if entry.suits is not None else [entry.suit]
    ranks = entry.ranks if entry.ranks is not None else [entry.rank]
    out = []
    for suit in suits:
        for rank in ranks:
            if entry.name:
                rank_text = str(rank if rank is not None else "")
                name = entry.name.replace("{suit}", str(suit or "")).replace("{rank}", rank_text).strip()
            else:
                name = " ".join(str(p) for p in (suit, rank) if p not in (None, "")) or "card"
            out.append((name, rank, str(suit or "")))
    return out


def standard_cards(jokers: int) -> list[tuple[str, str, int, str]]:
    """``(id, name, rank, suit)`` of a standard deck."""
    cards = [(f"{RANK_LABELS[rank]}{SUIT_LETTERS[suit]}", f"{RANK_LABELS[rank]}{SUIT_SYMBOLS[suit]}", rank, suit)
             for suit in SUITS for rank in range(2, 15)]
    return cards + [(f"JOKER{n}", "Joker", 0, "joker") for n in range(1, jokers + 1)]


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "card"


def id_prefix(deck: str, decks: int) -> str:
    """What a deck's card ids start with: its name when the contract has several decks, so no two decks share an id."""
    return f"{deck}_" if decks > 1 else ""


# ---------------------------------------------------------------------------
# Decks of a running contract
# ---------------------------------------------------------------------------

def _build_decks(contract: Any) -> tuple[dict[str, Deck], dict[str, Deck]]:
    by_name = {name: Deck(name, config.type, config.who, zones_for(config), config)
               for name, config in parse_kind(contract, KEY, CardsConfig).items()}
    return by_name, {deck.type: deck for deck in by_name.values()}


def decks(world: Any) -> tuple[dict[str, Deck], dict[str, Deck]]:
    """The contract's decks by mechanism name and by card type (parsed once per contract)."""
    empty: tuple[dict[str, Deck], dict[str, Deck]] = ({}, {})
    return per_contract(world, "cards", _build_decks, empty)


def deck_named(world: Any, name: Any, where: str) -> Deck:
    by_name, _ = decks(world)
    if not isinstance(name, str) or name not in by_name:
        raise RunError(f"'{name}' is not a declared cards mechanism (decks: {', '.join(by_name) or 'none'})", where)
    return by_name[name]


def _zone(deck: Deck, name: Any, where: str) -> Zone:
    zone = deck.zones.get(name) if isinstance(name, str) else None
    if zone is None:
        raise RunError(f"'{name}' is not a zone of {deck.name} (zones: {', '.join(deck.zones)})", where)
    return zone


# ---------------------------------------------------------------------------
# Reading and changing cards
# ---------------------------------------------------------------------------


def deck_cards(world: Any, deck: Deck) -> list[Entity]:
    return list(world.entities_of(deck.type))


def cards_in(world: Any, deck: Deck, zone: str, owner: str | None = None) -> list[Entity]:
    """Cards in a zone (of one owner, or of every owner when None), bottom first."""
    items = [c for c in world.entities_of(deck.type)
             if _props(c).get("zone") == zone and (owner is None or _props(c).get("owner") == owner)]
    items.sort(key=lambda c: _props(c).get("order") or 0)
    return items


def card_visible(world: Any, card: Any, viewer: str | None) -> bool:
    """Whether the player with id ``viewer`` may see this card's face (None: a public observer)."""
    _, by_type = decks(world)
    deck = by_type.get(getattr(card, "entity_type", None))
    if deck is None:
        return True
    props = _props(card)
    if props.get("face_up"):
        return True
    zone = deck.zones.get(props.get("zone"))
    if zone is not None and zone.visible == "public":
        return True
    if viewer is None:
        return False
    if zone is not None and zone.visible == "owner" and props.get("owner") == viewer:
        return True
    return viewer in (props.get("seen_by") or [])


def card_names(cards: Any, ids: bool = False) -> str:
    items = cards if isinstance(cards, (list, tuple)) else [cards]
    parts = []
    for card in items:
        if card is None:
            continue
        name = getattr(card, "name", None) or str(card)
        card_id = getattr(card, "id", None)
        parts.append(f"{name} [{card_id}]" if ids and card_id and card_id != name else name)
    return ", ".join(parts)


def _set(world: Any, card: Entity, values: Mapping[str, Any]) -> None:
    for key, value in values.items():
        if _props(card).get(key) != value:
            world.set_prop(card, key, value)


def place(world: Any, deck: Deck, cards: list[Entity], zone_name: str, owner: str | None, where: str,
          face_up: bool = False, bottom: bool = False) -> None:
    """Move cards (in the order given) onto the top (or bottom) of a zone."""
    zone = _zone(deck, zone_name, where)
    if zone.owned and not owner:
        raise RunError(f"zone '{zone_name}' belongs to a player: say whose it is", where)
    owner_id = owner if zone.owned else ""
    moving = {c.id for c in cards}
    orders = [_props(c).get("order") or 0 for c in cards_in(world, deck, zone_name, owner_id) if c.id not in moving]
    top, low = (max(orders) + 1, min(orders) - 1) if orders else (0, 0)
    for card in cards:
        props = _props(card)
        values: dict[str, Any] = {"zone": zone_name, "owner": owner_id,
                                  "face_up": bool(face_up) and zone.visible != "hidden"}
        previous = deck.zones.get(props.get("zone"))
        if props.get("owner") and previous is not None and previous.owned and props.get("owner") != owner_id:
            values["played_by"] = props["owner"]
        if bottom:
            values["order"], low = low, low - 1
        else:
            values["order"], top = top, top + 1
        _set(world, card, values)


def _owners(world: Any, deck: Deck, zone: Zone) -> list[str]:
    if not zone.owned:
        return [""]
    return sorted({_props(c).get("owner") or "" for c in world.entities_of(deck.type)
                   if _props(c).get("zone") == zone.name} - {""})


def shuffle(world: Any, deck: Deck, zone_name: str, owner: str | None, where: str) -> None:
    """Put a zone's cards in random order and turn them face down (every owner's pile when owner is None)."""
    zone = _zone(deck, zone_name, where)
    for pile_owner in ([owner if zone.owned else ""] if owner is not None else _owners(world, deck, zone)):
        cards = cards_in(world, deck, zone_name, pile_owner)
        positions = list(range(len(cards)))
        world.rng.shuffle(positions)
        for card, position in zip(cards, positions):
            _set(world, card, {"order": position, "face_up": False, "seen_by": []})


def _refill(world: Any, deck: Deck, owner: str, where: str) -> bool:
    """Shuffle the discard pile into an empty draw pile. False when there is nothing to refill with."""
    discard = deck.zones.get("discard")
    if discard is None or not deck.config.reshuffle:
        return False
    pile = cards_in(world, deck, "discard", owner if discard.owned else "")
    if deck.config.keep_top and pile:
        pile = pile[:-1]
    if not pile:
        return False
    place(world, deck, pile, "deck", owner, where)
    shuffle(world, deck, "deck", owner, where)
    whose = f"{world.entities[owner].name}'s" if owner in world.entities else "The"
    world.emit("cards", f"{whose} discard pile was shuffled into the draw pile.", data={"deck": deck.name})
    return True


def _take(world: Any, deck: Deck, source: str, owner: str, where: str) -> Entity | None:
    zone = _zone(deck, source, where)
    pile_owner = owner if zone.owned else ""
    pile = cards_in(world, deck, source, pile_owner)
    if not pile and source == "deck" and _refill(world, deck, pile_owner, where):
        pile = cards_in(world, deck, source, pile_owner)
    return pile[-1] if pile else None


def deal(world: Any, deck: Deck, count: int, recipients: list[Entity] | None, zone_name: str, face_up: bool,
         source: str, where: str) -> list[Entity]:
    """Deal ``count`` cards to each recipient in turn (round-robin) from the top of ``source``.
    Without recipients the cards go to the (shared) zone. Stops when the pile runs out."""
    target = _zone(deck, zone_name, where)
    if recipients is None and target.owned:
        recipients = list(world.entities_of(deck.who))
    seats: list[Entity | None] = list(recipients) if recipients is not None else [None]
    dealt: list[Entity] = []
    exhausted = False
    for _ in range(count):
        for who in seats:
            card = _take(world, deck, source, who.id if who is not None else "", where)
            if card is None:
                exhausted = True
                break
            place(world, deck, [card], zone_name, who.id if who is not None else None, where, face_up)
            dealt.append(card)
        if exhausted:
            break
    world.set_world(f"{deck.name}_drawn", [c.id for c in dealt])
    if dealt and not target.owned and target.visible == "public":
        world.emit("cards", f"Dealt to the {target.title.lower()}: {card_names(dealt)}.", data={"deck": deck.name})
    return dealt


def collect(world: Any, deck: Deck, zones: list[str] | None, where: str) -> None:
    """Gather cards back into the draw pile(s), face down and unseen, then shuffle. With personal
    decks only cards in owned zones go back (each to its owner's pile)."""
    personal = deck.zones["deck"].owned
    for card in world.entities_of(deck.type):
        props = _props(card)
        zone = deck.zones.get(props.get("zone"))
        if zones is not None and props.get("zone") not in zones:
            continue
        if personal and (zone is None or not zone.owned):
            continue
        _set(world, card, {"zone": "deck", "owner": props.get("owner") if personal else "", "face_up": False,
                           "seen_by": [], "played_by": ""})
    shuffle(world, deck, "deck", None, where)


def create_personal_cards(world: Any, deck: Deck, where: str) -> None:
    """Create every player's own copies of per-player card entries (once)."""
    entries = deck.config.deck if isinstance(deck.config.deck, list) else []
    players = list(world.entities_of(deck.who))
    scope = world.evaluation.scope()
    prefix = id_prefix(deck.name, len(decks(world)[0]))
    for entry in (e for e in entries if e.per_player):
        for player in players:
            for name, rank, suit in card_family(entry):
                for copy in range(1, entry.copies + 1):
                    card_id = f"{prefix}{slug(name)}_{player.id}" + (f"_{copy}" if copy > 1 else "")
                    if card_id in world.entities:
                        continue
                    zone = _zone(deck, entry.zone, where)
                    props = {**entry.props, "rank": rank, "suit": suit, "zone": entry.zone,
                             "owner": player.id if zone.owned else "", "order": copy}
                    world.evaluation.create(deck.type, card_id, name, props, None, scope, where)


# ---------------------------------------------------------------------------
# The game op's cards actions (the effect names the deck: {"game": <deck>, "action": ...})
# ---------------------------------------------------------------------------


def _entities(world: Any, value: Any, where: str, what: str = "cards") -> list[Entity]:
    items = value if isinstance(value, (list, tuple)) else ([] if value is None else [value])
    out = []
    for item in items:
        entity = world.entities.get(item) if isinstance(item, str) else item
        if not isinstance(entity, Entity):
            raise RunError(f"expected {what} (entities or ids), got {item!r}", where)
        out.append(entity)
    return out


def _cards(runner: Any, effect: dict[str, Any], vars: dict[str, Any], deck: Deck, where: str) -> list[Entity]:
    """The cards an action moves or shows: every one a card of the deck the action names."""
    cards = _entities(runner.world, runner.eval(effect["cards"], vars), f"{where}.cards")
    for card in cards:
        if card.entity_type != deck.type:
            raise RunError(f"{card.id} is not a card of {deck.name} (its cards are {deck.type} entities)",
                           f"{where}.cards")
    return cards


def _qty(runner: Any, raw: Any, vars: dict[str, Any], where: str) -> int:
    return whole(runner.eval(raw, vars), where, "qty")


def _name(runner: Any, raw: Any, vars: dict[str, Any], where: str, what: str) -> str:
    value = runner.eval(raw, vars) if is_expr(raw) else raw
    if not isinstance(value, str):
        raise RunError(f"{what} must be a name, got {value!r}", where)
    return value


def _owner(runner: Any, raw: Any, vars: dict[str, Any], where: str) -> str | None:
    value = runner.eval(raw, vars)
    if value is None:
        return None
    entity = runner.world.entity(value)
    if entity is None:
        raise RunError(f"expected a player (entity or id), got {value!r}", where)
    return entity.id


def _zone_check(*zone_keys: str) -> Callable[[Any, dict[str, Any], str], list[tuple[str, str, str | None]]]:
    """A static check that the literal zone names in ``zone_keys`` are zones of the deck the effect names."""

    def check(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
        deck = effect["game"]
        try:
            zones = zones_for(CardsConfig.model_validate(config_data(checker.c.mechanisms[deck])))
        except ValidationError:  # the config's own errors are reported against the mechanism
            return []
        return [(f"{path}.{key}", f"'{effect[key]}' is not a zone of {deck}", f"zones: {', '.join(zones)}")
                for key in zone_keys
                if isinstance(effect.get(key), str) and not is_expr(effect[key]) and effect[key] not in zones]

    return check


@family_action("game", ("cards",), "shuffle", keys=("zone", "owner"), check=_zone_check("zone"),
               example='{"game": "cards", "action": "shuffle", "zone": "deck"}  (random order, face down; an owned '
                       'zone shuffles each pile, or only `owner`\'s)')
def _shuffle_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    deck = deck_named(runner.world, effect["game"], where)
    shuffle(runner.world, deck, _name(runner, effect.get("zone", "deck"), vars, where, "zone"),
            _owner(runner, effect.get("owner"), vars, where), where)


@family_action("game", ("cards",), "collect", keys=("zones",),
               example='{"game": "cards", "action": "collect"}  (every card back into the draw pile, face down, '
                       'shuffled; `zones` limits which)')
def _collect_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    deck = deck_named(runner.world, effect["game"], where)
    zones = runner.eval(effect["zones"], vars) if "zones" in effect else None
    if zones is not None and not isinstance(zones, list):
        raise RunError(f"zones must be a list of zone names, got {zones!r}", where)
    collect(runner.world, deck, zones, where)


@family_action("game", ("cards",), "deal", keys=("qty", "to", "zone", "face_up", "from"),
               check=_zone_check("zone", "from"),
               example='{"game": "cards", "action": "deal", "qty": 2, "to": "$filter(player, $it.chips > 0)"}  '
                       '(round-robin from the top; no `to`: every player, or the zone itself when it is shared: '
                       '{"game": "cards", "action": "deal", "qty": 3, "zone": "board"})')
def _deal_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    deck = deck_named(world, effect["game"], where)
    recipients = _entities(world, runner.eval(effect["to"], vars), where, "players") if "to" in effect else None
    deal(world, deck, _qty(runner, effect.get("qty", 1), vars, where), recipients,
         _name(runner, effect.get("zone", "hand"), vars, where, "zone"),
         bool(runner.eval(effect.get("face_up", False), vars)),
         _name(runner, effect.get("from", "deck"), vars, where, "from"), where)


@family_action("game", ("cards",), "draw", keys=("qty", "who", "zone", "face_up"), check=_zone_check("zone"),
               example='{"game": "cards", "action": "draw", "qty": 1}  (for $actor, or `who`; an empty draw pile is '
                       'refilled from the discard pile; the cards are in $world.<deck>_drawn)')
def _draw_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    deck = deck_named(world, effect["game"], where)
    who = runner.eval(effect["who"], vars) if "who" in effect else vars.get("actor")
    recipients = _entities(world, who, where, "a player")
    if len(recipients) != 1:
        raise RunError("`draw` needs one player (`who`, default $actor)", where)
    deal(world, deck, _qty(runner, effect.get("qty", 1), vars, where), recipients,
         _name(runner, effect.get("zone", "hand"), vars, where, "zone"),
         bool(runner.eval(effect.get("face_up", False), vars)), "deck", where)


@family_action("game", ("cards",), "burn", keys=("qty",),
               example='{"game": "cards", "action": "burn", "qty": 1}  (top cards of the draw pile to the hidden burn '
                       'zone)')
def _burn_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    deck = deck_named(runner.world, effect["game"], where)
    deal(runner.world, deck, _qty(runner, effect.get("qty", 1), vars, where), None, "burn", False, "deck", where)


@family_action("game", ("cards",), "move", keys=("cards", "to", "owner", "face_up", "bottom"), required=("cards", "to"),
               check=_zone_check("to"),
               example='{"game": "cards", "action": "move", "cards": "$params.card", "to": "tableau", "owner": '
                       '"$actor", "face_up": true}  (onto the top, or the bottom)')
def _move_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    deck = deck_named(world, effect["game"], where)
    place(world, deck, _cards(runner, effect, vars, deck, where), _name(runner, effect["to"], vars, where, "to"),
          _owner(runner, effect.get("owner"), vars, where), where,
          bool(runner.eval(effect.get("face_up", False), vars)), bool(runner.eval(effect.get("bottom", False), vars)))


@family_action("game", ("cards",), "play", keys=("cards", "to"), required=("cards",), check=_zone_check("to"),
               example='{"game": "cards", "action": "play", "cards": "$params.card", "to": "trick"}  (face up onto '
                       'a zone; default the discard pile)')
def _play_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    deck = deck_named(world, effect["game"], where)
    cards = _cards(runner, effect, vars, deck, where)
    owners = {_props(c).get("owner") for c in cards}
    place(world, deck, cards, _name(runner, effect.get("to", "discard"), vars, where, "to"),
          next(iter(owners)) if len(owners) == 1 else None, where, face_up=True)


@family_action("game", ("cards",), "discard", keys=("cards",), required=("cards",),
               example='{"game": "cards", "action": "discard", "cards": "$params.card"}  (onto the discard pile; its '
                       'owner\'s pile with personal decks)')
def _discard_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    deck = deck_named(world, effect["game"], where)
    for card in _cards(runner, effect, vars, deck, where):
        place(world, deck, [card], "discard", _props(card).get("owner") or None, where)


@family_action("game", ("cards",), "give", keys=("cards", "to", "zone"), required=("cards", "to"),
               check=_zone_check("zone"),
               example='{"game": "cards", "action": "give", "cards": "$params.card", "to": "$params.to"}  (into '
                       'another player\'s hand; only the two of them see which)')
def _give_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    deck = deck_named(world, effect["game"], where)
    receiver = _owner(runner, effect["to"], vars, where)
    if receiver is None:
        raise RunError("`give` needs a player in `to`", where)
    zone = _name(runner, effect.get("zone", "hand"), vars, where, "zone")
    cards = _cards(runner, effect, vars, deck, where)
    givers = {_props(c).get("owner") for c in cards if _props(c).get("owner")}
    for card in cards:
        seen = list(_props(card).get("seen_by") or [])
        giver = _props(card).get("owner")
        if giver and giver != receiver and giver not in seen:
            _set(world, card, {"seen_by": seen + [giver]})
    place(world, deck, cards, zone, receiver, where)
    names = ", ".join(world.entities[g].name for g in sorted(givers) if g in world.entities) or "Someone"
    world.emit("cards", f"{names} passed you {card_names(cards)}.", to=(receiver,), data={"deck": deck.name})


def _reveal(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str, viewers: Any) -> None:
    world = runner.world
    cards = _cards(runner, effect, vars, deck_named(world, effect["game"], where), where)
    if not cards:
        return
    ids = None if viewers is None else [e.id for e in _entities(world, viewers, where, "players")]
    say = runner.said(effect["say"], vars, ids) if effect.get("say") else ""  # for the players it is shown to
    if ids is None:
        for card in cards:
            _set(world, card, {"face_up": True})
        by_owner: dict[str, list[Entity]] = {}
        for card in cards:
            by_owner.setdefault(_props(card).get("owner") or "", []).append(card)
        parts = [f"{world.entities[owner].name} shows {card_names(held)}" if owner in world.entities
                 else f"Revealed: {card_names(held)}" for owner, held in by_owner.items()]
        world.emit("cards", say or "; ".join(parts) + ".")
        return
    for card in cards:
        seen = list(_props(card).get("seen_by") or [])
        _set(world, card, {"seen_by": seen + [i for i in ids if i not in seen]})
    world.emit("cards", say or f"You see: {card_names(cards)}.", to=tuple(ids))


def _check_reveal(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
    if "to" not in effect:  # face up for everyone: its `say` is news to every player
        checker._shared_text(effect.get("say"), f"{path}.say", {})
    return []


@family_action("game", ("cards",), "reveal", keys=("cards", "to", "say"), required=("cards",), templates=("say",),
               check=_check_reveal,
               example='{"game": "cards", "action": "reveal", "cards": "$hand($it)"}  (face up for everyone; with '
                       '`to`, shown only to those players)')
def _reveal_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    _reveal(runner, effect, vars, where, runner.eval(effect["to"], vars) if "to" in effect else None)


@family_action("game", ("cards",), "peek", keys=("cards", "to", "say"), required=("cards",), templates=("say",),
               example='{"game": "cards", "action": "peek", "cards": "$top_cards(deck, 3)", "to": "$actor"}  (only '
                       '`to` (default $actor) sees the cards, from now on)')
def _peek_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    viewers = runner.eval(effect["to"], vars) if "to" in effect else vars.get("actor")
    if viewers is None:
        raise RunError("`peek` needs a player in `to` (default $actor)", where)
    _reveal(runner, effect, vars, where, viewers)


@family_action("game", ("cards",), "setup", internal=True,
               example='{"game": "cards", "action": "setup"}  (create per-player cards and shuffle every draw pile; '
                       'generated for round 1)')
def _setup_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    deck = deck_named(runner.world, effect["game"], where)
    create_personal_cards(runner.world, deck, where)
    shuffle(runner.world, deck, "deck", None, where)


# ---------------------------------------------------------------------------
# Expression functions
# ---------------------------------------------------------------------------


def _function_deck(call: Call, name: Any) -> Deck:
    by_name, _ = decks(call.scope.world)
    if name is not None:
        if name not in by_name:
            raise ExprError(f"${call.name}: '{name}' is not a declared cards mechanism (decks: "
                            f"{', '.join(by_name) or 'none'})", call.source)
        return by_name[name]
    if len(by_name) != 1:
        raise ExprError(f"${call.name}: " + ("no cards mechanism is declared" if not by_name else
                        f"there are several decks ({', '.join(by_name)}): name one as the last argument"), call.source)
    return next(iter(by_name.values()))


def _owner_arg(call: Call, value: Any, required: bool) -> str | None:
    if value is None:
        if required:
            raise ExprError(f"${call.name}: expected a player, got null", call.source)
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, Entity):
        return value.id
    raise ExprError(f"${call.name}: expected a player (entity or id), got {value!r}", call.source)


def _zone_arg(call: Call, deck: Deck, value: Any) -> str:
    if not isinstance(value, str) or value not in deck.zones:
        raise ExprError(f"${call.name}: '{value}' is not a zone of {deck.name} (zones: {', '.join(deck.zones)})",
                        call.source)
    return value


@function("hand(player, deck?)", "The cards in a player's hand, oldest first.", min_args=1, max_args=2, family="game")
def _hand_function(call: Call) -> list[Entity]:
    deck = _function_deck(call, call.arg(1))
    return cards_in(call.scope.world, deck, "hand", _owner_arg(call, call.arg(0), True))


@function("zone(name, owner?, deck?)", "The cards in a zone (one owner's, or everyone's), bottom first.", min_args=1,
          max_args=3, family="game")
def _zone_function(call: Call) -> list[Entity]:
    deck = _function_deck(call, call.arg(2))
    return cards_in(call.scope.world, deck, _zone_arg(call, deck, call.arg(0)), _owner_arg(call, call.arg(1), False))


@function("top_card(zone, owner?, deck?)", "The top card of a zone (the last card placed), or null.", min_args=1,
          max_args=3, family="game")
def _top_card_function(call: Call) -> Entity | None:
    deck = _function_deck(call, call.arg(2))
    cards = cards_in(call.scope.world, deck, _zone_arg(call, deck, call.arg(0)), _owner_arg(call, call.arg(1), False))
    return cards[-1] if cards else None


@function("top_cards(zone, n, owner?, deck?)", "The top n cards of a zone, top first.", min_args=2, max_args=4,
          family="game")
def _top_cards_function(call: Call) -> list[Entity]:
    deck = _function_deck(call, call.arg(3))
    n = call.arg(1)
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ExprError(f"$top_cards: n must be a whole number ≥ 0, got {n!r}", call.source)
    cards = cards_in(call.scope.world, deck, _zone_arg(call, deck, call.arg(0)), _owner_arg(call, call.arg(2), False))
    return list(reversed(cards))[:n]


@function("card_visible(card, viewer)",
          "True when `viewer` may see the card's face (public zone, face up, own hand, or peeked).",
          min_args=2, max_args=2, family="game")
def _card_visible_function(call: Call) -> bool:
    return card_visible(call.scope.world, call.arg(0), _owner_arg(call, call.arg(1), False))


@function("card_names(cards, ids?)", "Card names as text ('A♠, K♥'); with ids true each name is followed by its [id].",
          min_args=1, max_args=2, family="game")
def _card_names_function(call: Call) -> str:
    value = call.arg(0)
    items = value if isinstance(value, (list, tuple)) else [value]
    entities = getattr(call.scope.world, "entities", {})
    return card_names([entities.get(item, item) if isinstance(item, str) else item for item in items],
                      bool(call.arg(1, False)))


@function("cards_table(deck, viewer)",
          "Lines describing every zone of a deck as `viewer` may see it: visible cards by name, hidden ones only "
          "counted (the viewer's own hand is left out). Used by the table view.", min_args=2, max_args=2, family="game")
def _cards_table_function(call: Call) -> list[str]:
    world: Any = call.scope.world
    deck = _function_deck(call, call.arg(0))
    return table_text(world, deck, _owner_arg(call, call.arg(1), False))


def table_text(world: Any, deck: Deck, viewer: str | None) -> list[str]:
    groups: dict[tuple[str, str], list[Entity]] = {}
    for card in world.entities_of(deck.type):
        props = _props(card)
        groups.setdefault((props.get("zone") or "", props.get("owner") or ""), []).append(card)
    lines: list[str] = []
    counts: list[str] = []
    for zone in sorted(deck.zones.values(), key=lambda z: z.name == "hand"):
        owners = sorted({owner for (name, owner) in groups if name == zone.name})
        for owner in owners:
            if zone.name == "hand" and owner == viewer:
                continue
            cards = sorted(groups[(zone.name, owner)], key=lambda c: _props(c).get("order") or 0)
            seen = [c for c in cards if card_visible(world, c, viewer)]
            who = world.entities[owner].name if owner in world.entities else owner
            label = f"{who}'s {zone.title.lower()}" if owner else zone.title
            if zone.show == "count" or (not seen and zone.owned and owner):
                counts.append(f"{label} {len(cards)}")
            elif zone.show == "top":
                top = cards[-1]
                shown = top.name if card_visible(world, top, viewer) else "face down"
                lines.append(f"{label}: {shown} on top ({len(cards)} cards)")
            else:
                hidden = len(cards) - len(seen)
                lines.append(f"{label}: {card_names(seen) or 'no visible cards'}"
                             + (f" + {hidden} face down" if hidden else ""))
    if counts:
        lines.append("Card counts: " + ", ".join(counts))
    return lines
