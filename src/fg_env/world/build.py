"""Build the starting world from a contract: global properties, named and generated entities, starting links and
physics."""
from __future__ import annotations

import copy
import math
from typing import Any, overload

from ..assets.store import AssetStore
from ..contract import (
    MAX_POPULATION,
    MAX_ROUNDS,
    MAX_STAGE_PASSES,
    MAX_TURN_ACTIONS,
    MAX_TURN_CALLS,
    Contract,
    EntitySpec,
    LinkSpec,
)
from ..effects.runner import EffectRunner
from ..errors import RunError
from ..expr import ExprError, compile_expr, is_expr, resolve, truthy  # noqa: F401
from ..expr.objects import Entity
from ..information.gate import render
from ..physics.world import build_physics
from ..sampling.seeds import SeedTree
from ..stdlib.dates import parse_moment
from . import networks as _networks  # noqa: F401  (registers network and keyed-draw functions)
from .abort import Abort
from .defaults import default_order, world_reads
from .store import World

__all__ = ["build_world"]


def build_world(contract: Contract, inputs: dict[str, Any], seeds: SeedTree, arm: str | None = None,
                assets: AssetStore | None = None) -> World:
    world = World(contract, inputs, seeds, arm)
    if assets is not None:
        world.assets = assets
    world.luck.main = seeds.rng("build")
    pending_briefs: list[tuple[str, str, dict[str, Any], str]] = []
    try:
        world.rounds = _rounds(world)
        world.start = _clock_start(world)
        _count_settings(world)
        world.round = 0
        world.build_space()
        _world_props(world)
        generated = 0
        for entity_id, named in contract.entities.items():
            if named.generates:
                _generate(world, entity_id, named, generated, pending_briefs)
                generated += 1
                continue
            evaluation = world.evaluation
            name = render(world, named.name, {}, viewer=None) if named.name else entity_id  # a template, as generated
            evaluation.create(named.type, entity_id, name, named.props, _placed(world, entity_id, named.at, {}),
                              evaluation.scope(), f"entities.{entity_id}")
            if named.brief:
                pending_briefs.append((entity_id, named.brief, {}, f"entities.{entity_id}.brief"))
        for index, (relation, path, link) in enumerate(contract.starting_links()):
            _links(world, relation, link, path, index, seeds)
        _world_props(world, after_entities=True)
        build_physics(world)
        world.series = {name: [] for name in contract.series_outputs()}
        world.metrics = {name: None for name in contract.series_outputs()}
        # Briefs render once the whole world exists, so they can count and read everything.
        for entity_id, template, vars, path in pending_briefs:
            actor = world.entities[entity_id]
            world.entity_briefs[entity_id] = render(world, template, {"actor": actor, **vars}, viewer=actor,
                                                    subject="actor", path=path).strip()
        _build_hooks(world)
    except ExprError as exc:
        raise RunError(str(exc), "build") from None
    except Abort as refusal:  # nothing to roll back to while building: a full cell is a contract error
        raise RunError(refusal.reason, "build") from None
    world.commit()
    world.luck.main = seeds.rng("run")
    return world


def _build_hooks(world: World) -> None:
    """The events on ``create.<type>`` for every entity made at build — once the whole world exists, in creation order.
    Entities they create fire their own."""
    contract = world.contract
    if not any(event.on.startswith("create.") for event in contract.events):
        return
    runner = EffectRunner(world)
    for entity in list(world.entities.values()):
        if entity.alive:
            runner.lifecycle("create", entity, f"entities.{entity.id}")


def _value(world: World, raw: Any, vars: dict[str, Any]) -> Any:
    # Literal lists and maps are copied, so a run never shares (or mutates) the contract's objects.
    return compile_expr(raw)(world.evaluation.scope(**vars)) if is_expr(raw) else copy.deepcopy(raw)


def _placed(world: World, entity_id: str, raw: Any, vars: dict[str, Any]) -> Any:
    """Where entity ``entity_id`` starts, drawn (`$random_empty(…)`) from a stream of its own."""
    with world.luck.stream("build", "entity", entity_id, "at"):
        return _value(world, raw, vars)


