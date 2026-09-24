"""Ready-to-run starting contracts: ``fg_env.new("game")`` / ``fg-env new game my_game.json``.

Each template is small, checks clean and runs with random agents, and shows one way of working: a blank
world, a turn-based game with a winner, a market, a simulation of a population, and a social setting with a vote.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Union

from ..errors import ContractError, Issue

__all__ = ["TEMPLATES", "new"]

_BLANK: Dict[str, Any] = {
    "name": "My environment",
    "brief": {"situation": "Describe the world in a sentence or two.", "rules": "Say what agents can do and what happens."},
    "clock": {"rounds": 5},
    "types": {"worker": {"agent": True, "props": {"score": 0}}},
    "entities": {"ada": {"type": "worker"}, "bo": {"type": "worker"}},
    "actions": {"work": {"by": "worker", "description": "Work to raise your score.",
                         "params": {"effort": {"type": "int", "min": 1, "max": 3}},
                         "do": "$actor.score += $params.effort if $chance(0.7) else 0",
                         "outcome": "Your score is now {$actor.score}."}},
    "views": {"scores": {"for": "worker", "title": "Scores", "of": "worker", "show": "{name}: {score}"}},
    "outputs": {"top_score": "$max(worker, $it.score)"},
}

_GAME: Dict[str, Any] = {
    "name": "Take the last stone",
    "brief": {"situation": "Two players share a pile of 15 stones.",
              "rules": "Take turns removing 1 to 3 stones. Whoever takes the last stone wins."},
    "clock": {"rounds": 15, "unit": "turn"},
    "world": {"stones": 15},
    "types": {"player": {"agent": True, "props": {"taken": 0}}},
    "entities": {"north": {"type": "player", "name": "North"}, "south": {"type": "player", "name": "South"}},
    "actions": {"take": {"by": "player", "description": "Take stones from the pile.",
                         "params": {"count": {"type": "int", "min": 1, "max": "$min(3, $world.stones)"}},
                         "do": ["$world.stones -= $params.count", "$actor.taken += $params.count",
                                {"if": "$world.stones == 0",
                                 "then": {"end": "last_stone", "winner": "$actor", "say": "{$actor.name} takes the last stone."}}],
                         "announce": "{$actor.name} takes {$params.count}; {$world.stones} left."}},
    "stages": [{"name": "play", "turns": "sequential", "must_act": True}],
    "views": {"pile": {"for": "player", "show": "Stones left: {$world.stones}."}},
    "outputs": {"winner": "$result.winner.name if $result.winner else 'nobody'"},
    "game": {"players": "player", "returns": "1 if $result.winner == $actor else 0"},
}

_MARKET: Dict[str, Any] = {
    "name": "Bread market",
    "brief": {"situation": "A baker sells bread to three households each day.",
              "rules": "Each morning the baker sets a price. Then households buy what they can afford.",
              "roles": {"baker": "Earn as much as you can.", "household": "Feed your family on a budget."}},
    "clock": {"rounds": 7, "unit": "day"},
    "inputs": {"cost": {"type": "number", "default": 1.0, "description": "What one loaf costs the baker."}},
    "types": {"baker": {"agent": True, "props": {"cash": 0.0, "price": 2.0}},
              "household": {"agent": True, "props": {"cash": 20.0, "loaves": 0}}},
    "entities": {"mo": {"type": "baker", "name": "Mo"}},
    "population": [{"type": "household", "count": 3, "name": "Household {$i}"}],
    "actions": {
        "set_price": {"by": "baker", "description": "Set today's price per loaf.",
                      "params": {"price": {"type": "number", "min": 1, "max": 6}},
                      "do": "$actor.price = $params.price", "announce": "Bread costs {$params.price|money} today."},
        "buy": {"by": "household", "description": "Buy loaves at today's price.",
                "params": {"qty": {"type": "int", "min": 1, "max": "$min(3, $floor($actor.cash / $entity(mo).price))"}},
                "when": {"expr": "$actor.cash >= $entity(mo).price", "why": "You cannot afford a loaf"},
                "do": [{"transfer": "cash", "from": "$actor", "to": "mo", "amount": "$params.qty * $entity(mo).price"},
                       "$entity(mo).cash -= $params.qty * $inputs.cost", "$actor.loaves += $params.qty"]},
    },
    "stages": [{"name": "morning", "actions": ["set_price"]},
               {"name": "shopping", "actions": ["buy"], "order": "random"}],
    "views": {"shop": {"for": "household", "show": "Bread costs {$entity(mo).price|money}. You have {cash|money}."},
              "books": {"for": "baker", "show": "Cash {cash|money}, price {price|money}."}},
    "metrics": {"price": "$entity(mo).price", "sold": "$sum(household, $it.loaves)"},
    "outputs": {"profit": {"expr": "$entity(mo).cash", "type": "number"},
                "loaves_sold": {"expr": "$metrics.sold", "type": "int"}},
}

_SIMULATION: Dict[str, Any] = {
    "name": "Wealth exchange",
    "brief": {"situation": "Fifty people start with the same wealth and trade at random.",
              "rules": "Every round each person with money gives one unit to a random other person."},
    "clock": {"rounds": 30},
    "inputs": {"people": {"type": "int", "default": 50, "min": 1}},
    "types": {"person": {"props": {"wealth": 5}}},
    "population": [{"type": "person", "count": "$inputs.people"}],
    "events": [{"phase": "end", "when": "$count(person) > 1", "each": "person", "where": "$it.wealth > 0",
                "do": [{"transfer": "wealth", "from": "$it", "to": "$choice($filter(person, $it.id != $outer.id))",
                        "amount": 1}]}],
    "metrics": {"gini": "$gini($map(person, $it.wealth))", "broke": "$count(person, $it.wealth == 0)"},
    "invariants": ["$sum(person, $it.wealth) == 5 * $inputs.people"],
    "outputs": {"gini": {"expr": "$metrics.gini", "type": "number"}, "broke": {"expr": "$metrics.broke", "type": "int"}},
}

_SOCIAL: Dict[str, Any] = {
    "name": "Town meeting",
    "brief": {"situation": "Five neighbours decide how to spend the town's small budget.",
              "rules": "Everyone speaks once, then the town votes on a park, a library or a road."},
    "clock": {"rounds": 1, "unit": "meeting"},
    "types": {"neighbour": {"agent": True, "props": {"hope": {"type": "enum", "values": ["park", "library", "road"],
                                                               "default": "$choice(['park', 'library', 'road'])",
                                                               "private": True}}}},
    "population": [{"type": "neighbour", "count": 5, "name": "Neighbour {$i}",
                    "brief": "You would most like a {$actor.hope}."}],
    "records": {"chat": {"fields": {"text": "text"}, "show": "{author}: {text}"}},
    "actions": {"speak": {"by": "neighbour", "description": "Say what you think the town should do.",
                          "params": {"text": {"type": "text", "max_len": 280}},
                          "do": {"post": "chat", "text": "$params.text"}}},
    "mechanisms": {"budget": {"kind": "decision", "mode": "ballot", "who": "neighbour",
                              "options": ["park", "library", "road"], "question": "What should the town build?"}},
    "stages": [{"name": "talk", "actions": ["speak"]}],
    "views": {"meeting": {"for": "neighbour", "title": "The meeting so far", "of": "chat", "show": "{author}: {text}",
                          "empty": "Nobody has spoken yet."}},
    "outputs": {"hopes": "$tally($map(neighbour, $it.hope))"},
}

#: Template name → (what it shows, the contract).
TEMPLATES: Dict[str, tuple] = {
    "blank": ("one agent type, one action, a view and an output: the smallest useful start", _BLANK),
    "game": ("a two-player turn-based game with a winner and per-seat returns", _GAME),
    "market": ("a seller and a crowd of buyers trading with money over days", _MARKET),
    "simulation": ("a population with no agents, world events, metrics and an invariant", _SIMULATION),
    "social": ("agents talking in a shared record and deciding with a vote mechanism", _SOCIAL),
}


def new(template: str = "blank", path: Union[str, "os.PathLike[str]", None] = None, *,
        name: Optional[str] = None, overwrite: bool = False) -> Dict[str, Any]:
    """A ready-to-run contract from a template (blank, game, market, simulation, social).

    With ``path`` it is also written there as JSON (an existing file is kept unless ``overwrite``); ``name``
    replaces the contract's name (default: the template's, or the file name when a path is given)."""
    if template not in TEMPLATES:
        from difflib import get_close_matches

        hint = get_close_matches(template, list(TEMPLATES), n=1)
        raise ContractError([Issue("(template)", f"'{template}' is not a template",
                                   (f"did you mean '{hint[0]}'? " if hint else "") + f"templates: {', '.join(TEMPLATES)}")])
    contract = copy.deepcopy(TEMPLATES[template][1])
    target = Path(path) if path is not None else None
    if name is not None:
        contract["name"] = name
    elif target is not None:
        contract["name"] = target.stem.replace("_", " ").replace("-", " ").strip().capitalize() or contract["name"]
    if target is not None:
        if target.exists() and not overwrite:
            raise FileExistsError(f"'{target}' already exists: choose another path, or overwrite it on purpose")
        target.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract
