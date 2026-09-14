"""Build the starting world from a contract: global properties, named entities, the
sampled population, starting links and physics."""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from ..entity import Entity
from .contract import MAX_POPULATION, MAX_ROUNDS, Contract, LinkSpec, PopulationSpec
from .errors import RunError
from .expr import ExprError, compile_expr, is_expr, truthy  # noqa: F401
from .seeds import SeedTree
from .template import compile_template
from .world import SdkWorld

__all__ = ["build_world"]


def build_world(contract: Contract, inputs: Dict[str, Any], seeds: SeedTree, arm: Optional[str] = None) -> SdkWorld:
    world = SdkWorld(contract, inputs, seeds, arm)
    world.rng = seeds.rng("build")
    pending_briefs: List[Tuple[str, str, Dict[str, Any], str]] = []
    try:
        world.rounds = _rounds(world)
        world.round = 0
        _world_props(world)
        for entity_id, named in contract.entities.items():
            world.create(named.type, entity_id, named.name or entity_id, named.props, _value(world, named.at, {}),
                         world.scope(), f"entities.{entity_id}")
            if named.brief:
                pending_briefs.append((entity_id, named.brief, {}, f"entities.{entity_id}.brief"))
        for index, group in enumerate(contract.population):
            _population(world, group, index, pending_briefs)
        for index, link in enumerate(contract.links):
            _links(world, link, index, seeds)
        _world_props(world, after_entities=True)
        world.build_physics()
        world.series = {name: [] for name in contract.metrics}
        world.metrics = {name: None for name in contract.metrics}
        # Briefs render once the whole world exists, so they can count and read everything.
        for entity_id, template, vars, path in pending_briefs:
            actor = world.entities[entity_id]
            try:
                world.entity_briefs[entity_id] = compile_template(template, "actor").render(
                    world.scope(actor=actor, **vars)).strip()
            except ExprError as exc:
                raise RunError(str(exc), path) from None
    except ExprError as exc:
        raise RunError(str(exc), "build") from None
    world.journal.clear()
    world.rng = seeds.rng("run")
    return world


def _value(world: SdkWorld, raw: Any, vars: Dict[str, Any]) -> Any:
    # Literal lists and maps are copied, so a run never shares (or mutates) the contract's objects.
    return compile_expr(raw)(world.scope(**vars)) if is_expr(raw) else copy.deepcopy(raw)


def _rounds(world: SdkWorld) -> int:
    rounds = _value(world, world.contract.clock.rounds, {})
    if isinstance(rounds, float) and rounds.is_integer():
        rounds = int(rounds)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise RunError(f"must be a whole number ≥ 1, got {rounds!r}", "clock.rounds")
    if rounds > MAX_ROUNDS:
        raise RunError(f"{rounds:,} rounds is more than the limit of {MAX_ROUNDS:,}", "clock.rounds")
    return rounds


def _capped(count: int, path: str) -> int:
    if count > MAX_POPULATION:
        raise RunError(f"{count:,} entities is more than the limit of {MAX_POPULATION:,}", path)
    return count


_ENTITY_FUNCTIONS = frozenset({"entity", "exists", "records", "neighbors", "relation", "linked", "events"})


def _needs_entities(world: SdkWorld, raw: Any) -> bool:
    if not is_expr(raw):
        return False
    compiled = compile_expr(raw)
    return bool(compiled.functions & _ENTITY_FUNCTIONS) or any(
        symbol in world.contract.types for _, symbol in compiled.calls if symbol)


def _world_props(world: SdkWorld, after_entities: bool = False) -> None:
    """World defaults are evaluated before entities exist, except those that read entities
    (`$sum(tier, ...)`, `$entity(x)`), which are evaluated once the world is populated."""
    for name, spec in world.contract.world.items():
        if _needs_entities(world, spec.default) != after_entities:
            continue
        try:
            value = _value(world, spec.default, {})
        except ExprError as exc:
            raise RunError(str(exc), f"world.{name}") from None
        world.props[name] = world._coerce(spec, value, f"world.{name}")


