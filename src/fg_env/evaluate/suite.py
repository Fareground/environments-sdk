"""Evaluation suites: scenarios, each a contract with the seats a focal participant may take and who fills the rest.

A suite is one contract, a list of scenarios, or a JSON file ``{"scenarios": [...]}`` (contract paths relative
to the file). A scenario is a contract, or ``{"contract", "name"?, "inputs"?, "arm"?, "seats"?, "score"?,
"background"?, "baseline"?, "modes"?, "tags"?, "held_out"?}``; fields it leaves out come from the
:func:`~fg_env.evaluate.evaluate` call.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..api import default_data_dir, load, parse
from ..contract import Contract
from ..runtime import Env
from ..tournament.scoring import SeatScorer

__all__ = ["Scenario", "SCENARIO_FIELDS", "DEFAULT_MODES", "scenarios"]

SCENARIO_FIELDS = ("contract", "name", "inputs", "arm", "seats", "score", "background", "baseline", "modes", "tags",
                   "held_out")
#: Without ``modes``, the focal participant takes every seat.
DEFAULT_MODES: Mapping[str, float] = {"all": 1.0}


@dataclass(frozen=True)
class Scenario:
    """One scenario, checked: ``seats`` the focal participant may take, ``agents`` every starting agent's name."""

    name: str
    contract: Contract
    data_dir: Optional[Path]
    inputs: Mapping[str, Any]
    arm: Optional[str]
    seats: Tuple[str, ...]
    scorer: SeatScorer
    background: Any
    baseline: Any
    modes: Mapping[str, float]
    tags: Tuple[str, ...]
    held_out: bool

    def focal_seats(self, mode: str) -> int:
        """How many seats the focal participant takes in ``mode``: the share of the seats, rounded, at least one."""
        return min(len(self.seats), max(1, math.floor(self.modes[mode] * len(self.seats) + 0.5)))

    @property
    def stand_in(self) -> Any:
        """Who plays the focal seats in the baseline runs: the baseline, else the background."""
        return self.baseline if self.baseline is not None else self.background


def scenarios(suite: Any, defaults: Mapping[str, Any], focal: Any) -> List[Scenario]:
    """Every scenario of ``suite``, checked (contract, seats, modes, score and participants) before anything runs."""
    entries, folder = _entries(suite)
    if not entries:
        raise ValueError("the suite has no scenarios")
    built = [_scenario(entry, defaults, focal, folder, index) for index, entry in enumerate(entries)]
    names = [case.name for case in built]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(f"scenario names must be unique; repeated: {', '.join(repeated)} (give each scenario a name)")
    return built


def _entries(suite: Any) -> Tuple[List[Any], Optional[Path]]:
    if isinstance(suite, Contract):
        return [suite], None
    if isinstance(suite, (str, os.PathLike)) and not str(suite).lstrip().startswith("{"):
        path = Path(suite)
        data = _json_file(path) if path.suffix == ".json" and path.is_file() else None
        if isinstance(data, Mapping) and "scenarios" in data:
            return _listed(data, str(path)), path.parent
        return [suite], None
    if isinstance(suite, Mapping) and "scenarios" in suite:
        return _listed(suite, "the suite"), None
    if isinstance(suite, Sequence) and not isinstance(suite, str):
        return list(suite), None
    return [suite], None


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None  # not a readable suite: parsed as a contract, which reports the problem precisely


def _listed(data: Mapping[str, Any], where: str) -> List[Any]:
    extra = [key for key in data if key != "scenarios"]
    if extra:
        raise ValueError(f"{where} has unknown field(s) {', '.join(extra)}; a suite is {{\"scenarios\": [...]}}")
    listed = data["scenarios"]
    if not isinstance(listed, list):
        raise ValueError(f"{where}: scenarios must be a list of scenarios")
    return listed


