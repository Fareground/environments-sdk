"""Normalization rules for state, outcomes and reuse: earlier sections rewritten into their current homes.

* ``metrics`` → ``outputs`` with ``series``; ``$metrics.x`` → ``$outputs.x``.
* ``policies`` → ``types.<t>.policies`` (a policy several types play is copied to each).
* ``population`` → generator entries of ``entities`` (keyed by type; ``mix``, ``quota``, ``members`` and ``raking``
  are refused with how to say them now).
* ``links`` → ``relations.<r>.links`` (each entry without its ``relation``).
* Each arm's ``patch`` is a contract fragment, so its earlier forms are rewritten too.
"""
from __future__ import annotations

import copy
import difflib
import re
from collections.abc import Callable
from typing import Any

from ..errors import ContractError, Issue
from .normalize import normalize, rule

__all__: list[str] = []


def _strings(value: Any, change: Callable[[str], str]) -> Any:
    """``value`` with ``change`` applied to every text in it (keys included)."""
    if isinstance(value, str):
        return change(value)
    if isinstance(value, list):
        return [_strings(item, change) for item in value]
    if isinstance(value, dict):
        return {change(key) if isinstance(key, str) else key: _strings(item, change) for key, item in value.items()}
    return value


def _replace_all(data: dict[str, Any], pattern: re.Pattern[str], replacement: str) -> bool:
    """Apply ``pattern`` → ``replacement`` to every text in ``data`` (in place); whether anything changed."""
    changed = False

    def change(text: str) -> str:
        nonlocal changed
        new = pattern.sub(replacement, text)
        changed = changed or new != text
        return new

    rewritten = _strings(data, change)
    if changed:
        data.clear()
        data.update(rewritten)
    return changed


# -- metrics → outputs.series ---------------------------------------------------------------------------------------

_METRICS_ROOT = re.compile(r"\$metrics\b")


def _expr_of(spec: Any) -> Any:
    return spec.get("expr") if isinstance(spec, dict) else spec


@rule
def metrics_into_outputs(data: dict[str, Any]) -> list[str]:
    """``metrics: {m: e}`` → ``outputs: {m: {expr: e, series: true}}``. An output of the same name keeps its own
    expression and samples the metric's (``series: e``); one that only read the metric (``$metrics.m``) becomes the
    metric, sampled."""
    notes = []
    metrics = data.pop("metrics", None)
    if isinstance(metrics, dict) and metrics:
        outputs = data.setdefault("outputs", {})
        if not isinstance(outputs, dict):  # the parser reports the malformed section
            data["metrics"] = metrics
            return []
        for name, metric in metrics.items():
            spec = dict(metric) if isinstance(metric, dict) else {"expr": metric}
            output = outputs.get(name)
            if output is None:
                outputs[name] = {**spec, "series": True}
                notes.append(f"metrics.{name}: now outputs.{name} with series: true")
                continue
            merged = dict(output) if isinstance(output, dict) else {"expr": output}
            if _expr_of(output) in (spec.get("expr"), f"$metrics.{name}"):
                merged.update(expr=spec.get("expr"), series=True)
            else:
                merged["series"] = spec.get("expr")
            for key in ("unit", "description"):
                if spec.get(key) and not merged.get(key):
                    merged[key] = spec[key]
            outputs[name] = merged
            notes.append(f"metrics.{name}: merged into outputs.{name} as its series")
    elif metrics is not None and not isinstance(metrics, dict):
        data["metrics"] = metrics  # malformed: left for the parser
    if _replace_all(data, _METRICS_ROOT, "$outputs"):
        notes.append("$metrics.x: now $outputs.x (a series output's latest sample)")
    return notes


# -- shared: the types of a document not yet parsed --------------------------------------------------------------------


