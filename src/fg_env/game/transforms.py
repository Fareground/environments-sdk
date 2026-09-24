"""Game transforms: a contract in, an ordinary contract out — checkable, previewable and playable by any
participant, LLMs included.

* :func:`repeated` — a one-round game played ``rounds`` times; every seat sees earlier rounds in its history,
  and its return is the total.
* :func:`misere` — every return negated: the usual winner loses.
* :func:`zerosum` — each return minus the mean of all seats' returns, which makes any game zero-sum.
* :func:`zero_sum_check` — random playouts that show which utility class a game's returns actually have.

:meth:`Game.start_at` starts a game from a position instead (it is a game, not a contract).
"""
from __future__ import annotations

import copy
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..api import ContractLike, expand
from .game import game
from .steps import apply_step, random_step

__all__ = ["repeated", "misere", "zerosum", "zero_sum_check", "UtilityCheck"]

_ACTOR = re.compile(r"\$actor\b")


def _contract(source: ContractLike) -> dict[str, Any]:
    data = copy.deepcopy(expand(source, mechanisms=True))
    spec = data.get("game")
    if not isinstance(spec, dict) or not spec.get("returns"):
        raise ValueError("the contract declares no game.returns, so there is nothing to transform: declare what "
                         "each seat scores")
    return data


def _seat_types(data: Mapping[str, Any]) -> list[str]:
    listed = data["game"].get("players")
    if listed:
        return [listed] if isinstance(listed, str) else list(listed)
    return [name for name, spec in (data.get("types") or {}).items() if isinstance(spec, dict) and spec.get("agent")]


def _add_rule(data: dict[str, Any], text: str) -> None:
    brief = data.setdefault("brief", {})
    brief["rules"] = f"{brief['rules']} {text}" if brief.get("rules") else text


def _ends_early(node: Any) -> bool:
    if isinstance(node, dict):
        return ("end" in node and isinstance(node["end"], str)) or any(_ends_early(value) for value in node.values())
    if isinstance(node, list):
        return any(_ends_early(item) for item in node)
    return False


def repeated(source: ContractLike, rounds: int, *, total_prop: str = "repeated_total") -> dict[str, Any]:
    """The one-round game played ``rounds`` times in a row. After every round each seat's return for that round is
    added to its ``total_prop``, which is the new return; what happened in earlier rounds stays in every seat's
    history. Events with ``at`` still fire only in the rounds they name."""
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ValueError(f"rounds must be a whole number ≥ 1, got {rounds!r}")
    data = _contract(source)
    length = (data.get("clock") or {}).get("rounds", 20)
    if length != 1:
        raise ValueError(f"repeated() repeats one-round games, and this contract lasts {length} rounds")
    if (data.get("end") or _ends_early(data.get("actions")) or _ends_early(data.get("events"))
        or _ends_early(data.get("blocks"))):
        raise ValueError("the contract can end a run early (`end`), which would stop the repetition; repeat games "
                         "whose rounds always run to the end")
    spec = data["game"]
    per_round = spec["returns"]
    types = _seat_types(data)
    for name in types:
        props = data["types"][name].setdefault("props", {})
        if total_prop in props:
            raise ValueError(f"type '{name}' already has a property '{total_prop}'; pass another total_prop")
        props[total_prop] = {"type": "number", "default": 0}
        data.setdefault("events", []).append({"name": f"{total_prop}_{name}", "phase": "end", "each": name,
                                              "do": [f"$it.{total_prop} += {_ACTOR.sub('$it', per_round)}"]})
    spec["returns"] = f"$actor.{total_prop}"
    spec.pop("rewards", None)
    for key in ("min_return", "max_return", "total"):
        if spec.get(key) is not None:
            spec[key] = spec[key] * rounds
    if "max_rounds" in spec:
        spec["max_rounds"] = rounds
    data.setdefault("clock", {})["rounds"] = rounds
    data["name"] = f"{data['name']} (repeated {rounds} times)"
    _add_rule(data, f"The game is played {rounds} times in a row: your score is your total over all of them, and you "
                    "see what happened in earlier rounds.")
    return data


