"""Mechanisms: native building blocks used by name in a contract.

.. code-block:: json

    "mechanisms": {"election": {"kind": "decision", "mode": "ballot", "who": "citizen", "options": ["yes", "no"],
                                "method": "supermajority", "quorum": 0.5}}

``kind`` names a family (``market``, ``decision`` …) and ``mode`` one of its variants, whose strict
config the rest of the entry is. A mechanism expands into ordinary contract sections — actions,
stages, world props, events, views, defs — backed by native functions and effect ops. Everything the engine does (checking,
preview, atomic actions, snapshots, determinism) therefore applies to it unchanged. Anything
the author declares under a generated name wins (a named event, trigger or end entry too), so generated
parts can be overridden (world properties excepted: they are the mechanism's state), while two mechanisms
generating different entries under one name is an error naming both; types the author declares gain the
mechanism's properties without losing their own. A mechanism may extend declared actions (``action_hooks``) and
stages (``stage_hooks``), and generate other mechanisms.
A declared stage that offers only mechanisms' actions and sets no ``max_actions`` allows, per turn, what each
mechanism attached to it allows (a hook's ``max_actions``, default 1).
"""
from __future__ import annotations

import copy
import json
import re
import typing
from difflib import get_close_matches
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ValidationError

from ..contract.rules import StageSpec
from ..errors import Issue
from ..parse_errors import shape_issue
from ..registry import FAMILIES, MechanismError, config_data, family_of_mode

__all__ = ["expand_mechanisms", "merge_sections", "generated_summary", "FAMILIES"]

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")

#: Sections merged by key: the author's entry wins over a generated one of the same name.
_KEYED = ("inputs", "world", "relations", "records", "actions", "views", "policies", "metrics",
          "outputs", "defs", "blocks", "arms", "patterns")
#: Stage settings a mechanism may fill in on a stage the author declared (never overriding the author).
_HOOK_SETTINGS = ("turns", "order", "who", "until", "passes", "quiet", "must_act", "auto", "brief")
#: Stage effect lists a mechanism may append to.
_HOOK_EFFECTS = ("on_enter", "on_exit", "on_idle", "on_wake", "on_turn_end")
#: ``max_actions`` in a hook is the mechanism's share of the stage's turn (see :func:`_share_turns`).
_HOOK_KEYS = frozenset({"actions", "max_actions", *_HOOK_EFFECTS, *_HOOK_SETTINGS})
#: Sections merged by appending generated items (an identical item is never added twice).
_LISTED = ("population", "links", "events", "triggers", "end", "invariants")
#: Listed sections whose items may have a `name`: a declared item of that name replaces the generated one.
_NAMED_ITEMS = ("events", "triggers", "end")
#: What an action hook may add to a declared action.
_ACTION_HOOK_KEYS = ("when", "do", "otherwise")
#: Words authors use for the agent type a mechanism involves; every family calls it `who`.
_ACTOR_WORDS = frozenset({"by", "of", "among", "voter", "voters", "bidder", "bidders", "player", "players",
                          "member", "members", "trader", "traders", "holder", "holders", "guest", "guests",
                          "party", "parties", "agent", "agents", "participants"})
#: Most mechanism uses one contract may expand, generated ones included.
MAX_MECHANISMS = 256


def expand_mechanisms(data: Mapping[str, Any], generated: Optional[Dict[str, Dict[str, List[str]]]] = None
                      ) -> Tuple[Dict[str, Any], List[Issue]]:
    """The contract with every declared mechanism expanded, plus any problems with their configs.

    With ``generated``, each use's entry lists the names it added, by section (see :func:`_added`)."""
    uses = data.get("mechanisms")
    if not uses:
        return dict(data), []
    if not isinstance(uses, Mapping):
        return dict(data), [Issue("mechanisms", "must be an object of {name: {kind, mode, ...config}}")]
    malformed = _malformed_sections(data)
    if malformed:  # mechanisms read and extend these sections: expanding into a malformed one only obscures it
        return dict(data), malformed
    out: Dict[str, Any] = copy.deepcopy(dict(data))
    issues: List[Issue] = []
    expanded: List[str] = []
    owners: Dict[Tuple[str, str], str] = {}  # (section, name) → the mechanism that generated it
    shares: Dict[str, int] = {}  # declared stage → the actions per turn its attached mechanisms allow
    while True:  # generated mechanisms are expanded too, until nothing new appears
        todo = [(name, use) for name, use in out["mechanisms"].items() if name not in expanded]
        if not todo:
            break
        if len(expanded) + len(todo) > MAX_MECHANISMS:
            issues.append(Issue("mechanisms", f"more than {MAX_MECHANISMS} mechanisms: do generated mechanisms generate each other without end?"))
            break
        for name, use in todo:
            expanded.append(name)
            before = _names(out) if generated is not None else {}
            issues.extend(_expand_one(out, name, use, owners, shares))
            if generated is not None:
                generated[str(name)] = _added(before, _names(out))
    _share_turns(data, out, shares)
    return out, issues


