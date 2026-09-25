"""The ``game.cards`` mode: a deck expands into a card type, card entities, deal events, card tools and views."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..registry import MechanismError, mode, use_key
from ._common import raw_is_a
from .cards import KEY, CardActionConfig, CardsConfig, Zone, card_family, id_prefix, slug, standard_cards, zones_for

__all__: list[str] = []

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
_INSPECT = "$card_visible($it, $viewer)"
_BASE_PROPS: dict[str, Any] = {
    "rank": {"type": "any", "default": 0, "description": "Rank (standard decks: 2–14, ace high)."},
    "suit": {"type": "text", "default": ""},
    "zone": {"type": "text", "default": "deck", "description": "Where the card is."},
    "owner": {"type": "text", "default": "", "description": "Player whose zone holds it ('' when shared)."},
    "order": {"type": "int", "default": 0, "description": "Position in its zone; the highest is the top."},
    "face_up": {"type": "bool", "default": False},
    "seen_by": {"type": "list", "default": []},
    "played_by": {"type": "text", "default": "", "description": "Last player who held it."},
}


def _prop_default(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"type": "bool", "default": False}
    if isinstance(value, (int, float)):
        return {"type": "number", "default": 0}
    if isinstance(value, str):
        return {"type": "text", "default": ""}
    if isinstance(value, list):
        return {"type": "list", "default": []}
    if isinstance(value, dict):
        return {"type": "map", "default": {}}
    return {"type": "any", "default": None}


def _card_entities(config: CardsConfig, zones: Mapping[str, Zone], taken: Mapping[str, Any], prefix: str
                   ) -> tuple[dict[str, Any], dict[str, Any]]:
    """Shared card entities (per-player cards are created in round 1) and the props their entries use."""
    entities: dict[str, Any] = {}
    props: dict[str, Any] = {}
    if config.deck == "standard":
        for order, (card_id, name, rank, suit) in enumerate(standard_cards(config.jokers)):
            entities[prefix + card_id] = {"type": config.type, "name": name,
                                          "props": {"rank": rank, "suit": suit, "order": order}}
    else:
        for index, entry in enumerate(config.deck):
            zone = zones.get(entry.zone)
            if zone is None:
                raise MechanismError(f"'{entry.zone}' is not a zone", f"zones: {', '.join(zones)}",
                                     f"deck[{index}].zone")
            if zone.owned and not entry.per_player:
                raise MechanismError(f"a shared card cannot start in the owned zone '{entry.zone}'",
                                     "set per_player: true, or start it in a shared zone", f"deck[{index}].zone")
            for key, value in entry.props.items():
                spec = _prop_default(value)
                if key in props and props[key]["type"] != spec["type"]:
                    spec = {"type": "any", "default": None}
                props[key] = spec
            if entry.per_player:
                continue
            for name, rank, suit in card_family(entry):
                for _ in range(entry.copies):
                    base = prefix + slug(name)
                    card_id, n = base, 1
                    while card_id in entities:
                        n += 1
                        card_id = f"{base}_{n}"
                    entities[card_id] = {"type": config.type, "name": name, "props": {
                        **entry.props, "rank": rank, "suit": suit, "zone": entry.zone, "order": len(entities)}}
    clashes = sorted(set(entities) & set(taken))
    if clashes:
        raise MechanismError(f"card ids {clashes[:5]} clash with declared entities", "rename those entities", "deck")
    return entities, props


def _tool(value: Any) -> CardActionConfig | None:
    if value is None or value is False:
        return None
    return CardActionConfig() if value is True else value


def _card_param(config: CardsConfig, where: str | None, description: str) -> dict[str, Any]:
    rule = "$it.zone == 'hand' and $it.owner == $actor.id" + (f" and ({where})" if where else "")
    return {"type": "entity", "of": config.type, "where": rule, "description": description}


def _action(config: CardsConfig, tool: CardActionConfig, description: str, params: dict[str, Any], do: list[Any],
            outcome: str, announce: str | None, private: bool = False) -> dict[str, Any]:
    action: dict[str, Any] = {"by": config.who, "description": tool.description or description,
                              "params": {**params, **tool.params}, "when": list(tool.when), "do": do + list(tool.do),
                              "outcome": tool.outcome or outcome, "terminal": tool.terminal}
    if private:
        action["private"] = True
    elif tool.announce or announce:
        action["announce"] = tool.announce or announce
    return action


def _actions(name: str, config: CardsConfig, zones: Mapping[str, Zone]) -> dict[str, Any]:
    actions: dict[str, Any] = {}
    play = _tool(config.play)
    if play is not None:
        target = play.to or "discard"
        zone = zones.get(target)
        if zone is None:
            raise MechanismError(f"'{target}' is not a zone", f"zones: {', '.join(zones)}", "play.to")
        public = zone.visible == "public"
        move = {"game": name, "action": "play", "cards": "$params.card", "to": target} if public else \
            {"game": name, "action": "move", "cards": "$params.card", "to": target,
             **({"owner": "$actor"} if zone.owned else {})}
        legal = " Only cards you may play now are listed." if play.where else ""
        actions[f"{name}_play"] = _action(
            config, play, f"Play a card from your hand to the {zone.title.lower()}.{legal}",
            {"card": _card_param(config, play.where, "The card to play.")}, [move], "You played {$params.card.name}.",
            "{$actor.name} plays {$params.card.name}." if public else "{$actor.name} plays a card face down.")
    discard = _tool(config.discard)
    if discard is not None:
        public = zones["discard"].visible == "public"
        actions[f"{name}_discard"] = _action(
            config, discard, "Discard a card from your hand.",
            {"card": _card_param(config, discard.where, "The card to discard.")},
            [{"game": name, "action": "discard", "cards": "$params.card"}], "You discarded {$params.card.name}.",
            "{$actor.name} discards {$params.card.name}." if public else "{$actor.name} discards a card.")
    draw = _tool(config.draw)
    if draw is not None:
        drawn = f"$world.{name}_drawn"
        actions[f"{name}_draw"] = _action(
            config, draw, "Draw from the draw pile.", {}, [{"game": name, "action": "draw", "qty": draw.qty}],
            f"You drew {{$card_names({drawn}) or 'nothing: no cards are left'}}.",
            f"{{$actor.name}} draws {{$len({drawn})}} {{$'card' if $len({drawn}) == 1 else 'cards'}}.")
    give = _tool(config.give)
    if give is not None:
        actions[f"{name}_give"] = _action(
            config, give, "Give a card from your hand to another player (only the two of you see which).",
            {"card": _card_param(config, give.where, "The card to give."),
             "to": {"type": "entity", "of": config.who, "where": "$it.id != $actor.id",
                    "description": "Who receives it."}},
            [{"game": name, "action": "give", "cards": "$params.card", "to": "$params.to"}],
            "You passed {$params.card.name} to {$params.to.name}.", None, private=True)
    return actions


def _events(name: str, config: CardsConfig) -> list[dict[str, Any]]:
    deal: dict[str, Any] = {"game": name, "action": "deal", "qty": config.hand_size}
    if config.deal_to:
        deal["to"] = f"$filter({config.who}, {config.deal_to})"
    dealing = [deal] if config.hand_size not in (0, "0") else []
    setup: dict[str, Any] = {"name": f"{name}_setup", "at": 1, "do": [{"game": name, "action": "setup"}]}
    if config.deal == "start":
        setup["do"] += dealing + list(config.after_deal)
    elif config.deal == "never":
        setup["do"] += list(config.after_deal)
    events = [setup]
    if config.deal == "round":
        events.append({"name": f"{name}_deal", "every": 1,
                       "do": [{"game": name, "action": "collect"}, *dealing, *config.after_deal]})
    return events


def _deck_holds_the_deal(config: CardsConfig, contract: Mapping[str, Any]) -> None:
    """A deal of `hand_size` to every player needs that many cards in the shared draw pile: a deck too small would deal
    some players fewer cards without a word. Checked when the numbers are written out (a literal hand size, a
    shared deck, players named or counted)."""
    if not isinstance(config.hand_size, int) or config.hand_size <= 0 or config.personal or config.deal_to:
        return
    if config.deck == "standard":
        cards = 52 + config.jokers
    else:
        cards = sum(len(entry.suits or [None]) * len(entry.ranks or [None]) * entry.copies for entry in config.deck
                    if entry.zone == "deck" and not entry.per_player)
    players = 0
    for entity in (contract.get("entities") or {}).values():
        if not isinstance(entity, Mapping) or not raw_is_a(contract, str(entity.get("type")), config.who):
            continue
        count = entity.get("count", None if "from" in entity else 1)
        if isinstance(count, bool) or not isinstance(count, int):
            return  # how many players there are is worked out as the world is built
        players += count
    if config.hand_size * players > cards:
        raise MechanismError(f"dealing {config.hand_size} cards to each of {players} players takes "
                             f"{config.hand_size * players:,}, but the deck has {cards:,}",
                             f"deal at most {cards // max(1, players)} each, or use a bigger deck", "hand_size")


@mode("game", "cards", CardsConfig,
      "A deck of cards as world state: card entities with a zone (deck, hand, discard, burn or declared zones), "
      "owner and order; who sees a card is enforced by the engine (views, inspect, tools). Generates the card "
      "type, the cards, round-1 setup (shuffle and deal), optional `<name>_play` / `<name>_discard` / `<name>_draw` / "
      "`<name>_give` tools listing only legal cards, and views of your hand and the table. Functions: "
      "$hand, $zone, $top_card, $top_cards, $card_names, $poker_rank, $blackjack_value, $sets, $runs, "
      "$trick_winner, $follow_suit.",
      example={"who": "player", "hand_size": 7, "keep_top": True,
               "after_deal": [{"game": "my_cards", "action": "deal", "qty": 1, "zone": "discard"}],
               "play": {"where": "$it.suit == $top_card(discard).suit or $it.rank == $top_card(discard).rank"},
               "draw": True})
def _expand_cards(name: str, config: CardsConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    types = contract.get("types") or {}
    if config.who not in types:
        raise MechanismError(f"who '{config.who}' is not a declared type", f"types: {', '.join(types) or 'none'}",
                             "who")
    if not _NAME.match(config.type):
        raise MechanismError(f"'{config.type}' is not a valid type name", "use letters, digits and _", "type")
    declared = types.get(config.type)
    if isinstance(declared, Mapping) and declared.get("inspect") != _INSPECT:
        raise MechanismError(f"type '{config.type}' is declared without the card visibility rule, so hidden cards "
                             "could be inspected",
                             f"remove the declaration, or add \"inspect\": \"{_INSPECT}\"", "type")
    _deck_holds_the_deal(config, contract)
    for zone_name in config.zones:
        if not _NAME.match(zone_name):
            raise MechanismError(f"'{zone_name}' is not a valid zone name", "use letters, digits and _",
                                 f"zones.{zone_name}")
    zones = zones_for(config)
    if not zones["hand"].owned or zones["hand"].visible == "public":
        raise MechanismError("the hand zone must stay owned and not public", "declare another zone instead",
                             "zones.hand")
    decks = [n for n, use in (contract.get("mechanisms") or {}).items() if use_key(use) == KEY]
    for other in decks[:decks.index(name)]:
        if ((contract["mechanisms"][other].get("type") or CardsConfig.model_fields["type"].default) == config.type):
            raise MechanismError(f"decks '{other}' and '{name}' both hold cards of type '{config.type}'",
                                 f"give each deck its own card type, e.g. \"type\": \"{name}_card\"", "type")
    entities, entry_props = _card_entities(config, zones, contract.get("entities") or {}, id_prefix(name, len(decks)))
    props = {**_BASE_PROPS, **entry_props, **config.props}
    fragment: dict[str, Any] = {
        "types": {config.type: {"description": f"A card of the {name} deck.", "props": props, "inspect": _INSPECT}},
        "entities": entities,
        "world": {f"{name}_drawn": {"type": "list", "default": [],
                                    "description": "Cards moved by the last deal or draw."}},
        "events": _events(name, config),
        "actions": _actions(name, config, zones),
    }
    if config.views:
        fragment["views"] = {
            f"{name}_hand": {"for": config.who, "title": f"Your hand ({{$len($hand($actor, '{name}'))}})",
                             "show": f"{{$card_names($hand($actor, '{name}'), true) or 'no cards'}}"},
            f"{name}_table": {"for": config.who, "title": "Cards on the table", "of": f"$cards_table('{name}', $actor)",
                              "show": "{$it}"},
        }
    return fragment
