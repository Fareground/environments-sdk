"""Plumbing shared by the social mechanisms: config lookup, ids, and state-versioned caches.

Nothing here holds run state. Caches hang off the world and are keyed by its journal version and
round, so any change (including a rollback) invalidates them, and a restored run starts empty.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Type, TypeVar

from pydantic import BaseModel

from ...entity import Entity
from ..errors import RunError
from ..expr import ExprError, compile_expr
from ..registry import MechanismError, config_data, describe, use_key

__all__ = ["NAME", "props", "cache", "config_of", "uses_of", "only_use", "single_use_check", "eid", "ids", "entity",
           "require_type", "check_expr", "edges", "seat_order", "literal_name_check"]

#: A generated identifier (room, group, faction, item): letters, digits and _, starting with a letter.
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")

_M = TypeVar("_M", bound=BaseModel)

#: Roots every expression may read, wherever it runs.
GLOBAL_ROOTS = frozenset({"inputs", "world", "physics", "clock", "round", "stage", "metrics", "series", "arm",
                          "pending"})


def props(item: Any) -> Dict[str, Any]:
    """An entity's properties, typed for reading any declared value."""
    return item.properties  # type: ignore[no-any-return]


def cache(world: Any, namespace: str) -> Dict[Any, Any]:
    """A scratch dict for derived values, emptied whenever the world changes."""
    store: Dict[str, Any] = world.__dict__.setdefault("_social_cache", {})
    stamp = (world.journal.version, world.round, world.stage)
    if store.get("$stamp") != stamp:
        store.clear()
        store["$stamp"] = stamp
    return store.setdefault(namespace, {})


def config_of(world: Any, name: str, kind: str, model: Type[_M]) -> _M:
    """The validated config of mechanism ``name`` (which must be of ``kind``), parsed once per contract."""
    parsed: Dict[Any, Any] = world.__dict__.setdefault("_social_configs", {})
    key = (id(world.contract), name, kind)
    found = parsed.get(key)
    if found is None:
        raw = world.contract.mechanisms.get(name)
        if not isinstance(raw, Mapping) or use_key(raw) != kind:
            declared = ", ".join(uses_of(world.contract.mechanisms, kind)) or "none declared"
            raise RunError(f"'{name}' is not a declared {describe(kind)} mechanism ({describe(kind)} mechanisms: {declared})",
                           f"mechanisms.{name}")
        found = parsed[key] = model.model_validate(config_data(raw))
    return found  # type: ignore[no-any-return]


def uses_of(mechanisms: Mapping[str, Any], kind: str) -> List[str]:
    return [n for n, use in (mechanisms or {}).items() if use_key(use) == kind]


def only_use(world: Any, kind: str, source: Optional[str]) -> str:
    """The name of the contract's single mechanism of ``kind`` (functions that take no name use it)."""
    names = uses_of(world.contract.mechanisms, kind)
    if len(names) != 1:
        raise ExprError(f"this function needs exactly one `{kind}` mechanism in the contract (found {len(names)})", source)
    return names[0]


def single_use_check(kind: str, contract: Mapping[str, Any]) -> None:
    """Refuse a second mechanism of a kind whose functions address the contract's only one."""
    names = uses_of(contract.get("mechanisms") or {}, kind)
    if len(names) > 1:
        raise MechanismError(f"a contract declares at most one `{kind}` mechanism (found {', '.join(names)})",
                             "merge them into one")


def eid(value: Any, source: Optional[str] = None) -> str:
    if isinstance(value, Entity):
        return value.id
    if isinstance(value, str) and value:
        return str.__str__(value)
    raise ExprError(f"expected an entity or id, got {value!r}", source)


def ids(value: Any, source: Optional[str] = None) -> List[str]:
    """Entity ids from an entity, an id, or a list of either (order kept, duplicates dropped)."""
    if value is None:
        return []
    items: Sequence[Any] = value if isinstance(value, (list, tuple)) else [value]
    return list(dict.fromkeys(eid(item, source) for item in items))


def entity(world: Any, value: Any, where: str, kind: Optional[str] = None) -> Entity:
    """The alive entity ``value`` names, optionally required to be of ``kind`` (subtypes included)."""
    found = world.entities.get(eid(value, where)) if isinstance(value, (str, Entity)) else None
    if found is None or not found.alive:
        raise RunError(f"expected an existing entity, got {value!r}", where)
    if kind is not None and not world.is_a(found.entity_type, kind):
        raise RunError(f"{found.id} is a {found.entity_type}, not a {kind}", where)
    return found  # type: ignore[no-any-return]


def require_type(contract: Mapping[str, Any], type_name: Optional[str], field: str, agent: bool = False) -> None:
    """Fail the expansion unless ``type_name`` is declared (and, with ``agent``, takes turns)."""
    if type_name is None:
        return
    types = contract.get("types") or {}
    if type_name not in types:
        raise MechanismError(f"{field} '{type_name}' is not a declared type", f"types: {', '.join(types) or 'none'}", field)
    if agent and not _is_agent(types, type_name):
        raise MechanismError(f"{field} '{type_name}' is not an agent type", f"set \"agent\": true on {type_name}", field)


def _is_agent(types: Mapping[str, Any], name: str) -> bool:
    seen = set()
    current: Optional[str] = name
    while current is not None and current in types and current not in seen:
        seen.add(current)
        spec = types[current] or {}
        if spec.get("agent"):
            return True
        current = spec.get("extends")
    return False


def check_expr(source: Any, field: str, roots: Sequence[str]) -> None:
    """Fail the expansion when a config expression is malformed or reads a root it cannot have."""
    if not isinstance(source, str):
        return
    try:
        compiled = compile_expr(source)
    except ExprError as exc:
        raise MechanismError(f"{exc.detail}", f"expression: {source}", field) from None
    unknown = set(compiled.roots) - GLOBAL_ROOTS - set(roots)
    if unknown:
        allowed = ", ".join(f"${r}" for r in sorted(set(roots))) or "only the global roots"
        raise MechanismError(f"${sorted(unknown)[0]} is not available here", f"available: {allowed}", field)


def edges(world: Any, relation: str) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """(out, in) adjacency of a relation, cached per state. A symmetric relation lists both ways."""
    if relation not in world.links:
        raise RunError(f"'{relation}' is not a declared relation", f"relations.{relation}")
    found = cache(world, f"edges:{relation}")
    if not found:
        out: Dict[str, List[str]] = {}
        into: Dict[str, List[str]] = {}
        symmetric = world.contract.relations[relation].symmetric
        for a, b in world.links[relation]:
            out.setdefault(a, []).append(b)
            into.setdefault(b, []).append(a)
            if symmetric:
                out.setdefault(b, []).append(a)
                into.setdefault(a, []).append(b)
        found["out"], found["in"] = out, into
    return found["out"], found["in"]


def seat_order(world: Any) -> Dict[str, int]:
    """Entity id → creation position: a deterministic order that survives snapshots."""
    found = cache(world, "seat")
    if not found:
        found.update({entity_id: i for i, entity_id in enumerate(world.entities)})
    return found


def literal_name_check(kind: str, op: str) -> Any:
    """A static check for an op whose main key names a mechanism of ``kind``."""

    def check(checker: Any, effect: Mapping[str, Any], path: str) -> list:
        name = effect.get(op)
        names = uses_of(checker.c.mechanisms, kind)
        if name not in names:
            return [(f"{path}.{op}", f"'{name}' is not a declared {kind} mechanism",
                     f"{kind} mechanisms: {', '.join(names) or 'none declared'}")]
        return []

    return check