def misere(source: ContractLike) -> dict[str, Any]:
    """The same game with every return (and declared reward) negated."""
    data = _contract(source)
    spec = data["game"]
    spec["returns"] = f"-({spec['returns']})"
    if spec.get("rewards"):
        spec["rewards"] = f"-({spec['rewards']})"
    low, high = spec.get("min_return"), spec.get("max_return")
    spec["min_return"] = -high if high is not None else None
    spec["max_return"] = -low if low is not None else None
    for key in ("min_return", "max_return"):
        if spec[key] is None:
            del spec[key]
    if spec.get("total") is not None:
        spec["total"] = -spec["total"]
    data["name"] = f"Misère {data['name']}"
    _add_rule(data, "Misère: every score is negated, so the usual winner loses.")
    return data


def zerosum(source: ContractLike) -> dict[str, Any]:
    """The same game with each seat's return minus the mean return of all seats (seats of one type)."""
    data = _contract(source)
    types = _seat_types(data)
    if len(types) != 1:
        raise ValueError(f"zerosum() needs seats of one type, and this game's seats have types {types}")
    spec = data["game"]
    returns = spec["returns"]
    spec["returns"] = f"({returns}) - $avg({types[0]}, {_ACTOR.sub('$it', returns)})"
    spec.pop("rewards", None)
    spec["utility"] = "zero_sum"
    low, high = spec.get("min_return"), spec.get("max_return")
    spec.pop("total", None)
    if low is not None and high is not None:
        spec["min_return"], spec["max_return"] = low - high, high - low
    else:
        spec.pop("min_return", None)
        spec.pop("max_return", None)
    data["name"] = f"{data['name']} (zero-sum)"
    _add_rule(data, "Your score is your result minus the average result of all players.")
    return data


@dataclass(frozen=True)
class UtilityCheck:
    """What random playouts showed about a game's returns. ``totals`` are the smallest and largest sum of all
    seats' returns seen; ``spread`` the largest difference between two seats' returns in one playout."""

    playouts: int
    lowest_total: float
    highest_total: float
    spread: float

    @property
    def zero_sum(self) -> bool:
        return abs(self.lowest_total) <= _TOLERANCE and abs(self.highest_total) <= _TOLERANCE

    @property
    def constant_sum(self) -> bool:
        return self.highest_total - self.lowest_total <= _TOLERANCE

    @property
    def identical(self) -> bool:
        return self.spread <= _TOLERANCE

    @property
    def utility(self) -> str:
        """The strongest class consistent with every playout (evidence, not proof)."""
        if self.zero_sum:
            return "zero_sum"
        if self.identical:
            return "identical"
        return "constant_sum" if self.constant_sum else "general_sum"


_TOLERANCE = 1e-9


def zero_sum_check(source: ContractLike, *, playouts: int = 100, seed: int = 0,
                   inputs: Mapping[str, Any] | None = None) -> UtilityCheck:
    """Play ``playouts`` random games and report how their returns add up (see :class:`UtilityCheck`)."""
    if isinstance(playouts, bool) or not isinstance(playouts, int) or playouts < 1:
        raise ValueError(f"playouts must be a whole number ≥ 1, got {playouts!r}")
    subject = game(source, inputs=inputs, seed=seed)
    rng = random.Random(f"fg-env-zero-sum:{seed}")
    lowest, highest, spread = float("inf"), float("-inf"), 0.0
    for _ in range(playouts):
        state = subject.new_initial_state()
        try:
            while not state.is_terminal():
                apply_step(state, random_step(state, rng))
            returns = state.returns()
        finally:
            state.close()
        total = sum(returns)
        lowest, highest = min(lowest, total), max(highest, total)
        spread = max(spread, max(returns) - min(returns))
    return UtilityCheck(playouts, lowest, highest, spread)