def _scenario(entry: Any, defaults: Mapping[str, Any], focal: Any, folder: Optional[Path], index: int) -> Scenario:
    if not (isinstance(entry, Mapping) and "contract" in entry):
        entry = {"contract": entry}
    label = str(entry.get("name") or f"scenario {index + 1}")
    unknown = [key for key in entry if key not in SCENARIO_FIELDS]
    if unknown:
        raise ValueError(f"{label}: unknown field(s) {', '.join(map(str, unknown))} (fields: {', '.join(SCENARIO_FIELDS)})")

    def field(key: str) -> Any:
        return entry[key] if key in entry else defaults.get(key)

    source = entry["contract"]
    if folder is not None and isinstance(source, str) and not source.lstrip().startswith("{") \
            and not Path(source).is_absolute():
        source = str(folder / source)
    contract = parse(source)
    data_dir = default_data_dir(source)
    inputs, arm = dict(field("inputs") or {}), field("arm")
    probe = load(contract, inputs=inputs, seed=0, arm=arm, data_dir=data_dir)
    name = str(entry.get("name") or (contract.name + (f" ({arm})" if arm else "")))
    agents = {e.id: e.name for e in probe.world.entities.values() if probe.contract.is_agent(e.entity_type)}
    try:
        seats = _seats(field("seats"), probe, agents)
        scorer = SeatScorer(probe.contract, field("score"), seats, agents)
        for role, player in (("focal", focal), ("background", field("background")), ("baseline", field("baseline"))):
            if player is not None:
                _check_participant(probe, seats[0], role, player)
        return Scenario(name, contract, data_dir, inputs, arm, tuple(seats), scorer, field("background"),
                        field("baseline"), _modes(field("modes")), _tags(entry.get("tags")),
                        _held_out(entry.get("held_out", False)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"scenario '{name}': {exc}") from None


def _check_participant(probe: Env, seat: str, role: str, player: Any) -> None:
    try:
        probe.driver.bind({seat: player})
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{role}: {exc}") from None


def _seats(value: Any, probe: Env, agents: Mapping[str, str]) -> List[str]:
    shown = ", ".join(list(agents)[:20]) or "none"
    if value is None:
        if not agents:
            raise ValueError("the contract starts with no agents, so there is no seat to evaluate in")
        return list(agents)
    if isinstance(value, str):
        if value in agents:
            return [value]
        if value in probe.contract.types:
            typed = [seat for seat in agents if probe.contract.is_a(probe.world.entities[seat].entity_type, value)]
            if typed:
                return typed
            raise ValueError(f"seats: no starting agent is a {value} (agents: {shown})")
        raise ValueError(f"seats: '{value}' is neither a starting agent nor a type (agents: {shown})")
    if not isinstance(value, Sequence) or not value:
        raise ValueError(f"seats must be an agent id, a type, or a list of agent ids; got {value!r}")
    listed = list(value)
    for seat in listed:
        if seat not in agents:
            raise ValueError(f"seat {seat!r} is not an agent the contract starts with (agents: {shown})")
    if len(set(listed)) != len(listed):
        raise ValueError(f"seats are listed more than once: {listed}")
    return listed


def _modes(value: Any) -> Dict[str, float]:
    if value is None:
        return dict(DEFAULT_MODES)
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"modes must map a mode name to the share of seats the focal participant takes, like "
                         f"{{'resident': 0.75, 'visitor': 0.25}}; got {value!r}")
    modes: Dict[str, float] = {}
    for name, share in value.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"mode names are non-empty text, got {name!r}")
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
            raise ValueError(f"mode '{name}' must be a share of the seats above 0 and at most 1, got {share!r}")
        modes[name] = float(share)
    return modes


def _tags(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    listed = [value] if isinstance(value, str) else value
    if not isinstance(listed, (list, tuple)) or not all(isinstance(tag, str) and tag for tag in listed):
        raise ValueError(f"tags must be a list of names, got {value!r}")
    return tuple(dict.fromkeys(listed))


def _held_out(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"held_out must be true or false, got {value!r}")
    return value
