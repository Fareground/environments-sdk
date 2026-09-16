"""Small contracts for pattern tests: declare patterns and measures, run idle agents, read the series."""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

import fg_env


def world(patterns: Dict[str, Any], *, rounds: int = 6, clock: Optional[Dict[str, Any]] = None,
          metrics: Optional[Dict[str, Any]] = None, **sections: Any) -> Dict[str, Any]:
    """A contract with one idle agent, the given patterns, and a metric for every recorded measure."""
    contract: Dict[str, Any] = {
        "name": "Patterns under test",
        "clock": {"rounds": rounds, **(clock or {})},
        "types": {"clerk": {"agent": True}, **sections.pop("types", {})},
        "entities": {"clerk": {"type": "clerk"}, **sections.pop("entities", {})},
        "actions": {"wait": {"by": "clerk", "do": []}},
        "patterns": copy.deepcopy(patterns),
        "metrics": dict(metrics or {}),
    }
    contract.update(sections)
    return contract


def series(patterns: Dict[str, Any], measures: Dict[str, str], *, seed: int = 1, values: Optional[Dict[str, Any]] = None,
           **options: Any) -> Dict[str, list]:
    """Every measure's value each round of an idle run (``values`` are the run's input values)."""
    contract = world(patterns, metrics=measures, **options)
    result = fg_env.run(contract, "idle", seed=seed, inputs=values or {})
    assert result.status == "completed", result.error
    return {name: result.series[name] for name in measures}


def errors(contract: Dict[str, Any]) -> list:
    return [issue for issue in fg_env.check(contract, rounds=0) if issue.severity == "error"]
