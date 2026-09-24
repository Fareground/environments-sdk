"""Forks: continue a run from a moment under changes — what if, from round 10, the other arm applied?

``env.fork(arm=..., inputs=..., patch=..., contract=..., seed=..., effects=[...])`` continues a live run
(between rounds); ``fg_env.fork(contract, snapshot, ...)`` continues a stored snapshot. The state is
kept — entities, properties, links, records, the log, the clock — and the new rules run from here.

What the state cannot follow is refused, each with its fix: a type, property, relation or record
that is gone while the state still holds some of it; values the new declarations refuse; physics
variables that are gone; a round budget the run is already past. What
the new contract adds starts from its declaration (new properties get their defaults, new metrics
start sampling). A `once` event that already fired keeps that memory only while it is
declared unchanged; an edited one counts as new.
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..api import ContractLike, _merge, apply_arm, contract_source, default_data_dir, located, parse
from ..checks import BASE, _Checker, check_contract, parse_contract
from ..contract import Contract, PropSpec
from ..contract.inputs import resolve_inputs
from ..errors import ContractError, Issue, RunError, SnapshotError
from ..expr import ExprError, compile_expr, is_expr
from ..sampling.seeds import SeedTree
from ..world.build import _rounds
from ..world.defaults import default_order
from ..world.links import _fields as link_fields
from ..world.live import Abort, SdkWorld, _copy
from .snapshot import KEEP_ARM, decode, encode, matching_contract, recording_start, restore_state, take_snapshot

if TYPE_CHECKING:
    from ..runtime.env import Env

__all__ = ["fork", "fork_env"]

#: Most entity ids named in one compatibility problem.
_NAMED_IDS = 5


def fork(contract: ContractLike, snapshot: Mapping[str, Any], *, arm: Any = KEEP_ARM,
         inputs: Mapping[str, Any] | None = None, patch: Mapping[str, Any] | None = None,
         to: ContractLike | None = None, seed: int | None = None, effects: list[Any] | None = None,
         parallel: int = 8, hosts: Any = None, data_dir: str | os.PathLike[str] | None = None) -> Env:
    """A run continuing ``snapshot`` (taken with ``contract``) under changes.

    * ``arm`` — another declared arm (``None`` for none); its patch applies and its inputs are set. Inputs
      the old arm set, and that still hold its values, go back to the contract's defaults first.
    * ``inputs`` — input values from here on (rules reading ``$inputs`` see them; the built world stays).
    * ``patch`` — deep-merged into the contract (objects merge, lists replace), after the arm.
    * ``to`` — a whole replacement contract (its arms and patch apply the same way).
    * ``seed`` — the luck from here on comes from this seed instead of continuing the run's streams.
    * ``effects`` — interventions applied at the fork, atomically, logged as a `fork` event, invariants checked.

    Raises :class:`ContractError` listing everything the state cannot follow, each with a fix.
    """
    from ..runtime.env import Env

    return _fork(Env, contract, snapshot, arm=arm, inputs=inputs, patch=patch, to=to, seed=seed, effects=effects,
                 parallel=parallel, hosts=hosts, data_dir=data_dir)


def fork_env(env: Env, *, arm: Any = KEEP_ARM, inputs: Mapping[str, Any] | None = None,
             patch: Mapping[str, Any] | None = None, contract: ContractLike | None = None,
             seed: int | None = None, effects: list[Any] | None = None) -> Env:
    """``env.fork``: :func:`fork` from the live run's current state (``contract`` is a whole replacement)."""
    from ..host.hosts import hosts_for
    from ..runtime.env import Env

    if arm is KEEP_ARM and not (inputs or patch or contract is not None or seed is not None or effects):
        return env.clone()  # nothing changes: the run as it is, continuing exactly
    if env._in_round:
        raise RunError("changes apply between rounds: finish the round first (env.run(rounds=1)), or copy the run "
                       "as it is with env.clone()", "fork")
    forked = _fork(Env, env.contract, take_snapshot(env), arm=arm, inputs=inputs, patch=patch, to=contract,
                   seed=seed, effects=effects, parallel=env.parallel, hosts=hosts_for(env.world), data_dir=None,
                   unarmed_source=env.origin.unarmed)
    forked.time_limit = env.time_limit
    forked.driver.spec = dict(env.driver.spec)  # the same participants as the run (and env.clone()) play on
    return forked


