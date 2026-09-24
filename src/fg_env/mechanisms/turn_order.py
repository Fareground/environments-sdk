"""Turn order beyond seats: initiative, rotating first player, snake order, skips and extra turns (the ``flow``
family's ``order`` mode).

.. code-block:: json

    "initiative": {"kind": "flow", "mode": "order", "who": "unit", "by": "$it.speed", "skip": "$it.hp <= 0",
                   "extra_turns": true, "stage": {"actions": ["move", "attack"], "turns": "sequential"}}

    {"flow": "initiative", "action": "extra_turn", "who": "$actor"}

The order is ``$turn_rank($it, name)``, usable as any stage's ``order``: agents sorted by ``by``
(highest first unless ``ascending``; seats break ties), then rotated one seat per round
(``rotate``), then reversed on even rounds (``snake``). With ``stage`` the mechanism declares that
stage itself (order and skip filled in); with ``extra_turns`` it also declares ``<name>_extra``
right after it, which wakes agents granted an extra turn with the ``extra_turn`` action until none
is left (one action per extra turn).
"""
from __future__ import annotations

import copy
import weakref
from collections.abc import Mapping
from typing import Any

from pydantic import Field, ValidationError

from ..contract import StageSpec
from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function, truthy
from ..registry import MechanismError, family_action, mode
from ..world.entity import Entity
from . import _common as common
from ._common import Config

__all__ = ["OrderConfig", "ordered"]

KEY = "flow.order"


class OrderConfig(Config):
    """Who acts in which order."""

    who: str = Field(..., description="Agent type whose order this is (subtypes included).")
    by: str | None = Field(None, description="Initiative ($it): highest first (see ascending); seats break ties.")
    ascending: bool = Field(False, description="Lowest initiative first.")
    rotate: bool = Field(False, description="The first seat moves one place each round.")
    snake: bool = Field(False, description="Reverse the order every other round (snake draft).")
    skip: str | None = Field(None, description="Agents who sit out ($it): folded, bankrupt, eliminated.")
    stage: dict[str, Any] | None = Field(None,
                                         description="Declare a stage (ordinary stage fields) using this order; its "
                                                     "name defaults to the mechanism's.")
    extra_turns: bool = Field(False,
                              description="Also declare <name>_extra after the stage for extra turns granted with the "
                                          "extra_turn action.")
    max_extra: int = Field(3, ge=1, description="Most extra turns one agent can chain in a round.")
    views: bool = Field(True, description="Show agents this round's order.")


@mode("flow", "order", OrderConfig,
      "Turn order: initiative by an expression, rotating first player, snake order, skip conditions and extra turns. "
      "Use $turn_rank($it, name) as any stage's order, or give `stage` to declare the stage. $turn_order(name) is "
      "this round's order.",
      example={"who": "player", "by": "$it.speed", "skip": "$it.folded", "rotate": True,
               "stage": {"actions": ["bet", "fold"], "turns": "sequential"}})
