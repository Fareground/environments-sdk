"""Arm inputs the caller replaced.

An arm may set inputs; inputs the caller passes (``load(..., inputs=)``, an experiment's or sweep's inputs) win
over them. That precedence stays, but a replaced arm input quietly changes what the arm tests, so it is surfaced:
as a run diagnostic and in experiment tables.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..contract import Contract

__all__ = ["arm_input_overrides", "override_message"]


def arm_input_overrides(contract: Contract, arm: str | None, inputs: Mapping[str, Any]) -> list[tuple[str, Any, Any]]:
    """``(input, the arm's value, the value used)`` for every input ``arm`` sets that ``inputs`` holds differently.
    Inputs read from a data file are left out: their loaded value is not comparable with what the arm wrote."""
    if arm is None or arm not in contract.arms:
        return []
    out = []
    for name, arm_value in contract.arms[arm].inputs.items():
        spec = contract.inputs.get(name)
        if name not in inputs or (spec is not None and spec.source):
            continue
        if inputs[name] != arm_value:
            out.append((name, arm_value, inputs[name]))
    return out


def override_message(arm: str, name: str, arm_value: Any, given: Any) -> str:
    return (f"the caller's input {name}={_shown(given)} replaced arm '{arm}''s {name}={_shown(arm_value)}, so these "
            "runs do not test what the arm sets")


def _shown(value: Any) -> str:
    return json.dumps(value, default=str)
