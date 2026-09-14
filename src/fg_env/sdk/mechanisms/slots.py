"""Worker placement: capacity-limited action spaces claimed each round and reset — the ``slots`` mechanism.

Claims live in ``$world.<name>_claims`` ({space: [player ids]}) and each player's
``<name>_workers`` counts the workers they may still place this round.
"""
from __future__ import annotations

from typing import Any, cast, Dict, List, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function, is_expr, truthy
from ..registry import MechanismError, effect_op, mechanism
from ..world import Abort
from .contract_cache import parse_kind, per_contract

__all__ = ["SlotsConfig", "open_spaces"]

def _props(entity: Entity) -> Dict[str, Any]:
    """An entity's properties, typed loosely: values are whatever the contract declared."""
    return cast(Dict[str, Any], entity.properties)



class SpaceConfig(BaseModel):
    """One action space."""

    model_config = ConfigDict(extra="forbid")

    capacity: Union[int, str] = Field(1, description="Workers it holds per round (number or expression).")
    description: str = Field("", description="What placing a worker here does (shown on the board).")
    when: Optional[str] = Field(None, description="Who may use it ($actor), e.g. \"$actor.wood >= 2\".")
    do: List[Any] = Field(default_factory=list, description="Effects when a worker is placed ($actor).")


class SlotsConfig(BaseModel):
    """Action spaces agents claim with a limited number of workers each round."""

    model_config = ConfigDict(extra="forbid")

    workers: str = Field(..., description="Agent type that places workers.")
    spaces: Dict[str, SpaceConfig] = Field(..., description="{space name: {capacity, description, when, do}}.")
    per_round: Union[int, str] = Field(1, description="Workers each player places per round (number or expression over $it).")
    once_per_space: bool = Field(True, description="A player may claim each space at most once per round.")
    action: str = Field("place_worker", description="Name of the generated tool.")
    stage: Optional[str] = Field(None, description="Offer the tool in this declared stage; default: a generated placement stage.")
    views: bool = Field(True, description="Generate the board view.")


def _configs(world: Any) -> Dict[str, SlotsConfig]:
    return per_contract(world, "slots", lambda contract: parse_kind(contract, "slots", SlotsConfig), {})


def _config(world: Any, name: Any, where: str) -> SlotsConfig:
    configs = _configs(world)
    if not isinstance(name, str) or name not in configs:
        raise RunError(f"'{name}' is not a declared slots mechanism (slots: {', '.join(configs) or 'none'})", where)
    return configs[name]


def _number(world: Any, raw: Any, vars: Dict[str, Any], where: str) -> int:
    value = compile_expr(raw)(world.scope(**vars)) if is_expr(raw) else raw
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or float(value) != int(value):
        raise RunError(f"must be a whole number ≥ 0, got {value!r}", where)
    return int(value)


def open_spaces(world: Any, name: str, config: SlotsConfig, player: Entity) -> List[str]:
    """Spaces ``player`` may claim now, in declared order."""
    claims = world.props.get(f"{name}_claims") or {}
    out = []
    for space, spec in config.spaces.items():
        taken = claims.get(space) or []
        if len(taken) >= _number(world, spec.capacity, {"actor": player}, f"mechanisms.{name}.spaces.{space}.capacity"):
            continue
        if config.once_per_space and player.id in taken:
            continue
        if spec.when is not None:
            try:
                if not truthy(compile_expr(spec.when)(world.scope(actor=player))):
                    continue
            except ExprError as exc:
                raise RunError(str(exc), f"mechanisms.{name}.spaces.{space}.when") from None
        out.append(space)
    return out