def _population(world: SdkWorld, spec: PopulationSpec, index: int,
                pending_briefs: List[Tuple[str, str, Dict[str, Any], str]]) -> None:
    path = f"population[{index}]"
    rows: List[Any]
    if spec.from_ is not None:
        rows = _value(world, spec.from_, {})
        if not isinstance(rows, list):
            raise RunError(f"`from` must give a list of rows, got {type(rows).__name__}", path)
        if spec.where:
            rows = [row for row in rows if truthy(_value(world, spec.where, {"row": row}))]
        count = _value(world, spec.count, {}) if spec.count is not None else None
        if count is not None:
            rows = _sample(world, rows, spec, _capped(int(_whole(count, f"{path}.count")), f"{path}.count"), path)
    else:
        if spec.count is None:
            raise RunError("give `count`, `from`, or both", path)
        count = _capped(int(_whole(_value(world, spec.count, {}), f"{path}.count")), f"{path}.count")
        rows = [None] * count
    id_template = compile_template(spec.id, None) if spec.id else None
    name_template = compile_template(spec.name, None) if spec.name else None
    title = spec.type.replace("_", " ").title()
    for n, row in enumerate(rows, start=1):
        vars = {"i": n, "row": row}
        scope = world.scope(**vars)
        if id_template is not None:
            entity_id = id_template.render(scope)
        elif isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"] not in world.entities:
            entity_id = row["id"]
        else:
            entity_id = None
        if name_template is not None:
            name = name_template.render(scope)
        elif isinstance(row, dict) and isinstance(row.get("name"), str):
            name = row["name"]
        else:
            name = f"{title} {n}"
        at = _value(world, spec.at, vars)
        created = world.create(spec.type, entity_id, name, spec.props, at, scope, f"{path}[{n}]")
        if spec.brief:
            pending_briefs.append((created.id, spec.brief, {"i": n, "row": row}, f"{path}.brief"))


