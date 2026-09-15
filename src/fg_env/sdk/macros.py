"""Parse-time macros: repeated contract structure generated from data.

A macro is an object with ``for`` and ``make`` (and ``as``, optionally ``index``). It repeats
``make`` once per value, with ``{name}`` placeholders in strings and keys replaced by that value:

.. code-block:: json

    "stages": [{"for": ["flop", "turn", "river"], "as": "street",
                "make": {"name": "{street}", "actions": ["bet_{street}"]}}],
    "actions": {"bet_{street}": {"for": ["flop", "turn", "river"], "as": "street",
                                 "make": {"by": "player", "do": ["$actor.bet_{street} = true"]}}}

* In a list, a macro becomes one item per value (a ``make`` that is itself a macro nests the loops).
* As a map entry whose key holds a placeholder of the loop, it becomes one entry per value, named
  by the key; under any other key it becomes a list of the made values.
* ``for`` is a list, ``{"range": n}`` / ``{"range": [start, end]}`` / ``{"range": [start, end, step]}``
  (the end excluded, like ``$range``), or a placeholder naming a list from an outer loop.
* ``{street}`` alone in a string keeps the value's type (a number stays a number); inside longer
  text numbers and text are written as they are and lists and maps as JSON. ``{street.cards}`` reads
  a field (``{row.0}`` a list position) and ``{n+1}`` / ``{n-1}`` offset a whole number.
* ``index`` names the 0-based position (``"index": "i"`` → ``{i}``). Placeholders whose name is not
  a loop variable are left alone, so templates like ``{name}`` keep working; ``{{`` stays literal.

Macros are expanded when a contract is read (each imported file on its own, before it is merged),
so checks, loads, runs and experiment workers all see the expanded contract.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

from .errors import ContractError, Issue

__all__ = ["expand_macros", "is_macro", "MAX_MACRO_ITEMS", "MAX_MACRO_DEPTH", "MAX_MACRO_NODES"]

#: Most values all macros in one contract file may generate.
MAX_MACRO_ITEMS = 10_000
#: Deepest nesting of macros inside macros.
MAX_MACRO_DEPTH = 8
#: Most JSON values (strings, numbers, lists, objects) the expanded contract may hold.
MAX_MACRO_NODES = 2_000_000

_KEYS = frozenset({"for", "as", "index", "make"})
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+)*)(?:([+-])(\d+))?\}(?!\})")
_FIX = 'write {"for": [values], "as": "name", "make": {...}}'


class _MacroError(Exception):
    def __init__(self, path: str, message: str, fix: Optional[str] = None):
        super().__init__(message)
        self.path, self.fix = path, fix


def is_macro(value: Any) -> bool:
    """True when ``value`` is a macro object: it has both ``for`` and ``make``, or only macro fields with ``make``
    (a macro missing its ``for``, reported as one). Data with a field named ``make`` (a car's make) is not a macro."""
    if not isinstance(value, Mapping) or "make" not in value:
        return False
    return "for" in value or (len(value) > 1 and set(value) <= _KEYS)


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
        self.issues: List[Issue] = []
        self.items = 0
        self.nodes = 0

    # -- walking -----------------------------------------------------------------

    def walk(self, value: Any, env: Dict[str, Any], path: str, depth: int) -> Any:
        self.nodes += 1
        if self.nodes > MAX_MACRO_NODES:
            raise ContractError([Issue(path or "(contract)", f"macros would make the contract larger than {MAX_MACRO_NODES:,} values",
                                       "generate less, or split the environment")])
        if isinstance(value, str):
            return self.text(value, env, path) if env else value
        if isinstance(value, list):
            out: List[Any] = []
            for index, item in enumerate(value):
                where = f"{path}[{index}]"
                if is_macro(item):
                    out.extend(self.attempt(lambda item=item, where=where: [v for _, v in self.loop(item, env, where, depth)], []))
                else:
                    out.append(self.walk(item, env, where, depth))
            return out
        if isinstance(value, Mapping):
            if is_macro(value):
                self.issues.append(Issue(path or "(contract)", "a macro makes list items or map entries, not a single value",
                                         "put it inside a list, or under a key such as \"bet_{street}\""))
                return value
            mapped: Dict[str, Any] = {}
            for key, item in value.items():
                where = _join(path, str(key))
                if is_macro(item):
                    self.attempt(lambda key=key, item=item, where=where: self.entries(mapped, key, item, env, where, depth), None)
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

    def put(self, mapped: Dict[str, Any], name: str, value: Any, where: str) -> None:
        if name in mapped:
            self.issues.append(Issue(where, f"'{name}' is given twice (a macro and another entry, or two macros, make it)",
                                     "give each generated entry a distinct name"))
            return
        mapped[name] = value

    def entries(self, mapped: Dict[str, Any], key: str, macro: Mapping[str, Any], env: Dict[str, Any], where: str,
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

    def loop_names(self, macro: Mapping[str, Any], where: str) -> List[str]:
        """Every variable a macro and the macros nested in its ``make`` define."""
        names: List[str] = []
        current: Any = macro
        while is_macro(current):
            names += [current[k] for k in ("as", "index") if isinstance(current.get(k), str)]
            current = current.get("make")
        return names

    def loop(self, macro: Mapping[str, Any], env: Dict[str, Any], where: str, depth: int) -> Iterator[Tuple[Dict[str, Any], Any]]:
        """``(variables, made value)`` for each value of the loop, nested loops flattened."""
        if depth >= MAX_MACRO_DEPTH:
            raise _MacroError(where, f"macros are nested more than {MAX_MACRO_DEPTH} deep", "flatten the loops into data")
        unknown = sorted(set(macro) - _KEYS)
        if unknown:
            raise _MacroError(_join(where, unknown[0]), "is not a macro field",
                              f"a macro takes for, as, index and make: {_FIX}; data with fields named `for` and `make` "
                              "is read as a macro, so rename one of them (e.g. `vehicle_make`)")
        if "for" not in macro:
            raise _MacroError(where, "a macro needs `for`: the values to repeat over",
                              f"{_FIX}; if this is data, not a macro, rename its `make` field (e.g. `vehicle_make`)")
        name = self.variable(macro, "as", env, where, required=True)
        index_name = self.variable(macro, "index", env, where, required=False)
        if index_name is not None and index_name == name:
            raise _MacroError(_join(where, "index"), f"`index` and `as` both name '{name}'", "give the position its own name")
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

    def variable(self, macro: Mapping[str, Any], key: str, env: Dict[str, Any], where: str, required: bool) -> Optional[str]:
        name = macro.get(key)
        if name is None and not required:
            return None
        at = _join(where, key)
        if name is None:
            raise _MacroError(where, "a macro needs `as`: the placeholder name for each value", _FIX)
        if not isinstance(name, str) or not _NAME.match(name):
            raise _MacroError(at, f"must be a name of letters, digits and _, got {json.dumps(name)}", 'e.g. "street"')
        if name in env:
            raise _MacroError(at, f"'{name}' is already a variable of an enclosing macro", "give the inner loop its own name")
        return name

    def values(self, source: Any, env: Dict[str, Any], where: str, depth: int) -> List[Any]:
        if isinstance(source, str):
            resolved = self.text(source, env, where) if env else source
            if not isinstance(resolved, list):
                raise _MacroError(where, f"must be a list, a range or a placeholder giving a list; {json.dumps(source)} "
                                         f"gives {_kind(resolved)}", 'e.g. ["flop", "turn"] or {"range": [1, 4]}')
            return resolved
        if isinstance(source, list):
            return list(self.walk(source, env, where, depth))
        if isinstance(source, Mapping) and set(source) == {"range"}:
            return self.range(source["range"], env, _join(where, "range"))
        raise _MacroError(where, f"must be a list, a range or a placeholder giving a list, got {_kind(source)}",
                          'e.g. ["flop", "turn"], {"range": 3} or {"range": [1, 4]}')

    def range(self, spec: Any, env: Dict[str, Any], where: str) -> List[int]:
        bounds = spec if isinstance(spec, list) else [spec]
        bounds = [self.text(b, env, where) if isinstance(b, str) and env else b for b in bounds]
        if not 1 <= len(bounds) <= 3 or not all(isinstance(b, int) and not isinstance(b, bool) for b in bounds):
            raise _MacroError(where, f"must be a whole number n (0..n-1) or [start, end] or [start, end, step], got {json.dumps(spec)}",
                              'e.g. {"range": [1, 4]} gives 1, 2, 3')
        start, end, step = (0, bounds[0], 1) if len(bounds) == 1 else (bounds[0], bounds[1], bounds[2] if len(bounds) == 3 else 1)
        if step == 0:
            raise _MacroError(where, "the step cannot be 0")
        count = max(0, -(-(end - start) // step))
        if count > MAX_MACRO_ITEMS:
            raise _MacroError(where, f"the range has {count:,} values; macros generate at most {MAX_MACRO_ITEMS:,}")
        return list(range(start, end, step))

    # -- placeholders -------------------------------------------------------------

    def text(self, source: str, env: Dict[str, Any], where: str) -> Any:
        """``source`` with loop placeholders replaced; a lone placeholder gives the value itself."""
        whole = _PLACEHOLDER.fullmatch(source)
        if whole is not None and whole.group(1) in env:
            return copy.deepcopy(self.lookup(whole, env, where))

        def replace(match: "re.Match[str]") -> str:
            if match.group(1) not in env:
                return match.group(0)
            return _written(self.lookup(match, env, where))

        return _PLACEHOLDER.sub(replace, source)

    def lookup(self, match: "re.Match[str]", env: Dict[str, Any], where: str) -> Any:
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
                raise _MacroError(where, f"{match.group(0)}: only a whole number can be offset; {reached} is {_kind(value)}")
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
