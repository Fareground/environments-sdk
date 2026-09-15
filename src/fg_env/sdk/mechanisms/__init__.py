"""Mechanisms: native building blocks used by name in a contract.

.. code-block:: json

    "mechanisms": {"election": {"kind": "ballot", "voters": "citizen", "options": ["yes", "no"],
                                "method": "supermajority", "quorum": 0.5}}

A mechanism expands into ordinary contract sections — actions, stages, world props, events,
views, defs — backed by native functions and effect ops. Everything the engine does (checking,
preview, atomic actions, snapshots, determinism) therefore applies to it unchanged. Anything
the author declares under a generated name wins, so generated parts can be overridden; types
the author declares gain the mechanism's properties without losing their own. A mechanism may
extend declared actions (``action_hooks``) and stages (``stage_hooks``), and generate other mechanisms.
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
_KEYED = ("inputs", "world", "relations", "records", "actions", "views", "policies", "metrics",
          "outputs", "defs", "blocks", "arms")
#: Stage settings a mechanism may fill in on a stage the author declared (never overriding the author).
_HOOK_SETTINGS = ("turns", "order", "who", "until", "passes", "quiet", "max_actions", "max_calls", "must_act", "auto",
                  "brief")
#: Stage effect lists a mechanism may append to.
_HOOK_EFFECTS = ("on_enter", "on_exit", "on_idle", "on_wake", "on_turn_end")
_HOOK_KEYS = frozenset({"actions", *_HOOK_EFFECTS, *_HOOK_SETTINGS})
#: Sections merged by appending generated items (an identical item is never added twice).
_LISTED = ("population", "links", "events", "end", "invariants")
#: What an action hook may add to a declared action.
_ACTION_HOOK_KEYS = ("when", "do", "otherwise")
#: Most mechanism uses one contract may expand, generated ones included.
MAX_MECHANISMS = 256


def expand_mechanisms(data: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[Issue]]:
    """The contract with every declared mechanism expanded, plus any problems with their configs."""
    uses = data.get("mechanisms")
    if not uses:
        return dict(data), []
    if not isinstance(uses, Mapping):
        return dict(data), [Issue("mechanisms", "must be an object of {name: {kind, ...config}}")]
    out: Dict[str, Any] = copy.deepcopy(dict(data))
    issues: List[Issue] = []
    expanded: List[str] = []
    while True:  # generated mechanisms are expanded too, until nothing new appears
        todo = [(name, use) for name, use in out["mechanisms"].items() if name not in expanded]
        if not todo:
            break
        if len(expanded) + len(todo) > MAX_MECHANISMS:
            issues.append(Issue("mechanisms", f"more than {MAX_MECHANISMS} mechanisms: do generated mechanisms generate each other without end?"))
            break
        for name, use in todo:
            expanded.append(name)
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
                _merge(out, fragment)
            except MechanismError as exc:
                issues.append(Issue(f"{path}.{exc.path}" if exc.path else path, str(exc), exc.fix))
                continue
            except Exception as exc:  # a broken mechanism must not crash parsing: report it against its use
                issues.append(Issue(path, f"the `{kind}` mechanism failed to expand: {type(exc).__name__}: {exc}",
                                    "this is a bug in the mechanism; report it with the contract"))
                continue
    return out, issues


def _merge(data: Dict[str, Any], fragment: Mapping[str, Any]) -> None:
    for section, value in fragment.items():
        if section == "types":
            types = data.setdefault("types", {})
            for type_name, spec in value.items():
                if type_name not in types:
                    types[type_name] = copy.deepcopy(spec)
                    continue
                _fill(types[type_name], spec)
        elif section == "entities":
            entities = data.setdefault("entities", {})
            for entity_id, spec in value.items():
                if entity_id in entities and isinstance(entities[entity_id], dict):
                    _fill(entities[entity_id], spec)
                else:
                    entities.setdefault(entity_id, copy.deepcopy(spec))
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
        elif section == "action_hooks":
            _hook_actions(data, value)
        elif section == "mechanisms":
            uses = data.setdefault("mechanisms", {})
            for use_name, use in value.items():
                uses.setdefault(use_name, copy.deepcopy(use))
        else:
            raise MechanismError(f"a mechanism produced an unknown section '{section}'")


def _fill(declared: Dict[str, Any], generated: Mapping[str, Any]) -> None:
    """Give a type or entity the author declared the generated props and fields it lacks; the author's win."""
    props = declared.setdefault("props", {})
    for prop, prop_value in (generated.get("props") or {}).items():
        props.setdefault(prop, copy.deepcopy(prop_value))
    for key, item in generated.items():
        if key != "props":
            declared.setdefault(key, copy.deepcopy(item))


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
        unknown = set(hook) - _HOOK_KEYS
        if unknown:
            raise MechanismError(f"a stage hook cannot set {', '.join(sorted(unknown))}",
                                 f"hooks set: {', '.join(sorted(_HOOK_KEYS))}", "stage")
        for key in _HOOK_SETTINGS:  # turn settings the author left unset
            if key in hook:
                stage.setdefault(key, copy.deepcopy(hook[key]))
        for key in _HOOK_EFFECTS:
            effects = stage.setdefault(key, [])
            seen = {_canonical(e) for e in effects}
            effects.extend(copy.deepcopy(e) for e in hook.get(key) or [] if _canonical(e) not in seen)