def _expand(name: str, cfg: OrderConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    common.types_in(contract, cfg.who, "who")
    if not _agent(contract, cfg.who):
        raise MechanismError(f"'{cfg.who}' is not an agent type", f"set types.{cfg.who}.agent: true", "who")
    if cfg.extra_turns and cfg.stage is None:
        raise MechanismError("extra_turns needs `stage`", "declare the stage here", "extra_turns")
    fragment: dict[str, Any] = {}
    if cfg.views:
        fragment["views"] = {name: {"for": cfg.who, "title": "Turn order",
                                    "show": f"{{$join($map($turn_order('{name}'), $it.name), ', ')}}"}}
    if cfg.stage is None:
        return fragment
    stage = copy.deepcopy(cfg.stage)
    if "order" in stage:
        raise MechanismError("the order mode sets the stage's `order`", "remove `order`", "stage.order")
    stage.setdefault("name", name)
    stage["order"] = f"$turn_rank($it, '{name}')"
    sits_out = f"not ({cfg.skip})" if cfg.skip else None
    stage["who"] = _both(_both(f"$is($it, {cfg.who})", sits_out), stage.get("who"))
    stages = [_validated(stage, "stage")]
    if cfg.extra_turns:
        counter = f"{name}_extra"
        fragment["types"] = {cfg.who: {"props": {counter: {"type": "int", "default": 0, "min": 0, "max": cfg.max_extra,
                                                           "description": "Extra turns waiting this round."}}}}
        waiting = _both(f"$it.{counter} > 0", sits_out)
        extra = {**copy.deepcopy(cfg.stage), "name": counter, "order": stage["order"], "turns": "sequential",
                 "max_actions": 1, "who": _both(waiting, cfg.stage.get("who")),
                 "until": f"$count({cfg.who}, {waiting}) == 0", "passes": cfg.max_extra, "quiet": "wake",
                 "brief": ("Extra turn. " + str(cfg.stage.get("brief") or "")).strip(), "on_enter": [],
                 "on_idle": [*(cfg.stage.get("on_idle") or []), f"$actor.{counter} -= 1"],
                 "on_exit": [*(cfg.stage.get("on_exit") or []),
                             {"each": cfg.who, "where": f"$it.{counter} > 0", "do": [f"$it.{counter} = 0"]}]}
        stages.append(_validated(extra, "stage"))
        spend = {"if": f"$stage == '{counter}'", "then": [f"$actor.{counter} -= 1"]}
        names = _stage_action_names(contract, stage)
        fragment["action_hooks"] = {a: {"do": [spend]} for a in names if _only_among(contract, a, cfg.who)}
    fragment["stages"] = stages
    return fragment


def _both(a: str | None, b: str | None) -> str | None:
    if a is None or b is None:
        return a or b
    return f"({a}) and ({b})"


def _agent(contract: Mapping[str, Any], type_name: str) -> bool:
    types = contract.get("types") or {}
    current, seen = type_name, []
    while isinstance(types.get(current), Mapping) and current not in seen:
        if types[current].get("agent"):
            return True
        seen.append(current)
        current = types[current].get("extends")
    return False


def _validated(stage: dict[str, Any], field: str) -> dict[str, Any]:
    try:
        StageSpec.model_validate(stage)
    except ValidationError as exc:
        error = exc.errors()[0]
        where = ".".join(str(p) for p in error["loc"])
        message = "is not a stage field" if error["type"] == "extra_forbidden" else error["msg"]
        raise MechanismError(message, None, f"{field}.{where}" if where else field) from None
    return stage


def _stage_action_names(contract: Mapping[str, Any], stage: Mapping[str, Any]) -> list[str]:
    listed = stage.get("actions", "all")
    if listed == "all":
        return list(contract.get("actions") or {})
    if isinstance(listed, list):
        return list(listed)
    if isinstance(listed, Mapping):
        return [a for names in listed.values() for a in names]
    return []


def _only_among(contract: Mapping[str, Any], action: str, among: str) -> bool:
    spec = (contract.get("actions") or {}).get(action)
    if not isinstance(spec, Mapping):
        return False
    by = spec.get("by")
    allowed = [by] if isinstance(by, str) else list(by or [])
    return bool(allowed) and all(common.raw_is_a(contract, t, among) for t in allowed)


# ---------------------------------------------------------------------------
# Run time
# ---------------------------------------------------------------------------

_ORDERS: weakref.WeakKeyDictionary[Any, dict[str, tuple[Any, list[str]]]] = weakref.WeakKeyDictionary()


def ordered(world: Any, name: str, where: str) -> list[Entity]:
    """This round's order for the order mechanism ``name`` (skipped agents left out)."""
    cfg = common.config(world, name, KEY, OrderConfig, where)
    state = (world.journal.version, world.round, world.stage)
    cached = _ORDERS.setdefault(world, {}).get(name)
    if cached is not None and cached[0] == state:
        return [world.entities[i] for i in cached[1]]
    # Every seat ever declared (removed members included), so rotation advances one seat per round
    # whoever is skipped or gone; skipped and removed members are left out only afterwards.
    kinds = set(world.contract.subtypes(cfg.who))
    seats = [e for e in world.entities.values() if e.entity_type in kinds]
    try:
        if cfg.by:
            key = compile_expr(cfg.by)
            keyed = [(key(world.scope(it=m)), i, m) for i, m in enumerate(seats)]
            keyed.sort(key=lambda t: t[1])
            keyed.sort(key=lambda t: t[0], reverse=not cfg.ascending)
            seats = [m for _, _, m in keyed]
        if cfg.rotate and seats:
            shift = (world.round - 1) % len(seats)
            seats = seats[shift:] + seats[:shift]
        members = [m for m in seats if m.alive]
        if cfg.skip:
            skip = compile_expr(cfg.skip)
            members = [m for m in members if not truthy(skip(world.scope(it=m)))]
    except ExprError as exc:
        raise RunError(str(exc), f"mechanisms.{name}") from None
    except TypeError:
        raise RunError("`by` must give comparable values (numbers or text)", f"mechanisms.{name}.by") from None
    if cfg.snake and world.round % 2 == 0:
        members.reverse()
    _ORDERS[world][name] = (state, [m.id for m in members])
    return members


@function("turn_rank(entity, order)",
          "The entity's place (0 = first) in this round's turn order; skipped agents come last.",
          min_args=2, max_args=2)
def _turn_rank(call: Call) -> int:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    if entity is None:
        raise ExprError(f"$turn_rank: expected an entity, got {call.arg(0)!r}", call.source)
    try:
        order = ordered(world, str(call.arg(1)), call.source)
    except RunError as exc:
        raise ExprError(str(exc), call.source) from None
    ids = [m.id for m in order]
    return ids.index(entity.id) if entity.id in ids else len(ids)


@function("turn_order(order)", "The agents in this round's turn order (skipped agents left out).", min_args=1,
          max_args=1)
def _turn_order(call: Call) -> list[Entity]:
    try:
        return ordered(call.scope.world, str(call.arg(0)), call.source)
    except RunError as exc:
        raise ExprError(str(exc), call.source) from None


def _check_extra(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
    name = effect["flow"]
    if not common.parsed(checker.c.mechanisms[name], OrderConfig).extra_turns:
        return [(f"{path}.flow", f"turn order '{name}' does not allow extra turns",
                 f"set mechanisms.{name}.extra_turns: true")]
    return []


@family_action("flow", ("order",), "extra_turn", keys=("who",), required=("who",), check=_check_extra,
               example='{"flow": "initiative", "action": "extra_turn", "who": "$actor"}  (the agent takes another turn '
                       'this round)')
def _extra_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["flow"]
    cfg = common.config(world, name, KEY, OrderConfig, where)
    if not cfg.extra_turns:
        raise RunError(f"turn order '{name}' does not allow extra turns", where)
    counter = f"{name}_extra"
    for entity in common.entities_of(world, runner.eval(effect["who"], vars), f"{where}.who"):
        if counter in entity.properties:
            waiting = common.whole(entity.properties[counter], f"{where}.{counter}", low=0)
            world.set_prop(entity, counter, min(cfg.max_extra, waiting + 1))
