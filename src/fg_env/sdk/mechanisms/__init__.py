"""Mechanisms: native building blocks used by name in a contract.

.. code-block:: json

    "mechanisms": {"election": {"kind": "ballot", "voters": "citizen", "options": ["yes", "no"],
                                "method": "supermajority", "quorum": 0.5}}

A mechanism expands into ordinary contract sections — actions, stages, world props, events,
views, defs — backed by native functions and effect ops. Everything the engine does (checking,
preview, atomic actions, snapshots, determinism) therefore applies to it unchanged. Anything
the author declares under a generated name wins, so generated parts can be overridden; types
the author declares gain the mechanism's properties without losing their own.
"""
from __future__ import annotations

import copy
import json
import re
from difflib import get_close_matches
from typing import Any, Dict, List, Mapping, Tuple

from pydantic import ValidationError

from ..errors import Issue
from ..registry import MECHANISMS, MechanismError

__all__ = ["expand_mechanisms", "MECHANISMS"]

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")

#: Sections merged by key: the author's entry wins over a generated one of the same name.
_KEYED = ("inputs", "world", "entities", "relations", "records", "actions", "views", "policies", "metrics",
          "outputs", "defs", "blocks", "arms")
#: Sections merged by appending generated items (an identical item is never added twice).
_LISTED = ("population", "links", "events", "end", "invariants")


def expand_mechanisms(data: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[Issue]]:
    """The contract with every declared mechanism expanded, plus any problems with their configs."""
    uses = data.get("mechanisms")
    if not uses:
        return dict(data), []
    if not isinstance(uses, Mapping):
        return dict(data), [Issue("mechanisms", "must be an object of {name: {kind, ...config}}")]
    out: Dict[str, Any] = copy.deepcopy(dict(data))
    issues: List[Issue] = []
    for name, use in uses.items():
        path = f"mechanisms.{name}"
        if not isinstance(name, str) or not _NAME.match(name):
            issues.append(Issue(path, "a mechanism name starts with a letter and uses letters, digits and _",
                                "rename it, e.g. 'election'"))
            continue
        if not isinstance(use, Mapping) or "kind" not in use:
            issues.append(Issue(path, "needs a `kind`", f"kinds: {', '.join(sorted(MECHANISMS))}"))
            continue
        kind = use["kind"]
        spec = MECHANISMS.get(kind) if isinstance(kind, str) else None
        if spec is None:
            hint = get_close_matches(str(kind), list(MECHANISMS), n=1)
            issues.append(Issue(f"{path}.kind", f"'{kind}' is not a mechanism kind",
                                f"did you mean '{hint[0]}'?" if hint else f"kinds: {', '.join(sorted(MECHANISMS))}"))
            continue
        try:
            config = spec.config.model_validate({k: v for k, v in use.items() if k != "kind"})
        except ValidationError as exc:
            for error in exc.errors():
                where = ".".join(str(p) for p in error["loc"])
                message = "is not a field here" if error["type"] == "extra_forbidden" else (
                    "is required" if error["type"] == "missing" else error["msg"])
                fields = ", ".join(spec.config.model_fields)
                issues.append(Issue(f"{path}.{where}" if where else path, message, f"`{kind}` takes: {fields}"))
            continue
        try:
            fragment = spec.expand(name, config, out)
        except MechanismError as exc:
            issues.append(Issue(f"{path}.{exc.path}" if exc.path else path, str(exc), exc.fix))
            continue
        _merge(out, fragment)
    return out, issues


def _merge(data: Dict[str, Any], fragment: Mapping[str, Any]) -> None:
    for section, value in fragment.items():
        if section == "types":
            types = data.setdefault("types", {})
            for type_name, spec in value.items():
                if type_name not in types:
                    types[type_name] = copy.deepcopy(spec)
                    continue
                props = types[type_name].setdefault("props", {})
                for prop, prop_spec in (spec.get("props") or {}).items():
                    props.setdefault(prop, copy.deepcopy(prop_spec))
        elif section in _KEYED:
            target = data.setdefault(section, {})
            for key, item in value.items():
                target.setdefault(key, copy.deepcopy(item))
        elif section in _LISTED:
            target_list = data.setdefault(section, [])
            seen = {_canonical(item) for item in target_list}
            for item in value:
                if _canonical(item) not in seen:
                    target_list.append(copy.deepcopy(item))
        elif section == "stages":
            stages = data.setdefault("stages", [])
            names = {s.get("name") for s in stages if isinstance(s, Mapping)}
            for stage in value:
                if stage.get("name") not in names:
                    stages.append(copy.deepcopy(stage))
        elif section == "brief":
            brief = data.setdefault("brief", {})
            for key, text in value.items():
                if isinstance(text, str):  # rules / situation: the author's text first, the mechanism's after, once
                    current = brief.get(key) or ""
                    if text and text not in current:
                        brief[key] = f"{current}\n\n{text}" if current else text
                elif isinstance(text, Mapping):  # roles: the author's role text wins
                    roles = brief.setdefault(key, {})
                    for role, role_text in text.items():
                        roles.setdefault(role, role_text)
        elif section == "clock":
            clock = data.setdefault("clock", {})
            for key, item in value.items():
                clock.setdefault(key, copy.deepcopy(item))
        elif section == "stage_hooks":
            _hook_stages(data, value)
        else:
            raise MechanismError(f"a mechanism produced an unknown section '{section}'")


def _hook_stages(data: Dict[str, Any], hooks: Mapping[str, Mapping[str, Any]]) -> None:
    """Add actions and effects to stages the author declared (identical effects are added once)."""
    stages: Dict[Any, Dict[str, Any]] = {s.get("name"): s for s in data.get("stages", []) if isinstance(s, dict)}
    for stage_name, hook in hooks.items():
        stage = stages.get(stage_name)
        if stage is None:
            raise MechanismError(f"there is no stage '{stage_name}' to attach to",
                                 f"stages: {', '.join(map(str, stages)) or 'none declared'}", "stage")
        actions = hook.get("actions") or []
        current = stage.get("actions", "all")
        if isinstance(current, list):
            stage["actions"] = current + [a for a in actions if a not in current]
        elif isinstance(current, dict):
            for names in current.values():
                names.extend(a for a in actions if a not in names)
        for key in ("on_enter", "on_exit"):
            effects = stage.setdefault(key, [])
            seen = {_canonical(e) for e in effects}
            effects.extend(copy.deepcopy(e) for e in hook.get(key) or [] if _canonical(e) not in seen)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


from . import voting  # noqa: E402,F401  (registers the built-in mechanisms)
from . import boards  # noqa: E402,F401  (registers the board-game mechanism)
