"""Small deterministic samples of declared input boundaries, not exhaustive fuzzing."""
from __future__ import annotations

import copy
import itertools
import math
from typing import Any, Iterator, Tuple

from ..inputs import check_value, resolve_inputs


def _values(spec: Any, value: Any, path: tuple) -> Iterator[Tuple[tuple, Any]]:
    choices = []
    if spec.type in ("int", "number"):
        low, high = spec.min, spec.max
        if spec.type == "int":
            low = math.ceil(low) if low is not None else None
            high = math.floor(high) if high is not None else None
        choices = [0, low, high]
    elif spec.type in ("list", "table") and isinstance(value, list):
        choices = [[], value[:1]]
        if spec.type == "table" and value:
            choices += [list(reversed(value)), [*value, value[0]]]
    elif spec.type == "enum":
        choices = spec.values or []
    elif spec.type == "bool" and isinstance(value, bool):
        choices = [not value]
    seen = []
    for candidate in choices:
        if candidate is None or candidate == value or candidate in seen:
            continue
        if check_value(spec.type, candidate, spec) is not None:
            continue
        seen.append(candidate)
        yield path, copy.deepcopy(candidate)
    if spec.type == "map" and spec.fields and isinstance(value, dict):
        for key, field in spec.fields.items():
            if key in value:
                yield from _values(field, value[key], (*path, key))
    elif spec.type == "table" and spec.fields and value:
        for key, field in spec.fields.items():
            if key in value[0]:
                yield from _values(field, value[0][key], (*path, 0, key))
    elif spec.type == "list" and spec.items and value:
        yield from _values(spec.items, value[0], (*path, 0))


def boundary_plan(contract: Any, inputs: Any, names: Any, limit: int) -> tuple:
    base = resolve_inputs(contract, inputs, contract._folder)
    names = list(contract.inputs) if names is None else names
    stream = (case for name in names for case in _values(contract.inputs[name], base[name], (name,)))
    cases = list(itertools.islice(stream, limit + 1))
    return base, cases[:limit], len(cases) > limit


def override(base: dict, path: tuple, value: Any) -> dict:
    inputs = copy.deepcopy(base)
    node = inputs
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = copy.deepcopy(value)
    return inputs


def subject(path: tuple) -> str:
    return "inputs" + "".join(f"[{key}]" if isinstance(key, int) else f".{key}" for key in path)
