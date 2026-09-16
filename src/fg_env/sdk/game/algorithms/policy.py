"""Tabular policies: action probabilities per information state, saved as JSON and played as participants.

A policy file reads ``{"fg_env_policy": 1, "game": id, "policy": {information-state key: {call text: p}}}``.
Keys are :meth:`GameState.information_state` digests, so a policy belongs to one contract version: a changed
brief or view changes every key. Information states the policy does not list are played uniformly at random.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Mapping, Sequence, Union

__all__ = ["TabularPolicy"]


class TabularPolicy:
    """``{information-state key: {call text: probability}}``, with uniform play where a state is missing."""

    def __init__(self, table: Mapping[str, Mapping[str, float]], game: str = ""):
        self.table: Dict[str, Dict[str, float]] = {key: dict(value) for key, value in table.items()}
        self.game = game

    def probabilities(self, infoset: str, actions: Sequence[str]) -> List[float]:
        """The probability of each call (by text) in an information state; uniform when the policy has none."""
        if not actions:
            return []
        known = self.table.get(infoset)
        weights = [max(0.0, known.get(action, 0.0)) for action in actions] if known else []
        total = sum(weights)
        if total <= 0:
            return [1.0 / len(actions)] * len(actions)
        return [weight / total for weight in weights]

    def __len__(self) -> int:
        return len(self.table)

    def __contains__(self, infoset: object) -> bool:
        return infoset in self.table

    def to_dict(self) -> Dict[str, Any]:
        return {"fg_env_policy": 1, "game": self.game, "policy": self.table}

    def save(self, path: Union[str, "os.PathLike[str]"]) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=1, sort_keys=True)
            handle.write("\n")

    @classmethod
    def load(cls, path: Union[str, "os.PathLike[str]"]) -> "TabularPolicy":
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except OSError as exc:
            raise ValueError(f"cannot read policy file {os.fspath(path)}: {exc.strerror or exc}") from None
        except json.JSONDecodeError as exc:
            raise ValueError(f"policy file {os.fspath(path)} is not valid JSON: {exc}") from None
        return cls.from_dict(data, os.fspath(path))

    @classmethod
    def from_dict(cls, data: Any, where: str = "policy") -> "TabularPolicy":
        if not isinstance(data, Mapping) or data.get("fg_env_policy") != 1 or not isinstance(data.get("policy"), Mapping):
            raise ValueError(f"{where} is not an fg_env policy (expected {{\"fg_env_policy\": 1, \"policy\": {{...}}}})")
        table = data["policy"]
        for key, value in table.items():
            if not isinstance(value, Mapping) or not all(
                    isinstance(p, (int, float)) and not isinstance(p, bool) for p in value.values()):
                raise ValueError(f"{where}: information state {key!r} must map call texts to probabilities")
        return cls(table, str(data.get("game", "")))
