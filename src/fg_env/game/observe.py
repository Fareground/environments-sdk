"""What one seat can know: its observation (text and structure), its information state, and the state key.

* Observation text is exactly the update the seat would read now (views, what happened since its
  last turn); nothing is marked as read.
* The observation structure holds what the seat may see: its own properties, the other entities it
  may inspect with their public properties, the declared views, and — when it acts — its tools.
* The information state is perfect recall of what the seat was told and did: its brief, every event
  it could see (other agents' public actions, news, messages to it, outcomes of its own sealed
  choices) and its own actions with their arguments, in order, then the current views and its own
  sealed choices. Hidden state other seats hold never enters it.
* The state key identifies the world and the pending decision (not the log or what agents were told).
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from ..contract import StageSpec
from ..copying.snapshot import encode
from ..expr.objects import Entity
from ..expr.template import format_value
from ..runtime.turn import Turn, entity_dict
from .space import as_turn

if TYPE_CHECKING:
    from ..runtime.env import Env

__all__ = ["observation_text", "observation_struct", "information_state", "state_key", "digest"]


def _stage(env: Env, turn: Turn | None) -> StageSpec:
    if turn is not None:
        return turn.stage
    return env._stage_spec() or env.contract.stage_list()[0]


def _peek(env: Env, actor: Entity, turn: Turn | None) -> Turn:
    """A turn that only looks: the seat's own paused turn's picture, or a neutral one."""
    if turn is not None and turn.actor is actor:
        return Turn(env, actor, turn.stage, turn.reason, turn.staged, peek=True)
    return Turn(env, actor, _stage(env, turn), "", False, peek=True)


def observation_text(env: Env, actor: Entity, turn: Turn | None) -> str:
    peek = _peek(env, actor, turn)
    with as_turn(env, peek):
        return peek.update


def observation_struct(env: Env, actor: Entity, turn: Turn | None, actions: list[Any] | None) -> dict[str, Any]:
    peek = _peek(env, actor, turn)
    stage = peek.stage
    with as_turn(env, peek):
        views = {name: env.perception.render_view(name, view, actor) for name, view in env.contract.views.items()
                 if not view.look and env.perception._applies(view, actor, stage)}
        others = []
        for entity in env.world.entities.values():
            if entity is actor or not entity.alive or not peek._may_inspect(entity):
                continue
            specs = env.contract.props_of(entity.entity_type)
            shown = entity_dict(entity)
            shown["props"] = {key: value for key, value in shown["props"].items()
                              if not (specs.get(key) is not None and specs[key].private)}
            others.append(shown)
    return {"entity": actor.id, "round": env.world.round, "stage": stage.name, "me": entity_dict(actor),
            "views": {name: text for name, text in views.items() if text is not None}, "entities": others,
            "actions": actions}


def information_state(env: Env, actor: Entity, turn: Turn | None) -> str:
    peek = _peek(env, actor, turn)
    lines: list[str] = [env.perception.brief(actor), "", "History:"]
    with as_turn(env, peek):
        for event in env.world.log:
            if not event.visible_to(actor.id):
                continue
            if event.kind == "action" and event.actor == actor.id:
                data = event.data
                args = ", ".join(f"{k}={format_value(v)}" for k, v in (data.get("params") or {}).items())
                lines.append(f"- round {event.round}: you: {data.get('action')}({args})"
                             + ("" if data.get("success", True) else " — it did not succeed"))
                continue
            line = env.perception._event_line(event, actor)
            if line:
                lines.append(f"- round {event.round}: {line}")
        lines += ["", "Now:"]
        stage = peek.stage
        for name, view in env.contract.views.items():
            if view.look or not env.perception._applies(view, actor, stage):
                continue
            block = env.perception.render_view(name, view, actor)
            if block:
                lines.append(block)
    sealed = [item for staged in env.origin.staged if staged.actor is actor and not staged.done
              for item in staged.pending]
    if turn is not None and turn.actor is actor and turn.staged and not sealed:
        sealed = list(turn.pending)
    if sealed:
        lines.append("Sealed this turn: " + "; ".join(json.dumps(encode(item), sort_keys=True, default=str)
                                                      for item in sealed))
    return "\n".join(lines)


def state_key(env: Env, pending: dict[str, Any]) -> str:
    rows = [[e.id, e.entity_type, e.alive, e.location_id, encode(e.properties)] for e in env.world.entities.values()]
    return digest(json.dumps(_world_data(env, rows, pending), sort_keys=True, default=str))


def visible_key(env: Env, actor: Entity, pending: dict[str, Any]) -> str:
    """A key for the state with what ``actor`` cannot see left out: other entities' private properties and events not
    addressed to it. Two states with equal keys differ at most in what the rules hide from ``actor``."""
    world, contract = env.world, env.contract
    rows = []
    for entity in world.entities.values():
        props = entity.properties
        if entity is not actor:
            specs = contract.props_of(entity.entity_type)
            props = {key: value for key, value in props.items()
                     if not (specs.get(key) is not None and specs[key].private)}
        rows.append([entity.id, entity.entity_type, entity.alive, entity.location_id, encode(props)])
    data = _world_data(env, rows, pending)
    data["log"] = [[event.round, event.kind, event.text, event.actor, encode(event.data)]
                   for event in world.log if event.visible_to(actor.id)]
    return digest(json.dumps(data, sort_keys=True, default=str))


def _world_data(env: Env, entities: list[Any], pending: dict[str, Any]) -> dict[str, Any]:
    world = env.world
    return {
        "entities": entities,
        "props": encode(world.props), "round": world.round, "stage": world.stage, "time": world.time,
        "links": {kind: sorted([a, b, v, encode(world.link_fields.get(kind, {}).get((a, b)))]
                               for (a, b), v in edges.items()) for kind, edges in world.links.items()},
        "records": {name: [encode({k: v for k, v in row.items() if k not in ("seq", "round", "stage")}) for row in rows]
                    for name, rows in world.records_store.items()},
        "scheduled": [[due, encode(item)] for due, _, item in world.scheduled],
        "wake": encode(world.wake_requests), "wake_at": world.wake_at, "counters": world.counters,
        "end": encode(world.end_request), "fired": sorted(env._fired_once),
        "triggers": [sorted(env._trigger_armed.items()), sorted(env._triggers_fired)],
        "used": encode(env._used_round), "pending": pending,
    }


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
