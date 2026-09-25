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
from ..expr import ExprError, compile_expr, truthy
from ..expr.hidden import REVEALS, reveals
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
    return env.rules.stage_spec() or env.contract.stage_list()[0]


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
        views = {name: env.information.render_view(name, view, actor) for name, view in env.contract.views.items()
                 if not view.look and env.information.applies(view, actor)}
        others = []
        for entity in env.world.entities.values():
            if entity is actor or not entity.alive or not env.information.may_inspect(actor, entity):
                continue
            shown = entity_dict(entity)
            shown["props"] = {key: value for key, value in shown["props"].items()
                              if not env.world.hides(entity, key, actor)}
            others.append(shown)
    return {"entity": actor.id, "round": env.world.round, "stage": stage.name, "me": entity_dict(actor),
            "views": {name: text for name, text in views.items() if text is not None}, "entities": others,
            "actions": actions}


def information_state(env: Env, actor: Entity, turn: Turn | None) -> str:
    peek = _peek(env, actor, turn)
    lines: list[str] = [env.information.render_brief(actor), "", "History:"]
    with as_turn(env, peek):
        for event in env.world.log:
            if not env.world.evaluation.event_visible(event, actor):
                continue
            if event.kind == "action" and event.actor == actor.id:
                data = event.data
                args = ", ".join(f"{k}={format_value(v)}" for k, v in (data.get("params") or {}).items())
                lines.append(f"- round {event.round}: you: {data.get('action')}({args})"
                             + ("" if data.get("success", True) else " — it did not succeed"))
                continue
            line = env.information.event_line(event, actor)
            if line:
                lines.append(f"- round {event.round}: {line}")
        lines += ["", "Now:"]
        for name, view in env.contract.views.items():
            if view.look or not env.information.applies(view, actor):
                continue
            block = env.information.render_view(name, view, actor)
            if block:
                lines.append(block)
    sealed = [item for staged in env.state.staged if staged.actor is actor and not staged.done
              for item in staged.ledger.pending.items]
    if turn is not None and turn.actor is actor and turn.staged and not sealed:
        sealed = list(turn.ledger.pending.items)
    if sealed:
        lines.append("Sealed this turn: " + "; ".join(json.dumps(encode(item), sort_keys=True, default=str)
                                                      for item in sealed))
    return "\n".join(lines)


def state_key(env: Env, pending: dict[str, Any]) -> str:
    rows = [[e.id, e.entity_type, e.alive, e.location_id, encode(e.properties)] for e in env.world.entities.values()]
    return digest(json.dumps(_world_data(env, rows, pending), sort_keys=True, default=str))


def visible_key(env: Env, actor: Entity, pending: dict[str, Any]) -> str:
    """A key for the state with what ``actor`` cannot see left out: properties hidden from it (see expr/hidden.py)
    and events not addressed to it. Two states with equal keys differ at most in what the rules hide from ``actor``."""
    world = env.world
    owned = _owned(env, actor)
    rows = []
    for entity in world.entities.values():
        props = {key: value for key, value in entity.properties.items()
                 if entity.id in owned or not world.hides(entity, key, actor)}
        rows.append([entity.id, entity.entity_type, entity.alive, entity.location_id, encode(props)])
    data = _world_data(env, rows, pending)
    data["props"] = encode({key: value for key, value in world.props.items() if key not in world.hidden.world})
    data["log"] = [[event.round, event.kind, event.text, event.actor, encode(event.data)]
                   for event in world.log if world.evaluation.event_visible(event, actor)]
    return digest(json.dumps(data, sort_keys=True, default=str))


def _owned(env: Env, actor: Entity) -> set[str]:
    """The ids of the entities a view's or entity choice's `where` picks for ``actor`` as their owner (see
    expr/hidden.py): their private properties are not hidden from it."""
    contract, world = env.contract, env.world
    wheres = [(view.of, view.where) for view in contract.views.values()]
    wheres += [(param.of, param.where) for action in contract.actions.values() for param in action.params.values()
               if param.type == "entity"]
    owned: set[str] = set()
    for kind, where in wheres:
        if where is None or not reveals(contract, expr := compile_expr(where), kind) or "params" in expr.roots:
            continue
        for item in world.alive_of(str(kind)):
            try:
                if truthy(expr(world.evaluation.scope(actor=actor, viewer=actor, it=item, **{REVEALS: item}))):
                    owned.add(item.id)
            except ExprError:
                continue  # the view or tool reports it where it is shown
    return owned


def _world_data(env: Env, entities: list[Any], pending: dict[str, Any]) -> dict[str, Any]:
    world = env.world
    return {
        "entities": entities,
        "props": encode(world.props), "round": world.round, "stage": world.stage,
        "links": {kind: sorted([a, b, v, encode(world.link_fields.get(kind, {}).get((a, b)))]
                               for (a, b), v in edges.items()) for kind, edges in world.links.items()},
        "records": {name: [encode({k: v for k, v in row.items() if k not in ("seq", "round", "stage")}) for row in rows]
                    for name, rows in world.records_store.items()},
        "scheduled": [[due, encode(item)] for due, _, item in world.scheduled],
        "wake": encode(world.wake_requests), "counters": world.counters,
        "end": encode(world.end_request), "fired": sorted(world.fired_once),
        "armed": sorted(world.armed.items()),
        "used": encode(world.used_round), "pending": pending,
    }


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
