"""Hidden roles: a role deck dealt at random, teams that may know each other, role-gated tools and
reveal on elimination — the ``groups.roles`` mode.

``role`` and ``team`` are private player props, so nobody else can inspect them; the generated
views show a player only their own role and the teammates their team is allowed to know
(:func:`known_role` is the single rule). ``revealed_role`` is public and set when a role is revealed.
"""
from __future__ import annotations

import re
from typing import Any, cast, Dict, List, Literal, Mapping, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from ..entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function, is_expr
from ..registry import MechanismError, family_action, mode
from .contract_cache import parse_kind, per_contract

__all__ = ["RolesConfig", "known_role"]

def _props(entity: Entity) -> Dict[str, Any]:
    """An entity's properties, typed loosely: values are whatever the contract declared."""
    return cast(Dict[str, Any], entity.properties)


_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")
REST = "rest"
KEY = "groups.roles"


class RolesConfig(BaseModel):
    """Secret roles dealt to the agents of one type."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Agent type that receives roles.")
    deck: Dict[str, Union[int, str]] = Field(
        ..., description="{role: count}; a count may be an expression, and one role may be \"rest\" (everyone left).")
    teams: Dict[str, List[str]] = Field(default_factory=dict, description="{team: [roles]}; a role in no team is its own team.")
    know: List[str] = Field(default_factory=list, description="Teams (or roles) whose members know each other from the start.")
    alive: str = Field("living", description="Bool player property that says a player is still in the game (not the "
                                             "built-in `alive`, which turns false only when an entity is removed).")
    reveal: Literal["elimination", "never"] = Field("elimination", description="Reveal a role when its player is eliminated.")
    actions: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict, description="Role-gated tools: {name: {...action, \"roles\": [roles]}}; private unless said otherwise.")
    views: bool = Field(True, description="Generate views of your role, your known teammates and who is still in.")


def _configs(world: Any) -> Dict[str, RolesConfig]:
    return per_contract(world, KEY, lambda contract: parse_kind(contract, KEY, RolesConfig), {})


def _config_for(world: Any, entity: Entity) -> Optional[RolesConfig]:
    return next((c for c in _configs(world).values() if world.is_a(entity.entity_type, c.who)), None)


def team_of(config: RolesConfig, role: str) -> str:
    return next((team for team, roles in config.teams.items() if role in roles), role)


def known_role(world: Any, viewer: Any, player: Any) -> str:
    """The role ``viewer`` knows ``player`` has, or '' (their own, a revealed one, or a teammate their team may know)."""
    if not isinstance(player, Entity):
        return ""
    config = _config_for(world, player)
    props = _props(player)
    if config is None or not props.get("role"):
        return ""
    if props.get("revealed_role"):
        return str(props["revealed_role"])
    if not isinstance(viewer, Entity):
        return ""
    if viewer.id == player.id:
        return str(props["role"])
    mine = _props(viewer)
    if mine.get("team") and mine.get("team") == props.get("team") and (
            mine["team"] in config.know or (mine.get("role") in config.know and mine.get("role") == props.get("role"))):
        return str(props["role"])
    return ""


def deal_roles(world: Any, config: RolesConfig, where: str) -> None:
    players = list(world.entities_of(config.who))
    pool: List[str] = []
    rest: Optional[str] = None
    for role, raw in config.deck.items():
        if raw == REST:
            rest = role
            continue
        count = compile_expr(raw)(world.scope()) if is_expr(raw) else raw
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RunError(f"the count of {role} must be a whole number ≥ 0, got {count!r}", where)
        pool += [role] * count
    if len(pool) > len(players) or (rest is None and len(pool) != len(players)):
        raise RunError(f"the role deck holds {len(pool)} roles for {len(players)} players", where)
    pool += [rest] * (len(players) - len(pool)) if rest else []
    world.rng.shuffle(pool)
    for player, role in zip(players, pool):
        world.set_prop(player, "role", role)
        world.set_prop(player, "team", team_of(config, role))
        world.set_prop(player, "revealed_role", "")


def _article(word: str) -> str:
    return ("an " if word[:1].lower() in "aeiou" else "a ") + word


def _players(runner: Any, raw: Any, vars: Dict[str, Any], where: str) -> List[Entity]:
    value = runner.eval(raw, vars)
    items = value if isinstance(value, list) else ([] if value is None else [value])
    out = []
    for item in items:
        entity = runner.world.entity(item)
        if entity is None:
            raise RunError(f"expected players (entities or ids), got {item!r}", where)
        out.append(entity)
    return out


def _reveal(world: Any, player: Entity) -> None:
    world.set_prop(player, "revealed_role", _props(player).get("role") or "")


def _holders(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> Tuple[RolesConfig, List[Entity]]:
    """The named roles mechanism's config and the players `who` names, each one of its players."""
    name = effect["groups"]
    config = _configs(runner.world)[name]
    players = _players(runner, effect["who"], vars, f"{where}.who")
    for player in players:
        if not runner.world.is_a(player.entity_type, config.who):
            raise RunError(f"{player.id} is a {player.entity_type}, not a {config.who} holding a role of {name}", f"{where}.who")
    return config, players