def _rounds(world: World) -> int:
    clock = world.contract.clock
    rounds = _value(world, clock.rounds, {})
    if isinstance(rounds, float) and rounds.is_integer():
        rounds = int(rounds)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise RunError(f"must be a whole number ≥ 1, got {rounds!r}", "clock.rounds")
    if rounds > MAX_ROUNDS:
        raise RunError(f"{rounds:,} rounds is more than the limit of {MAX_ROUNDS:,}", "clock.rounds")
    return rounds


def _clock_start(world: World) -> str | None:
    """``clock.start`` as a calendar date: as written, or read from ``$inputs`` (null leaves the run without dates)."""
    raw = world.contract.clock.start
    if raw is None or not is_expr(raw):
        return raw
    value = _value(world, raw, {})
    if value is None:
        return None
    try:
        parse_moment(value if isinstance(value, str) else "")
    except ValueError:
        raise RunError(f"must give an ISO date like 2026-01-31, got {value!r}", "clock.start") from None
    return value


@overload
def whole_setting(world: World, raw: int | str, path: str, limit: int | None = None) -> int: ...
@overload
def whole_setting(world: World, raw: int | str | None, path: str, limit: int | None = None) -> int | None: ...
def whole_setting(world: World, raw: int | str | None, path: str, limit: int | None = None) -> int | None:
    """A count setting (a stage's `passes`, `max_actions` or `max_calls`, an event's `every`): a literal as written, or
    an expression over $inputs giving a whole number ≥ 1. Inputs never change during a run, so reading it again gives
    the same number."""
    if not isinstance(raw, str):
        return raw
    try:
        value = compile_expr(raw)(world.evaluation.scope())
    except ExprError as exc:
        raise RunError(str(exc), path) from None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RunError(f"must be a whole number ≥ 1, got {value!r}", path)
    if limit is not None and value > limit:
        raise RunError(f"is {value:,}, above the ceiling of {limit:,}", path)
    return value


def _count_settings(world: World) -> None:
    """Expression counts fail at load, not in the round that first reads them."""
    for stage in world.contract.stage_list():
        whole_setting(world, stage.passes, f"stages.{stage.name}.passes", MAX_STAGE_PASSES)
        whole_setting(world, stage.max_actions, f"stages.{stage.name}.max_actions", MAX_TURN_ACTIONS)
        whole_setting(world, stage.max_calls, f"stages.{stage.name}.max_calls", MAX_TURN_CALLS)


def _capped(count: int, path: str) -> int:
    if count > MAX_POPULATION:
        raise RunError(f"{count:,} entities is more than the limit of {MAX_POPULATION:,}", path)
    return count


_ENTITY_FUNCTIONS = frozenset({"entity", "exists", "records", "neighbors", "relation", "linked", "events",
                               "money_held"})


def _needs_entities(world: World, raw: Any) -> bool:
    if not is_expr(raw):
        return False
    compiled = compile_expr(raw)
    return bool(compiled.functions & _ENTITY_FUNCTIONS) or any(
        symbol in world.contract.types for _, symbol in compiled.calls if symbol)


def _world_props(world: World, after_entities: bool = False) -> None:
    """World defaults are evaluated before entities exist, except those that read entities
    (`$sum(tier, ...)`, `$entity(x)`) or read a world property that does, which are evaluated once the world is
    populated. A default reading other world properties (`$world.rates`) is evaluated after them."""
    specs = world.contract.world
    order, _ = default_order({name: spec.default for name, spec in specs.items()})
    late: set[str] = set()
    for name in order:
        if _needs_entities(world, specs[name].default) or world_reads(specs[name].default) & late:
            late.add(name)
    for name in order:
        spec = specs[name]
        if (name in late) != after_entities:
            continue
        try:
            with world.luck.stream("build", "world", name):  # its own luck, which no other default shifts
                value = _value(world, spec.default, {})
        except ExprError as exc:
            raise RunError(str(exc), f"world.{name}") from None
        world.props[name] = world.coerce(spec, value, f"world.{name}")