def _malformed_sections(data: Mapping[str, Any]) -> List[Issue]:
    """The sections mechanisms read or extend that are not the JSON shape the contract gives them."""
    issues = []
    for section in (*_KEYED, *_LISTED, "types", "entities", "stages", "brief", "clock", "game"):
        value = data.get(section)
        listed = section in (*_LISTED, "stages")
        if value is not None and not isinstance(value, list if listed else Mapping):
            issues.append(shape_issue(section, ["a list" if listed else "an object"], value))
    return issues


def _share_turns(declared: Mapping[str, Any], out: Dict[str, Any], shares: Mapping[str, int]) -> None:
    """A stage the author declared for mechanisms only, without ``max_actions``, gives each mechanism attached to it
    the actions it allows per turn, so a chat message never ends a turn meant for trading and voting too. A stage
    with actions of the author's own keeps its budget: only the author knows how many of their moves a turn holds."""
    authored = {s.get("name"): s for s in declared.get("stages") or [] if isinstance(s, Mapping)}
    for stage in out.get("stages") or []:
        mine = authored.get(stage.get("name")) if isinstance(stage, dict) else None
        if mine is None or stage["name"] not in shares or "max_actions" in mine:
            continue
        offered = mine.get("actions", "all")
        own = offered == "all" and bool(declared.get("actions")) or isinstance(offered, list) and bool(offered) \
            or isinstance(offered, Mapping) and any(offered.values())
        if own:
            continue
        stage["max_actions"] = shares[stage["name"]]
        calls = StageSpec.model_fields["max_calls"].default
        if "max_calls" not in mine and stage["max_actions"] >= calls:  # room to retry a refused call
            stage["max_calls"] = stage["max_actions"] + calls // 2


#: Sections whose entries have names (``stages`` by each stage's name).
_NAMED = ("actions", "stages", "views", "records", "world", "metrics", "outputs", "defs", "blocks", "types", "entities")
#: Sections of unnamed items, reported by how many were added.
_COUNTED = ("events", "triggers", "end", "invariants", "population", "links")


