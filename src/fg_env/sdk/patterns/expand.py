"""Reading a contract's ``patterns``: each validated by its kind, plus what the section adds to the contract —
a metric for every pattern with ``record: true``, and the world property memory patterns keep their state in."""
from __future__ import annotations

import copy
import re
from difflib import get_close_matches
from typing import Any, Dict, List, Mapping, Tuple

from pydantic import BaseModel, ValidationError

from ..errors import Issue
from . import catalogue  # noqa: F401 — registers every kind
from .base import KINDS, MEMORY_STATE, PatternConfig

__all__ = ["expand_patterns", "validated"]

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
#: What the dynamics family's modes became.
_FOLDED = {"drift": "trend, seasonal, random_walk or mean_reversion", "shocks": "shocks", "priors": "draw"}


def validated(name: str, spec: Any) -> Tuple[PatternConfig | None, List[Issue]]:
    """``spec`` as its kind's config, or the issues saying what to fix."""
    path = f"patterns.{name}"
    if not isinstance(name, str) or not _NAME.match(name):
        return None, [Issue(path, "a pattern name starts with a letter and uses letters, digits and _", "rename it, e.g. 'winter'")]
    if not isinstance(spec, Mapping) or "kind" not in spec:
        return None, [Issue(path, "needs a `kind`", f"kinds: {', '.join(sorted(KINDS))} (guide('patterns'))")]
    kind = spec["kind"]
    if kind not in KINDS:
        if kind in _FOLDED:
            return None, [Issue(f"{path}.kind", f"'{kind}' is not a pattern kind", f"use kind {_FOLDED[kind]}")]
        hint = get_close_matches(str(kind), list(KINDS), n=1)
        return None, [Issue(f"{path}.kind", f"'{kind}' is not a pattern kind",
                            f"did you mean '{hint[0]}'?" if hint else f"kinds: {', '.join(sorted(KINDS))}")]
    model = KINDS[kind].model
    try:
        return model.model_validate(spec), []
    except ValidationError as exc:
        return None, [_issue(path, kind, model, error) for error in exc.errors()]


def _issue(path: str, kind: str, model: Any, error: Mapping[str, Any]) -> Issue:
    loc = [part for part in error["loc"] if not (isinstance(part, str) and ("[" in part or part.startswith("function")))]
    at = ".".join([path, *(f"[{p}]" if isinstance(p, int) else str(p) for p in loc)]).replace(".[", "[")
    if error["type"] == "extra_forbidden":
        field = str(loc[-1])
        fields = _fields(model, loc[:-1])
        hint = get_close_matches(field, fields, n=1)
        return Issue(at, f"`{field}` is not a field of a `{kind}` pattern" if len(loc) == 1 else f"`{field}` is not a field here",
                     (f"did you mean '{hint[0]}'? " if hint else "") + f"it takes: {', '.join(fields)}")
    if error["type"] == "missing":
        info = model.model_fields.get(str(loc[0])) if len(loc) == 1 else None
        about = f"`{loc[0]}`: {info.description}" if info is not None and info.description else None
        return Issue(at, "is required", about or f"see guide('patterns') for `{kind}`")
    message = str(error["msg"]).removeprefix("Value error, ")
    return Issue(at if loc else path, message, f"see guide('patterns') for `{kind}`")


def _fields(model: Any, loc: List[Any]) -> List[str]:
    current = model
    for part in loc:
        if isinstance(part, int) or not (isinstance(current, type) and issubclass(current, BaseModel)):
            continue
        info = current.model_fields.get(str(part))
        current = _model_in(info.annotation) if info is not None else None
    if isinstance(current, type) and issubclass(current, BaseModel):
        return [name for name in current.model_fields if name != "kind"]
    return []


def _model_in(annotation: Any) -> Any:
    import typing

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        found = _model_in(arg)
        if found is not None:
            return found
    return None


def expand_patterns(data: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[Issue]]:
    """The contract with what its patterns add, plus problems with their configs."""
    raw = data.get("patterns")
    if not raw:
        return dict(data), []
    if not isinstance(raw, Mapping):
        return dict(data), [Issue("patterns", "must be an object of {name: {kind, ...}}", "guide('patterns')")]
    out: Dict[str, Any] = dict(data)
    issues: List[Issue] = []
    metrics: Dict[str, Any] = {}
    memory = False
    for name, spec in raw.items():
        cfg, problems = validated(name, spec)
        issues += problems
        if cfg is None:
            continue
        kind = KINDS[cfg.kind]
        memory = memory or kind.shape == "memory"
        if cfg.record:
            if name in (data.get("metrics") or {}):
                issues.append(Issue(f"patterns.{name}.record", f"a metric is already named '{name}'",
                                    "rename the metric or the pattern (a recorded pattern is a metric of its own name)"))
                continue
            if kind.arg_names(cfg):
                issues.append(Issue(f"patterns.{name}.record", f"'{name}' is called with {', '.join(kind.arg_names(cfg))}, "
                                                               "so it has no value of its own to record",
                                    "record a metric that calls it instead"))
                continue
            expr = f"$pattern_values('{name}')" if cfg.keyed else f"$pattern.{name}"
            metrics[name] = {"expr": expr, "description": cfg.description or f"The {cfg.kind} pattern '{name}'.",
                             "unit": cfg.unit}
    if metrics:
        out["metrics"] = {**copy.deepcopy(dict(data.get("metrics") or {})), **metrics}
    if memory:
        world = dict(data.get("world") or {}) if isinstance(data.get("world") or {}, Mapping) else data.get("world")
        if isinstance(world, dict):
            world.setdefault(MEMORY_STATE, {"type": "map", "default": {},
                                            "description": "State memory patterns carry between rounds (managed by the engine)."})
            out["world"] = world
    return out, issues
