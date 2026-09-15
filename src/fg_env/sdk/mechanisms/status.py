"""Status effects: named, timed conditions on entities — poison, stun, shield, curse.

.. code-block:: json

    "conditions": {"kind": "status_effects", "on": "unit", "statuses": {
        "poison": {"duration": 3, "stacking": "refresh", "max_stacks": 3, "tick": ["$it.hp -= 2 * $stacks"]},
        "stun": {"duration": 1, "blocks": ["attack", "cast"]},
        "shield": {"duration": 2, "modifiers": {"armor": 3}, "immune": ["poison"]}}}

State lives in one map property per carrier (``$it.conditions``)::

    {"poison": {"stacks": 2, "since": 3, "until": 6, "source": "orc_1"}}

Timing: a status applied in round r with duration d is active from that moment through round r + d
and expires at the end of that round. It ticks once in each of those d rounds (not in the round it
was applied), so ``stun`` for 1 blocks the target's next round. ``duration: null`` lasts until cleansed.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Tuple, Union

from pydantic import Field

from ...entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, function, truthy
from ..registry import MechanismError, effect_op, mechanism
from ..template import compile_template
from . import _common as common
from ._common import Config, Effects, ModifierSpec, Number

__all__ = ["StatusDef", "StatusConfig", "active_stacks"]

KIND = "status_effects"
#: ``apply_status`` rounds when the apply effect gives none: the status's own duration.
DEFAULT_ROUNDS = object()


class StatusDef(Config):
    """One named status."""

    description: str = ""
    duration: Optional[int] = Field(1, ge=1, description="Rounds it lasts after the round it is applied in; null = until cleansed.")
    stacking: Literal["refresh", "extend", "independent", "ignore"] = Field(
        "refresh", description="Applying it again: refresh (add a stack, reset the timer), extend (add a stack and the "
                               "duration), independent (each application its own timer), ignore (no effect while active).")
    max_stacks: int = Field(1, ge=1)
    tick: Effects = Field(default_factory=list, description="Effects each round it is active ($it, $stacks, $source).")
    modifiers: Dict[str, Union[Number, ModifierSpec]] = Field(
        default_factory=dict, description="{prop: add | {add, mul}} per stack, read with $effective(entity, prop).")
    blocks: Union[Literal["all"], List[str]] = Field(default_factory=list, description="Actions the carrier cannot take while it is active.")
    blocked_why: str = Field("", description="Why a blocked action is refused.")
    immune: List[str] = Field(default_factory=list, description="Statuses that cannot be applied while this one is active.")
    unless: Optional[str] = Field(None, description="Expression over $it: when true the status cannot be applied to it.")
    on_apply: Effects = Field(default_factory=list, description="Effects each time it is applied ($it, $stacks, $source).")
    on_expire: Effects = Field(default_factory=list, description="Effects when it runs out ($it, $stacks, $source).")
    say: str = Field("", description="News when it is applied (template over $it, $stacks).")
    expire_say: str = Field("", description="News when it runs out (template over $it).")
    cleansable: bool = Field(True, description="Removed by a cleanse of 'all' (a cleanse naming it always removes it).")


class StatusConfig(Config):
    """Named statuses on entities of some types."""

    on: Union[str, List[str]] = Field(..., description="Type(s) that can carry these statuses (subtypes included).")
    statuses: Dict[str, StatusDef] = Field(
        ..., description="{name: {duration, stacking, max_stacks, tick, modifiers, blocks, blocked_why, immune, unless, "
                         "on_apply, on_expire, say, expire_say, cleansable}}. Durations count rounds after the round "
                         "of application; tick effects see $it (carrier), $stacks and $source.")
    tick_phase: Literal["start", "end"] = Field("start", description="When statuses tick: start (before agents act) or end of the round. Expiry is always at the end.")
    view: bool = Field(True, description="Show every agent who is affected by what.")


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


@mechanism(KIND, StatusConfig,
           "Named statuses on entities: timed or permanent, stacking, ticking effects each round, property modifiers "
           "($effective), blocked actions, immunity, expiry news and cleansing. Apply with {\"apply\": name, \"to\": ...}, "
           "remove with {\"cleanse\": name | \"all\", \"from\": ...}; read with $has_status, $status_stacks, "
           "$status_rounds. State is the map property <name> on each carrier.",
           example={"kind": KIND, "on": "unit", "statuses": {
               "poison": {"duration": 3, "max_stacks": 3, "tick": ["$it.hp -= 2 * $stacks"]},
               "stun": {"duration": 1, "blocks": ["attack"], "blocked_why": "you are stunned"},
               "shield": {"duration": 2, "modifiers": {"armor": 3}, "immune": ["poison"]}}})
def _expand(name: str, cfg: StatusConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    on = common.types_in(contract, cfg.on, "on")
    _unique_statuses(name, cfg, contract)
    hooks: Dict[str, Dict[str, List[Any]]] = {}
    for status, spec in cfg.statuses.items():
        if not common.NAME.match(status):
            raise MechanismError(f"status name '{status}' must start with a letter and use letters, digits and _",
                                 None, f"statuses.{status}")
        for other in spec.immune:
            if other not in cfg.statuses:
                raise MechanismError(f"'{other}' is not a status here", common.suggest(other, cfg.statuses),
                                     f"statuses.{status}.immune")
        why = spec.blocked_why or f"{status} prevents it"
        for action in common.check_names(contract, spec.blocks, f"statuses.{status}.blocks", on):
            hooks.setdefault(action, {"when": []})["when"].append(
                {"expr": f"not $has_status($actor, '{status}')", "why": why})
    prop = {"type": "map", "default": {}, "description": f"Active statuses ({name})."}
    events = [{"name": f"{name}_expire", "phase": "end", "do": [{"status_expire": name}]}]
    if any(spec.tick for spec in cfg.statuses.values()):
        events.insert(0, {"name": f"{name}_tick", "phase": cfg.tick_phase, "do": [{"status_tick": name}]})
    fragment: Dict[str, Any] = {"action_hooks": hooks, "types": {t: {"props": {name: prop}} for t in on}, "events": events}
    if cfg.view:
        views = {}
        for t in on:
            views[name if len(on) == 1 else f"{name}_{t}"] = {
                "title": "Statuses", "of": t, "where": f"$len($keys($it.{name})) > 0",
                "show": f"{{name}}: {{$status_text($it, '{name}')}}"}
        fragment["views"] = views
    return fragment


def _unique_statuses(name: str, cfg: StatusConfig, contract: Mapping[str, Any]) -> None:
    for other, raw in common.uses(contract, KIND):
        if other == name or not isinstance(raw.get("statuses"), Mapping):
            continue
        clash = sorted(set(raw["statuses"]) & set(cfg.statuses))
        if clash:
            raise MechanismError(f"status '{clash[0]}' is also declared by '{other}'",
                                 "give each status one name across the contract", f"statuses.{clash[0]}")


# ---------------------------------------------------------------------------
# Run time
# ---------------------------------------------------------------------------


def _index(contract: Any) -> Dict[str, Tuple[str, StatusConfig]]:
    """status name → (mechanism name, config)."""
    out: Dict[str, Tuple[str, StatusConfig]] = {}
    for mech, raw in common.uses(contract, KIND):
        cfg = common.parsed(raw, StatusConfig)
        for status in cfg.statuses:
            out.setdefault(status, (mech, cfg))
    return out


def _lookup(world: Any, status: Any, where: str) -> Tuple[str, StatusConfig, StatusDef]:
    index = _index(world.contract)
    if not isinstance(status, str) or status not in index:
        raise RunError(f"'{status}' is not a declared status ({common.suggest(str(status), index)})", where)
    mech, cfg = index[status]
    return mech, cfg, cfg.statuses[status]


def _state(entity: Entity, mech: str) -> Dict[str, Any]:
    value: Any = entity.properties.get(mech)
    return dict(value) if isinstance(value, Mapping) else {}


def active_stacks(entry: Mapping[str, Any], round_number: int, ticking: bool = False) -> int:
    """Stacks of one status entry; with ``ticking`` only those applied before ``round_number``."""
    timers = entry.get("timers")
    if timers is not None:
        return sum(1 for applied, _ in timers if not ticking or applied < round_number)
    if ticking and entry.get("since", 0) >= round_number:
        return 0
    return int(entry.get("stacks", 0))


def _later(a: Optional[int], b: Optional[int]) -> Optional[int]:
    return None if a is None or b is None else max(a, b)


def apply_status(runner: Any, status: str, target: Entity, rounds: Any, stacks: int, source: Optional[Entity],
                 where: str) -> bool:
    """Apply a status; False when the target is immune or cannot carry it."""
    world = runner.world
    mech, cfg, spec = _lookup(world, status, where)
    if mech not in target.properties:
        raise RunError(f"{target.id} ({target.entity_type}) cannot carry statuses of '{mech}'", where)
    if not target.alive:
        return False
    state = _state(target, mech)
    if spec.unless and truthy(common.evaluate(world, spec.unless, f"mechanisms.{mech}.statuses.{status}.unless", it=target)):
        return False
    if any(status in cfg.statuses[other].immune for other in state if other in cfg.statuses):
        return False
    now = world.round
    duration = spec.duration if rounds is DEFAULT_ROUNDS else rounds
    until = None if duration is None else now + common.whole(duration, f"{where}.rounds")
    current = state.get(status)
    source_id = source.id if isinstance(source, Entity) else None
    if current is None:
        entry: Dict[str, Any] = {"stacks": min(spec.max_stacks, stacks), "since": now, "until": until, "source": source_id}
        if spec.stacking == "independent":
            entry["timers"] = [[now, until]] * entry["stacks"]
    elif spec.stacking == "ignore":
        return False
    else:
        entry = dict(current)
        entry["source"] = source_id or current.get("source")
        if spec.stacking == "independent":
            timers = [list(t) for t in current.get("timers") or []] + [[now, until]] * stacks
            entry["timers"] = timers[-spec.max_stacks:]
            entry["stacks"] = len(entry["timers"])
            ends = [t[1] for t in entry["timers"]]
            entry["until"] = None if None in ends else max(ends)
        else:
            entry["stacks"] = min(spec.max_stacks, int(current.get("stacks", 1)) + stacks)
            if spec.stacking == "extend":
                entry["until"] = None if current.get("until") is None or duration is None else current["until"] + duration
            else:
                entry["until"] = _later(current.get("until"), until)
    state[status] = entry
    world.set_prop(target, mech, state)
    vars = {"it": target, "stacks": entry["stacks"], "source": source}
    runner.run(spec.on_apply, vars, f"mechanisms.{mech}.statuses.{status}.on_apply")
    _say(runner, mech, spec.say, vars, f"mechanisms.{mech}.statuses.{status}.say")
    return True


def _say(runner: Any, mech: str, template: str, vars: Dict[str, Any], where: str) -> None:
    if not template:
        return
    try:
        text = compile_template(template, None).render(runner.world.scope(**vars))
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    if text.strip():
        runner.world.emit(mech, text, data={"mechanism": KIND})


def _status_names(value: Any) -> List[str]:
    return [value] if isinstance(value, str) else list(value or [])


def _check_apply(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
    known = _index(checker.c)
    status = effect.get("apply")
    if status not in known:
        return [(f"{path}.apply", f"'{status}' is not a declared status", common.suggest(str(status), known))]
    return []


@effect_op("apply", keys=("to", "rounds", "stacks", "source"), required=("to",), literal=("apply",), check=_check_apply,
           example='{"apply": "poison", "to": "$params.target", "rounds": 3, "stacks": 1}  (a declared status; '
                   'rounds and stacks are optional, source defaults to $actor)')
def _apply_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    targets = common.entities_of(world, runner.eval(effect["to"], vars), f"{where}.to")
    rounds = runner.eval(effect["rounds"], vars) if "rounds" in effect else DEFAULT_ROUNDS
    stacks = common.whole(runner.eval(effect.get("stacks", 1), vars), f"{where}.stacks")
    source = runner.eval(effect["source"], vars) if "source" in effect else vars.get("actor")
    source_entity = world.entity(source) if source is not None else None
    for target in targets:
        apply_status(runner, effect["apply"], target, rounds, stacks, source_entity, where)


def _check_cleanse(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
    known = _index(checker.c)
    problems: List[Tuple[str, str, Optional[str]]] = []
    if effect.get("cleanse") != "all":
        for status in _status_names(effect.get("cleanse")):
            if status not in known:
                problems.append((f"{path}.cleanse", f"'{status}' is not a declared status", common.suggest(status, known)))
    return problems


@effect_op("cleanse", keys=("from",), required=("from",), literal=("cleanse",), check=_check_cleanse,
           example='{"cleanse": "poison", "from": "$params.ally"}  (a status, a list, or "all" for every cleansable one; '
                   'on_expire does not run)')
def _cleanse_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    wanted = effect["cleanse"]
    for target in common.entities_of(world, runner.eval(effect["from"], vars), f"{where}.from"):
        for mech, raw in common.uses(world.contract, KIND):
            if mech not in target.properties:
                continue
            cfg = common.parsed(raw, StatusConfig)
            state = _state(target, mech)
            keep = {s: e for s, e in state.items()
                    if not (s in _status_names(wanted) or (wanted == "all" and (s not in cfg.statuses or cfg.statuses[s].cleansable)))}
            if keep != state:
                world.set_prop(target, mech, keep)


def _check_step(checker: Any, effect: Dict[str, Any], path: str, op: str) -> List[Tuple[str, str, Optional[str]]]:
    name = effect.get(op)
    raw = checker.c.mechanisms.get(name)
    if not isinstance(raw, Mapping) or raw.get("kind") != KIND:
        return [(f"{path}.{op}", f"'{name}' is not a declared {KIND} mechanism", None)]
    cfg = common.parsed(raw, StatusConfig)
    on = [cfg.on] if isinstance(cfg.on, str) else cfg.on
    carried = {t for t in on if t in checker.c.types}
    base = set(common.base_roots())
    for status, spec in cfg.statuses.items():
        at = f"mechanisms.{name}.statuses.{status}"
        roots = base | {"it", "stacks", "source"}
        types = {"it": carried}
        for key in ("tick", "on_apply", "on_expire"):
            checker.effects(getattr(spec, key), f"{at}.{key}", roots, dict(types))
        for key in ("say", "expire_say"):
            checker.template(getattr(spec, key) or None, f"{at}.{key}", None, roots, types)
        checker.expr(spec.unless, f"{at}.unless", base | {"it"}, types)
        for prop, modifier in spec.modifiers.items():
            if not any(prop in checker.type_props.get(t, ()) for t in carried):
                checker.error(f"{at}.modifiers.{prop}", f"{'/'.join(sorted(carried))} has no property '{prop}'")
            terms = [modifier.add, modifier.mul] if isinstance(modifier, ModifierSpec) else [modifier]
            for term in terms:
                checker.value(term, f"{at}.modifiers.{prop}", base | {"it"}, types)
    return []


@effect_op("status_tick", keys=(), literal=("status_tick",), check=lambda c, e, p: _check_step(c, e, p, "status_tick"),
           example='{"status_tick": "conditions"}  (run every active status\'s tick effects now; generated each round)')
def _tick_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["status_tick"]
    cfg = common.config(world, mech, KIND, StatusConfig, where)
    on = [cfg.on] if isinstance(cfg.on, str) else cfg.on
    for carrier in common.carriers(world, on):
        for status, spec in cfg.statuses.items():
            if not carrier.alive:
                break
            entry = _state(carrier, mech).get(status)
            if not entry or not spec.tick:
                continue
            stacks = active_stacks(entry, world.round, ticking=True)
            if stacks <= 0:
                continue
            source = world.entities.get(entry.get("source")) if entry.get("source") else None
            runner.run(spec.tick, {"it": carrier, "stacks": stacks, "source": source},
                       f"mechanisms.{mech}.statuses.{status}.tick")


@effect_op("status_expire", keys=(), literal=("status_expire",), check=lambda c, e, p: _check_step(c, e, p, "status_expire"),
           example='{"status_expire": "conditions"}  (end statuses whose time is up; generated at the end of each round)')
def _expire_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["status_expire"]
    cfg = common.config(world, mech, KIND, StatusConfig, where)
    on = [cfg.on] if isinstance(cfg.on, str) else cfg.on
    now = world.round
    for carrier in common.carriers(world, on):
        state = _state(carrier, mech)
        ended: List[Tuple[str, Dict[str, Any]]] = []
        changed = False
        for status, entry in list(state.items()):
            timers = entry.get("timers")
            if timers is not None:
                left = [t for t in timers if t[1] is None or t[1] > now]
                if len(left) == len(timers):
                    continue
                changed = True
                if left:  # some stacks ran out: the status stays with fewer stacks
                    ends = [t[1] for t in left]
                    state[status] = {**entry, "timers": left, "stacks": len(left), "until": None if None in ends else max(ends)}
                    continue
            elif entry.get("until") is None or entry["until"] > now:
                continue
            changed = True
            del state[status]
            ended.append((status, entry))
        if not changed:
            continue
        world.set_prop(carrier, mech, state)
        for status, entry in ended:
            spec = cfg.statuses.get(status)
            if spec is None:
                continue
            source = world.entities.get(entry.get("source")) if entry.get("source") else None
            vars_ = {"it": carrier, "stacks": int(entry.get("stacks", 1)), "source": source}
            runner.run(spec.on_expire, vars_, f"mechanisms.{mech}.statuses.{status}.on_expire")
            _say(runner, mech, spec.expire_say, vars_, f"mechanisms.{mech}.statuses.{status}.expire_say")


# ---------------------------------------------------------------------------
# Functions and modifiers
# ---------------------------------------------------------------------------


def _entry(call: Call) -> Tuple[Optional[Entity], Optional[Dict[str, Any]]]:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    status = call.arg(1)
    if call.arg(0) is not None and entity is None:
        raise ExprError(f"${call.name}: expected an entity, got {call.arg(0)!r}", call.source)
    index = _index(world.contract)
    if status not in index:
        raise ExprError(f"${call.name}: '{status}' is not a declared status ({common.suggest(str(status), index)})", call.source)
    if entity is None:
        return None, None
    return entity, _state(entity, index[status][0]).get(status)


@function("has_status(entity, status)", "True when the entity has the status, e.g. $has_status($actor, 'stun').",
          min_args=2, max_args=2)
def _has_status(call: Call) -> bool:
    return _entry(call)[1] is not None


@function("status_stacks(entity, status)", "Stacks of the status on the entity (0 when it has none).", min_args=2, max_args=2)
def _status_stacks(call: Call) -> int:
    entry = _entry(call)[1]
    return active_stacks(entry, 0) if entry else 0


@function("status_rounds(entity, status)",
          "Rounds the status lasts after this one (0 = it ends this round); null when permanent or absent.",
          min_args=2, max_args=2)
def _status_rounds(call: Call) -> Optional[int]:
    entry = _entry(call)[1]
    if not entry or entry.get("until") is None:
        return None
    world: Any = call.scope.world
    return max(0, int(entry["until"]) - int(world.round))


@function("status_text(entity, mechanism)", "The entity's statuses as text: 'poison ×2 (1 more round), stun (ends this round)'.",
          min_args=2, max_args=2)
def _status_text(call: Call) -> str:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    mech = call.arg(1)
    if entity is None:
        raise ExprError(f"$status_text: expected an entity, got {call.arg(0)!r}", call.source)
    parts = []
    for status, entry in _state(entity, str(mech)).items():
        stacks = active_stacks(entry, 0)
        label = status + (f" ×{stacks}" if stacks > 1 else "")
        until = entry.get("until")
        if until is None:
            parts.append(label)
            continue
        left = max(0, int(until) - world.round)
        parts.append(f"{label} ({'ends this round' if left == 0 else f'{left} more round' + ('s' if left > 1 else '')})")
    return ", ".join(parts)


def _modifiers(world: Any, entity: Entity, prop: str) -> Iterable[Tuple[float, float]]:
    for mech, raw in common.uses(world.contract, KIND):
        state = entity.properties.get(mech)
        if not isinstance(state, Mapping) or not state:
            continue
        cfg = common.parsed(raw, StatusConfig)
        for status, entry in state.items():
            spec = cfg.statuses.get(status)
            if spec is None or prop not in spec.modifiers:
                continue
            stacks = active_stacks(entry, 0)
            add, mul = common.modifier_terms(world, spec.modifiers[prop], entity,
                                             f"mechanisms.{mech}.statuses.{status}.modifiers.{prop}")
            yield add * stacks, mul ** stacks


common.MODIFIER_SOURCES.append(_modifiers)
