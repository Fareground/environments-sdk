"""Network measures over relations, and per-key random draws that stay aligned across experiment arms."""
from __future__ import annotations

import math
import random
from collections import deque
from typing import Any, Dict, List, Optional, Set

from .expr import Call, ExprError, charge, function

__all__: List[str] = []


def _relation(call: Call, index: int) -> str:
    kind = call.arg(index)
    world = call.scope.world
    if not isinstance(kind, str) or kind not in getattr(world, "links", {}):
        known = ", ".join(getattr(world, "links", {})) or "none declared"
        raise ExprError(f"${call.name}: '{kind}' is not a declared relation (relations: {known})", call.source)
    return kind


def _id(call: Call, value: Any) -> str:
    if hasattr(value, "entity_type"):
        return str(value.id)
    if isinstance(value, str):
        return value
    raise ExprError(f"${call.name}: expected an entity or id, got {value!r}", call.source)


def _adjacent(call: Call, kind: str) -> Dict[str, Dict[str, int]]:
    world: Any = call.scope.world
    alive = {eid for eid, e in world.entities.items() if e.alive}
    return {a: {b: c for b, c in row.items() if b in alive} for a, row in world.adjacent[kind].items() if a in alive}


@function("degree(entity, relation)", "How many living entities `entity` is linked with by `relation` (either direction).",
          min_args=2, max_args=2)
def _degree(call: Call) -> int:
    kind = _relation(call, 1)
    return len(_adjacent(call, kind).get(_id(call, call.arg(0)), {}))


@function("hops(a, b, relation)", "Fewest links between a and b along `relation` (either direction); null when unreachable.",
          min_args=3, max_args=3)
def _hops(call: Call) -> Optional[int]:
    kind = _relation(call, 2)
    start, goal = _id(call, call.arg(0)), _id(call, call.arg(1))
    graph = _adjacent(call, kind)
    if start == goal:
        return 0
    seen, queue = {start}, deque([(start, 0)])
    while queue:
        node, distance = queue.popleft()
        charge(1, call.source)
        for neighbour in graph.get(node, {}):
            if neighbour == goal:
                return distance + 1
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append((neighbour, distance + 1))
    return None


@function("components(type, relation)", "Groups of entities of `type` connected by `relation`, largest first (lists of entities).",
          min_args=2, max_args=2)
def _components(call: Call) -> List[List[Any]]:
    kind = _relation(call, 1)
    members = call.collection(0)
    ids = {m.id for m in members}
    graph = _adjacent(call, kind)
    by_id = {m.id: m for m in members}
    seen: Set[str] = set()
    groups: List[List[Any]] = []
    for member in members:
        if member.id in seen:
            continue
        group, queue = [], deque([member.id])
        seen.add(member.id)
        while queue:
            node = queue.popleft()
            charge(1, call.source)
            group.append(by_id[node])
            for neighbour in graph.get(node, {}):
                if neighbour in ids and neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        groups.append(group)
    groups.sort(key=len, reverse=True)
    return groups


@function("clustering(entity, relation)", "Share of an entity's neighbour pairs that are linked to each other (0 to 1).",
          min_args=2, max_args=2)
def _clustering(call: Call) -> float:
    kind = _relation(call, 1)
    graph = _adjacent(call, kind)
    neighbours = list(graph.get(_id(call, call.arg(0)), {}))
    k = len(neighbours)
    if k < 2:
        return 0.0
    charge(k * k, call.source)
    closed = sum(1 for i in range(k) for j in range(i + 1, k) if neighbours[j] in graph.get(neighbours[i], {}))
    return closed / (k * (k - 1) / 2)


def _keyed_rng(call: Call, key: Any) -> random.Random:
    world = call.scope.world
    seeds = getattr(world, "seeds", None)
    if seeds is None:
        raise ExprError(f"${call.name} needs a running world", call.source)
    parts = key if isinstance(key, list) else [key]
    return random.Random(seeds.derive("keyed", *[p.id if hasattr(p, "entity_type") else str(p) for p in parts]))


@function("random_for(key)", "Uniform number in [0, 1) fixed by `key` (an entity, text or list): the same key gives the same draw "
          "in every arm of an experiment, however many other draws happen.", min_args=1, max_args=1)
def _random_for(call: Call) -> float:
    return _keyed_rng(call, call.arg(0)).random()


@function("normal_for(key, mean, sd)", "Normal draw fixed by `key` (aligned across experiment arms).", min_args=3, max_args=3)
def _normal_for(call: Call) -> float:
    mean, sd = call.number(1), call.number(2)
    if sd < 0 or not math.isfinite(sd):
        raise ExprError(f"$normal_for: sd must be a number ≥ 0, got {sd}", call.source)
    return _keyed_rng(call, call.arg(0)).gauss(mean, sd)


@function("chance_for(key, p)", "True with probability p, fixed by `key` (aligned across experiment arms).", min_args=2, max_args=2)
def _chance_for(call: Call) -> bool:
    return _keyed_rng(call, call.arg(0)).random() < call.number(1)
