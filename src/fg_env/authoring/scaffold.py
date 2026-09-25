"""Ready-to-run starting contracts: ``fg_env.new("auction")`` / ``fg-env new auction my_auction.json``.

``blank`` is the smallest useful start; every other template is a cookbook recipe (``guide('cookbook')``): a small,
complete contract for one common pattern, which checks clean, runs with random agents and gives a known answer with
its coded policy.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..contract.layout import dumps
from ..errors import ContractError, Issue

__all__ = ["TEMPLATES", "RECIPES", "Recipe", "recipe", "new"]

_BLANK: dict[str, Any] = {
    "name": "My environment",
    "brief": {"situation": "Describe the world in a sentence or two.",
              "rules": "Say what agents can do and what happens."},
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

@dataclass(frozen=True)
class Recipe:
    """A cookbook recipe: a complete contract in ``recipes/<name>.json`` for one common pattern, and the known answer
    its coded policy gives (the cookbook states it, and the tests hold it to it)."""

    about: str
    #: The coded participant every agent plays in the known answer (``None``: a simulation without agents).
    policy: str | None = None
    #: Outputs of ``fg_env.run(<recipe>, policy, seed=1)``.
    answer: dict[str, Any] | None = None
    #: How to work the answer out by hand.
    why: str = ""


#: The cookbook, in the order ``guide('cookbook')`` shows it.
RECIPES: dict[str, Recipe] = {
    "auction": Recipe("sealed bids resolved together when the bidding ends", "policy:shade",
                      {"revenue": 120, "lots_won": {"bidder_1": 3, "bidder_2": 0, "bidder_3": 0}},
                      "every bidder bids 80% of its worth (40, 32, 24), so bidder_1 wins all three lots at 40."),
    "vote": Recipe("talk in a shared record, then a secret ballot", "policy:sincere",
                   {"result": "park", "votes": {"park": 2, "library": 1}},
                   "Ana and Ben want a park and Cai a library; everyone votes for what they want."),
    "negotiation": Recipe("alternating offers with private limits and a deadline", "policy:concede",
                          {"deal": True, "price": 70},
                          "the seller asks 80, the buyer offers 30, the seller comes down to 70, and the buyer "
                          "accepts any price at least 10 below its worth of 80."),
    "hidden_roles": Recipe("private roles, a secret night stage and open accusations", "policy:first"),
    "market": Recipe("posted prices, and buyers paying with a transfer", "policy:steady",
                     {"profit": 30, "loaves_sold": 30},
                     "each household buys 2 loaves a day at 2 until its 20 runs out after 5 days: 30 loaves, each "
                     "earning 2 - 1."),
    "queue": Recipe("arrivals served first come, first served", "policy:two",
                    {"served": 22, "cost": 80},
                    "2 counters serve 6 a round: 4 + 6 + 6 + 6 of the 23 arrivals; 2 counters × 10 × 4 rounds."),
    "spread": Recipe("an infection passing along a contact network (no agents)"),
    "board_game": Recipe("a turn-based board game with a winner and zero-sum seats", "policy:first_free",
                         {"winner": "X"},
                         "both mark the lowest free cell, so X holds 0, 2, 4 and 6 and wins on the diagonal."),
    "economy": Recipe("gathering, eating and building with one action a day", "policy:balanced",
                      {"houses": 3, "settlers_left": 3},
                      "gathering food whenever it has less than 2, each settler has 6 wood after day 5 and builds a "
                      "house on day 6."),
    "grid": Recipe("moving on a grid and harvesting a regrowing layer", "policy:greedy"),
    "simulation": Recipe("a population changing over time, measured every round (no agents)"),
}


def recipe(name: str) -> dict[str, Any]:
    """The contract of the cookbook recipe ``name``, as its file holds it."""
    loaded: dict[str, Any] = json.loads(files(__package__).joinpath("recipes", f"{name}.json").read_text("utf-8"))
    return loaded


#: Template name → (what it shows, the contract): the blank start and every cookbook recipe.
TEMPLATES: dict[str, tuple[str, dict[str, Any]]] = {
    "blank": ("one agent type, one action, a view and an output: the smallest useful start", _BLANK),
    **{name: (spec.about, recipe(name)) for name, spec in RECIPES.items()},
}


def new(template: str = "blank", path: str | os.PathLike[str] | None = None, *,
        name: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    """A ready-to-run contract from a template: ``blank`` or a cookbook recipe (``auction``, ``vote``, ``market`` …;
    ``TEMPLATES`` lists them all).

    With ``path`` it is also written there as JSON (an existing file is kept unless ``overwrite``); ``name``
    replaces the contract's name (default: the template's, or the file name when a path is given)."""
    if template not in TEMPLATES:
        from difflib import get_close_matches

        hint = get_close_matches(template, list(TEMPLATES), n=1)
        raise ContractError([Issue("(template)", f"'{template}' is not a template",
                                   (f"did you mean '{hint[0]}'? " if hint else "")
                                   + f"templates: {', '.join(TEMPLATES)}")])
    contract = copy.deepcopy(TEMPLATES[template][1])
    target = Path(path) if path is not None else None
    if name is not None:
        contract["name"] = name
    elif target is not None:
        contract["name"] = target.stem.replace("_", " ").replace("-", " ").strip().capitalize() or contract["name"]
    if target is not None:
        if target.exists() and not overwrite:
            raise FileExistsError(f"'{target}' already exists: choose another path, or overwrite it on purpose")
        target.write_text(dumps(contract), encoding="utf-8")
    return contract
