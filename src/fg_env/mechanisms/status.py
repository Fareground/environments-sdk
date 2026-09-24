"""Statuses: named, timed conditions on entities — poison, stun, shield, curse (the ``game`` family's
``status`` mode).

.. code-block:: json

    "conditions": {"kind": "game", "mode": "status", "who": "unit", "statuses": {
        "poison": {"duration": 3, "stacking": "refresh", "max_stacks": 3, "tick": ["$it.hp -= 2 * $stacks"]},
        "stun": {"duration": 1, "blocks": ["attack", "cast"]},
        "shield": {"duration": 2, "modifiers": {"armor": 3}, "immune": ["poison"]}}}

    {"game": "conditions", "action": "apply", "status": "poison", "who": "$params.target"}

State lives in one map property per carrier (``$it.conditions``)::

    {"poison": {"stacks": 2, "since": 3, "until": 6, "source": "orc_1"}}

Timing: a status applied in round r with duration d is active from that moment through round r + d
and expires at the end of that round. It ticks once in each of those d rounds (not in the round it
was applied), so ``stun`` for 1 blocks the target's next round. ``duration: null`` lasts until cleansed.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import Field

from ..errors import RunError
from ..expr import Call, ExprError, function
from ..expr.objects import Entity
from ..information.gate import render
from ..registry import MechanismError, family_action, mechanism_config, mode, parsed
from . import _common as common
from ._common import Config, Effects, ModifierSpec, Number

__all__ = ["StatusDef", "StatusConfig", "active_stacks"]

KEY = "game.status"
#: Rounds when an apply gives none: the status's own duration.
DEFAULT_ROUNDS = object()


class StatusDef(Config):
    """One named status."""

    description: str = ""
    duration: int | None = Field(1, ge=1,
                                 description="Rounds it lasts after the round it is applied in; null = until cleansed.")
    stacking: Literal["refresh", "extend", "independent", "ignore"] = Field(
        "refresh", description="Applying it again: refresh (add a stack, reset the timer), extend (add a stack and "
                               "the duration), independent (each application its own timer), ignore (no effect while "
                               "active).")
    max_stacks: int = Field(1, ge=1, description="Most stacks it can have at once.")
    tick: Effects = Field(default_factory=list, description="Effects each round it is active ($it, $stacks, $source).")
    modifiers: dict[str, Number | ModifierSpec] = Field(
        default_factory=dict, description="{prop: add | {add, mul}} per stack, read with $effective(entity, prop).")
    blocks: Literal["all"] | list[str] = Field(default_factory=list,
                                               description="Actions the carrier cannot take while it is active.")
    blocked_why: str = Field("", description="Why a blocked action is refused.")
    immune: list[str] = Field(default_factory=list,
                              description="Statuses that cannot be applied while this one is active.")
    unless: str | None = Field(None, description="Expression over $it: when true the status cannot be applied to it.")
    on_apply: Effects = Field(default_factory=list,
                              description="Effects each time it is applied ($it, $stacks, $source).")
    on_expire: Effects = Field(default_factory=list, description="Effects when it runs out ($it, $stacks, $source).")
    say: str = Field("", description="News when it is applied (template over $it, $stacks).")
    expire_say: str = Field("", description="News when it runs out (template over $it).")
    cleansable: bool = Field(True, description="Removed by a cleanse of 'all' (a cleanse naming it always removes it).")


class StatusConfig(Config):
    """Named statuses on entities of some types."""

    who: str | list[str] = Field(..., description="Type(s) that can carry these statuses (subtypes included).")
    statuses: dict[str, StatusDef] = Field(
        ..., description="{name: {duration, stacking, max_stacks, tick, modifiers, blocks, blocked_why, immune, "
                         "unless, on_apply, on_expire, say, expire_say, cleansable}}. Durations count rounds after "
                         "the round of application; tick effects see $it (carrier), $stacks and $source.")
    phase: Literal["start", "end"] = Field("start",
                                           description="When statuses tick: start (before agents act) or end of the "
                                                       "round. Expiry is always at the end.")
    views: bool = Field(True, description="Show every agent who is affected by what.")


def _carrier_types(cfg: StatusConfig) -> list[str]:
    return [cfg.who] if isinstance(cfg.who, str) else list(cfg.who)


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


@mode("game", "status", StatusConfig,
      "Named statuses on entities: timed or permanent, stacking, ticking effects each round, property modifiers "
      "($effective), blocked actions, immunity, expiry news and cleansing. Apply with the `apply` action, remove with "
      "`cleanse`; read with $has_status, $status_stacks, $status_rounds. State is the map property <name> on each "
      "carrier.",
      example={"who": "unit", "statuses": {
          "poison": {"duration": 3, "max_stacks": 3, "tick": ["$it.hp -= 2 * $stacks"]},
          "stun": {"duration": 1, "blocks": ["attack"], "blocked_why": "you are stunned"},
          "shield": {"duration": 2, "modifiers": {"armor": 3}, "immune": ["poison"]}}})
def _expand(name: str, cfg: StatusConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    on = common.types_in(contract, cfg.who, "who")
    _unique_statuses(name, cfg, contract)
    hooks: dict[str, dict[str, list[Any]]] = {}
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
    events = [{"name": f"{name}_expire", "phase": "end", "do": [{"game": name, "action": "expire"}]}]
    if any(spec.tick for spec in cfg.statuses.values()):
        events.insert(0, {"name": f"{name}_tick", "phase": cfg.phase, "do": [{"game": name, "action": "tick"}]})
    fragment: dict[str, Any] = {"action_hooks": hooks, "types": {t: {"props": {name: prop}} for t in on},
                                "events": events}
    if cfg.views:
        views = {}
        for t in on:
            views[name if len(on) == 1 else f"{name}_{t}"] = {
                "title": "Statuses", "of": t, "where": f"$len($keys($it.{name})) > 0",
                "show": f"{{name}}: {{$status_text($it, '{name}')}}"}
        fragment["views"] = views
    return fragment


def _unique_statuses(name: str, cfg: StatusConfig, contract: Mapping[str, Any]) -> None:
    for other, raw in common.uses(contract, KEY):
        if other == name or not isinstance(raw.get("statuses"), Mapping):
            continue
        clash = sorted(set(raw["statuses"]) & set(cfg.statuses))
        if clash:
            raise MechanismError(f"status '{clash[0]}' is also declared by '{other}'",
                                 "give each status one name across the contract", f"statuses.{clash[0]}")


# ---------------------------------------------------------------------------
# Run time
# ---------------------------------------------------------------------------


def _index(contract: Any) -> dict[str, tuple[str, StatusConfig]]:
    """status name → (mechanism name, config), over every status mechanism."""
    out: dict[str, tuple[str, StatusConfig]] = {}
    for mech, raw in common.uses(contract, KEY):
        cfg = parsed(raw, StatusConfig)
        for status in cfg.statuses:
            out.setdefault(status, (mech, cfg))
    return out


def _state(entity: Entity, mech: str) -> dict[str, Any]:
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


def _later(a: int | None, b: int | None) -> int | None:
    return None if a is None or b is None else max(a, b)


def apply_status(runner: Any, mech: str, cfg: StatusConfig, status: str, target: Entity, rounds: Any, stacks: int,
                 source: Entity | None, where: str) -> bool:
    """Apply a status of the mechanism ``mech``; False when the target is immune or cannot carry it."""
    world = runner.world
    spec = cfg.statuses[status]
    if mech not in target.properties:
        raise RunError(f"{target.id} ({target.entity_type}) cannot carry statuses of '{mech}'", f"{where}.who")
    if not target.alive:
        return False
    state = _state(target, mech)
    if spec.unless is not None and common.condition(world, spec.unless, f"mechanisms.{mech}.statuses.{status}.unless",
                                                    it=target):
        return False
    if any(status in cfg.statuses[other].immune for other in state if other in cfg.statuses):
        return False
    now = world.round
    duration = spec.duration if rounds is DEFAULT_ROUNDS else rounds
    until = None if duration is None else now + common.whole(duration, f"{where}.rounds")
    current = state.get(status)
    source_id = source.id if isinstance(source, Entity) else None
    if current is None:
        entry: dict[str, Any] = {"stacks": min(spec.max_stacks, stacks), "since": now, "until": until,
                                 "source": source_id}
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
                entry["until"] = (None if current.get("until") is None or duration is None else current["until"]
                                  + duration)
            else:
                entry["until"] = _later(current.get("until"), until)
    state[status] = entry
    world.set_prop(target, mech, state)
    vars = {"it": target, "stacks": entry["stacks"], "source": source}
    runner.run(spec.on_apply, vars, f"mechanisms.{mech}.statuses.{status}.on_apply")
    _say(runner, mech, spec.say, vars, f"mechanisms.{mech}.statuses.{status}.say")
    return True


def _say(runner: Any, mech: str, template: str, vars: dict[str, Any], where: str) -> None:
    if not template:
        return
    text = render(runner.world, template, vars, viewer=None, path=where)
    if text.strip():
        runner.world.emit(mech, text, data={"mechanism": KEY})


def _status_names(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


def _declared(checker: Any, effect: Mapping[str, Any]) -> StatusConfig:
    return parsed(checker.c.mechanisms[effect["game"]], StatusConfig)


def _unknown_statuses(cfg: StatusConfig, mech: str, names: list[str], path: str,
                      allow_all: bool) -> list[tuple[str, str, str | None]]:
    return [(f"{path}.status", f"'{status}' is not a status of {mech}", common.suggest(status, cfg.statuses))
            for status in names if status not in cfg.statuses and not (allow_all and status == "all")]


def _check_apply(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
    status = effect.get("status")
    if not isinstance(status, str):
        return [(f"{path}.status", "`status` names one status", None)]
    return _unknown_statuses(_declared(checker, effect), effect["game"], [status], path, False)


@family_action("game", ("status",), "apply", keys=("status", "who", "rounds", "stacks", "source"),
               required=("status", "who"), literal=("status",), check=_check_apply,
               example='{"game": "conditions", "action": "apply", "status": "poison", "who": "$params.target", '
                       '"rounds": 3, "stacks": 1}  (rounds and stacks are optional; source defaults to $actor)')
def _apply_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["game"]
    cfg = mechanism_config(world, mech, KEY, StatusConfig, where)
    status = effect["status"]
    if status not in cfg.statuses:
        raise RunError(f"'{status}' is not a status of {mech} ({common.suggest(str(status), cfg.statuses)})",
                       f"{where}.status")
    targets = common.entities_of(world, runner.eval(effect["who"], vars), f"{where}.who")
    rounds = runner.eval(effect["rounds"], vars) if "rounds" in effect else DEFAULT_ROUNDS
    stacks = common.whole(runner.eval(effect.get("stacks", 1), vars), f"{where}.stacks")
    source = runner.eval(effect["source"], vars) if "source" in effect else vars.get("actor")
    source_entity = world.entity(source) if source is not None else None
    for target in targets:
        apply_status(runner, mech, cfg, status, target, rounds, stacks, source_entity, where)


def _check_cleanse(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
    return _unknown_statuses(_declared(checker, effect), effect["game"], _status_names(effect.get("status")),
                             path, True)


@family_action("game", ("status",), "cleanse", keys=("status", "who"), required=("status", "who"),
               literal=("status",), check=_check_cleanse,
               example='{"game": "conditions", "action": "cleanse", "status": "poison", "who": "$params.ally"}  '
                       '(a status, a list, or "all" for every cleansable one; on_expire does not run)')
def _cleanse_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["game"]
    cfg = mechanism_config(world, mech, KEY, StatusConfig, where)
    wanted = effect["status"]
    names = _status_names(wanted)
    for status in names:
        if status not in cfg.statuses and wanted != "all":
            raise RunError(f"'{status}' is not a status of {mech} ({common.suggest(status, cfg.statuses)})",
                           f"{where}.status")
    for target in common.entities_of(world, runner.eval(effect["who"], vars), f"{where}.who"):
        if mech not in target.properties:
            continue
        state = _state(target, mech)
        keep = {s: e for s, e in state.items()
                if not (s in names or (wanted == "all" and (s not in cfg.statuses or cfg.statuses[s].cleansable)))}
        if keep != state:
            world.set_prop(target, mech, keep)


def _check_rules(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
    name = effect["game"]
    cfg = _declared(checker, effect)
    carried = {t for t in _carrier_types(cfg) if t in checker.c.types}
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


@family_action("game", ("status",), "tick", check=_check_rules, internal=True,
               example='{"game": "conditions", "action": "tick"}  (run every active status\'s tick effects now; '
                       'generated each round)')
def _tick_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["game"]
    cfg = mechanism_config(world, mech, KEY, StatusConfig, where)
    for carrier in common.carriers(world, _carrier_types(cfg)):
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


@family_action("game", ("status",), "expire", check=_check_rules, internal=True,
               example='{"game": "conditions", "action": "expire"}  (end statuses whose time is up; generated at '
                       'the end of each round)')
def _expire_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["game"]
    cfg = mechanism_config(world, mech, KEY, StatusConfig, where)
    now = world.round
    for carrier in common.carriers(world, _carrier_types(cfg)):
        state = _state(carrier, mech)
        ended: list[tuple[str, dict[str, Any]]] = []
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
                    state[status] = {**entry, "timers": left, "stacks": len(left),
                                     "until": None if None in ends else max(ends)}
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


def _entry(call: Call) -> tuple[Entity | None, dict[str, Any] | None]:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    status = call.arg(1)
    if call.arg(0) is not None and entity is None:
        raise ExprError(f"${call.name}: expected an entity, got {call.arg(0)!r}", call.source)
    index = _index(world.contract)
    if status not in index:
        raise ExprError(f"${call.name}: '{status}' is not a declared status ({common.suggest(str(status), index)})",
                        call.source)
    if entity is None:
        return None, None
    return entity, _state(entity, index[status][0]).get(status)


@function("has_status(entity, status)", "True when the entity has the status, e.g. $has_status($actor, 'stun').",
          min_args=2, max_args=2, family="game")
def _has_status(call: Call) -> bool:
    return _entry(call)[1] is not None


@function("status_stacks(entity, status)", "Stacks of the status on the entity (0 when it has none).", min_args=2,
          max_args=2, family="game")
def _status_stacks(call: Call) -> int:
    entry = _entry(call)[1]
    return active_stacks(entry, 0) if entry else 0


@function("status_rounds(entity, status)",
          "Rounds the status lasts after this one (0 = it ends this round); null when permanent or absent.",
          min_args=2, max_args=2, family="game")
def _status_rounds(call: Call) -> int | None:
    entry = _entry(call)[1]
    if not entry or entry.get("until") is None:
        return None
    world: Any = call.scope.world
    return max(0, int(entry["until"]) - int(world.round))


@function("status_text(entity, mechanism)",
          "The entity's statuses as text: 'poison ×2 (1 more round), stun (ends this round)'.", min_args=2, max_args=2,
          family="game")
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
        parts.append(f"{label} "
                     f"({'ends this round' if left == 0 else f'{left} more round' + ('s' if left > 1 else '')})")
    return ", ".join(parts)


def _modifiers(world: Any, entity: Entity, prop: str) -> Iterable[tuple[float, float]]:
    for mech, raw in common.uses(world.contract, KEY):
        state = entity.properties.get(mech)
        if not isinstance(state, Mapping) or not state:
            continue
        cfg = parsed(raw, StatusConfig)
        for status, entry in state.items():
            spec = cfg.statuses.get(status)
            if spec is None or prop not in spec.modifiers:
                continue
            stacks = active_stacks(entry, 0)
            add, mul = common.modifier_terms(world, spec.modifiers[prop], entity,
                                             f"mechanisms.{mech}.statuses.{status}.modifiers.{prop}")
            yield add * stacks, mul ** stacks


common.MODIFIER_SOURCES.append(_modifiers)
