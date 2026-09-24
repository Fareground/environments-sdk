"""Normalization rules for state, outcomes and reuse: earlier sections rewritten into their current homes.

* ``for``/``make`` macros → the structure they generate, expanded in place (runs first: a macro may make any
  section).
* ``metrics`` → ``outputs`` with ``series``; ``$metrics.x`` → ``$outputs.x``.
* ``policies`` → ``types.<t>.policies`` (a policy several types play is copied to each).
* ``population`` → generator entries of ``entities`` (keyed by type; ``mix``, ``quota``, ``members`` and ``raking``
  are refused with how to say them now).
* ``links`` → ``relations.<r>.links`` (each entry without its ``relation``).
* ``game`` → ``types.<player>.score`` (``returns`` over $actor → ``value`` over $it; the claims, which describe and
  check derive, and ``total`` are dropped; ``rewards`` is refused).
* ``blocks`` → ``defs`` with ``do``; the ``{"block": b, "with": ...}`` effect → ``{"call": b, "with": ...}``.
* ``assets`` → ``inputs`` of ``type: file`` (``file`` or ``folder`` → ``source``; the kind filter ``type`` goes:
  a file's kind is its extension).
* Each arm's ``patch`` is a contract fragment, so its earlier forms are rewritten too.
"""
from __future__ import annotations

import copy
import difflib
import json
import re
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from ..errors import ContractError, Issue
from .normalize import normalize, rule

__all__ = ["expand_macros"]


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


# -- for/make macros → expanded ---------------------------------------------------------------------------------------
#
# A macro is an object with ``for`` and ``make`` (and ``as``, optionally ``index``). It repeats ``make`` once per value,
# with ``{name}`` placeholders in strings and keys replaced by that value. In a list it becomes one item per value; as a
# map entry whose key holds a loop placeholder, one entry per value named by the key; under any other key a list of
# the made values. ``for`` is a list, ``{"range": n}`` / ``[start, end]`` / ``[start, end, step]``, or a placeholder
# naming a list from an outer loop. ``{x}`` alone keeps the value's type; ``{x.f}`` reads a field, ``{n+1}`` offsets a
# number; ``index`` names the 0-based position. Each file is expanded on its own, before it is merged.


@rule
def macros_expanded(data: dict[str, Any]) -> list[str]:
    """Every ``for``/``make`` macro → what it generates (as ``fg-env expand`` shows it)."""
    if not _has_macro(data):
        return []
    expanded = expand_macros(data)
    data.clear()
    data.update(expanded)
    return ["for/make macros: expanded in place (write the expanded form)"]


#: Most values all macros in one contract file may generate.
MAX_MACRO_ITEMS = 10_000
#: Deepest nesting of macros inside macros.
MAX_MACRO_DEPTH = 8
#: Most JSON values (strings, numbers, lists, objects) the expanded contract may hold.
MAX_MACRO_NODES = 2_000_000

_MACRO_KEYS = frozenset({"for", "as", "index", "make"})
_MACRO_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)(?:([+-])(\d+))?\}(?!\})")
_FIX = 'write {"for": [values], "as": "name", "make": {...}}'


class _MacroError(Exception):
    def __init__(self, path: str, message: str, fix: str | None = None):
        super().__init__(message)
        self.path, self.fix = path, fix


def is_macro(value: Any) -> bool:
    """True when ``value`` is a macro object: it has both ``for`` and ``make``, or only macro fields with ``make``
    (a macro missing its ``for``, reported as one). Data with a field named ``make`` (a car's make) is not a macro."""
    if not isinstance(value, Mapping) or "make" not in value:
        return False
    return "for" in value or (len(value) > 1 and set(value) <= _MACRO_KEYS)


