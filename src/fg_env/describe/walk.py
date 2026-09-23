"""Walking a contract: every text with its path, where that text acts, and which functions and ops draw chance."""
from __future__ import annotations

import inspect
import re
from functools import lru_cache
from typing import Any, Dict, FrozenSet, Iterator, List, Mapping, Sequence, Set, Tuple

from ..contract import Contract
from ..expr import FUNCTIONS
from ..registry import FAMILIES, OPS, use_key

__all__ = ["dumped", "texts", "effect_nodes", "in_effects", "roles", "calls", "world_reads", "random_functions",
           "random_ops", "draws", "ops_in", "mechanism_kinds"]

_CALL = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_WORLD = re.compile(r"\$world\.([A-Za-z_][A-Za-z0-9_]*)")
#: Sections that build the world before round 1.
_SETUP = frozenset({"world", "types", "entities", "population", "links", "relations", "space"})
#: Sections whose expressions change or steer the world during play.
_RULES = frozenset({"actions", "events", "triggers", "stages", "end", "defs", "blocks", "physics", "invariants",
                    "feeds"})
#: Fields whose text an agent reads: briefs, descriptions, news, outcomes, entries, titles, refusal reasons.
_TEXT_FIELDS = frozenset({"brief", "description", "outcome", "announce", "say", "show", "why", "title", "empty", "invalid"})
#: Parameter fields that shape the tool an agent is offered (choices and bounds); they are rules too.
_TOOL_FIELDS = frozenset({"values", "where", "min", "max"})
#: Sections read as data about the contract, not by the running world: raw mechanism config is already expanded,
#: arm patches apply only when that arm runs, policies are participants, metrics and outputs measure.
_ASIDE = frozenset({"mechanisms", "arms", "policies", "inputs", "metrics", "outputs"})
#: Fields holding effect lists.
_EFFECT_LISTS = frozenset({"do", "otherwise", "then", "else", "on_enter", "on_exit", "on_idle", "on_wake", "on_turn_end",
                           "on_timeout", "on_create", "on_remove"})
#: Type fields whose effects run whenever an entity is created or removed, during play too.
_HOOKS = frozenset({"on_create", "on_remove"})


def dumped(contract: Contract) -> Dict[str, Any]:
    """The parsed contract (mechanisms expanded) as plain data with contract field names."""
    return contract.model_dump(by_alias=True)


def texts(node: Any, path: str = "") -> Iterator[Tuple[str, str]]:
    """Every string in ``node`` (a dumped contract) with its path: ``actions.buy.do[0]``."""
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from texts(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from texts(value, f"{path}[{index}]")


def effect_nodes(node: Any, path: str = "") -> Iterator[Tuple[str, Dict[str, Any]]]:
    """Every object in ``node`` with its path (effect operations are among them)."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from effect_nodes(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from effect_nodes(value, f"{path}[{index}]")


def _parts(path: str) -> List[str]:
    return [part for part in re.split(r"[.\[\]]", path) if part and not part.isdigit()]


def in_effects(path: str) -> bool:
    """Whether ``path`` lies inside an effect list the running world executes."""
    parts = _parts(path)
    return bool(parts) and parts[0] not in _ASIDE and bool(_EFFECT_LISTS.intersection(parts))


def roles(path: str) -> FrozenSet[str]:
    """Where text at ``path`` acts: ``setup``, ``rules`` and/or ``shown`` (to agents); empty for measurement and data."""
    parts = _parts(path)
    if not parts or parts[0] in _ASIDE:
        return frozenset()
    last = parts[-1]
    found: Set[str] = set()
    if parts[0] in ("brief", "views") or last in _TEXT_FIELDS or ("params" in parts and last in _TOOL_FIELDS):
        found.add("shown")
    if parts[0] in _SETUP and last not in _TEXT_FIELDS:
        found.add("setup")
        if _HOOKS.intersection(parts):
            found.add("rules")
    if parts[0] in _RULES and last not in _TEXT_FIELDS:
        found.add("rules")
    return frozenset(found)


def calls(text: str) -> Set[str]:
    """Names of functions an expression text calls (``$shuffle(...)`` → ``shuffle``)."""
    return set(_CALL.findall(text))


def world_reads(text: str) -> Set[str]:
    """World properties an expression text reads (``$world.pot`` → ``pot``)."""
    return set(_WORLD.findall(text))


def _uses_rng(target: Any) -> bool:
    try:
        return "rng" in inspect.getsource(target)
    except (OSError, TypeError):  # no source available (a compiled or dynamically created callable)
        return True


@lru_cache(maxsize=1)
def random_functions() -> FrozenSet[str]:
    """Built-in functions whose implementation draws from the run's random generator (or whose source is unavailable)."""
    return frozenset(name for name, spec in FUNCTIONS.items() if _uses_rng(spec.impl))


@lru_cache(maxsize=1)
def random_ops() -> FrozenSet[str]:
    """Native effect ops — family actions as ``family.action`` — whose implementation draws from the run's random
    generator (or whose source is unavailable)."""
    plain = {name for name, spec in OPS.items() if spec.select is None and _uses_rng(spec.run)}
    actions = {op.name for family in FAMILIES.values() for table in family.actions.values() for op in table.values()
               if _uses_rng(op.run)}
    return frozenset(plain | actions)


def draws(contract: Contract, texts: Sequence[str]) -> bool:
    """Whether evaluating any of ``texts`` may draw randomness (a random function, directly or through a def): reading
    what does not draw changes nothing, so it may be read ahead, or skipped when nobody reads it."""
    drawing, pending = random_functions(), list(texts)
    seen: Set[str] = set()
    while pending:
        called = calls(pending.pop())
        if called & drawing:
            return True
        for name in called - seen:
            seen.add(name)
            if name in contract.defs:
                pending.append(contract.defs[name].expr)
    return False


def ops_in(node: Mapping[str, Any]) -> Set[str]:
    """The native ops an effect object names, a family op as ``family.action``."""
    names = {key for key in node if key in OPS}
    for family in FAMILIES:
        if family in names:
            names.discard(family)
            names.add(f"{family}.{node.get('action')}")
    return names


def mechanism_kinds(contract: Contract) -> Set[str]:
    """What the contract's mechanisms are: each ``family.mode`` and its family."""
    out: Set[str] = set()
    for raw in contract.mechanisms.values():
        key = use_key(raw)
        if key is not None:
            out |= {key, key.split(".", 1)[0]}
    return out