def _slots_check(key: str) -> Any:
    def check(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
        names = [n for n, use in checker.c.mechanisms.items() if isinstance(use, Mapping) and use.get("kind") == "slots"]
        if effect.get(key) not in names:
            return [(f"{path}.{key}", f"'{effect.get(key)}' is not a declared slots mechanism", f"slots: {', '.join(names) or 'none'}")]
        return []
    return check


@effect_op("place_worker", keys=("space", "player"), required=("space",), check=_slots_check("place_worker"),
           example='{"place_worker": "board", "space": "$params.space"}  (claim a space for $actor or `player`; fails when it is full)')
def _place_worker_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["place_worker"]
    config = _config(world, name, where)
    player = world.entity(runner.eval(effect["player"], vars) if "player" in effect else vars.get("actor"))
    if player is None:
        raise RunError("place_worker needs a player (`player`, default $actor)", where)
    space = runner.eval(effect["space"], vars) if is_expr(effect["space"]) else effect["space"]
    if space not in config.spaces:
        raise RunError(f"'{space}' is not a space (spaces: {', '.join(config.spaces)})", where)
    if _props(player).get(f"{name}_workers", 0) <= 0:
        raise Abort("You have no workers left this round.")
    if space not in open_spaces(world, name, config, player):
        raise Abort(f"You cannot place a worker on {space} now: it is full, already yours, or not open to you.")
    claims = dict(world.props.get(f"{name}_claims") or {})
    claims[space] = list(claims.get(space) or []) + [player.id]
    world.set_world(f"{name}_claims", claims)
    world.set_prop(player, f"{name}_workers", _props(player)[f"{name}_workers"] - 1)


@effect_op("reset_slots", keys=(), check=_slots_check("reset_slots"),
           example='{"reset_slots": "board"}  (empty every space and give each player their workers back; generated every round)')
def _reset_slots_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["reset_slots"]
    config = _config(world, name, where)
    world.set_world(f"{name}_claims", {})
    for player in world.entities_of(config.workers):
        world.set_prop(player, f"{name}_workers", _number(world, config.per_round, {"it": player}, f"mechanisms.{name}.per_round"))


def _function_config(call: Call) -> Tuple[str, SlotsConfig]:
    name = call.arg(0)
    configs = _configs(call.scope.world)
    if name not in configs:
        raise ExprError(f"${call.name}: '{name}' is not a declared slots mechanism (slots: {', '.join(configs) or 'none'})", call.source)
    return name, configs[name]


@function("open_spaces(slots, player)", "Names of the action spaces `player` may claim now.", min_args=2, max_args=2)
def _open_spaces_function(call: Call) -> List[str]:
    name, config = _function_config(call)
    player = call.scope.world.entity(call.arg(1))
    if player is None:
        raise ExprError(f"$open_spaces: expected a player, got {call.arg(1)!r}", call.source)
    return open_spaces(call.scope.world, name, config, player)


@function("claims(slots, space)", "The players with a worker on `space` this round.", min_args=2, max_args=2)
def _claims_function(call: Call) -> List[Entity]:
    name, _ = _function_config(call)
    world: Any = call.scope.world
    return [world.entities[pid] for pid in (world.props.get(f"{name}_claims") or {}).get(call.arg(1)) or [] if pid in world.entities]


@function("slot_board(slots)", "Lines describing every action space: capacity, who claimed it, what it does.", min_args=1, max_args=1)
def _slot_board_function(call: Call) -> List[str]:
    name, config = _function_config(call)
    world: Any = call.scope.world
    claims = world.props.get(f"{name}_claims") or {}
    lines = []
    for space, spec in config.spaces.items():
        taken = [world.entities[pid].name for pid in claims.get(space) or [] if pid in world.entities]
        capacity = _number(world, spec.capacity, {}, f"mechanisms.{name}.spaces.{space}.capacity")
        state = ", ".join(taken) if taken else "open"
        lines.append(f"{space} ({len(taken)}/{capacity}): {state}" + (f" — {spec.description}" if spec.description else ""))
    return lines


@mechanism("slots", SlotsConfig,
           "Worker placement: capacity-limited action spaces. Generates a `place_worker` tool whose `space` lists only "
           "open spaces, runs each space's effects when claimed, resets claims and workers every round, and shows the "
           "board. Functions: $open_spaces, $claims.",
           example={"kind": "slots", "workers": "farmer", "per_round": 2,
                    "spaces": {"forest": {"capacity": 1, "description": "+2 wood", "do": ["$actor.wood += 2"]},
                               "market": {"capacity": 2, "when": "$actor.wood >= 1", "do": ["$actor.wood -= 1", "$actor.coins += 3"]}}})
def _expand_slots(name: str, config: SlotsConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    types = contract.get("types") or {}
    if config.workers not in types:
        raise MechanismError(f"workers '{config.workers}' is not a declared type", f"types: {', '.join(types) or 'none'}", "workers")
    if not config.spaces:
        raise MechanismError("declare at least one space", None, "spaces")
    for space in config.spaces:
        if "'" in space:
            raise MechanismError(f"space name {space!r} cannot contain quotes", None, f"spaces.{space}")
    counter = f"{name}_workers"
    opened = f"$open_spaces('{name}', $actor)"
    effects: List[Any] = [{"place_worker": name, "space": "$params.space"}]
    effects += [{"if": f"$params.space == '{space}'", "then": list(spec.do)} for space, spec in config.spaces.items() if spec.do]
    action = {"by": config.workers, "description": "Place a worker on an open action space.",
              "params": {"space": {"type": "enum", "values": opened, "description": "The space to claim."}},
              "when": [{"expr": f"$actor.{counter} > 0", "why": "You have no workers left this round."},
                       {"expr": f"$len({opened}) > 0", "why": "Every space you could use is taken."}],
              "do": effects, "announce": "{$actor.name} places a worker on {$params.space}.", "terminal": True}
    fragment: Dict[str, Any] = {
        "types": {config.workers: {"props": {counter: {"type": "int", "default": 0, "min": 0,
                                                       "description": "Workers left to place this round."}}}},
        "world": {f"{name}_claims": {"type": "map", "default": {}, "description": "{space: [player ids]} this round."}},
        "actions": {config.action: action},
        "events": [{"name": f"{name}_reset", "phase": "start", "do": [{"reset_slots": name}]}],
    }
    if config.stage is None:
        fragment["stages"] = [{"name": name, "turns": "sequential", "actions": [config.action], "who": f"$it.{counter} > 0",
                               "until": f"$all({config.workers}, $it.{counter} == 0 or $len($open_spaces('{name}', $it)) == 0)",
                               "passes": 100, "max_actions": 1, "must_act": True, "on_idle": [f"$actor.{counter} = 0"]}]
    else:
        fragment["stage_hooks"] = {config.stage: {"actions": [config.action]}}
    if config.views:
        fragment["views"] = {f"{name}_board": {"for": config.workers, "title": "Action spaces", "of": f"$slot_board('{name}')",
                                               "show": "{$it}"}}
    return fragment
