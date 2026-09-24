"""Relationships and factions, modes of the ``groups`` family: decaying trust with threshold events; alliances and
``$allies``.

.. code-block:: json

    "mechanisms": {
      "bonds": {"kind": "groups", "mode": "relationships", "relations": {"trust": {"baseline": 0, "decay": 0.1,
                "thresholds": [{"at": 0.7, "direction": "above", "say": "{$from.name} now trusts {$to.name}."}]}}},
      "blocs": {"kind": "groups", "mode": "factions", "who": "nation", "factions": {"entente": {"members": ["fr",
      "uk"]}},
                "alliances": true}}

``relationships`` manages relations (declaring them unless the author already did): every round
each link moves ``decay`` of the way back to ``baseline``, and the ``relate`` action changes a link
(created at the baseline when missing). A threshold fires when a link crosses it — once, or again
after it crosses back (hysteresis) — emitting news to ``to`` and running ``do`` with ``$from``,
``$to``, ``$value``. The crossed state lives in the world prop ``<name>_crossed``.

``factions`` keeps the world prop ``<name>``: {faction: {title, members, invited, allies,
proposals, open}}. Two agents are allies when they share a faction or their factions are allied;
an alliance forms when both factions have proposed it.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..errors import RunError
from ..expr import Call, ExprError, function
from ..expr.objects import Entity
from ..registry import MechanismError, config_data, family_action, mechanism_config, mode
from ..world.live import Abort
from ._common import ToolsSetting, tools_field
from ._social import NAME, check_expr, eid, entity, named_use, require_type, seat_order

__all__ = ["RelationshipsConfig", "FactionsConfig"]

RELATIONSHIPS = "groups.relationships"
FACTIONS = "groups.factions"
_TICK_EPSILON = 1e-9


class Threshold(BaseModel):
    """An event when a relation crosses a value."""

    model_config = ConfigDict(extra="forbid")

    at: float
    direction: Literal["above", "below"] = Field("above",
                                                 description="Fires when the value rises to/above, or falls to/below, "
                                                             "`at`.")
    say: str = Field("", description="News text (template over $from, $to, $value).")
    to: Literal["none", "from", "to", "both", "all"] = Field("both", description="Who reads the news.")
    do: list[Any] = Field(default_factory=list, description="Effects when it fires ($from, $to, $value).")
    once: bool = Field(False, description="Fire at most once per pair; otherwise again after crossing back.")


class RelationDynamics(BaseModel):
    """A relation whose values drift back to a baseline."""

    model_config = ConfigDict(extra="forbid")

    baseline: float = Field(0.0, description="Where values settle (and where a new link starts).")
    decay: float = Field(0.0, ge=0, le=1, description="Share of the gap to the baseline closed every round.")
    min: float = -1.0
    max: float = 1.0
    symmetric: bool = False
    description: str = ""
    thresholds: list[Threshold] = Field(default_factory=list)


class RelationshipsConfig(BaseModel):
    """Relations that decay toward a baseline and fire events at thresholds."""

    model_config = ConfigDict(extra="forbid")

    relations: dict[str, RelationDynamics] = Field(...,
                                                   description="{relation: {baseline, decay, min, max, symmetric, "
                                                               "thresholds}}.")
    phase: Literal["start", "end"] = Field("end", description="When relations decay each round.")


# ---------------------------------------------------------------------------
# relationships
# ---------------------------------------------------------------------------


def _check_thresholds(runner: Any, name: str, relation: str, spec: RelationDynamics, a: str, b: str,
                      where: str) -> None:
    world = runner.world
    value = world.relation(a, b, relation)
    if value is None or not spec.thresholds:
        return
    crossed_prop = f"{name}_crossed"
    for index, threshold in enumerate(spec.thresholds):
        key = f"{relation}|{a}|{b}|{index}"
        crossed = world.props.get(crossed_prop) or {}
        holds = value >= threshold.at if threshold.direction == "above" else value <= threshold.at
        if key in crossed:
            if not holds and not threshold.once:  # crossed back: armed again
                world.set_world(crossed_prop, {k: v for k, v in crossed.items() if k != key})
            continue
        if holds:
            # Marked before firing, so effects that change this relation again cannot fire it twice.
            world.set_world(crossed_prop, {**crossed, key: True})
            _fire(runner, name, relation, threshold, a, b, value, where)


def _fire(runner: Any, name: str, relation: str, threshold: Threshold, a: str, b: str, value: float,
          where: str) -> None:
    world = runner.world
    local = {"from": world.entities.get(a), "to": world.entities.get(b), "value": value}
    if threshold.say:
        audience = {"none": (), "from": (a,), "to": (b,), "both": (a, b), "all": None}[threshold.to]
        if audience != ():
            world.emit(name, runner.text(threshold.say, local), to=audience,
                       data={"mechanism": name, "relation": relation, "from": a, "to": b, "value": value})
    if threshold.do:
        runner.run(threshold.do, local, f"mechanisms.{name}.relations.{relation}.thresholds.do")


def _relate_check(checker: Any, effect: dict[str, Any], path: str) -> list[tuple[str, str, str | None]]:
    issues: list[tuple[str, str, str | None]] = []
    if ("add" in effect) == ("set" in effect):
        issues.append((path, "`groups.relate` takes exactly one of `add` or `set`",
                       "add changes the value by an amount; set replaces it"))
    name, relation = effect["groups"], effect.get("relation")
    try:
        relations = RelationshipsConfig.model_validate(config_data(checker.c.mechanisms[name])).relations
    except ValidationError:  # the config's own errors are reported against the mechanism
        return issues
    if isinstance(relation, str) and relation not in relations:
        issues.append((f"{path}.relation", f"'{relation}' is not a relation of {name}",
                       f"relations: {', '.join(relations)}"))
    return issues


@family_action("groups", ("relationships",), "relate", keys=("relation", "from", "to", "add", "set"),
               required=("relation", "from", "to"), literal=("relation",), check=_relate_check,
               example='{"groups": "bonds", "action": "relate", "relation": "trust", "from": "$actor", "to": '
                       '"$params.partner", "add": 0.2}  (change a relation by `add`, which stops at its min/max, or '
                       'replace it with `set`; a missing link starts at its baseline; thresholds fire)')
def _relate_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name, relation = effect["groups"], effect["relation"]
    config = mechanism_config(world, name, RELATIONSHIPS, RelationshipsConfig)
    spec = config.relations.get(relation)
    if spec is None:
        raise RunError(f"'{relation}' is not a relation of {name} (relations: {', '.join(config.relations)})",
                       f"{where}.relation")
    if ("add" in effect) == ("set" in effect):
        raise RunError("`groups.relate` takes exactly one of `add` or `set`", where)
    a = entity(world, runner.eval(effect["from"], vars), f"{where}.from")
    b = entity(world, runner.eval(effect["to"], vars), f"{where}.to")
    amount = runner.eval(effect["add"] if "add" in effect else effect["set"], vars)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise RunError(f"`relate` needs a number, got {amount!r}", where)
    current = world.relation(a, b, relation)
    if current is None:
        current = spec.baseline
    if "add" in effect:  # a relation lives in its range: adding stops at its min or max
        amount = min(max(current + amount, spec.min), spec.max)
    world.link(relation, a, b, amount, where)
    key = world._key(relation, a.id, b.id)
    _check_thresholds(runner, name, relation, spec, key[0], key[1], where)


@family_action("groups", ("relationships",), "tick", internal=True,
               example='{"groups": "bonds", "action": "tick"}  (one round of decay toward baselines, then thresholds; '
                       'generated for you)')
def _tick_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["groups"]
    config = mechanism_config(world, name, RELATIONSHIPS, RelationshipsConfig)
    order = seat_order(world)
    for relation, spec in config.relations.items():
        pairs = sorted(world.links.get(relation, {}), key=lambda k: (order.get(k[0], 0), order.get(k[1], 0)))
        for a, b in pairs:
            value = world.links[relation][(a, b)]
            if spec.decay > 0:
                moved = value + (spec.baseline - value) * spec.decay
                if abs(moved - spec.baseline) < _TICK_EPSILON:
                    moved = spec.baseline
                if moved != value:
                    world.link(relation, a, b, moved, where)
            _check_thresholds(runner, name, relation, spec, a, b, where)


@mode("groups", "relationships", RelationshipsConfig,
      "Relations (trust, affinity, rivalry) that drift back toward a baseline every round and fire threshold events "
      "(news and effects with $from, $to, $value) when crossed. Change them with the `relate` action; read them with "
      "$relation(a, b, kind).",
      example={"relations": {"trust": {"baseline": 0, "decay": 0.1, "thresholds": [
          {"at": 0.7, "say": "{$from.name} now trusts {$to.name}."}]}}})
def _expand_relationships(name: str, config: RelationshipsConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    relations: dict[str, Any] = {}
    for relation, spec in config.relations.items():
        if not NAME.match(relation):
            raise MechanismError(f"'{relation}' is not a valid relation name", "use letters, digits and _", "relations")
        if spec.min > spec.max or not spec.min <= spec.baseline <= spec.max:
            raise MechanismError(f"{relation}: need min ≤ baseline ≤ max", None, f"relations.{relation}")
        relations[relation] = {"symmetric": spec.symmetric, "min": spec.min, "max": spec.max,
                               "description": spec.description or f"Drifts toward {spec.baseline}."}
        for index, threshold in enumerate(spec.thresholds):
            if threshold.say:
                _check_template(threshold.say, f"relations.{relation}.thresholds[{index}].say")
    return {"relations": relations,
            "world": {f"{name}_crossed": {"type": "map", "default": {},
                                          "description": "Thresholds currently crossed."}},
            "events": [{"name": f"{name}_tick", "phase": config.phase, "do": [{"groups": name, "action": "tick"}]}]}


def _check_template(source: str, field: str) -> None:
    from ..expr.template import compile_template

    try:
        template = compile_template(source, None)
    except ExprError as exc:
        raise MechanismError(exc.detail, f"template: {source}", field) from None
    for expr in template.expressions:
        check_expr(expr.source, field, ("from", "to", "value"))


# ---------------------------------------------------------------------------
# factions
# ---------------------------------------------------------------------------


class FactionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = ""
    members: list[str] = Field(default_factory=list)
    open: bool = Field(False, description="Anyone may join without an invitation.")


class FactionsConfig(BaseModel):
    """Factions and alliances among agents."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Agent type that belongs to factions.")
    factions: dict[str, FactionSpec] = Field(default_factory=dict, description="{id: {title, members, open}}.")
    allies: list[list[str]] = Field(default_factory=list, description="Starting alliances as [faction, faction] pairs.")
    one: bool = Field(True, description="An agent belongs to at most one faction.")
    found: bool = Field(False, description="Members may found new factions.")
    alliances: bool = Field(True, description="Offer tools to propose and break alliances between factions.")
    joining: bool = Field(True, description="Offer join, leave and invite tools.")
    tools: ToolsSetting = tools_field()