def _fork(cls: Any, contract: ContractLike, snapshot: Mapping[str, Any], *, arm: Any, inputs: Mapping[str, Any] | None,
          patch: Mapping[str, Any] | None, to: ContractLike | None, seed: int | None,
          effects: list[Any] | None, parallel: int, hosts: Any, data_dir: Any,
          unarmed_source: Contract | None = None) -> Env:
    old, unarmed = matching_contract(contract, snapshot)
    if "part_way" in snapshot:
        raise SnapshotError(f"this snapshot was taken part-way through round {snapshot.get('round')}, and changes "
                            "apply between rounds: restore it (fg_env.Env.restore), finish the round with "
                            "env.run(rounds=1), then fork the run (env.fork(...))")
    if unarmed_source is not None:
        unarmed = unarmed_source
    base = parse(to) if to is not None else unarmed
    old_arm = snapshot.get("arm")
    new_arm = old_arm if arm is KEEP_ARM else arm
    if new_arm is not None and new_arm not in base.arms:
        raise ContractError([Issue("arm", f"'{new_arm}' is not a declared arm",
                                   f"arms: {', '.join(base.arms) or 'none'}")],
                            title="the fork cannot be made")
    # Continuing the same arm keeps the current rules, including earlier patches.
    # A different arm or replacement contract deliberately selects a new rule base.
    new = old if to is None and new_arm == old_arm else (apply_arm(base, new_arm) if new_arm is not None else base)
    if patch:
        new = located(parse_contract(_merge(contract_source(new), dict(patch))), new._folder)
    problems = [issue for issue in check_contract(new) if issue.severity == "error"]
    if problems:
        raise ContractError(problems, title="the forked contract is invalid")
    resolved = resolve_inputs(new, _inputs(unarmed, base, snapshot, old_arm, new_arm, inputs),
                              default_data_dir(new, data_dir) or default_data_dir(unarmed))
    issues = compatibility(old, new, snapshot)
    if effects:
        checker = _Checker(new)
        checker.effects(list(effects), "fork.effects", set(BASE), {})
        issues += [issue for issue in checker.issues if issue.severity == "error"]
    if issues:
        raise ContractError(issues, title="the run cannot continue under these changes")
    env = _restore(cls, old, new, snapshot, resolved, new_arm, parallel)
    env.origin.unarmed = base
    if hosts is not None:
        from ..host.hosts import bind

        bind(env, hosts)
    if seed is not None:
        tree = SeedTree(seed)
        env.seed, env.seeds, env.world.seeds = seed, tree, tree
        env.world.rng = tree.rng("fork", env.world.round)
    world = env.world
    world.emit("fork", "", to=(), data={"arm": new_arm, "inputs": sorted(inputs or {}), "patch": sorted(patch or {}),
                                        "contract": to is not None, "seed": seed, "effects": len(effects or [])})
    world.journal.clear()
    env._emitted = len(world.log)
    if effects:
        env._atomic(list(effects), {}, "fork.effects")
    env._check_invariants("fork")
    env._emitted = len(world.log)
    env.origin.base = take_snapshot(env)
    # The fork's changes are not in its build, so a recording of it replays from here.
    env.origin.start = recording_start(env.origin.base) if world.exposures is not None else None
    return env


