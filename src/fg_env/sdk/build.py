"""Build the starting world from a contract: global properties, named entities, the
sampled population, starting links and physics."""
from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Optional, Set, Tuple

from ..entity import Entity
from .contract import MAX_POPULATION, MAX_ROUNDS, Contract, LinkSpec, PopulationSpec
from .effects import EffectRunner
from .errors import RunError
from .expr import ExprError, compile_expr, is_expr, resolve, truthy  # noqa: F401
from .seeds import SeedTree
from .template import compile_template
from .world import SdkWorld
from . import networks as _networks  # noqa: F401  (registers network and keyed-draw functions)

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
        _build_hooks(world)
    except ExprError as exc:
        raise RunError(str(exc), "build") from None
    world.journal.clear()
    world.rng = seeds.rng("run")
    return world


def _build_hooks(world: SdkWorld) -> None:
    """on_create for every entity made at build — once the whole world exists, in creation order —
    unless its type sets on_create_at_build false. Entities the hooks create run their own hooks."""
    contract = world.contract
    if not any(spec.on_create for spec in contract.types.values()):
        return
    runner = EffectRunner(world)
    for entity in list(world.entities.values()):
        if entity.alive and contract.hooks_at_build(entity.entity_type):
            runner.lifecycle("on_create", entity, f"entities.{entity.id}")


def _value(world: SdkWorld, raw: Any, vars: Dict[str, Any]) -> Any:
    # Literal lists and maps are copied, so a run never shares (or mutates) the contract's objects.
    return compile_expr(raw)(world.scope(**vars)) if is_expr(raw) else copy.deepcopy(raw)


