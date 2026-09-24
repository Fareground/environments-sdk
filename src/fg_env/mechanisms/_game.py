"""What a game mode knows about the contract's `game` section: its seats, each seat's returns and the utility class.

A mode fills the section only when it is certainly right: the author has declared none of seats, returns or
utility, and this is the contract's only mechanism that scores seats (one board, or one pot). Otherwise the
section is left to the author, so returns are never stitched together from two sources.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..registry import uses_of

__all__ = ["game_section"]

#: Modes that know what each seat scores.
SCORING = ("game.board", "game.pot")
#: `game` fields that say who the seats are or what they score: an author who sets any of them owns the section.
SEATING = ("players", "seat", "returns", "rewards", "utility", "total")


def game_section(contract: Mapping[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """``{"game": spec}`` to merge into the contract, or ``{}`` when the section is not this mechanism's to fill."""
    declared = contract.get("game")
    if declared is not None and (not isinstance(declared, Mapping) or any(key in declared for key in SEATING)):
        return {}
    scorers = sum(len(uses_of(contract.get("mechanisms"), key)) for key in SCORING)
    return {"game": spec} if scorers == 1 else {}