def _generate(world: World, key: str, spec: EntitySpec, ordinal: int,
              pending_briefs: list[tuple[str, str, dict[str, Any], str]]) -> None:
    """The entities a generator entry makes (``ordinal``: how many generators came before it, whose sampling stream
    it keeps)."""
    path = f"entities.{key}"
    rows: list[Any]
    if spec.from_ is not None:
        rows = _value(world, spec.from_, {})
        if not isinstance(rows, list):
            raise RunError(f"`from` must give a list of rows, got {type(rows).__name__}", path)
        if spec.where:
            rows = [row for row in rows if truthy(_value(world, spec.where, {"row": row}))]
        count = _value(world, spec.count, {}) if spec.count is not None else None
        if count is not None:
            rows = _sample(world, rows, spec, _capped(int(_whole(count, f"{path}.count")), f"{path}.count"), path,
                           ordinal)
    else:
        count = _capped(int(_whole(_value(world, spec.count, {}), f"{path}.count")), f"{path}.count")
        rows = [None] * count
    title = spec.type.replace("_", " ").title()
    for n, row in enumerate(rows, start=1):
        vars = {"i": n, "row": row}
        scope = world.evaluation.scope(**vars)
        if spec.id:  # generated ids and names are world data: the rules' own words
            entity_id = render(world, spec.id, vars, viewer=None)
        elif isinstance(row, dict) and isinstance(row.get("id"), str):
            entity_id = row["id"]
        else:
            entity_id = f"{key}_{n}"
        if spec.name:
            name = render(world, spec.name, vars, viewer=None)
        elif isinstance(row, dict) and isinstance(row.get("name"), str):
            name = row["name"]
        else:
            name = f"{title} {n}"
        created = world.evaluation.create(spec.type, entity_id, name, spec.props,
                                          _placed(world, entity_id, spec.at, vars), scope, f"{path}[{n}]")
        if spec.brief:
            pending_briefs.append((created.id, spec.brief, vars, f"{path}.brief"))