def _rounds(world: SdkWorld) -> int:
    clock = world.contract.clock
    if clock.mode == "continuous":
        horizon = _value(world, clock.horizon, {}) if clock.horizon is not None else None
        if horizon is not None and (isinstance(horizon, bool) or not isinstance(horizon, (int, float)) or horizon < 0):
            raise RunError(f"must be a time ≥ 0, got {horizon!r}", "clock.horizon")
        world.horizon = float(horizon) if horizon is not None else None
        if "rounds" not in clock.model_fields_set:
            if world.horizon is None:
                raise RunError("a continuous clock needs a `horizon` (or an explicit `rounds` budget)", "clock")
            return MAX_ROUNDS
    rounds = _value(world, clock.rounds, {})
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
        elif spec.raking is not None:
            raise RunError("raking reweights rows for sampling; give a `count` to draw", f"{path}.raking")
    else:
        if spec.count is None:
            raise RunError("give `count`, `from`, or both", path)
        count = _capped(int(_whole(_value(world, spec.count, {}), f"{path}.count")), f"{path}.count")
        rows = [None] * count
    id_template = compile_template(spec.id, None) if spec.id else None
    name_template = compile_template(spec.name, None) if spec.name else None
    title = spec.type.replace("_", " ").title()
    archetypes = _archetypes(world, spec, len(rows), path)
    declared = world.contract.props_of(spec.type)
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
        props = dict(spec.props)
        archetype = archetypes[n - 1] if archetypes else None
        if archetype is not None:
            props.update(archetype.props)
            if "archetype" in declared:
                props["archetype"] = archetype.name
        created = world.create(spec.type, entity_id, name, props, at, scope, f"{path}[{n}]")
        brief = "\n".join(text for text in (spec.brief, archetype.brief if archetype else None) if text)
        if brief:
            pending_briefs.append((created.id, brief, {"i": n, "row": row}, f"{path}.brief"))
        for m_index, members in enumerate(spec.members):
            _members(world, members, created, row, f"{path}.members[{m_index}]", pending_briefs)


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
    if spec.raking is not None:
        weights = rake(rows, weights, spec.raking.margins, spec.raking.iterations, spec.raking.tolerance, f"{path}.raking")
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
    if spec.rows is not None:
        rows = _value(world, spec.rows, {})
        if not isinstance(rows, list):
            raise RunError(f"`rows` must give a list of edges, got {type(rows).__name__}", f"{path}.rows")
        for position, row in enumerate(rows):
            if not isinstance(row, dict) or "from" not in row or "to" not in row:
                raise RunError(f"edge {position + 1} needs `from` and `to`, got {row!r}", f"{path}.rows")
            ends = []
            for key in ("from", "to"):
                entity = world.entity(row[key])
                if entity is None:
                    raise RunError(f"edge {position + 1}: no entity '{row[key]}'", f"{path}.rows")
                ends.append(entity)
            pair = {"from": ends[0], "to": ends[1], "row": row}
            fields = {name: row[name] for name in world.contract.relations[spec.relation].props if name in row}
            fields.update(_fields(world, spec, pair, path))
            world.link(spec.relation, ends[0], ends[1], row.get("value", _value(world, spec.value, pair)), path, fields)
        return
    if spec.among is None:
        if spec.from_ is None or spec.to is None:
            raise RunError("give `from` and `to`, or `among` with a `graph`", path)
        source, target = _endpoint(world, spec.from_, path), _endpoint(world, spec.to, path)
        pair = {"from": source, "to": target}
        world.link(spec.relation, source, target, _value(world, spec.value, pair), path, _fields(world, spec, pair, path))
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
                _pair_link(world, spec, members[i], members[j], path)
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
    elif graph == "scale_free":
        m = _value(world, spec.m, {}) if spec.m is not None else 2
        if isinstance(m, bool) or not isinstance(m, int) or m < 1:
            raise RunError(f"m must be a whole number ≥ 1, got {m!r}", f"{path}.m")
        pairs = _preferential(n, m, rng)
    elif graph == "blocks":
        if spec.block is None:
            raise RunError("graph blocks needs `block` (an expression over $it giving each member's group)", path)
        groups = [_value(world, spec.block, {"it": member}) for member in members]
        inside = p_value if p_value is not None else 0.3
        between = _value(world, spec.p_between, {}) if spec.p_between is not None else 0.02
        if isinstance(between, bool) or not isinstance(between, (int, float)) or not 0 <= between <= 1:
            raise RunError(f"p_between must be a number from 0 to 1, got {between!r}", f"{path}.p_between")
        pairs = {(i, j) for i in range(n) for j in range(i + 1, n)
                 if rng.random() < (inside if groups[i] == groups[j] else between)}
    elif graph == "lattice":
        side = max(1, math.ceil(math.sqrt(n)))
        pairs = set()
        for i in range(n):
            row_i, col_i = divmod(i, side)
            for j in (i + 1, i + side):
                if j < n and (j == i + side or divmod(j, side)[0] == row_i):
                    pairs.add((i, j))
            del col_i
    elif graph == "star":
        hub_value = _value(world, spec.hub, {}) if spec.hub is not None else (members[0] if members else None)
        hub = world.entity(hub_value)
        if n and (hub is None or hub not in members):
            raise RunError(f"the hub must be one of the {spec.among} members, got {hub_value!r}", f"{path}.hub")
        centre = members.index(hub) if hub is not None else 0
        pairs = {_pair(centre, j) for j in range(n) if j != centre}
    elif graph == "bipartite":
        if spec.with_ is None:
            raise RunError("graph bipartite needs `with` (the other type)", path)
        others = world.entities_of(spec.with_)
        chance = p_value if p_value is not None else min(1.0, (degree or 2) / max(1, len(others)))
        for member in members:
            for other in others:
                if member is not other and rng.random() < chance:
                    _pair_link(world, spec, member, other, path)
        return
    else:
        raise RunError(f"unknown graph '{graph}' (complete, ring, random, small_world, scale_free, blocks, lattice, "
                       "star, bipartite)", f"{path}.graph")
    for i, j in sorted(pairs):
        value = _value(world, spec.value, {})
        world.link(spec.relation, members[i], members[j], value, path,
                   _fields(world, spec, {"from": members[i], "to": members[j]}, path))
        if not world.contract.relations[spec.relation].symmetric:
            world.link(spec.relation, members[j], members[i], value, path,
                       _fields(world, spec, {"from": members[j], "to": members[i]}, path))


def _pair_link(world: SdkWorld, spec: LinkSpec, source: Entity, target: Entity, path: str) -> None:
    pair = {"from": source, "to": target}
    world.link(spec.relation, source, target, _value(world, spec.value, pair), path, _fields(world, spec, pair, path))


def _fields(world: SdkWorld, spec: LinkSpec, pair: Dict[str, Any], path: str) -> Dict[str, Any]:
    """The link fields a `links` entry sets for one pair (values, templates or expressions over $from, $to, $row)."""
    try:
        return {name: resolve(copy.deepcopy(raw), world.scope(**pair)) for name, raw in spec.props.items()}
    except ExprError as exc:
        raise RunError(str(exc), f"{path}.props") from None