@family_action("groups", ("roles",), "deal", internal=True,
               example='{"groups": "roles", "action": "deal"}  (shuffle the role deck and deal it; generated for round 1)')
def _deal_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    deal_roles(runner.world, _configs(runner.world)[effect["groups"]], where)


@family_action("groups", ("roles",), "eliminate", keys=("who", "say"), required=("who",), templates=("say",),
               example='{"groups": "roles", "action": "eliminate", "who": "$out", "say": "{$out.name} is exiled."}  (out of '
                       'the game; the role is revealed unless reveal: never)')
def _eliminate_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    config, players = _holders(runner, effect, vars, where)
    for player in players:
        if not _props(player).get(config.alive):
            continue
        world.set_prop(player, config.alive, False)
        text = runner.text(effect["say"], vars) if effect.get("say") else f"{player.name} is out of the game."
        if config.reveal == "elimination":
            _reveal(world, player)
            text += f" They were {_article(_props(player)['role'])}."
        world.emit("eliminated", text, data={"player": player.id})


@family_action("groups", ("roles",), "reveal", keys=("who",), required=("who",),
               example='{"groups": "roles", "action": "reveal", "who": "$filter(player, true)"}  (make these players\' '
                       'roles public)')
def _reveal_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    players = [p for p in _holders(runner, effect, vars, where)[1] if not _props(p).get("revealed_role")]
    for player in players:
        _reveal(world, player)
    if players:
        world.emit("revealed", "Roles revealed: " + ", ".join(f"{p.name} was {_article(_props(p)['role'])}" for p in players) + ".")


@function("known_role(viewer, player)", "The role `viewer` knows `player` has ('' when unknown): their own, a revealed "
          "role, or a teammate's when their team knows each other.", min_args=2, max_args=2)
def _known_role_function(call: Call) -> str:
    world: Any = call.scope.world
    return known_role(world, world.entity(call.arg(0)), world.entity(call.arg(1)))


@function("teammates(player)", "The other players `player` knows are on their team (empty when their team is secret).",
          min_args=1, max_args=1)
def _teammates_function(call: Call) -> List[Entity]:
    world: Any = call.scope.world
    player = world.entity(call.arg(0))
    if player is None:
        raise ExprError(f"$teammates: expected a player, got {call.arg(0)!r}", call.source)
    config = _config_for(world, player)
    if config is None:
        return []
    return [q for q in world.entities_of(config.who)
            if q.id != player.id and _props(q).get("team") == _props(player).get("team") and known_role(world, player, q)]


@function("team_alive(team)", "How many players of `team` (or role) are still in the game.", min_args=1, max_args=1)
def _team_alive_function(call: Call) -> int:
    team = call.arg(0)
    world: Any = call.scope.world
    total = 0
    for config in _configs(world).values():
        total += sum(1 for p in world.entities_of(config.who) if _props(p).get(config.alive)
                     and team in (_props(p).get("team"), _props(p).get("role")))
    return total


