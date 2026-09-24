"""Claims a contract makes about itself, checked against what fg_env derives from it.

A claim the derivation contradicts is an error; a claim the derivation cannot settle is a warning, so an
author learns which properties are asserted rather than verified.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..errors import Issue

__all__ = ["CLAIMS", "claim_issues"]


def _length(metadata: Mapping[str, Any]) -> Any:
    return metadata["max_game_length"]["rounds"]


def _space(metadata: Mapping[str, Any]) -> Any:
    return metadata["action_space"]["kind"]


def _space_size(metadata: Mapping[str, Any]) -> Any:
    return metadata["action_space"]["size"]


#: Claimable property → (how to read the derived value, the evidence key that explains it).
CLAIMS: dict[str, tuple] = {
    "dynamics": (lambda m: m["dynamics"], "dynamics"),
    "chance_mode": (lambda m: m["chance_mode"], "chance_mode"),
    "information": (lambda m: m["information"], "information"),
    "utility": (lambda m: m["utility"], "utility"),
    "num_players": (lambda m: m["num_players"], "players"),
    "min_players": (lambda m: m["min_players"], "players"),
    "max_players": (lambda m: m["max_players"], "players"),
    "max_rounds": (_length, "max_game_length"),
    "action_space": (_space, "action_space"),
    "num_distinct_actions": (_space_size, "action_space"),
}


def claim_issues(metadata: Mapping[str, Any], claims: Mapping[str, Any], path: str = "game") -> list[Issue]:
    """Issues for ``claims`` (property → claimed value) that ``metadata`` contradicts or cannot verify."""
    issues: list[Issue] = []
    for key, claimed in claims.items():
        where = f"{path}.{key}"
        if key not in CLAIMS:
            issues.append(Issue(where, "is not a property fg_env derives, so it cannot be verified",
                                "derivable: " + ", ".join(CLAIMS), "warning"))
            continue
        read: Callable[[Mapping[str, Any]], Any] = CLAIMS[key][0]
        derived = read(metadata)
        reasons = metadata["evidence"].get(CLAIMS[key][1], [])
        because = f" ({'; '.join(reasons[:2])})" if reasons else ""
        if derived is None or derived == "unknown":
            issues.append(Issue(where, f"is declared {claimed!r} but cannot be verified from the contract{because}",
                                "keep the claim only if you are sure of it", "warning"))
        elif derived != claimed:
            issues.append(Issue(where, f"is declared {claimed!r} but the contract makes it {derived!r}{because}",
                                "correct the declaration, or change the contract parts named"))
    return issues