def _whole(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or float(value) != int(value):
        raise RunError(f"must be a whole number ≥ 0, got {value!r}", where)
    return value


def _sample(world: World, rows: list[Any], spec: EntitySpec, count: int, path: str, ordinal: int) -> list[Any]:
    rng = world.seeds.rng("population", f"population[{ordinal}]")  # the stream generators have always drawn from
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
        raise RunError(f"asked for {count} but only {len(rows)} rows qualify",
                       f"{path}.count → lower count or set replace: true")
    if weights is None:
        return rng.sample(rows, count)
    # Efraimidis–Spirakis: weighted sampling without replacement, keeps row order stable.
    keyed = [(rng.random() ** (1.0 / w) if w > 0 else -1.0, i) for i, w in enumerate(weights)]
    keyed.sort(reverse=True)
    chosen = [i for key, i in keyed[:count] if key >= 0]
    if len(chosen) < count:
        raise RunError(f"only {len(chosen)} rows have a positive weight; {count} requested", f"{path}.weight")
    return [rows[i] for i in sorted(chosen)]


def _links(world: World, relation: str, spec: LinkSpec, path: str, index: int, seeds: SeedTree) -> None:
    """The starting links one entry of ``relation`` makes (``index``: its place in the build, whose random stream
    it draws from)."""
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
            fields = {name: row[name] for name in world.contract.relations[relation].props if name in row}
            fields.update(_fields(world, spec, pair, path))
            world.link(relation, ends[0], ends[1], row.get("value", _value(world, spec.value, pair)), path, fields)
        return
    if spec.among is None:
        if spec.from_ is None or spec.to is None:
            raise RunError("give `from` and `to`, or `among` with a `graph`", path)
        source, target = _endpoint(world, spec.from_, path), _endpoint(world, spec.to, path)
        pair = {"from": source, "to": target}
        world.link(relation, source, target, _value(world, spec.value, pair), path,
                   _fields(world, spec, pair, path))
        return
    members: list[Entity] = world.entities_of(spec.among)
    if spec.where:
        members = [m for m in members if truthy(_value(world, spec.where, {"it": m}))]
    rng = seeds.rng("links", index)
    n = len(members)
    degree = _value(world, spec.degree, {}) if spec.degree is not None else None
    per_pair = isinstance(spec.p, str) and is_expr(spec.p) and bool({"from", "to"} & compile_expr(spec.p).roots)
    p_value = _value(world, spec.p, {}) if spec.p is not None and not per_pair else None
    if degree is not None and (isinstance(degree, bool) or not isinstance(degree, (int, float)) or degree < 1):
        raise RunError(f"degree must be a number ≥ 1, got {degree!r}", f"{path}.degree")
    if p_value is not None and (isinstance(p_value, bool) or not isinstance(p_value, (int, float)) or not 0 <= p_value
                                <= 1):
        raise RunError(f"p must be a number from 0 to 1, got {p_value!r}", f"{path}.p")
    degree = int(degree) if degree is not None else None
    directed = not world.contract.relations[relation].symmetric

    def probability(i: int, j: int, default: float) -> float:
        if not per_pair:
            return p_value if p_value is not None else default
        value = _value(world, spec.p, {"from": members[i], "to": members[j]})
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise RunError(f"p must be a number from 0 to 1, got {value!r} for {members[i].id} → {members[j].id}",
                           f"{path}.p")
        return float(value)

    pairs: set[tuple[int, int]] = set()
    graph = spec.graph or "complete"
    if graph == "complete":
        pairs = {(i, j) for i in range(n) for j in range(i + 1, n)}
    elif graph == "ring":
        k = max(1, (degree or 2) // 2)
        pairs = {_pair(i, (i + d) % n) for i in range(n) for d in range(1, k + 1) if n > 1 and i != (i + d) % n}
    elif graph == "random":
        # On a one-way relation every ordered pair is drawn on its own (follows, trusts);
        # on a symmetric relation each unordered pair once. Either way `degree` is the mean number of
        # neighbours, as in every other graph: a member linked either way to another has it as a neighbour.
        default = min(1.0, (degree or 4) / max(1, n - 1))
        if directed:
            default = 1 - math.sqrt(1 - default)
            one_way = [(i, j) for i in range(n) for j in range(n) if i != j and rng.random()
                       < probability(i, j, default)]
            for i, j in one_way:
                _pair_link(world, relation, spec, members[i], members[j], path)
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
                    _pair_link(world, relation, spec, member, other, path)
        return
    else:
        raise RunError(f"unknown graph '{graph}' (complete, ring, random, small_world, scale_free, blocks, lattice, "
                       "star, bipartite)", f"{path}.graph")
    for i, j in sorted(pairs):
        value = _value(world, spec.value, {})
        world.link(relation, members[i], members[j], value, path,
                   _fields(world, spec, {"from": members[i], "to": members[j]}, path))
        if not world.contract.relations[relation].symmetric:
            world.link(relation, members[j], members[i], value, path,
                       _fields(world, spec, {"from": members[j], "to": members[i]}, path))


def _pair_link(world: World, relation: str, spec: LinkSpec, source: Entity, target: Entity, path: str) -> None:
    pair = {"from": source, "to": target}
    world.link(relation, source, target, _value(world, spec.value, pair), path, _fields(world, spec, pair, path))


def _fields(world: World, spec: LinkSpec, pair: dict[str, Any], path: str) -> dict[str, Any]:
    """The link fields a `links` entry sets for one pair (values, templates or expressions over $from, $to, $row)."""
    try:
        return {name: resolve(copy.deepcopy(raw), world.evaluation.scope(**pair)) for name, raw in spec.props.items()}
    except ExprError as exc:
        raise RunError(str(exc), f"{path}.props") from None


def _preferential(n: int, m: int, rng: Any) -> set[tuple[int, int]]:
    """Barabási–Albert: each new member links to m existing members chosen by degree."""
    pairs: set[tuple[int, int]] = set()
    seed_size = min(n, m + 1)
    for i in range(seed_size):
        for j in range(i + 1, seed_size):
            pairs.add((i, j))
    targets: list[int] = [v for pair in pairs for v in pair] or list(range(seed_size))
    for new in range(seed_size, n):
        chosen: set[int] = set()
        while len(chosen) < min(m, new):
            chosen.add(rng.choice(targets))
        for old in sorted(chosen):
            pairs.add(_pair(old, new))
            targets.extend((old, new))
    return pairs


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def _endpoint(world: World, raw: str, path: str) -> Entity:
    value = _value(world, raw, {})
    entity = world.entity(value)
    if entity is None:
        raise RunError(f"no entity '{value}'", path)
    return entity