def _whole(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or float(value) != int(value):
        raise RunError(f"must be a whole number ≥ 0, got {value!r}", where)
    return value


def _sample(world: SdkWorld, rows: List[Any], spec: PopulationSpec, count: int, path: str) -> List[Any]:
    rng = world.seeds.rng("population", path)
    weights = None
    if spec.weight:
        weights = []
        for row in rows:
            w = _value(world, spec.weight, {"row": row})
            if isinstance(w, bool) or not isinstance(w, (int, float)) or w < 0 or not math.isfinite(w):
                raise RunError(f"row weight must be a number ≥ 0, got {w!r}", f"{path}.weight")
            weights.append(float(w))
    if spec.replace:
        if not rows:
            raise RunError("no rows to sample from", path)
        if weights is not None and sum(weights) <= 0:
            raise RunError("all row weights are zero", f"{path}.weight")
        return rng.choices(rows, weights=weights, k=count)
    if count > len(rows):
        raise RunError(f"asked for {count} but only {len(rows)} rows qualify", f"{path}.count → lower count or set replace: true")
    if weights is None:
        return rng.sample(rows, count)
    # Efraimidis–Spirakis: weighted sampling without replacement, keeps row order stable.
    keyed = [(rng.random() ** (1.0 / w) if w > 0 else -1.0, i) for i, w in enumerate(weights)]
    keyed.sort(reverse=True)
    chosen = [i for key, i in keyed[:count] if key >= 0]
    if len(chosen) < count:
        raise RunError(f"only {len(chosen)} rows have a positive weight; {count} requested", f"{path}.weight")
    return [rows[i] for i in sorted(chosen)]


def _links(world: SdkWorld, spec: LinkSpec, index: int, seeds: SeedTree) -> None:
    path = f"links[{index}]"
    if spec.among is None:
        if spec.from_ is None or spec.to is None:
            raise RunError("give `from` and `to`, or `among` with a `graph`", path)
        world.link(spec.relation, _endpoint(world, spec.from_, path), _endpoint(world, spec.to, path),
                   _value(world, spec.value, {}), path)
        return
    members: List[Entity] = world.entities_of(spec.among)
    if spec.where:
        members = [m for m in members if truthy(_value(world, spec.where, {"it": m}))]
    rng = seeds.rng("links", index)
    n = len(members)
    degree = _value(world, spec.degree, {}) if spec.degree is not None else None
    per_pair = isinstance(spec.p, str) and is_expr(spec.p) and bool({"from", "to"} & compile_expr(spec.p).roots)
    p_value = _value(world, spec.p, {}) if spec.p is not None and not per_pair else None
    if degree is not None and (isinstance(degree, bool) or not isinstance(degree, (int, float)) or degree < 1):
        raise RunError(f"degree must be a number ≥ 1, got {degree!r}", f"{path}.degree")
    if p_value is not None and (isinstance(p_value, bool) or not isinstance(p_value, (int, float)) or not 0 <= p_value <= 1):
        raise RunError(f"p must be a number from 0 to 1, got {p_value!r}", f"{path}.p")
    degree = int(degree) if degree is not None else None
    directed = not world.contract.relations[spec.relation].symmetric

    def probability(i: int, j: int, default: float) -> float:
        if not per_pair:
            return p_value if p_value is not None else default
        value = _value(world, spec.p, {"from": members[i], "to": members[j]})
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise RunError(f"p must be a number from 0 to 1, got {value!r} for {members[i].id} → {members[j].id}", f"{path}.p")
        return float(value)

    pairs: Set[Tuple[int, int]] = set()
    graph = spec.graph or "complete"
    if graph == "complete":
        pairs = {(i, j) for i in range(n) for j in range(i + 1, n)}
    elif graph == "ring":
        k = max(1, (degree or 2) // 2)
        pairs = {_pair(i, (i + d) % n) for i in range(n) for d in range(1, k + 1) if n > 1 and i != (i + d) % n}
    elif graph == "random":
        # On a one-way relation every ordered pair is drawn on its own (follows, trusts);
        # on a symmetric relation each unordered pair once.
        default = min(1.0, (degree or 4) / max(1, n - 1))
        if directed:
            one_way = [(i, j) for i in range(n) for j in range(n) if i != j and rng.random() < probability(i, j, default)]
            for i, j in one_way:
                world.link(spec.relation, members[i], members[j], _value(world, spec.value, {"from": members[i], "to": members[j]}), path)
            return
        pairs = {(i, j) for i in range(n) for j in range(i + 1, n) if rng.random() < probability(i, j, default)}
    elif graph == "small_world":
        if per_pair:
            raise RunError("a per-pair p ($from, $to) works with graph random", f"{path}.p")
        k = max(1, (degree or 4) // 2)
        p = p_value if p_value is not None else 0.1
        ring = sorted({_pair(i, (i + d) % n) for i in range(n) for d in range(1, k + 1) if n > 1 and i != (i + d) % n})
        for a, b in ring:
            if rng.random() < p and n > 2:
                options = [c for c in range(n) if c != a and _pair(a, c) not in pairs]
                if options:
                    b = rng.choice(options)
            pairs.add(_pair(a, b))
    else:
        raise RunError(f"unknown graph '{graph}' (complete, ring, random, small_world)", f"{path}.graph")
    for i, j in sorted(pairs):
        value = _value(world, spec.value, {})
        world.link(spec.relation, members[i], members[j], value, path)
        if not world.contract.relations[spec.relation].symmetric:
            world.link(spec.relation, members[j], members[i], value, path)


def _pair(a: int, b: int) -> Tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def _endpoint(world: SdkWorld, raw: str, path: str) -> Entity:
    value = _value(world, raw, {})
    entity = world.entity(value)
    if entity is None:
        raise RunError(f"no entity '{value}'", path)
    return entity
