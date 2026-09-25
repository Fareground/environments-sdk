"""The JSON shape of the parts of a contract that reading it walks, checked before anything reads them.

Reading a contract rewrites earlier forms and expands mechanisms before the models validate it, and both walk the
sections: which one is an object, which a list, which entries are objects and which fields are names. A contract
whose shape is wrong there is told so here — each malformed field as a :class:`~fg_env.errors.Issue` at its path —
instead of failing inside the reader with a Python error. Everything else about the shape is the models' to check.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import Issue
from .parse_errors import shape_issue

__all__ = ["malformed"]

#: Sections that are objects, and sections that are lists (in every form of the language).
OBJECTS = ("inputs", "world", "relations", "records", "actions", "views", "policies", "metrics", "outputs", "defs",
           "blocks", "arms", "types", "entities", "mechanisms", "brief", "clock", "game")
LISTS = ("population", "links", "events", "end", "invariants", "stages")
#: Sections whose every entry is an object (no shorthand).
ENTRIES = ("types", "actions", "records", "views", "mechanisms", "relations", "arms")
#: Fields of a mechanism use that name something.
MECHANISM_NAMES = ("kind", "mode")


def malformed(data: Mapping[str, Any]) -> list[Issue]:
    """Every field reading ``data`` walks that is not the shape the language gives it."""
    issues = [shape_issue(section, ["a list" if section in LISTS else "an object"], value)
              for section in (*OBJECTS, *LISTS) if (value := data.get(section)) is not None
              and not isinstance(value, list if section in LISTS else Mapping)]
    if issues:
        return issues
    for section in ENTRIES:
        for name, entry in (data.get(section) or {}).items():
            if not isinstance(entry, Mapping):
                issues.append(shape_issue(f"{section}.{name}", ["an object"], entry))
    if issues:
        return issues
    for kind, spec in (data.get("types") or {}).items():
        if spec.get("props") is not None and not isinstance(spec["props"], Mapping):
            issues.append(shape_issue(f"types.{kind}.props", ["an object"], spec["props"]))
    for name, spec in (data.get("actions") or {}).items():
        if spec.get("params") is not None and not isinstance(spec["params"], Mapping):
            issues.append(shape_issue(f"actions.{name}.params", ["an object"], spec["params"]))
    for name, use in (data.get("mechanisms") or {}).items():
        issues += [shape_issue(f"mechanisms.{name}.{key}", ["a name (text)"], use[key])
                   for key in MECHANISM_NAMES if use.get(key) is not None and not isinstance(use[key], str)]
    for index, stage in enumerate(data.get("stages") or []):
        issues += _stage(stage, f"stages[{index}]")
    for name, arm in (data.get("arms") or {}).items():
        if arm.get("patch") is not None and not isinstance(arm["patch"], Mapping):
            issues.append(shape_issue(f"arms.{name}.patch", ["an object"], arm["patch"]))
        elif isinstance(arm.get("patch"), Mapping):
            issues += [Issue(f"arms.{name}.patch.{issue.path}", issue.message, issue.fix)
                       for issue in malformed(arm["patch"])]
    return issues


def _stage(stage: Any, path: str) -> list[Issue]:
    if not isinstance(stage, Mapping):
        return [shape_issue(path, ["an object"], stage)]
    issues = []
    if "name" in stage and not isinstance(stage["name"], str):  # a missing one is the model's to report
        issues.append(shape_issue(f"{path}.name", ["a name (text)"], stage["name"]))
    actions = stage.get("actions")
    names = actions.values() if isinstance(actions, Mapping) else [actions] if isinstance(actions, list) else None
    if "actions" in stage and not isinstance(actions, str) and (names is None or not all(
            isinstance(listed, list) and all(isinstance(item, str) for item in listed) for listed in names)):
        issues.append(shape_issue(f"{path}.actions", ['"all"', "a list of action names",
                                                      "an object of {type: [action names]}"], actions))
    return issues
