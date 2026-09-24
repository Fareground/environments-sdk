"""Abilities over time: per-action cooldowns and charges, and channeled actions that complete later (the
``conditions`` family's ``cooldowns`` and ``channeling`` modes).

.. code-block:: json

    "abilities": {"kind": "conditions", "mode": "cooldowns", "actions": {"fireball": {"cooldown": 2},
                                                                        "heal": {"charges": 2, "recharge": 3}}},
    "spells": {"kind": "conditions", "mode": "channeling", "actions": {"meteor": {"rounds": 2,
               "resolve": ["$params.target.hp -= 10"], "interrupt": "$has_status($actor, 'stun')"}}}

Both attach to actions the contract declares: a cooldown adds a readiness condition and starts the
cooldown when the action succeeds; a channeled action stores its arguments when taken and runs
``resolve`` with the same ``$actor`` and ``$params`` when the channel completes. In effects the
limited or channeled action is named by ``ability`` (``action`` is the family op's sub-command).

Cooldown state is the map property ``<name>`` on each agent: ``{action: {ready, charges, since}}``.
Charges regenerate lazily from ``since``, so nothing ticks and a snapshot holds everything.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from ..errors import RunError
from ..expr import Call, ExprError, function
from ..expr.objects import Entity
from ..expr.template import compile_template
from ..registry import MechanismError, family_action, mechanism_config, mode, parsed, use_key
from ..world.live import Abort
from . import _common as common
from ._common import Config, Effects

__all__ = ["CooldownDef", "CooldownConfig", "ChannelDef", "ChannelConfig"]

COOLDOWNS = "conditions.cooldowns"
CHANNELING = "conditions.channeling"


# ---------------------------------------------------------------------------
# Cooldowns and charges
# ---------------------------------------------------------------------------


class CooldownDef(Config):
    """Limits on one action."""

    cooldown: int | str = Field(0,
                                description="Rounds the action is unavailable after each use (number or expression "
                                            "over $actor, $params).")
    charges: int | None = Field(None, ge=1, description="Uses stored; each use spends one.")
    recharge: int | None = Field(None, ge=1, description="Rounds to regain one spent charge.")
    start: int | None = Field(None, ge=0, description="Charges at the start (default: full).")
    why: str = Field("", description="Why the action is refused while not ready.")

    @model_validator(mode="after")
    def _shape(self) -> CooldownDef:
        if self.recharge is not None and self.charges is None:
            raise ValueError("recharge needs charges")
        if self.start is not None and (self.charges is None or self.start > self.charges):
            raise ValueError("start must be at most charges")
        if isinstance(self.cooldown, int) and self.cooldown < 0:
            raise ValueError("cooldown must be ≥ 0")
        return self


class CooldownConfig(Config):
    """Cooldowns and charges on declared actions."""

    actions: dict[str, CooldownDef] = Field(
        ..., description="{action: {cooldown, charges, recharge, start, why}}. cooldown N: after a use the action "
                         "is unavailable for the next N rounds; charges: uses stored, one regained every `recharge` "
                         "rounds.")
    views: bool = Field(True, description="Show each agent the state of its limited actions.")


@mode("conditions", "cooldowns", CooldownConfig,
      "Per-action cooldowns and charges with regeneration. Each listed action is offered only when ready, and "
      "starts its cooldown (spends a charge) when taken. Read with $ready(entity, action), $charges(entity, action), "
      "$cooldown_left(entity, action); the `reset` action makes one ready again.",
      example={"actions": {"fireball": {"cooldown": 2}, "heal": {"charges": 2, "recharge": 3}}})
def _expand_cooldowns(name: str, cfg: CooldownConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    _unique_actions(name, COOLDOWNS, cfg.actions, contract)
    declared = contract.get("actions") or {}
    hooks: dict[str, dict[str, list[Any]]] = {}
    agents: list[str] = []
    for action, spec in cfg.actions.items():
        common.check_names(contract, [action], f"actions.{action}", [])
        raw = declared[action]
        agents += [t for t in common.by_types(raw) if t not in agents]
        why = spec.why or f"{action.replace('_', ' ')} is not ready yet"
        start = {"conditions": name, "action": "start", "ability": action}
        hook: dict[str, list[Any]] = {"when": [{"expr": f"$ready($actor, '{action}')", "why": why}], "do": [start]}
        if raw.get("chance") is not None:
            hook["otherwise"] = [start]
        hooks[action] = hook
    types = common.types_in(contract, agents, "actions")
    fragment: dict[str, Any] = {"action_hooks": hooks,
                                "types": {t: {"props": {name: {"type": "map", "default": {},
                                                               "description": f"Cooldowns ({name})."}}}
                                          for t in types}}
    if cfg.views:
        fragment["views"] = {name: {"for": types, "title": "Abilities", "show": f"{{$ability_text($actor, '{name}')}}"}}
    return fragment


def _unique_actions(name: str, key: str, actions: Mapping[str, Any], contract: Mapping[str, Any]) -> None:
    for other, raw in common.uses(contract, key):
        if other != name and isinstance(raw.get("actions"), Mapping):
            clash = sorted(set(raw["actions"]) & set(actions))
            if clash:
                raise MechanismError(f"action '{clash[0]}' is also listed by '{other}'", "list each action once",
                                     f"actions.{clash[0]}")


def _index(contract: Any, key: str, model: Any) -> dict[str, tuple[str, Any]]:
    out: dict[str, tuple[str, Any]] = {}
    for mech, raw in common.uses(contract, key):
        cfg = parsed(raw, model)
        for action in cfg.actions:
            out.setdefault(action, (mech, cfg))
    return out


def _limited(world: Any, action: Any, where: str) -> tuple[str, CooldownConfig, CooldownDef]:
    index = _index(world.contract, COOLDOWNS, CooldownConfig)
    if not isinstance(action, str) or action not in index:
        raise RunError(f"'{action}' has no cooldown ({common.suggest(str(action), index)})", where)
    mech, cfg = index[action]
    return mech, cfg, cfg.actions[action]


def _charges(spec: CooldownDef, entry: Mapping[str, Any], now: int) -> tuple[int | None, int]:
    """Charges available now (None when the action has no charges) and the regeneration timer start."""
    if spec.charges is None:
        return None, now
    full = spec.start if spec.start is not None else spec.charges
    stored = int(entry.get("charges", full))
    since = int(entry.get("since", 0))
    if spec.recharge and stored < spec.charges:
        gained = (now - since) // spec.recharge
        if gained > 0:
            stored = min(spec.charges, stored + gained)
            since += gained * spec.recharge
    return stored, since


def _wait(spec: CooldownDef, entry: Mapping[str, Any], now: int) -> int:
    """Rounds until the action can be used again (0 = now)."""
    wait = max(0, int(entry.get("ready", 0)) - now)
    charges, since = _charges(spec, entry, now)
    if charges == 0:
        if not spec.recharge:
            return -1  # never again
        wait = max(wait, since + spec.recharge - now)
    return wait


def _entry(entity: Entity, mech: str, action: str) -> Mapping[str, Any]:
    state = entity.properties.get(mech)
    entry = state.get(action) if isinstance(state, Mapping) else None
    return entry if isinstance(entry, Mapping) else {}


def _arguments(call: Call) -> tuple[Entity, str, CooldownDef, Mapping[str, Any]]:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    if entity is None:
        raise ExprError(f"${call.name}: expected an entity, got {call.arg(0)!r}", call.source)
    try:
        mech, _, spec = _limited(world, call.arg(1), call.source)
    except RunError as exc:
        raise ExprError(str(exc), call.source) from None
    return entity, mech, spec, _entry(entity, mech, call.arg(1))


def _now(call: Call) -> int:
    world: Any = call.scope.world
    return int(world.round)


@function("ready(entity, action)",
          "True when a cooldown-limited action can be used now, e.g. $ready($actor, 'fireball').",
          min_args=2, max_args=2)
def _ready(call: Call) -> bool:
    _, _, spec, entry = _arguments(call)
    return _wait(spec, entry, _now(call)) == 0


@function("charges(entity, action)", "Charges of the action available now (null when it has no charges).", min_args=2,
          max_args=2)
def _charges_function(call: Call) -> int | None:
    _, _, spec, entry = _arguments(call)
    return _charges(spec, entry, _now(call))[0]


@function("cooldown_left(entity, action)", "Rounds until the action can be used again (0 = now, -1 = never).",
          min_args=2, max_args=2)
def _cooldown_left(call: Call) -> int:
    _, _, spec, entry = _arguments(call)
    return _wait(spec, entry, _now(call))


@function("ability_text(entity, mechanism)",
          "The entity's limited actions as text: 'fireball ready; heal 1/2 charges, next in 3 rounds'.",
          min_args=2, max_args=2)
def _ability_text(call: Call) -> str:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    if entity is None:
        raise ExprError(f"$ability_text: expected an entity, got {call.arg(0)!r}", call.source)
    raw = world.contract.mechanisms.get(call.arg(1))
    if use_key(raw) != COOLDOWNS:
        raise ExprError(f"$ability_text: '{call.arg(1)}' is not a declared conditions (cooldowns) mechanism",
                        call.source)
    cfg = parsed(raw, CooldownConfig)
    parts = []
    for action, spec in cfg.actions.items():
        if not any(world.is_a(entity.entity_type, t) for t in common.by_types(world.contract.actions[action])):
            continue
        entry = _entry(entity, str(call.arg(1)), action)
        wait = _wait(spec, entry, world.round)
        charges = _charges(spec, entry, world.round)[0]
        text = action.replace("_", " ")
        if charges is not None:
            text += f" {charges}/{spec.charges} charges"
        if wait == 0:
            text += " ready" if charges is None else ""
        elif wait < 0:
            text += " used up"
        else:
            text += f"{',' if charges is not None else ''} ready in {wait} round{'s' if wait > 1 else ''}"
        parts.append(text)
    return "; ".join(parts) or "none"


def _who(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> list[Entity]:
    value = runner.eval(effect["who"], vars) if "who" in effect else vars.get("actor")
    if value is None:
        raise RunError("needs `who` (the entity) outside an action", where)
    return common.entities_of(runner.world, value, f"{where}.who")


def _abilities(effect: Mapping[str, Any]) -> list[str]:
    value = effect.get("ability")
    return [] if value == "all" else ([value] if isinstance(value, str) else list(value or []))


def _check_abilities(key: str, model: Any, allow_all: bool) -> Any:
    """A check that every action ``ability`` names is listed by the op's mechanism."""

    def check(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
        mech = effect[key.split(".")[0]]
        listed = parsed(checker.c.mechanisms[mech], model).actions
        value = effect.get("ability")
        if not allow_all and not isinstance(value, str):
            return [(f"{path}.ability", "`ability` names one action", None)]
        return [(f"{path}.ability", f"'{n}' is not an action of {mech}", common.suggest(n, listed))
                for n in _abilities(effect) if n not in listed]

    return check


def _listed(world: Any, mech: str, cfg: Any, names: list[str], where: str) -> None:
    for name in names:
        if name not in cfg.actions:
            raise RunError(f"'{name}' is not an action of {mech} ({common.suggest(name, cfg.actions)})",
                           f"{where}.ability")


@family_action("conditions", ("cooldowns",), "start", keys=("ability", "who"), required=("ability",),
               literal=("ability",), check=_check_abilities(COOLDOWNS, CooldownConfig, False), internal=True,
               example='{"conditions": "abilities", "action": "start", "ability": "fireball", "who": "$actor"}  '
                       '(start the cooldown; added to each limited action)')
def _start_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["conditions"]
    cfg = mechanism_config(world, mech, COOLDOWNS, CooldownConfig, where)
    action = effect["ability"]
    _listed(world, mech, cfg, [action], where)
    spec = cfg.actions[action]
    now = world.round
    for entity in _who(runner, effect, vars, where):
        entry = dict(_entry(entity, mech, action))
        cooldown = common.whole(runner.eval(spec.cooldown, vars), f"mechanisms.{mech}.actions.{action}.cooldown", low=0)
        if cooldown > 0:
            entry["ready"] = now + cooldown + 1
        if spec.charges is not None:
            charges, since = _charges(spec, entry, now)
            entry["since"] = now if charges == spec.charges else since
            entry["charges"] = max(0, (charges or 0) - 1)
        current: Any = entity.properties.get(mech) or {}
        world.set_prop(entity, mech, {**current, action: entry})


@family_action("conditions", ("cooldowns",), "reset", keys=("ability", "who"), required=("ability",),
               literal=("ability",), check=_check_abilities(COOLDOWNS, CooldownConfig, True),
               example='{"conditions": "abilities", "action": "reset", "ability": "all", "who": "$params.ally"}  '
                       '(an action, a list, or all: ready with full charges; who defaults to $actor)')
def _reset_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["conditions"]
    cfg = mechanism_config(world, mech, COOLDOWNS, CooldownConfig, where)
    names = None if effect["ability"] == "all" else _abilities(effect)
    _listed(world, mech, cfg, names or [], where)
    for entity in _who(runner, effect, vars, where):
        state = entity.properties.get(mech)
        if not isinstance(state, Mapping) or not state:
            continue
        keep = {} if names is None else {a: e for a, e in state.items() if a not in names}
        if keep != state:
            world.set_prop(entity, mech, keep)


# ---------------------------------------------------------------------------
# Channeling
# ---------------------------------------------------------------------------


class ChannelDef(Config):
    """A channeled action."""

    rounds: int | str = Field(..., description="Rounds until it completes (number or expression over $actor, $params).")
    resolve: Effects = Field(default_factory=list,
                             description="Effects when it completes, with the original $actor and $params.")
    interrupt: str | None = Field(None,
                                  description="Expression over $actor, $params checked each round: when true the "
                                              "channel breaks.")
    on_interrupt: Effects = Field(default_factory=list, description="Effects when it breaks ($actor, $params).")
    say: str = Field("", description="News when it completes (template over $actor, $params).")
    interrupt_say: str = Field("", description="News when it breaks (template over $actor, $params).")

    @model_validator(mode="after")
    def _shape(self) -> ChannelDef:
        if isinstance(self.rounds, int) and self.rounds < 1:
            raise ValueError("rounds must be at least 1")
        return self


class ChannelConfig(Config):
    """Channeled (multi-round) actions."""

    actions: dict[str, ChannelDef] = Field(
        ..., description="{action: {rounds, resolve, interrupt, on_interrupt, say, interrupt_say}}. Taking the "
                         "action starts the channel (its own `do` runs at once: costs); `resolve` runs at the start "
                         "of the round `rounds` later with the same $actor and $params.")
    busy: Literal["all"] | list[str] = Field(
        "all",
        description="Actions the channeler cannot take meanwhile: all (every action of its type declared so far) or a "
                    "list.")
    views: bool = Field(True, description="Show a channeling agent what it is channeling and when it completes.")


@mode("conditions", "channeling", ChannelConfig,
      "Multi-round actions: taking a listed action starts a channel that resolves `rounds` later with the original "
      "arguments, keeps the channeler busy, and breaks when `interrupt` holds (or with the `interrupt` action). "
      "A `fail` in resolve fizzles it with every change rolled back. $channeling(entity) is the channel in progress "
      "({action, params, started, completes}) or null.",
      example={"actions": {"meteor": {"rounds": 2, "resolve": ["$params.target.hp -= 12"], "interrupt": "$actor.hp < 5",
                                      "say": "A meteor strikes!"}}})
def _expand_channeling(name: str, cfg: ChannelConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    _unique_actions(name, CHANNELING, cfg.actions, contract)
    declared = contract.get("actions") or {}
    casters: list[str] = []
    for action in cfg.actions:
        common.check_names(contract, [action], f"actions.{action}", [])
        casters += [t for t in common.by_types(declared[action]) if t not in casters]
    casters = common.types_in(contract, casters, "actions")
    busy = common.check_names(contract, cfg.busy, "busy", casters)
    hooks: dict[str, dict[str, list[Any]]] = {}
    free = {"expr": "not $channeling($actor)", "why": "you are channeling"}
    for action in dict.fromkeys([*busy, *cfg.actions]):
        hooks[action] = {"when": [free]}
    for action in cfg.actions:
        hooks[action]["do"] = [{"conditions": name, "action": "start", "ability": action}]
    fragment: dict[str, Any] = {
        "action_hooks": hooks,
        "types": {t: {"props": {name: {"type": "map", "default": {}, "description": f"Channel in progress ({name})."}}}
                  for t in casters},
        "events": [{"name": name, "phase": "start", "do": [{"conditions": name, "action": "step"}]}],
    }
    if cfg.views:
        fragment["views"] = {name: {"for": casters, "title": "Channeling", "when": f"$len($keys($actor.{name})) > 0",
                                    "show": f"You are channeling {{$actor.{name}.action}}; it completes at the start "
                                            f"of round {{$actor.{name}.completes}}."}}
    return fragment


def _channel_entry(world: Any, entity: Entity) -> tuple[str | None, Mapping[str, Any] | None]:
    for mech, _ in common.uses(world.contract, CHANNELING):
        state = entity.properties.get(mech)
        if isinstance(state, Mapping) and state:
            return mech, state
    return None, None


@function("channeling(entity)",
          "The channel the entity has in progress ({action, params, started, completes}), or null.",
          min_args=1, max_args=1)
def _channeling(call: Call) -> dict[str, Any] | None:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    if entity is None:
        if call.arg(0) is None:
            return None
        raise ExprError(f"$channeling: expected an entity, got {call.arg(0)!r}", call.source)
    state = _channel_entry(world, entity)[1]
    if not state:
        return None
    view = dict(state)
    view["params"] = common.thaw(state.get("params") or {}, world, version=state.get("capture_version", 0))
    view.pop("capture_version", None)
    return view


@family_action("conditions", ("channeling",), "start", keys=("ability",), required=("ability",), literal=("ability",),
               check=_check_abilities(CHANNELING, ChannelConfig, False), internal=True,
               example='{"conditions": "spells", "action": "start", "ability": "meteor"}  (start channeling with this '
                       'action\'s $actor and $params; added to each channeled action)')
def _channel_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["conditions"]
    cfg = mechanism_config(world, mech, CHANNELING, ChannelConfig, where)
    action = effect["ability"]
    _listed(world, mech, cfg, [action], where)
    actor = vars.get("actor")
    if not isinstance(actor, Entity):
        raise RunError("`start` runs inside an action ($actor)", where)
    if _channel_entry(world, actor)[1]:
        raise Abort("You are already channeling.")
    rounds = common.whole(runner.eval(cfg.actions[action].rounds, vars), f"mechanisms.{mech}.actions.{action}.rounds")
    world.set_prop(actor, mech, {"action": action, "params": common.freeze(vars.get("params") or {}),
                                 "capture_version": common.CAPTURE_VERSION, "started": world.round,
                                 "completes": world.round + rounds})


@family_action("conditions", ("channeling",), "interrupt", keys=("who",), required=("who",),
               example='{"conditions": "spells", "action": "interrupt", "who": "$params.target"}  (break the entity\'s '
                       'channel now: on_interrupt runs)')
def _interrupt_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["conditions"]
    cfg = mechanism_config(world, mech, CHANNELING, ChannelConfig, where)
    for entity in common.entities_of(world, runner.eval(effect["who"], vars), f"{where}.who"):
        state = entity.properties.get(mech)
        if isinstance(state, Mapping) and state:
            _break(runner, mech, cfg, entity, state)


def _break(runner: Any, mech: str, cfg: ChannelConfig, entity: Entity, state: Mapping[str, Any]) -> None:
    world = runner.world
    action = state.get("action")
    world.set_prop(entity, mech, {})
    spec = cfg.actions.get(str(action))
    if spec is None:
        return
    at = f"mechanisms.{mech}.actions.{action}"
    vars = {"actor": entity,
            "params": common.thaw(state.get("params") or {}, world, version=state.get("capture_version", 0))}
    runner.run(spec.on_interrupt, vars, f"{at}.on_interrupt")
    _news(runner, mech, spec.interrupt_say or f"{entity.name}'s {str(action).replace('_', ' ')} was interrupted.",
          vars, f"{at}.interrupt_say")


def _news(runner: Any, mech: str, template: str, vars: dict[str, Any], where: str) -> None:
    if not template:
        return
    try:
        text = compile_template(template, None).render(runner.world.scope(**vars))
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    if text.strip():
        runner.world.emit(mech, text, data={"mechanism": CHANNELING})


def _check_step(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
    name = effect["conditions"]
    cfg = parsed(checker.c.mechanisms[name], ChannelConfig)
    base = set(common.base_roots())
    for action, spec in cfg.actions.items():
        declared = checker.c.actions.get(action)
        if declared is None:
            continue
        at = f"mechanisms.{name}.actions.{action}"
        by = {declared.by} if isinstance(declared.by, str) else set(declared.by)
        roots, types = base | {"actor", "params"}, {"actor": by}
        for key in ("resolve", "on_interrupt"):
            checker.effects(getattr(spec, key), f"{at}.{key}", roots, dict(types), declared.params)
        checker.expr(spec.interrupt, f"{at}.interrupt", roots, types, declared.params)
        checker.value(spec.rounds, f"{at}.rounds", roots, types, declared.params)
        for key in ("say", "interrupt_say"):
            checker.template(getattr(spec, key) or None, f"{at}.{key}", None, roots, types, declared.params)
    return []


@family_action("conditions", ("channeling",), "step", check=_check_step, internal=True,
               example='{"conditions": "spells", "action": "step"}  (break or complete channels now; generated at the '
                       'start of each round)')
def _step_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["conditions"]
    cfg = mechanism_config(world, mech, CHANNELING, ChannelConfig, where)
    casters = sorted({t for a in cfg.actions if a in world.contract.actions
                      for t in common.by_types(world.contract.actions[a])})
    for entity in common.carriers(world, casters):
        state = entity.properties.get(mech)
        if not isinstance(state, Mapping) or not state:
            continue
        action = str(state.get("action"))
        spec = cfg.actions.get(action)
        if spec is None:
            world.set_prop(entity, mech, {})
            continue
        at = f"mechanisms.{mech}.actions.{action}"
        params = common.thaw(state.get("params") or {}, world, version=state.get("capture_version", 0))
        channel_vars = {"actor": entity, "params": params}
        if spec.interrupt is not None and common.condition(world, spec.interrupt, f"{at}.interrupt", **channel_vars):
            _break(runner, mech, cfg, entity, state)
            continue
        if world.round < int(state.get("completes", 0)):
            continue
        world.set_prop(entity, mech, {})
        mark = world.journal.mark()
        try:
            runner.run(spec.resolve, dict(channel_vars), f"{at}.resolve")
        except Abort as abort:
            world.journal.rollback(mark)
            world.emit(mech, f"{entity.name}'s {action.replace('_', ' ')} fizzled: {abort.reason}",
                       actor=entity.id, data={"mechanism": CHANNELING, "action": action, "fizzled": True})
            continue
        _news(runner, mech, spec.say, channel_vars, f"{at}.say")