def _hook_actions(data: Dict[str, Any], hooks: Mapping[str, Mapping[str, Any]]) -> None:
    """Append ``when`` conditions and ``do``/``otherwise`` effects to actions the author declared.

    The action stays the author's: nothing it declares is replaced, and an identical item is added once."""
    actions = data.get("actions") or {}
    for name, hook in hooks.items():
        action = actions.get(name)
        if not isinstance(action, dict):
            hint = get_close_matches(str(name), list(actions), n=1)
            raise MechanismError(f"there is no action '{name}' to attach to",
                                 f"did you mean '{hint[0]}'?" if hint else f"actions: {', '.join(actions) or 'none declared'}",
                                 "actions")
        unknown = set(hook) - set(_ACTION_HOOK_KEYS)
        if unknown:
            raise MechanismError(f"an action hook cannot set {', '.join(sorted(unknown))}",
                                 f"hooks set: {', '.join(_ACTION_HOOK_KEYS)}", "actions")
        for key in _ACTION_HOOK_KEYS:
            extra = list(hook.get(key) or [])
            if not extra:
                continue
            current = action.get(key)
            if key == "when" and isinstance(current, (str, Mapping)):
                current = [current]
            if current is not None and not isinstance(current, list):
                raise MechanismError(f"actions.{name}.{key} must be a list, got {type(current).__name__}",
                                     f"write actions.{name}.{key} as a list", "actions")
            merged = list(current or [])
            seen = {_canonical(item) for item in merged}
            for item in extra:
                if _canonical(item) not in seen:
                    merged.append(copy.deepcopy(item))
                    seen.add(_canonical(item))
            action[key] = merged


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


from . import voting  # noqa: E402,F401  (registers the built-in mechanisms)
from . import boards  # noqa: E402,F401  (registers the board-game mechanism)
from . import markets  # noqa: E402,F401  (registers the market mechanisms)
from . import card_scoring, cards, cards_mechanism, pot, roles, slots  # noqa: E402,F401  (cards, pots, roles, worker placement)
from . import social  # noqa: E402,F401  (registers the social mechanism family)
from . import status  # noqa: E402,F401
from . import abilities  # noqa: E402,F401
from . import locations  # noqa: E402,F401
from . import dynamics  # noqa: E402,F401
from . import procedure  # noqa: E402,F401
from . import turn_order  # noqa: E402,F401
from . import victory  # noqa: E402,F401
from . import judging, memory  # noqa: E402,F401  (host-evaluated intelligence: judges, game masters, memory, host tools)
from . import economy  # noqa: E402,F401  (registers the economy mechanisms)