@mode("groups", "roles", RolesConfig,
      "Secret roles: deals a shuffled role deck in round 1 into private `role` and `team` props, lets listed teams "
      "know each other, generates role-gated private tools, and reveals a role (public `revealed_role`) when the "
      "`eliminate` action takes its player out. Views show only your own role and known teammates. "
      "Functions: $known_role, $teammates, $team_alive.",
      example={"who": "player", "deck": {"werewolf": 2, "seer": 1, "villager": "rest"},
               "teams": {"wolves": ["werewolf"], "village": ["seer", "villager"]}, "know": ["wolves"],
               "actions": {"inspect_player": {"roles": ["seer"], "params": {"target": {"type": "entity", "of": "player"}},
                                              "do": ["$seen = $params.target.role"], "outcome": "{$params.target.name} is {$seen}."}}})
def _expand_roles(name: str, config: RolesConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    types = contract.get("types") or {}
    if config.who not in types:
        raise MechanismError(f"who '{config.who}' is not a declared type", f"types: {', '.join(types) or 'none'}", "who")
    roles = list(config.deck)
    for role in roles:
        if not _NAME.match(role):
            raise MechanismError(f"'{role}' is not a valid role name", "use letters, digits and _", f"deck.{role}")
    if sum(1 for v in config.deck.values() if v == REST) > 1:
        raise MechanismError("only one role can be \"rest\"", None, "deck")
    for team, members in config.teams.items():
        if not _NAME.match(team):
            raise MechanismError(f"'{team}' is not a valid team name", "use letters, digits and _", f"teams.{team}")
        for role in members:
            if role not in roles:
                raise MechanismError(f"team {team} lists '{role}', which is not in the deck", f"roles: {', '.join(roles)}", f"teams.{team}")
    teams = list(dict.fromkeys(team_of(config, role) for role in roles))
    for entry in config.know:
        if entry not in teams and entry not in roles:
            raise MechanismError(f"know lists '{entry}', which is neither a team nor a role", f"teams: {', '.join(teams)}", "know")
    alive = config.alive
    actions: Dict[str, Any] = {}
    for action_name, raw in config.actions.items():
        spec = dict(raw)
        gate = spec.pop("roles", None)
        allowed = [gate] if isinstance(gate, str) else list(gate or [])
        unknown = [r for r in allowed if r not in roles]
        if not allowed or unknown:
            raise MechanismError(f"a role-gated tool needs `roles` from the deck (got {gate!r})", f"roles: {', '.join(roles)}",
                                 f"actions.{action_name}.roles")
        who = " or ".join(allowed)
        spec["by"] = config.who
        spec["when"] = [{"expr": f"$actor.{alive}", "why": "You are out of the game."},
                        {"expr": f"$actor.role in [{', '.join(allowed)}]", "why": f"Only {_article(who)} can do this."}] + list(spec.get("when") or [])
        spec.setdefault("private", True)
        actions[action_name] = spec
    fragment: Dict[str, Any] = {
        "types": {config.who: {"props": {
            "role": {"type": "enum", "values": [""] + roles, "default": "", "private": True},
            "team": {"type": "enum", "values": [""] + teams, "default": "", "private": True},
            alive: {"type": "bool", "default": True},
            "revealed_role": {"type": "text", "default": "", "description": "The role, once revealed to everyone."},
        }}},
        "events": [{"name": f"{name}_deal", "at": 1, "do": [{"groups": name, "action": "deal"}]}],
        "actions": actions,
    }
    if config.views:
        out = f"{{$'. You are out of the game' if not $actor.{alive} else ''}}"
        fragment["views"] = {
            f"{name}_you": {"for": config.who, "title": "Your role",
                            "show": f"You are {{$actor.role|upper}}{{$' (team ' + $actor.team + ')' if $actor.team != $actor.role else ''}}{out}."},
            f"{name}_team": {"for": config.who, "title": "Your team", "of": "$teammates($actor)",
                             "show": f"[{{id}}] {{name}} — {{$known_role($actor, $it)}}{{$'' if $it.{alive} else ' (out)'}}"},
            f"{name}_players": {"for": config.who, "title": "Players", "of": config.who,
                                "show": f"[{{id}}] {{name}} — {{$'in' if $it.{alive} else 'out'}}"
                                        "{$' (was ' + $it.revealed_role + ')' if $it.revealed_role != '' else ''}"},
        }
    return fragment