def _factions(world: Any, name: str) -> dict[str, dict[str, Any]]:
    return world.props.get(name) or {}


def _mine(factions: Mapping[str, Mapping[str, Any]], agent: str) -> list[str]:
    return [f for f, spec in factions.items() if agent in spec["members"]]


def allies(world: Any, name: str, a: str, b: str) -> bool:
    factions = _factions(world, name)
    mine, theirs = _mine(factions, a), _mine(factions, b)
    if a == b:
        return bool(mine)
    return any(f == g or g in factions[f]["allies"] for f in mine for g in theirs)


@function("allies(a, b, mechanism?)",
          "True when a and b share a faction or belong to allied factions (groups factions mode).",
          min_args=2, max_args=3)
def _allies_fn(call: Call) -> bool:
    world: Any = call.scope.world
    name = named_use(call, FACTIONS, 2)
    return allies(world, name, eid(call.arg(0), call.source), eid(call.arg(1), call.source))


@function("faction_of(agent, mechanism?)", "Ids of the factions the agent belongs to.", min_args=1, max_args=2)
def _faction_of_fn(call: Call) -> list[str]:
    world: Any = call.scope.world
    return _mine(_factions(world, named_use(call, FACTIONS, 1)), eid(call.arg(0), call.source))


@function("factions(mechanism?)", "Every faction: [{id, title, members, allies, open}].", min_args=0, max_args=1)
def _factions_fn(call: Call) -> list[dict[str, Any]]:
    world: Any = call.scope.world
    return [{"id": f, "title": spec["title"], "members": list(spec["members"]), "allies": list(spec["allies"]),
             "open": spec["open"]} for f, spec in _factions(world, named_use(call, FACTIONS, 0)).items()]