def _types(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    types = data.get("types")
    return {name: spec for name, spec in types.items() if isinstance(spec, dict)} if isinstance(types, dict) else {}


def _lineage(types: dict[str, dict[str, Any]], name: str) -> list[str]:
    """``name`` and its ancestors, nearest first (stops at unknown types and cycles)."""
    chain: list[str] = []
    current: Any = name
    while isinstance(current, str) and current in types and current not in chain:
        chain.append(current)
        current = types[current].get("extends")
    return chain


def _is_agent(types: dict[str, dict[str, Any]], name: str) -> bool:
    return any(types[kind].get("agent") is True for kind in _lineage(types, name))


# -- policies → types.<t>.policies ----------------------------------------------------------------------------------


def _takes(data: dict[str, Any], kind: str, action: Any) -> bool:
    """Whether agents of ``kind`` may take ``action`` (its `by` names the type or an ancestor)."""
    actions = data.get("actions") if isinstance(data.get("actions"), dict) else {}
    by = actions[action].get("by") if isinstance(actions.get(action), dict) else None
    allowed = [by] if isinstance(by, str) else by if isinstance(by, list) else []
    return any(parent in allowed for parent in _lineage(_types(data), kind))


def _rules_of(spec: Any) -> list[Any]:
    return spec["rules"] if isinstance(spec, dict) and isinstance(spec.get("rules"), list) else []


def _policy_owners(data: dict[str, Any], name: str, spec: Any) -> list[str]:
    """The types a top-level policy belongs to: those that name it as their `policy`, else the agent types that may
    take the actions its rules call (the most general of them), else every agent type."""
    types = _types(data)
    users = [kind for kind, type_spec in types.items() if type_spec.get("policy") == name]
    if users:
        return users
    called = {rule.get("do") for rule in _rules_of(spec) if isinstance(rule, dict)} - {"pass", None}

    def takes(kind: str, action: Any) -> bool:
        return _takes(data, kind, action)

    agents = [kind for kind in types if _is_agent(types, kind)]
    fit = ([kind for kind in agents if called and all(takes(kind, a) for a in called)]
           or [kind for kind in agents if any(takes(kind, a) for a in called)] or agents)
    return [kind for kind in fit if not any(parent in fit for parent in _lineage(types, kind)[1:])]


def _own_copy(data: dict[str, Any], owner: str, spec: Any) -> Any:
    """``spec`` for agents of ``owner``: without the rules for declared actions none of them may take (a policy
    several types shared skipped those for each; they never acted)."""
    out = copy.deepcopy(spec)
    types = _types(data)
    players = [kind for kind in types if owner in _lineage(types, kind)]
    actions = data.get("actions") if isinstance(data.get("actions"), dict) else {}

    def playable(rule: Any) -> bool:
        action = rule.get("do") if isinstance(rule, dict) else None
        return action not in actions or any(_takes(data, kind, action) for kind in players)

    if _rules_of(out):
        out["rules"] = [rule for rule in out["rules"] if playable(rule)]
    return out


@rule
def policies_under_types(data: dict[str, Any]) -> list[str]:
    """``policies: {p: spec}`` → ``types.<t>.policies.p`` for each type that plays it."""
    policies = data.get("policies")
    if not isinstance(policies, dict) or not _types(data):
        return []  # nothing to move, or nowhere to move it: the parser reports what is wrong
    del data["policies"]
    notes = []
    for name, spec in policies.items():
        owners = _policy_owners(data, name, spec)
        if not owners:  # no agent type to play it: the parser reports the section
            data.setdefault("policies", {})[name] = spec
            continue
        for owner in owners:
            data["types"][owner].setdefault("policies", {}).setdefault(name, _own_copy(data, owner, spec))
        notes.append(f"policies.{name}: now under " + ", ".join(f"types.{owner}.policies" for owner in owners))
    return notes


# -- population → entities generators -------------------------------------------------------------------------------

#: What each dropped population field is now, as the refusal's fix.
_POPULATION_EXTRAS = {
    "mix": "give each entity its archetype with a prop and a brief that read it, e.g. \"props\": {\"archetype\": "
           "\"$get(['a', 'b'], ($i - 1) % 2)\"}, or label table rows before load with fg_env.personas.assign_labels "
           "and generate `from` them",
    "quota": "archetype shares are labels now: fg_env.personas.assign_labels gives exact shares",
    "members": "declare a second generator `from` a table of the members, each row naming its parent",
    "raking": "reweight the rows before load with fg_env.personas.rake and sample with \"weight\": \"$row.weight\"",
}


def _free_key(entities: dict[str, Any], base: str) -> str:
    if base not in entities:
        return base
    n = 2
    while f"{base}_{n}" in entities:
        n += 1
    return f"{base}_{n}"


@rule
def population_into_entities(data: dict[str, Any]) -> list[str]:
    """``population: [{type: t, count: n, ...}]`` → ``entities: {t: {type: t, count: n, ...}}``, after the named ones
    (the build order), each keyed by its type (``t_2`` when the key is taken)."""
    population = data.get("population")
    entities = data.get("entities", {})
    if not isinstance(population, list) or not isinstance(entities, dict):
        return []
    issues = [Issue(f"population[{index}].{key}", "is no longer part of the contract", fix)
              for index, group in enumerate(population) if isinstance(group, dict)
              for key, fix in _POPULATION_EXTRAS.items() if key in group]
    issues += [Issue(f"population[{index}]", "gives neither `count` nor `from`", "give `count`, `from`, or both")
               for index, group in enumerate(population)
               if isinstance(group, dict) and group.get("count") is None and group.get("from") is None]
    if issues:
        raise ContractError(issues, title="population cannot be rewritten as entities")
    del data["population"]
    data["entities"] = entities
    notes = []
    for index, group in enumerate(population):
        key = _free_key(entities, str(group.get("type")) if isinstance(group, dict) else "population")
        entities[key] = group
        notes.append(f"population[{index}]: now entities.{key}")
    return notes


# -- links → relations.<r>.links -------------------------------------------------------------------------------------


@rule
def links_under_relations(data: dict[str, Any]) -> list[str]:
    """``links: [{relation: r, ...}]`` → ``relations.r.links: [{...}]``, in the order written. A link whose relation
    a mechanism declares waits until the mechanisms are expanded."""
    links = data.get("links")
    relations = data.get("relations", {})
    if not isinstance(links, list) or not isinstance(relations, dict):
        return []
    undeclared = [(index, link.get("relation") if isinstance(link, dict) else None) for index, link in enumerate(links)
                  if not isinstance(link, dict) or not isinstance(relations.get(link.get("relation")), dict)]
    if undeclared:
        if data.get("mechanisms"):
            return []  # they may declare it; what is still undeclared once they have is reported by the parser
        issues = [Issue(f"links[{index}].relation", f"'{kind}' is not a declared relation",
                        f"did you mean '{close[0]}'?" if (close := difflib.get_close_matches(
                            str(kind), [str(k) for k in relations], n=1)) else "declare it under `relations`")
                  for index, kind in undeclared]
        raise ContractError(issues, title="links cannot be moved under their relations")
    del data["links"]
    data["relations"] = relations
    notes = []
    for index, link in enumerate(links):
        entry = {key: value for key, value in link.items() if key != "relation"}
        target = relations[link["relation"]].setdefault("links", [])
        target.append(entry)
        notes.append(f"links[{index}]: now relations.{link['relation']}.links[{len(target) - 1}]")
    return notes


# -- arm patches ----------------------------------------------------------------------------------------------------


@rule
def arm_patches(data: dict[str, Any]) -> list[str]:
    """Every rule, applied to each arm's patch on its own (the patch is merged into the rewritten contract)."""
    arms = data.get("arms")
    if not isinstance(arms, dict):
        return []
    notes = []
    for name, arm in arms.items():
        patch = arm.get("patch") if isinstance(arm, dict) else None
        if isinstance(patch, dict) and patch:
            arm["patch"], found = normalize(patch)
            notes += [f"arms.{name}.patch.{note}" for note in found]
    return notes
