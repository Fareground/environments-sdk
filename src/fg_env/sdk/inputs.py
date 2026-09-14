"""Validate caller-supplied inputs against the contract's declared inputs."""
from __future__ import annotations

import copy
import datetime as _dt
import math
from difflib import get_close_matches
from typing import Any, Dict, List, Mapping, Optional

from .contract import Contract, InputSpec
from .errors import InputError, Issue

__all__ = ["resolve_inputs", "check_value"]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_value(type_name: str, value: Any, spec: Optional[InputSpec] = None) -> Optional[str]:
    """Return a problem description, or None when ``value`` fits ``type_name``."""
    if type_name == "any":
        return None
    if type_name == "number":
        if not _is_number(value):
            return f"must be a number, got {value!r}"
    elif type_name == "int":
        if not _is_number(value) or float(value) != int(value):
            return f"must be a whole number, got {value!r}"
    elif type_name == "bool":
        if not isinstance(value, bool):
            return f"must be true or false, got {value!r}"
    elif type_name == "text":
        if not isinstance(value, str):
            return f"must be text, got {value!r}"
    elif type_name == "enum":
        allowed = (spec.values if spec else None) or []
        if value not in allowed:
            return f"must be one of {allowed}, got {value!r}"
    elif type_name == "list":
        if not isinstance(value, list):
            return f"must be a list, got {type(value).__name__}"
    elif type_name == "map":
        if not isinstance(value, dict):
            return f"must be an object, got {type(value).__name__}"
    elif type_name == "date":
        if not isinstance(value, str):
            return f"must be an ISO date text like 2026-01-31, got {value!r}"
        try:
            _dt.date.fromisoformat(value[:10])
        except ValueError:
            return f"must be an ISO date like 2026-01-31, got {value!r}"
    elif type_name == "table":
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            return "must be a list of rows (objects)"
        columns = (spec.columns if spec else None) or {}
        for index, row in enumerate(value):
            for column, column_type in columns.items():
                if column not in row:
                    return f"row {index} is missing column '{column}'"
                problem = check_value(column_type, row[column])
                if problem:
                    return f"row {index} column '{column}' {problem}"
    else:
        return f"has unknown type '{type_name}'"
    if spec is not None and _is_number(value):
        if spec.min is not None and value < spec.min:
            return f"must be ≥ {spec.min:g}, got {value}"
        if spec.max is not None and value > spec.max:
            return f"must be ≤ {spec.max:g}, got {value}"
    return None


def resolve_inputs(contract: Contract, supplied: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Merge supplied inputs over defaults. Raises :class:`InputError` listing every problem."""
    supplied = dict(supplied or {})
    issues: List[Issue] = []
    declared = contract.inputs
    for name in supplied:
        if name not in declared:
            hint = get_close_matches(name, list(declared), n=1)
            issues.append(Issue(
                f"inputs.{name}", "is not a declared input",
                f"did you mean '{hint[0]}'?" if hint else f"declared inputs: {', '.join(declared) or 'none'}",
            ))
    resolved: Dict[str, Any] = {}
    for name, spec in declared.items():
        if name in supplied:
            value = supplied[name]
        elif spec.default is not None:
            value = copy.deepcopy(spec.default)
        elif spec.required:
            issues.append(Issue(f"inputs.{name}", "is required", f"supply inputs={{'{name}': ...}}"))
            continue
        else:
            value = None
        if value is not None:
            problem = check_value(spec.type, value, spec)
            if problem:
                issues.append(Issue(f"inputs.{name}", problem, spec.description or None))
                continue
            if spec.type == "int":
                value = int(value)
        resolved[name] = value
    if issues:
        raise InputError(issues)
    return resolved