@function("joinable(agent, mechanism?)", "Factions the agent may join now: open ones and those it was invited to.",
          min_args=1, max_args=2)
def _joinable_fn(call: Call) -> list[str]:
    world: Any = call.scope.world
    name = named_use(call, FACTIONS, 1)
    config = mechanism_config(world, name, FACTIONS, FactionsConfig)
    agent = eid(call.arg(0), call.source)
    factions = _factions(world, name)
    if config.one and _mine(factions, agent):
        return []
    return [f for f, spec in factions.items() if agent not in spec["members"]
            and (spec["open"] or agent in spec["invited"])]


# ---------------------------------------------------------------------------
# The groups op's factions actions
# ---------------------------------------------------------------------------

#: action → (keys it needs, keys it may take, example keys, what it does). The agent is `who`, default $actor.
_FACTION_ACTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...], str, str]] = {
    "join": (("in",), (), '"in": "$params.faction"', "join a faction that is open or invited you"),
    "leave": (("in",), (), '"in": "$params.faction"', "leave a faction"),
    "invite": (("in", "guest"), (), '"in": "$params.faction", "guest": "$params.guest"',
               "invite `guest` into your faction"),
    "found": ((), ("title",), '"title": "$params.title"', "found a new faction with `who` as its first member"),
    "ally": (("in", "other"), (), '"in": "$params.faction", "other": "$params.other"',
             "propose an alliance with `other`, or accept the one it proposed"),
    "break_alliance": (("in", "other"), (), '"in": "$params.faction", "other": "$params.other"',
                       "end the alliance between `in` and `other`"),
    "add": (("in",), (), '"in": "rebels", "who": "$it"', "put `who` in a faction without consent"),
    "remove": (("in",), (), '"in": "rebels", "who": "$it"', "take `who` out of a faction without consent"),
}