def _inputs(unarmed: Contract, base: Contract, snapshot: Mapping[str, Any], old_arm: str | None,
            new_arm: str | None, given: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = {key: value for key, value in decode(snapshot["inputs"]).items() if key in base.inputs}
    if new_arm != old_arm:
        for key, value in (unarmed.arms[old_arm].inputs if old_arm in unarmed.arms else {}).items():
            if key in merged and merged[key] == value:  # set by the old arm: back to the contract's default
                del merged[key]
        merged.update(base.arms[new_arm].inputs if new_arm is not None else {})
    merged.update(given or {})
    return merged


# -- compatibility ---------------------------------------------------------------------------------------


def compatibility(old: Contract, new: Contract, snapshot: Mapping[str, Any]) -> list[Issue]:
    """Everything in ``snapshot``'s state that ``new`` cannot hold, each with its fix."""
    issues: list[Issue] = []
    probe = SdkWorld(new, decode(snapshot["inputs"]), SeedTree(0))
    missing_types: dict[str, list[str]] = {}
    for row in snapshot["entities"]:
        kind = row["type"]
        if kind not in new.types:
            missing_types.setdefault(kind, []).append(row["id"])
            continue
        _values(issues, new.props_of(kind), decode(row["props"]), f"types.{kind}.props", row["id"], probe)
        if row.get("at") is not None:
            try:
                probe._check_location(row["at"], "space")
            except RunError as exc:
                issues.append(Issue("space", f"{row['id']} stands at {row['at']!r}: {exc.args[0].split(': ', 1)[-1]}",
                                    "keep a space that holds every current position"))
    for kind, ids in missing_types.items():
        issues.append(Issue(f"types.{kind}", f"is gone, but {len(ids)} entities of it exist ({_named(ids)})",
                            "keep the type, or remove those entities before forking"))
    _values(issues, new.world, decode(snapshot["props"]), "world", "the world", probe)
    for kind, edges in snapshot["links"].items():
        if not edges:
            continue
        if kind not in new.relations:
            issues.append(Issue(f"relations.{kind}", f"is gone, but {len(edges)} links of it exist",
                                "keep the relation"))
            continue
        if kind in old.relations and old.relations[kind].symmetric != new.relations[kind].symmetric:
            issues.append(Issue(f"relations.{kind}.symmetric", "cannot change while links of it exist",
                                "keep `symmetric` as it was"))
        held = {name for row in edges if len(row) > 3 for name in decode(row[3])}
        for name in sorted(held - set(new.relations[kind].props)):
            issues.append(Issue(f"relations.{kind}.props.{name}", "is gone, but links hold values for it",
                                "keep the link field"))
    _records(issues, new, snapshot)
    _physics(issues, new, snapshot)
    _clock(issues, new, snapshot, probe)
    _pending(issues, old, new, snapshot)
    return issues


def _pending(issues: list[Issue], old: Contract, new: Contract, snapshot: Mapping[str, Any]) -> None:
    """Pending code survives a fork, even when its originating event or action is replaced."""
    before, after = _Checker(old), _Checker(new)
    for index, (_, _, encoded) in enumerate(snapshot["scheduled"]):
        item = decode(encoded)
        effects = item.get("effects", [])
        if not effects:
            continue
        roots = set(BASE) | set(item.get("vars", {}))
        path = f"scheduled[{index}].effects"
        # Captures no longer carry the original action's parameter/type declarations.
        # Compare checks so missing authoring metadata cannot reject a valid continuation.
        before.issues.clear()
        after.issues.clear()
        before.effects(effects, path, roots, {})
        after.effects(effects, path, roots, {})
        existing = {(issue.path, issue.message) for issue in before.issues if issue.severity == "error"}
        for issue in after.issues:
            if issue.severity == "error" and (issue.path, issue.message) not in existing:
                issues.append(Issue(issue.path,
                                    f"pending rule from {item.get('path', 'an earlier rule')}: {issue.message}",
                                    "keep its dependencies until the pending work finishes" +
                                    (f"; {issue.fix}" if issue.fix else "")))


def _values(issues: list[Issue], specs: Mapping[str, PropSpec], values: Mapping[str, Any], path: str, owner: str,
            probe: SdkWorld) -> None:
    for prop, value in values.items():
        spec = specs.get(prop)
        if spec is None:
            issues.append(Issue(f"{path}.{prop}", f"is gone, but {owner} holds a value for it",
                                "keep the property (the state still has it)"))
            continue
        problem = _refused(probe, spec, value)
        if problem:
            issues.append(Issue(f"{path}.{prop}", f"{owner} holds {_shown(value)}, which the new declaration refuses: "
                                                  f"{problem}", "keep a declaration that accepts the current value"))


def _refused(probe: SdkWorld, spec: PropSpec, value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if spec.min is not None and value < spec.min:
            return f"below the minimum {spec.min:g}"
        if spec.max is not None and value > spec.max:
            return f"above the maximum {spec.max:g}"
    try:
        probe._coerce(spec, value, "fork")
    except RunError as exc:
        return str(exc).split(": ", 1)[-1]
    return None


def _records(issues: list[Issue], new: Contract, snapshot: Mapping[str, Any]) -> None:
    from ..checks.roots import ENTRY_FIELDS

    for name, rows in snapshot["records"].items():
        if not rows:
            continue
        spec = new.records.get(name)
        if spec is None:
            issues.append(Issue(f"records.{name}", f"is gone, but it holds {len(rows)} entries", "keep the record"))
            continue
        held: set[str] = {key for row in rows for key in row if not key.startswith("$")} - set(ENTRY_FIELDS)
        for field in sorted(held - set(spec.fields)):
            issues.append(Issue(f"records.{name}.fields.{field}", "is gone, but entries hold values for it",
                                "keep the field"))


def _physics(issues: list[Issue], new: Contract, snapshot: Mapping[str, Any]) -> None:
    held = snapshot.get("physics")
    if not held:
        return
    names = sorted((held.get("variables") or {}) if isinstance(held.get("variables"), dict)
                   else [var.get("name") for var in held.get("variables") or []])
    if new.physics is None:
        issues.append(Issue("physics", "is gone, but the run holds physics state", "keep the physics section"))
        return
    for name in names:
        if name not in new.physics.vars:
            issues.append(Issue(f"physics.vars.{name}", "is gone, but the run holds its value", "keep the variable"))


def _clock(issues: list[Issue], new: Contract, snapshot: Mapping[str, Any], probe: SdkWorld) -> None:
    try:
        rounds = _rounds(probe)
    except (RunError, ExprError) as exc:
        issues.append(Issue("clock.rounds", str(exc)))
        return
    if rounds < snapshot["round"]:
        issues.append(Issue("clock.rounds", f"is {rounds}, but the run is already at round {snapshot['round']}",
                            f"allow at least {snapshot['round']} rounds"))


# -- rebuilding ------------------------------------------------------------------------------------------


def _restore(cls: Any, old: Contract, new: Contract, snapshot: Mapping[str, Any], inputs: dict[str, Any],
             arm: str | None, parallel: int) -> Env:
    data = dict(snapshot)
    data["inputs"], data["arm"] = encode(inputs), arm
    data["fired_once"] = _remap(old.events, new.events, snapshot["fired_once"])
    armed = snapshot["armed"]
    data["armed"] = {str(j): armed[str(i)] for i, j in _pairs(old.events, new.events, [int(k) for k in armed])}
    series = decode(snapshot["series"])
    length = max((len(values) for values in series.values()), default=0)
    data["series"] = encode({name: series.get(name, [None] * length) for name in new.metrics})
    metrics = decode(snapshot["metrics"])
    data["metrics"] = encode({name: metrics.get(name) for name in new.metrics})
    env = restore_state(cls, new, data, parallel)
    world = env.world
    world.rounds = _rounds(world)
    _fill(env, new)
    _physics_params(env, old, new)
    if env.status == "completed" and env.ended_by == "rounds" and world.round < world.rounds:
        env.status, env.ended_by = "running", None
        if world.log and world.log[-1].kind == "end":  # the run is not over after all
            world.log.pop()
            world._seq -= 1
    world.journal.clear()
    world.touch()
    return env


def _fill(env: Env, new: Contract) -> None:
    """Give every entity and the world the properties the new contract adds, from their declarations."""
    world = env.world
    for entity in world.entities.values():
        for prop, spec in new.props_of(entity.entity_type).items():
            if prop not in entity.properties:
                entity.properties[prop] = _default(world, spec, f"types.{entity.entity_type}.props.{prop}", it=entity)
    order, _ = default_order({prop: spec.default for prop, spec in new.world.items()})
    for prop in order:
        if prop not in world.props:
            world.props[prop] = _default(world, new.world[prop], f"world.{prop}")
    for kind, edges in world.links.items():
        declared = new.relations[kind].props
        if not declared:
            continue
        table = world.link_fields.setdefault(kind, {})
        for key in edges:
            held = table.get(key, {})
            if set(held) != set(declared):  # fields the new contract adds start from their defaults
                table[key] = {**(link_fields(world, kind, key[0], key[1], None, {}, f"relations.{kind}") or {}), **held}


def _default(world: SdkWorld, spec: PropSpec, path: str, **vars: Any) -> Any:
    raw = spec.default
    try:
        value = compile_expr(raw)(world.scope(**vars)) if is_expr(raw) else _copy(raw)
    except ExprError as exc:
        raise RunError(str(exc), f"{path}.default") from None
    try:
        return world._coerce(spec, value, path)
    except Abort as refusal:  # a new property's default outside its own bounds is a contract error
        raise RunError(refusal.reason, f"{path}.default") from None


def _physics_params(env: Env, old: Contract, new: Contract) -> None:
    """Params whose declaration changed take the new value; unchanged ones keep what the run holds."""
    model, spec = env.world.physics, new.physics
    if model is None or spec is None:
        return
    scope = env.world.scope()
    before = old.physics.params if old.physics is not None else {}
    for name, raw in spec.params.items():
        if name in before and before[name] == raw and name in model.params:
            continue
        try:
            value = compile_expr(raw)(scope) if is_expr(raw) else raw
        except ExprError as exc:
            raise RunError(str(exc), f"physics.params.{name}") from None
        model.params[name] = float(value)
    for name in spec.read:
        model.params.setdefault(name, 0.0)


def _pairs(old: Sequence[Any], new: Sequence[Any], indices: Sequence[int]) -> list[tuple[int, int]]:
    """``(old index, new index)`` for each listed old item still declared unchanged in ``new``."""
    dumps = [item.model_dump(by_alias=True) for item in new]
    used: set[int] = set()
    out: list[tuple[int, int]] = []
    for index in indices:
        if not 0 <= index < len(old):
            continue
        wanted = old[index].model_dump(by_alias=True)
        same = [j for j, dump in enumerate(dumps) if dump == wanted and j not in used]
        if same:
            j = index if index in same else same[0]
            used.add(j)
            out.append((index, j))
    return out


def _remap(old: Sequence[Any], new: Sequence[Any], indices: Sequence[int]) -> list[int]:
    return sorted(j for _, j in _pairs(old, new, indices))


def _named(ids: Sequence[str]) -> str:
    shown = ", ".join(ids[:_NAMED_IDS])
    return shown + (f" and {len(ids) - _NAMED_IDS} more" if len(ids) > _NAMED_IDS else "")


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."