def expand_macros(data: Any) -> Any:
    """``data`` with every macro expanded (a copy; unchanged data when it holds none).

    Raises :class:`ContractError` listing every malformed macro with its path."""
    if not _has_macro(data):
        return data
    expander = _Expander()
    out = expander.walk(data, {}, "", 0)
    if expander.issues:
        raise ContractError(expander.issues, title="contract macros cannot be expanded")
    return out


def _has_macro(value: Any) -> bool:
    if isinstance(value, Mapping):
        return is_macro(value) or any(_has_macro(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_macro(v) for v in value)
    return False


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


class _Expander:
    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self.items = 0
        self.nodes = 0

    # -- walking -----------------------------------------------------------------

    def walk(self, value: Any, env: dict[str, Any], path: str, depth: int) -> Any:
        self.nodes += 1
        if self.nodes > MAX_MACRO_NODES:
            raise ContractError([Issue(path or "(contract)",
                                       f"macros would make the contract larger than {MAX_MACRO_NODES:,} values",
                                       "generate less, or split the environment")])
        if isinstance(value, str):
            return self.text(value, env, path) if env else value
        if isinstance(value, list):
            out: list[Any] = []
            for index, item in enumerate(value):
                where = f"{path}[{index}]"
                if is_macro(item):
                    out.extend(self.attempt(lambda item=item,
                                            where=where: [v for _, v in self.loop(item, env, where, depth)], []))
                else:
                    out.append(self.walk(item, env, where, depth))
            return out
        if isinstance(value, Mapping):
            if is_macro(value):
                self.issues.append(Issue(path or "(contract)",
                                         "a macro makes list items or map entries, not a single value",
                                         "put it inside a list, or under a key such as \"bet_{street}\""))
                return value
            mapped: dict[str, Any] = {}
            for key, item in value.items():
                where = _join(path, str(key))
                if is_macro(item):
                    self.attempt(lambda key=key, item=item,
                                 where=where: self.entries(mapped, key, item, env, where, depth), None)
                    continue
                name = self.attempt(lambda key=key, where=where: self.text(key, env, where), key) if env else key
                if not isinstance(name, str):
                    self.issues.append(Issue(where, f"a key must be text, but its placeholder gives {_kind(name)}"))
                    continue
                self.put(mapped, name, self.walk(item, env, where, depth), where)
            return mapped
        return value

    def attempt(self, work: Any, fallback: Any) -> Any:
        try:
            return work()
        except _MacroError as exc:
            self.issues.append(Issue(exc.path, str(exc), exc.fix))
            return fallback

    def put(self, mapped: dict[str, Any], name: str, value: Any, where: str) -> None:
        if name in mapped:
            self.issues.append(Issue(where,
                                     f"'{name}' is given twice (a macro and another entry, or two macros, make it)",
                                     "give each generated entry a distinct name"))
            return
        mapped[name] = value

    def entries(self, mapped: dict[str, Any], key: str, macro: Mapping[str, Any], env: dict[str, Any], where: str,
                depth: int) -> None:
        """A macro under a map key: entries named by the key when it holds a loop placeholder, else a list."""
        loop_names = self.loop_names(macro, where)
        if not any(name in loop_names for name, *_ in _PLACEHOLDER.findall(key)):
            name = self.text(key, env, where) if env else key
            if not isinstance(name, str):
                raise _MacroError(where, f"a key must be text, but its placeholder gives {_kind(name)}")
            self.put(mapped, name, [v for _, v in self.loop(macro, env, where, depth)], where)
            return
        for inner, made in self.loop(macro, env, where, depth):
            name = self.text(key, inner, where)
            if not isinstance(name, str):
                raise _MacroError(where, f"a generated key must be text, but its placeholder gives {_kind(name)}")
            self.put(mapped, name, made, where)

    def loop_names(self, macro: Mapping[str, Any], where: str) -> list[str]:
        """Every variable a macro and the macros nested in its ``make`` define."""
        names: list[str] = []
        current: Any = macro
        while is_macro(current):
            names += [current[k] for k in ("as", "index") if isinstance(current.get(k), str)]
            current = current.get("make")
        return names

    def loop(self, macro: Mapping[str, Any], env: dict[str, Any], where: str,
             depth: int) -> Iterator[tuple[dict[str, Any], Any]]:
        """``(variables, made value)`` for each value of the loop, nested loops flattened."""
        if depth >= MAX_MACRO_DEPTH:
            raise _MacroError(where, f"macros are nested more than {MAX_MACRO_DEPTH} deep",
                              "flatten the loops into data")
        unknown = sorted(set(macro) - _MACRO_KEYS)
        if unknown:
            raise _MacroError(_join(where, unknown[0]), "is not a macro field",
                              f"a macro takes for, as, index and make: {_FIX}; data with fields named `for` and `make` "
                              "is read as a macro, so rename one of them (e.g. `vehicle_make`)")
        if "for" not in macro:
            raise _MacroError(where, "a macro needs `for`: the values to repeat over",
                              f"{_FIX}; if this is data, not a macro, rename its `make` field (e.g. `vehicle_make`)")
        name = self.variable(macro, "as", env, where)
        index_name = self.variable(macro, "index", env, where) if macro.get("index") is not None else None
        if index_name is not None and index_name == name:
            raise _MacroError(_join(where, "index"), f"`index` and `as` both name '{name}'",
                              "give the position its own name")
        values = self.values(macro["for"], env, _join(where, "for"), depth)
        make = macro["make"]
        for position, value in enumerate(values):
            inner = {**env, name: value}
            if index_name is not None:
                inner[index_name] = position
            if is_macro(make):
                yield from self.loop(make, inner, _join(where, "make"), depth + 1)
                continue
            self.items += 1
            if self.items > MAX_MACRO_ITEMS:
                raise _MacroError(where, f"macros would generate more than {MAX_MACRO_ITEMS:,} values",
                                  "generate less, or split the environment")
            yield inner, self.walk(copy.deepcopy(make), inner, _join(where, "make"), depth + 1)

    def variable(self, macro: Mapping[str, Any], key: str, env: dict[str, Any], where: str) -> str:
        name = macro.get(key)
        at = _join(where, key)
        if name is None:
            raise _MacroError(where, "a macro needs `as`: the placeholder name for each value", _FIX)
        if not isinstance(name, str) or not _MACRO_NAME.match(name):
            raise _MacroError(at, f"must be a name of letters, digits and _, got {json.dumps(name)}", 'e.g. "street"')
        if name in env:
            raise _MacroError(at, f"'{name}' is already a variable of an enclosing macro",
                              "give the inner loop its own name")
        return name

    def values(self, source: Any, env: dict[str, Any], where: str, depth: int) -> list[Any]:
        if isinstance(source, str):
            resolved = self.text(source, env, where) if env else source
            if not isinstance(resolved, list):
                raise _MacroError(where, "must be a list, a range or a placeholder giving a list; "
                                         f"{json.dumps(source)} gives {_kind(resolved)}",
                                  'e.g. ["flop", "turn"] or {"range": [1, 4]}')
            return resolved
        if isinstance(source, list):
            return list(self.walk(source, env, where, depth))
        if isinstance(source, Mapping) and set(source) == {"range"}:
            return self.range(source["range"], env, _join(where, "range"))
        raise _MacroError(where, f"must be a list, a range or a placeholder giving a list, got {_kind(source)}",
                          'e.g. ["flop", "turn"], {"range": 3} or {"range": [1, 4]}')

    def range(self, spec: Any, env: dict[str, Any], where: str) -> list[int]:
        bounds = spec if isinstance(spec, list) else [spec]
        bounds = [self.text(b, env, where) if isinstance(b, str) and env else b for b in bounds]
        if not 1 <= len(bounds) <= 3 or not all(isinstance(b, int) and not isinstance(b, bool) for b in bounds):
            raise _MacroError(where,
                              "must be a whole number n (0..n-1) or [start, end] or [start, end, step], got "
                              f"{json.dumps(spec)}",
                              'e.g. {"range": [1, 4]} gives 1, 2, 3')
        if len(bounds) == 1:
            start, end, step = 0, bounds[0], 1
        else:
            start, end, step = bounds[0], bounds[1], bounds[2] if len(bounds) == 3 else 1
        if step == 0:
            raise _MacroError(where, "the step cannot be 0")
        count = max(0, -(-(end - start) // step))
        if count > MAX_MACRO_ITEMS:
            raise _MacroError(where, f"the range has {count:,} values; macros generate at most {MAX_MACRO_ITEMS:,}")
        return list(range(start, end, step))

    # -- placeholders -------------------------------------------------------------

    def text(self, source: str, env: dict[str, Any], where: str) -> Any:
        """``source`` with loop placeholders replaced; a lone placeholder gives the value itself."""
        whole = _PLACEHOLDER.fullmatch(source)
        if whole is not None and whole.group(1) in env:
            return copy.deepcopy(self.lookup(whole, env, where))

        def replace(match: re.Match[str]) -> str:
            if match.group(1) not in env:
                return match.group(0)
            return _written(self.lookup(match, env, where))

        return _PLACEHOLDER.sub(replace, source)

    def lookup(self, match: re.Match[str], env: dict[str, Any], where: str) -> Any:
        name, fields, sign, amount = match.groups()
        value = env[name]
        reached = name
        for part in fields.split(".")[1:]:
            if isinstance(value, Mapping):
                if part not in value:
                    raise _MacroError(where, f"{match.group(0)}: {reached} has no field '{part}'",
                                      f"fields: {', '.join(map(str, value)) or 'none'}")
                value = value[part]
            elif isinstance(value, list) and part.isdigit():
                if int(part) >= len(value):
                    raise _MacroError(where, f"{match.group(0)}: {reached} has {len(value)} items, no item {part}")
                value = value[int(part)]
            else:
                raise _MacroError(where, f"{match.group(0)}: {reached} is {_kind(value)}, which has no '{part}'")
            reached = f"{reached}.{part}"
        if sign:
            if isinstance(value, bool) or not isinstance(value, int):
                raise _MacroError(where,
                                  f"{match.group(0)}: only a whole number can be offset; {reached} is {_kind(value)}")
            value = value + int(amount) if sign == "+" else value - int(amount)
        return value


def _written(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return f"the number {value}"
    if isinstance(value, str):
        return f"the text {json.dumps(value)[:60]}"
    return "a list" if isinstance(value, list) else "a map"


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


# -- game → types.<t>.score -----------------------------------------------------------------------------------------

_ACTOR = re.compile(r"\$actor\b")
#: game fields that become score fields.
_SCORE_FIELDS = {"seat": "seat", "utility": "utility", "min_return": "min", "max_return": "max"}


@rule
def game_into_scores(data: dict[str, Any]) -> list[str]:
    """``game: {players, returns, seat, utility, min_return, max_return}`` → ``types.<player>.score: {value, seat,
    utility, min, max}`` on each player type (every most general agent type when ``players`` is not given); a score
    the type already has keeps its own fields."""
    game = data.get("game")
    types = _types(data)
    if not isinstance(game, dict) or not types:
        return []
    if "rewards" in game:
        raise ContractError([Issue("game.rewards", "is no longer part of the contract",
                                   "a seat's reward is the change in its score since its previous step: keep what it "
                                   "earns in a property and score that, e.g. \"score\": {\"value\": \"$it.earned\"}")])
    players = game.get("players")
    if players is None:
        agents = [kind for kind in types if _is_agent(types, kind)]
        listed = [kind for kind in agents if not any(parent in agents for parent in _lineage(types, kind)[1:])]
    else:
        listed = [players] if isinstance(players, str) else list(players) if isinstance(players, list) else []
    unknown = [kind for kind in listed if kind not in types]
    if unknown or not listed:
        raise ContractError([Issue("game.players", f"'{unknown[0]}' is not a declared type" if unknown
                                   else "names no agent type", "name the agent types whose entities are the seats")])
    score = {new: game[old] for old, new in _SCORE_FIELDS.items() if old in game}
    if isinstance(game.get("returns"), str):
        score["value"] = _ACTOR.sub("$it", game["returns"])
    for kind in listed:
        own = types[kind].setdefault("score", {})
        for key, value in score.items():
            if isinstance(own, dict):  # else the parser reports the malformed score
                own.setdefault(key, value)
    del data["game"]
    return ["game: now the score of " + ", ".join(f"types.{kind}" for kind in listed)]


# -- blocks → defs with do; the block effect → call -----------------------------------------------------------------


def _calls(value: Any) -> tuple[Any, bool]:
    """``value`` with every ``{"block": b, "with": ...}`` effect written ``{"call": b, "with": ...}``."""
    if isinstance(value, list):
        items = [_calls(item) for item in value]
        return [item for item, _ in items], any(changed for _, changed in items)
    if not isinstance(value, dict):
        return value, False
    if isinstance(value.get("block"), str) and set(value) <= {"block", "with"}:
        return {("call" if key == "block" else key): item for key, item in value.items()}, True
    items = {key: _calls(item) for key, item in value.items()}
    return {key: item for key, (item, _) in items.items()}, any(changed for _, changed in items.values())


@rule
def blocks_into_defs(data: dict[str, Any]) -> list[str]:
    """``blocks: {b: {args, do}}`` → ``defs: {b: {args, do}}``, and each block effect → a call."""
    notes = []
    blocks = data.get("blocks")
    if isinstance(blocks, dict):
        defs = data.setdefault("defs", {})
        clashes = [name for name in blocks if isinstance(defs, dict) and name in defs]
        if not isinstance(defs, dict) or clashes:
            raise ContractError([Issue(f"blocks.{name}", f"'{name}' is also a def",
                                       "rename the block or the def: blocks become defs now") for name in clashes]
                                or [Issue("defs", "must be an object of {name: def}")])
        defs.update(blocks)
        del data["blocks"]
        notes += [f"blocks.{name}: now defs.{name}" for name in blocks]
    rewritten, changed = _calls(data)
    if changed:
        data.clear()
        data.update(rewritten)
        notes.append('{"block": b}: now {"call": b}')
    return notes


# -- assets → file inputs -------------------------------------------------------------------------------------------

#: Asset fields a file input keeps as they are.
_FILE_FIELDS = ("caption", "alt", "tags", "max_bytes", "describe", "description")


@rule
def assets_into_file_inputs(data: dict[str, Any]) -> list[str]:
    """``assets: {x: {file | folder, caption, ...}}`` → ``inputs: {x: {type: file, source, caption, ...}}``."""
    assets = data.get("assets")
    inputs = data.get("inputs", {})
    if not isinstance(assets, dict) or not isinstance(inputs, dict):
        return []
    clashes = [Issue(f"assets.{name}", f"'{name}' is also an input", "rename the asset or the input: files are "
                     "inputs now") for name in assets if name in inputs]
    if clashes:
        raise ContractError(clashes, title="assets cannot be rewritten as file inputs")
    for name, spec in assets.items():
        spec = spec if isinstance(spec, dict) else {}
        source = spec.get("file") if spec.get("file") is not None else spec.get("folder")
        inputs[name] = {"type": "file", **({"source": source} if source is not None else {}),
                        **{key: spec[key] for key in _FILE_FIELDS if key in spec}}
    del data["assets"]
    data["inputs"] = inputs
    return [f"assets.{name}: now inputs.{name} (type file)" for name in assets]


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