def _faction_runner(action: str) -> Callable[[Any, dict[str, Any], dict[str, Any], str], None]:
    def run(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
        world = runner.world
        name = effect["groups"]
        config = mechanism_config(world, name, FACTIONS, FactionsConfig)
        who = entity(world, runner.eval(effect["who"], vars) if "who" in effect else vars.get("actor"), where,
                     config.who)
        factions = {f: {**spec, "members": list(spec["members"]), "invited": list(spec["invited"]),
                        "allies": list(spec["allies"]), "proposals": list(spec["proposals"])}
                    for f, spec in _factions(world, name).items()}

        def faction(key: str) -> str:
            value = runner.eval(effect[key], vars)
            if not isinstance(value, str) or value not in factions:
                raise Abort(f"There is no faction {value!r}.")
            return value

        if action == "found":
            _found(world, name, config, factions, who, runner.eval(effect.get("title", ""), vars))
        elif action in ("join", "add"):
            _join(world, name, config, factions, who, faction("in"), consent=action == "join")
        elif action in ("leave", "remove"):
            fid = faction("in")
            if who.id not in factions[fid]["members"]:
                raise Abort(f"{who.name} is not in {fid}.")
            factions[fid]["members"].remove(who.id)
            world.emit(name, f"{who.name} left faction {fid}.", actor=who.id, data={"mechanism": name, "faction": fid})
        elif action == "invite":
            fid = faction("in")
            guest = entity(world, runner.eval(effect["guest"], vars), f"{where}.guest", config.who)
            if who.id not in factions[fid]["members"]:
                raise Abort(f"You are not in {fid}.")
            if guest.id in factions[fid]["members"] or guest.id in factions[fid]["invited"]:
                raise Abort(f"{guest.name} is already in or invited to {fid}.")
            factions[fid]["invited"].append(guest.id)
            world.emit(name, f"{who.name} invited you to join faction {fid}.", actor=who.id, to=(guest.id,),
                       data={"mechanism": name, "faction": fid})
        else:
            fid, other = faction("in"), faction("other")
            if who.id not in factions[fid]["members"]:
                raise Abort(f"You are not in {fid}.")
            if fid == other:
                raise Abort("A faction cannot ally with itself.")
            (_ally if action == "ally" else _break)(world, name, factions, fid, other, who)
        world.set_world(name, factions)

    return run


def _register_faction_actions() -> None:
    for action, (needs, takes, fields, doc) in _FACTION_ACTIONS.items():
        example = '{"groups": "blocs", "action": "' + action + '", ' + fields + f"}}  ({doc})"
        family_action("groups", ("factions",), action, keys=(*needs, *takes, "who"), required=needs,
                      example=example)(_faction_runner(action))


_register_faction_actions()


def _found(world: Any, name: str, config: FactionsConfig, factions: dict[str, Any], who: Entity, title: Any) -> None:
    if not config.found:
        raise Abort("New factions cannot be founded here.")
    if config.one and _mine(factions, who.id):
        raise Abort("Leave your faction before founding another.")
    if title is not None and (not isinstance(title, str) or len(title) > 60):
        raise Abort("A faction title is text of at most 60 characters.")
    n = len(factions) + 1
    while f"faction_{n}" in factions:
        n += 1
    fid = f"faction_{n}"
    factions[fid] = {"title": title or "", "members": [who.id], "invited": [], "allies": [], "proposals": [],
                     "open": False}
    world.emit(name, f"{who.name} founded faction {fid}.", actor=who.id, data={"mechanism": name, "faction": fid})


def _join(world: Any, name: str, config: FactionsConfig, factions: dict[str, Any], who: Entity, fid: str,
          consent: bool) -> None:
    spec = factions[fid]
    if who.id in spec["members"]:
        raise Abort(f"{who.name} is already in {fid}.")
    if consent and not (spec["open"] or who.id in spec["invited"]):
        raise Abort(f"Joining {fid} needs an invitation.")
    current = _mine(factions, who.id)
    if config.one and current:
        if consent:
            raise Abort(f"Leave {current[0]} before joining {fid}.")
        for old in current:
            factions[old]["members"].remove(who.id)
    if who.id in spec["invited"]:
        spec["invited"].remove(who.id)
    spec["members"].append(who.id)
    world.emit(name, f"{who.name} joined faction {fid}.", actor=who.id, data={"mechanism": name, "faction": fid})


def _ally(world: Any, name: str, factions: dict[str, Any], fid: str, other: str, who: Entity) -> None:
    if other in factions[fid]["allies"]:
        raise Abort(f"{fid} and {other} are already allied.")
    if fid in factions[other]["proposals"]:
        factions[other]["proposals"].remove(fid)
        factions[fid]["allies"].append(other)
        factions[other]["allies"].append(fid)
        world.emit(name, f"Factions {fid} and {other} are now allied.", actor=who.id,
                   data={"mechanism": name, "alliance": [fid, other]})
        return
    if other in factions[fid]["proposals"]:
        raise Abort(f"{fid} already proposed an alliance to {other}.")
    factions[fid]["proposals"].append(other)
    world.emit(name, f"Faction {fid} proposes an alliance with your faction {other}; ally with {fid} to accept.",
               actor=who.id, to=tuple(factions[other]["members"]), data={"mechanism": name, "proposal": [fid, other]})


def _break(world: Any, name: str, factions: dict[str, Any], fid: str, other: str, who: Entity) -> None:
    if other not in factions[fid]["allies"]:
        raise Abort(f"{fid} and {other} are not allied.")
    factions[fid]["allies"].remove(other)
    factions[other]["allies"].remove(fid)
    world.emit(name, f"{who.name} broke the alliance between {fid} and {other}.", actor=who.id,
               data={"mechanism": name, "alliance": [fid, other]})


_FACTION_LINE = ("{id}{$' — ' if $it.title else ''}{$it.title or ''}: {$len($it.members)} members "
                 "({$join($map($it.members, $text($entity($it))))}){$' · allied with ' + $join($it.allies) "
                 "if $len($it.allies) > 0 else ''}{$' · open to all' if $it.open else ''}")


@mode("groups", "factions", FactionsConfig,
      "Factions and alliances: membership with invitations (or open factions), founding, and alliances that form when "
      "both factions propose them. State in the world prop `<name>`; tools `<name>_join`, `<name>_leave`, "
      "`<name>_invite`, `<name>_found`, `<name>_ally`, `<name>_break_alliance`, and the same actions of the `groups` "
      "op for effects. Read with $allies(a, b), $faction_of(agent), $factions(), $joinable(agent); with several "
      "factions mechanisms, name one as the last argument ($factions('guilds')).",
      example={"who": "nation", "factions": {"entente": {"members": ["fr", "uk"]}, "central": {"members": ["de"]}}})
def _expand_factions(name: str, config: FactionsConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    require_type(contract, config.who, "who", agent=True)
    state: dict[str, Any] = {}
    for fid, spec in config.factions.items():
        if not NAME.match(fid):
            raise MechanismError(f"'{fid}' is not a valid faction id", "use letters, digits and _", "factions")
        state[fid] = {"title": spec.title, "members": list(dict.fromkeys(spec.members)), "invited": [], "allies": [],
                      "proposals": [], "open": spec.open}
    if config.one:
        seen: dict[str, str] = {}
        for fid, spec in state.items():
            for member in spec["members"]:
                if member in seen:
                    raise MechanismError(f"'{member}' is in both {seen[member]} and {fid}",
                                         "set one: false to allow it", "factions")
                seen[member] = fid
    for pair in config.allies:
        if len(pair) != 2 or pair[0] == pair[1] or any(f not in state for f in pair):
            raise MechanismError(f"alliance {pair} must name two declared factions", None, "allies")
        for a, b in (pair, pair[::-1]):
            if b not in state[a]["allies"]:
                state[a]["allies"].append(b)
    members = config.who
    actions: dict[str, Any] = {}
    if config.joining:
        actions[f"{name}_join"] = {"by": members, "description": "Join a faction that is open or invited you.",
                                   "params": {"faction": {"type": "enum", "values": f"$joinable($actor, '{name}')"}},
                                   "when": [{"expr": f"$len($joinable($actor, '{name}')) > 0",
                                             "why": "No faction will take you now."}],
                                   "do": [{"groups": name, "action": "join", "in": "$params.faction"}]}
        actions[f"{name}_leave"] = {"by": members, "description": "Leave a faction.",
                                    "params": {"faction": {"type": "enum", "values": f"$faction_of($actor, '{name}')"}},
                                    "when": [{"expr": f"$len($faction_of($actor, '{name}')) > 0",
                                              "why": "You are in no faction."}],
                                    "do": [{"groups": name, "action": "leave", "in": "$params.faction"}]}
        actions[f"{name}_invite"] = {"by": members, "description": "Invite someone into your faction.",
                                     "params": {"faction": {"type": "enum", "values": f"$faction_of($actor, '{name}')"},
                                                "guest": {"type": "entity", "of": members,
                                                          "description": "Who to invite."}},
                                     "when": [{"expr": f"$len($faction_of($actor, '{name}')) > 0",
                                               "why": "You are in no faction."}],
                                     "do": [{"groups": name, "action": "invite", "in": "$params.faction",
                                             "guest": "$params.guest"}],
                                     "private": True, "outcome": "Invitation sent to {$params.guest.name}."}
    if config.found:
        actions[f"{name}_found"] = {"by": members, "description": "Found a new faction with you as its first member.",
                                    "params": {"title": {"type": "text", "max_len": 60, "default": ""}},
                                    "do": [{"groups": name, "action": "found", "title": "$params.title"}],
                                    "per_round": 1}
    if config.alliances:
        mine = f"$faction_of($actor, '{name}')"
        actions[f"{name}_ally"] = {
            "by": members,
            "description": "Propose an alliance between your faction and another, or accept one proposed to you.",
            "params": {"faction": {"type": "enum", "values": mine},
                       "other": {"type": "enum",
                  "values": f"$filter($map($factions('{name}'), $it.id), not ($it in {mine}))"}},
            "when": [{"expr": f"$len({mine}) > 0 and $len($factions('{name}')) > 1",
                      "why": "You need a faction and another to ally with."}],
            "do": [{"groups": name, "action": "ally", "in": "$params.faction", "other": "$params.other"}]}
        actions[f"{name}_break_alliance"] = {
            "by": members, "description": "End an alliance of your faction.",
            "params": {"faction": {"type": "enum", "values": mine},
                       "other": {"type": "enum",
                                 "values": f"$flatten($map($filter($factions('{name}'), $it.id in {mine}), "
                                           "$it.allies))"}},
            "when": [{"expr": f"$len($flatten($map($filter($factions('{name}'), $it.id in {mine}), $it.allies))) > 0",
                      "why": "Your faction has no alliances."}],
            "do": [{"groups": name, "action": "break_alliance", "in": "$params.faction", "other": "$params.other"}]}
    return {"world": {name: {"type": "map", "default": state,
                             "description": "Factions: members, invitations, alliances."}},
            "actions": actions,
            "views": {name: {"for": members, "title": "Factions", "of": f"$factions('{name}')", "show": _FACTION_LINE,
                             "empty": "There are no factions."}}}