def _names(data: Mapping[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for section in _NAMED:
        value = data.get(section)
        if section == "stages" and isinstance(value, list):
            out[section] = [str(s.get("name")) for s in value if isinstance(s, Mapping)]
        elif isinstance(value, Mapping):
            out[section] = [str(key) for key in value]
    for section in _COUNTED:
        value = data.get(section)
        out[section] = [""] * len(value) if isinstance(value, list) else []
    return out


def _added(before: Mapping[str, List[str]], after: Mapping[str, List[str]]) -> Dict[str, List[str]]:
    """Names new in ``after``, by section; unnamed sections give one empty name per added item."""
    added: Dict[str, List[str]] = {}
    for section, names in after.items():
        old = before.get(section, [])
        new = names[len(old):] if section in _COUNTED else [n for n in names if n not in set(old)]
        if new:
            added[section] = new
    return added


def generated_summary(data: Mapping[str, Any]) -> List[str]:
    """One compact line per declared mechanism naming what it generated (and whether it can end the run), e.g.
    ``sale (market.auction): actions sale_bid · stages sale · outputs sale_sold, sale_revenue · 2 events``."""
    uses = data.get("mechanisms")
    if not isinstance(uses, Mapping) or not uses:
        return []
    generated: Dict[str, Dict[str, List[str]]] = {}
    out, _ = expand_mechanisms(data, generated)
    lines = []
    for name, parts in generated.items():
        use = (data.get("mechanisms") or {}).get(name)
        label = f"{use.get('kind')}.{use.get('mode')}" if isinstance(use, Mapping) and use.get("mode") else "generated"
        shown = [f"{section} {', '.join(names)}" if section in _NAMED else f"{len(names)} {section}"
                 for section, names in parts.items()]
        ends = " · can end the run" if _can_end(out["mechanisms"].get(name)) else ""
        lines.append(f"{name} ({label}): {' · '.join(shown) or 'extends declared parts only'}{ends}")
    return lines


def _can_end(use: Any) -> bool:
    found = _spec(use, "") if isinstance(use, Mapping) and "kind" in use else None
    if not isinstance(found, tuple):
        return False
    try:
        return found[0].ends(found[0].config.model_validate(config_data(use)))
    except ValidationError:
        return False


def _expand_one(out: Dict[str, Any], name: Any, use: Any, owners: Dict[Tuple[str, str], str],
                shares: Dict[str, int]) -> List[Issue]:
    """Validate one declared mechanism and merge what it generates into ``out``."""
    path = f"mechanisms.{name}"
    if not isinstance(name, str) or not _NAME.match(name):
        return [Issue(path, "a mechanism name starts with a letter and uses letters, digits and _",
                      "rename it, e.g. 'election'")]
    if not isinstance(use, Mapping) or "kind" not in use:
        return [Issue(path, "needs a `kind` (a family) and a `mode`", f"families: {', '.join(_kinds())}")]
    found = _spec(use, path)
    if isinstance(found, Issue):
        return [found]
    spec, label = found
    try:
        config = spec.config.model_validate(config_data(use))
    except ValidationError as exc:
        return [_config_issue(path, label, spec.config, error) for error in exc.errors()]
    try:
        fragment = _group_tools(name, config, spec.expand(name, config, out))
        clash = _claim(out, name, fragment, owners)
        if clash is not None:
            return [clash]
        merge_sections(out, fragment)
        for stage, hook in (fragment.get("stage_hooks") or {}).items():
            if hook.get("actions"):
                shares[stage] = shares.get(stage, 0) + int(hook.get("max_actions", 1))
    except MechanismError as exc:
        return [Issue(f"{path}.{exc.path}" if exc.path else path, str(exc), exc.fix)]
    except Exception as exc:  # a broken mechanism must not crash parsing: report it against its use
        return [Issue(path, f"the {label} mechanism failed to expand: {type(exc).__name__}: {exc}",
                      "this is a bug in the mechanism; report it with the contract")]
    return []


#: Sections whose generated entries are claimed by name: two mechanisms must not generate different ones alike.
_CLAIMED = (*_KEYED, "stages", *_NAMED_ITEMS, "entities", "mechanisms")


def _claim(out: Mapping[str, Any], name: str, fragment: Mapping[str, Any], owners: Dict[Tuple[str, str], str]
           ) -> Optional[Issue]:
    """Record the names ``fragment`` generates as ``name``'s; a different entry another mechanism generated under
    one of them is a clash (the author's own entries are not claimed: declaring one overrides the generated one,
    except a world property, which is the mechanism's state)."""
    claims = []
    for section in _CLAIMED:
        present = _entries(out, section)
        for key, item in _entries(fragment, section).items():
            other = owners.get((section, key))
            if section == "world" and other is None and key in present and _canonical(present[key]) != _canonical(item):
                return Issue(f"world.{key}", f"'{key}' is state the mechanism '{name}' keeps: declaring it breaks what it writes",
                             f"rename your world property (read the mechanism's as $world.{key})")
            if other is not None and other != name and _canonical(present.get(key)) != _canonical(item):
                return Issue(f"mechanisms.{name}", f"'{other}' and '{name}' both generate {section} '{key}'",
                             "configure one of them to generate a different name, or keep only one of them")
            if key not in present:
                claims.append((section, key))
    owners.update((claim, name) for claim in claims)
    return None


def _entries(data: Mapping[str, Any], section: str) -> Mapping[str, Any]:
    value = data.get(section)
    if section in ("stages", *_NAMED_ITEMS) and isinstance(value, list):
        return {str(s["name"]): s for s in value if isinstance(s, Mapping) and s.get("name")}
    return value if isinstance(value, Mapping) else {}


#: The former `dynamics` family and its old kind names, and what replaced each.
_FOLDED_INTO_PATTERNS = {
    "dynamics": "drift → trend, seasonal, random_walk or mean_reversion patterns (an event applies them to state); "
                "shocks → a shocks pattern an event reads; priors → draw patterns",
    "drift": "use trend, seasonal, random_walk or mean_reversion patterns, applied to state by an event when agents change it too",
    "shocks": "use a shocks pattern, and an event with when: $pattern.<name> > 0 for what it does",
    "priors": "use draw patterns: $pattern.<name>",
}


def _kinds() -> List[str]:
    return sorted(FAMILIES)


def _spec(use: Mapping[str, Any], path: str) -> Any:
    """``(spec, label)`` for a declared mechanism's kind and mode, or the Issue saying what is wrong."""
    kind = use["kind"]
    family = FAMILIES.get(kind) if isinstance(kind, str) else None
    if family is not None:
        mode = use.get("mode")
        modes = ", ".join(family.modes) or "none"
        if mode is None:
            return Issue(path, f"a `{kind}` mechanism needs a `mode`", f"{kind} modes: {modes}")
        spec = family.modes.get(mode) if isinstance(mode, str) else None
        if spec is None:
            hint = get_close_matches(str(mode), list(family.modes), n=1)
            return Issue(f"{path}.mode", f"'{mode}' is not a mode of `{kind}`",
                         f"did you mean '{hint[0]}'?" if hint else f"{kind} modes: {modes}")
        return spec, f"`{kind}` mode `{mode}`"
    if isinstance(kind, str) and kind in _FOLDED_INTO_PATTERNS:
        return Issue(f"{path}.kind", f"'{kind}' is no longer a mechanism: the world's own changes are `patterns`",
                     _FOLDED_INTO_PATTERNS[kind] + " (guide('patterns'))")
    owner = family_of_mode(kind) if isinstance(kind, str) else None
    if owner is not None:
        return Issue(f"{path}.kind", f"'{kind}' is a mode of kind '{owner}'",
                     f"write \"kind\": \"{owner}\", \"mode\": \"{kind}\" (guide('{owner}.{kind}') lists its fields)")
    hint = get_close_matches(str(kind), _kinds(), n=1)
    return Issue(f"{path}.kind", f"'{kind}' is not a mechanism family",
                 f"did you mean '{hint[0]}'?" if hint else f"families: {', '.join(_kinds())}")


def _config_issue(path: str, label: str, model: Any, error: Mapping[str, Any]) -> Issue:
    """One validation error of a mechanism's config, with the fields that spot takes."""
    loc = tuple(error["loc"])
    where = ".".join(str(p) for p in loc)
    at = f"{path}.{where}" if where else path
    if error["type"] == "extra_forbidden":
        field = str(loc[-1])
        fields = _fields_at(model, loc[:-1])
        hint = get_close_matches(field, fields, n=1) or (["who"] if "who" in fields and field in _ACTOR_WORDS else [])
        owner = label if len(loc) == 1 else f"`{'.'.join(str(p) for p in loc[:-1])}`"
        fix = (f"did you mean '{hint[0]}'? " if hint else "") + (f"{owner} takes: {', '.join(fields)}" if fields else "")
        return Issue(at, f"`{field}` is not a field of {owner}", fix.strip() or None)
    if error["type"] == "missing":
        info = model.model_fields.get(str(loc[0])) if len(loc) == 1 else None
        about = f"`{loc[0]}`: {info.description.rstrip('.')}. " if info is not None and info.description else ""
        return Issue(at, "is required", f"{about}{label} takes: {', '.join(model.model_fields)}")
    return Issue(at, str(error["msg"]), None)


def _fields_at(model: Any, loc: Tuple[Any, ...]) -> List[str]:
    """Field names of the config model reached by following ``loc`` (map keys, list indexes and union tags skipped)."""
    current: Any = model
    for part in loc:
        if isinstance(current, type) and issubclass(current, BaseModel) and part in current.model_fields:
            current = _model_in(current.model_fields[part].annotation)
    return list(current.model_fields) if isinstance(current, type) and issubclass(current, BaseModel) else []


def _model_in(annotation: Any) -> Any:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        found = _model_in(arg)
        if found is not None:
            return found
    return None


def _group_tools(name: str, config: Any, fragment: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a mode's ``tools`` setting: ``one`` offers every generated action inside one tool named after
    the mechanism; ``auto`` does so only when all of them take the same arguments; ``each`` changes nothing."""
    setting = getattr(config, "tools", None)
    actions = fragment.get("actions")
    if setting not in ("one", "auto") or not isinstance(actions, Mapping):
        return fragment
    grouped = [key for key, action in actions.items() if isinstance(action, Mapping)]
    if len(grouped) < 2:
        return fragment
    if setting == "auto" and len({_shape(actions[key]) for key in grouped}) > 1:
        return fragment
    return {**fragment, "actions": {key: ({**action, "tool": name} if key in grouped else action)
                                    for key, action in actions.items()}}


def _shape(action: Mapping[str, Any]) -> Tuple[Tuple[str, str, str], ...]:
    """The arguments an action takes: (name, type, entity type) for each parameter."""
    shape = []
    for pname, param in (action.get("params") or {}).items():
        if isinstance(param, str):
            shape.append((str(pname), param, ""))
        elif isinstance(param, Mapping):
            shape.append((str(pname), str(param.get("type", "")), str(param.get("of") or "")))
    return tuple(sorted(shape))


def merge_sections(data: Dict[str, Any], fragment: Mapping[str, Any]) -> None:
    """Merge contract sections into ``data`` (a mechanism's output, or an imported file); ``data``'s own entries win."""
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
            named = set(_entries(data, section)) if section in _NAMED_ITEMS else set()
            for item in value:
                if _canonical(item) not in seen and not (isinstance(item, Mapping) and item.get("name") in named):
                    target_list.append(copy.deepcopy(item))
        elif section == "stages":
            _merge_stages(data.setdefault("stages", []), value)
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
        elif section in ("clock", "game"):  # a mechanism (a board, a victory rule) may fill in what the author left out
            settings = data.setdefault(section, {})
            for key, item in value.items():
                settings.setdefault(key, copy.deepcopy(item))
        elif section == "stage_hooks":
            _hook_stages(data, value)
        elif section == "action_hooks":
            _hook_actions(data, value)
        elif section == "mechanisms":
            uses = data.setdefault("mechanisms", {})
            for use_name, use in value.items():
                uses.setdefault(use_name, copy.deepcopy(use))
        else:
            known = sorted({*_KEYED, *_LISTED, "types", "entities", "stages", "brief", "clock", "game", "stage_hooks", "action_hooks", "mechanisms"})
            raise MechanismError(f"unknown contract section '{section}'", f"sections: {', '.join(known)}")


def _merge_stages(stages: List[Any], generated: Sequence[Mapping[str, Any]]) -> None:
    """Add generated stages to the declared ones. A declared stage of the same name refines the generated one: it keeps
    the generated fields it does not set, and the generated stages around it keep their order (a declared `flop` still
    deals, and still comes after `preflop`). Without such a stage the generated ones follow the declared ones."""
    names = [s.get("name") if isinstance(s, Mapping) else None for s in stages]
    shared = [stage.get("name") for stage in generated if stage.get("name") in names]
    at = names.index(shared[0]) if shared else len(stages)
    for stage in generated:
        if stage.get("name") in names:
            at = names.index(stage.get("name"))
            for key, item in stage.items():
                stages[at].setdefault(key, copy.deepcopy(item))
            at += 1
        else:
            stages.insert(at, copy.deepcopy(stage))
            names.insert(at, stage.get("name"))
            at += 1


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
            written = stage.get(key, [])
            effects = stage[key] = [written] if isinstance(written, (str, Mapping)) else written
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
            if isinstance(current, (str, Mapping)):
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


from . import families  # noqa: E402,F401  (registers the mechanism families before their modes)
from . import voting  # noqa: E402,F401  (registers the built-in mechanisms)
from . import boards  # noqa: E402,F401  (registers the board-game mechanism)
from . import markets  # noqa: E402,F401  (registers the market mechanisms)
from . import card_scoring, cards, cards_mechanism, pot, roles, slots  # noqa: E402,F401  (cards, pots, roles, worker placement)
from . import social  # noqa: E402,F401  (registers the social mechanism family)
from . import matching  # noqa: E402,F401  (two-sided stable matching, a groups mode)
from . import status  # noqa: E402,F401
from . import abilities  # noqa: E402,F401
from . import locations  # noqa: E402,F401
from . import procedure  # noqa: E402,F401
from . import turn_order  # noqa: E402,F401
from . import victory  # noqa: E402,F401
from . import judging, memory  # noqa: E402,F401  (host-evaluated intelligence: judges, game masters, memory, host tools)
from . import economy  # noqa: E402,F401  (registers the economy mechanisms)
from . import ops_queue  # noqa: E402,F401  (registers the operations mechanisms)