def _archetypes(world: SdkWorld, spec: PopulationSpec, count: int, path: str) -> List[Any]:
    """The archetype of each generated entity, in order: exact shares (largest remainder, then shuffled)
    or independent weighted draws."""
    if not spec.mix:
        return []
    weights = []
    for position, archetype in enumerate(spec.mix):
        weight = _value(world, archetype.weight, {})
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0 or not math.isfinite(weight):
            raise RunError(f"weight must be a number ≥ 0, got {weight!r}", f"{path}.mix[{position}].weight")
        weights.append(float(weight))
    total = sum(weights)
    if total <= 0:
        raise RunError("every archetype weight is zero", f"{path}.mix")
    rng = world.seeds.rng("population", path, "mix")
    if not spec.quota:
        return rng.choices(list(spec.mix), weights=weights, k=count)
    exact = [count * w / total for w in weights]
    counts = [int(math.floor(x)) for x in exact]
    for position in sorted(range(len(exact)), key=lambda k: (-(exact[k] - counts[k]), k))[: count - sum(counts)]:
        counts[position] += 1
    assigned = [archetype for archetype, k in zip(spec.mix, counts) for _ in range(k)]
    rng.shuffle(assigned)
    return assigned


def _members(world: SdkWorld, spec: Any, parent: Entity, row: Any, path: str,
             pending_briefs: List[Tuple[str, str, Dict[str, Any], str]]) -> None:
    count = _value(world, spec.count, {"parent": parent, "row": row})
    count = _capped(int(_whole(count, f"{path}.count")), f"{path}.count")
    name_template = compile_template(spec.name, None) if spec.name else None
    title = spec.type.replace("_", " ").title()
    for n in range(1, count + 1):
        vars = {"parent": parent, "row": row, "i": n}
        scope = world.scope(**vars)
        name = name_template.render(scope) if name_template is not None else f"{parent.name} {title} {n}"
        props = dict(spec.props)
        if spec.parent_prop:
            props[spec.parent_prop] = parent.id
        member = world.create(spec.type, None, name, props, parent.location_id, scope, f"{path}[{n}]")
        if spec.link:
            world.link(spec.link, member, parent, 1, path)
        if spec.brief:
            pending_briefs.append((member.id, spec.brief, vars, f"{path}.brief"))


def rake(rows: List[Any], base: Optional[List[float]], margins: Dict[str, Dict[str, float]], iterations: int,
         tolerance: float, path: str) -> List[float]:
    """Iterative proportional fitting: weights whose weighted shares match every margin."""
    weights = list(base) if base is not None else [1.0] * len(rows)
    for column, targets in margins.items():
        total = sum(targets.values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise RunError(f"target shares for '{column}' sum to {total:.6g}, not 1", f"{path}.margins.{column}")
        present = {str(row.get(column)) for row in rows if isinstance(row, dict)}
        missing = [value for value, share in targets.items() if share > 0 and value not in present]
        if missing:
            raise RunError(f"no rows have {column} = {', '.join(missing)}", f"{path}.margins.{column}")
    for _ in range(iterations):
        worst = 0.0
        for column, targets in margins.items():
            totals: Dict[str, float] = {}
            for row, weight in zip(rows, weights):
                key = str(row.get(column)) if isinstance(row, dict) else ""
                totals[key] = totals.get(key, 0.0) + weight
            grand = sum(totals.values())
            if grand <= 0:
                raise RunError("all row weights are zero", path)
            for position, row in enumerate(rows):
                key = str(row.get(column)) if isinstance(row, dict) else ""
                target = targets.get(key)
                if target is None or totals.get(key, 0) <= 0:
                    weights[position] = 0.0 if target is None else weights[position]
                    continue
                factor = target * grand / totals[key]
                worst = max(worst, abs(factor - 1))
                weights[position] *= factor
        if worst < tolerance:
            break
    return weights


def _preferential(n: int, m: int, rng: Any) -> Set[Tuple[int, int]]:
    """Barabási–Albert: each new member links to m existing members chosen by degree."""
    pairs: Set[Tuple[int, int]] = set()
    seed_size = min(n, m + 1)
    for i in range(seed_size):
        for j in range(i + 1, seed_size):
            pairs.add((i, j))
    targets: List[int] = [v for pair in pairs for v in pair] or list(range(seed_size))
    for new in range(seed_size, n):
        chosen: Set[int] = set()
        while len(chosen) < min(m, new):
            chosen.add(rng.choice(targets))
        for old in sorted(chosen):
            pairs.add(_pair(old, new))
            targets.extend((old, new))
    return pairs


def _pair(a: int, b: int) -> Tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def _endpoint(world: SdkWorld, raw: str, path: str) -> Entity:
    value = _value(world, raw, {})
    entity = world.entity(value)
    if entity is None:
        raise RunError(f"no entity '{value}'", path)
    return entity
