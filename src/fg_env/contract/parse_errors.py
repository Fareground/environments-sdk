"""Structural errors in plain words: what a field must be, what it got, and how to write it instead.

The contract models are validated by pydantic, whose messages ("Input should be a valid list") name neither the
value nor the fix. These turn each validation error into an :class:`Issue` a first-time author can act on.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from difflib import get_close_matches
from typing import Any

from pydantic import BaseModel, ValidationError

from .. import contract as C
from ..errors import Issue

__all__ = ["validation_issues", "shape_issue"]

#: What a pydantic type error expects, and the loc tag a union branch of that type adds.
_EXPECTED: dict[str, tuple[str, str]] = {
    "list_type": ("a list", "list"), "dict_type": ("an object", "dict"), "model_type": ("an object", ""),
    "model_attributes_type": ("an object", ""), "string_type": ("text", "str"), "float_type": ("a number", "float"),
    "float_parsing": ("a number", "float"), "int_type": ("a whole number", "int"),
    "int_parsing": ("a whole number", "int"),
    "int_from_float": ("a whole number", "int"), "bool_type": ("true or false", "bool"),
    "bool_parsing": ("true or false", "bool"),
}
#: Longest value quoted back.
_SHOWN = 60


def _all_field_names() -> list[str]:
    names = set()
    for obj in vars(C).values():
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            for name, info in obj.model_fields.items():
                names.add(info.alias or name)
    return sorted(names)


_FIELD_NAMES = _all_field_names()
#: Contract sections with a guide page of their own.
_SECTIONS = set(C.Contract.model_fields) - {"fg_env", "name", "description"}


def _path(loc: Sequence[Any]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + str(part)
    return out or "(contract)"


def _got(value: Any) -> str:
    if isinstance(value, Mapping):
        return "an object"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, str):
        text = value if len(value) <= _SHOWN else value[:_SHOWN - 3] + "..."
        return f"the text {json.dumps(text, ensure_ascii=False)}"
    return json.dumps(value)


def _loc(error: Mapping[str, Any]) -> list[Any]:
    """The error's location without pydantic's validator and union-branch tags."""
    loc = [p for p in error["loc"] if not (isinstance(p, str) and ("[" in p or p.startswith("function")))]
    tag = _EXPECTED.get(error["type"], ("", ""))[1]
    if loc and tag and loc[-1] == tag:
        loc.pop()
    return loc


def _probability(value: Any) -> str:
    """``value`` as written inside ``$chance(...)``: a number or an expression as given, else ``p``."""
    if isinstance(value, str):
        return value
    return json.dumps(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else "p"


def _fix(path: str, expected: list[str], value: Any) -> str | None:
    section = path.split(".")[0].split("[")[0]
    if expected == ["a list"] and isinstance(value, Mapping):
        if path == "stages":
            first = next(iter(value), "morning")
            return (f'write a list, each stage naming itself: [{{"name": "{first}", ...}}, ...] (stages run in list '
                    'order)')
        return "write a list: [ ... ] around the items"
    if expected == ["a list"]:
        return "wrap it in a list: [ ... ]"
    if expected == ["an object"]:
        return (f"write an object {{...}} with the fields guide('{section}') "
                "lists") if section in C.Contract.model_fields \
            else "write an object {...}"
    if "a number" in expected or "a whole number" in expected:
        return ("write a whole number without quotes" if "a whole number" in expected
                else "write a number without quotes")
    if expected == ["true or false"]:
        return "write true or false, without quotes"
    return None


def validation_issues(exc: ValidationError) -> list[Issue]:
    """Every structural problem as a plain-worded Issue; a union's alternatives at one path become one Issue."""
    issues: list[Issue] = []
    unions: dict[str, tuple[list[str], Any]] = {}
    for error in exc.errors():
        loc = _loc(error)
        path = _path(loc)
        kind = error["type"]
        if kind in _EXPECTED:
            expected, value = unions.setdefault(path, ([], error.get("input")))
            if _EXPECTED[kind][0] not in expected:
                expected.append(_EXPECTED[kind][0])
            continue
        if kind == "extra_forbidden":
            key = str(loc[-1]) if loc else ""
            hint = get_close_matches(key, _FIELD_NAMES, n=1, cutoff=0.7)
            if hint and hint[0] == key:
                fix: str | None = f"'{key}' belongs to another part of the contract; remove it here"
            else:
                fix = f"did you mean '{hint[0]}'?" if hint else "remove it"
            if len(loc) == 3 and loc[0] == "events" and key in {"round", "rounds"}:
                fix = 'Use at for scheduled rounds (at=2 or at=[2, 4]); use every for an interval (every=2)'
            elif len(loc) == 3 and loc[0] == "events" and key == "chance":
                fix = ('an event fires at random through its when: "when": '
                       f'"$chance({_probability(error.get("input"))})"')
            elif loc and loc[0] == "inputs" and key == "options":
                fix = 'For a dropdown use type="enum", values=[...], display="select"; options is not an input field'
            elif (len(loc) >= 5 and loc[0] == "actions" and loc[2] == "params"
                  and all(part == "items" for part in loc[4:-1])):
                if key == "multiple_of":
                    fix = ('Action parameters use step for enforced increments from min (or zero); '
                           'for whole cents use min=0 or min=0.01 and step=0.01. '
                           'multiple_of belongs to configuration inputs')
                elif key in {"label", "display"}:
                    fix = ('Describe an action parameter with description; label/display configure '
                           'input controls, not participant action tools')
            elif len(loc) == 2 and loc[0] == "brief" and not get_close_matches(key, C.Brief.model_fields, cutoff=0.7):
                fix = f'Put role-specific instructions in brief.roles.{key}; keep shared instructions in brief.rules'
            issues.append(Issue(path, f"'{key}' is not a field here", fix))
        elif kind == "missing":
            issues.append(Issue(path, "is required"))
        else:
            issues.append(Issue(path, error["msg"], (error.get("ctx") or {}).get("fix")))
    issues.extend(shape_issue(path, expected, value) for path, (expected, value) in unions.items())
    return [_with_guide_part(issue) for issue in issues]


def shape_issue(path: str, expected: list[str], value: Any) -> Issue:
    """``path`` holds ``value`` but must be one of ``expected`` (\"a list\", \"an object\" …), with how to write it."""
    return _with_guide_part(Issue(path, f"must be {' or '.join(expected)}, got {_got(value)}",
                                  _fix(path, expected, value)))


def _with_guide_part(issue: Issue) -> Issue:
    """``issue`` with its fix pointing to the guide part that lists its section's fields (``see guide('stages')``),
    unless the fix already names the replacement or a guide part."""
    section = issue.path.split(".")[0].split("[")[0]
    fix = issue.fix or ""
    if section not in _SECTIONS or "did you mean" in fix or "guide(" in fix:
        return issue
    pointer = f"see guide('{section}')"
    return replace(issue, fix=f"{fix}; {pointer}" if fix else pointer)
