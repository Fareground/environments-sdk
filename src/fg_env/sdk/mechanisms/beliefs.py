"""Beliefs: what each agent holds to be true, with confidence, source and decay.

.. code-block:: json

    "mechanisms": {"memory": {"kind": "beliefs", "holders": "villager", "decay": 0.1,
                              "secondhand": 0.6, "trust": "trusts", "share": true}}

Each holder keeps a private map ``<name>`` of beliefs: ``{key: {value, confidence, source, told_by,
round}}``. ``learn`` records something observed (source ``direct``); ``tell`` passes a belief on
at lower confidence (× ``secondhand``, × the teller-to-listener ``trust`` link clamped to 0–1
when a trust relation is named). A new belief replaces an existing one about the same key when
its confidence is at least as high; the same value keeps the higher confidence. Every round
confidence decays (exponentially or linearly) and beliefs below ``forget_below`` are forgotten.

The map is a private prop, so inspect never shows it to others, and the generated view shows an
agent only its own beliefs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, function
from ..registry import MechanismError, effect_op, mechanism
from ..template import format_value
from ..world import Abort
from ._social import props, config_of, eid, entity, ids, literal_name_check, only_use, require_type, single_use_check

__all__ = ["BeliefsConfig"]

KIND = "beliefs"
#: Longest belief key.
MAX_KEY_LEN = 200


class BeliefsConfig(BaseModel):
    """An engine-owned world model per agent."""

    model_config = ConfigDict(extra="forbid")

    holders: str = Field(..., description="Entity type that holds beliefs (subtypes included).")
    decay: float = Field(0.1, ge=0, le=1, description="Confidence lost per round (a share with exponential, an amount with linear).")
    mode: Literal["exponential", "linear"] = Field("exponential", description="How confidence decays.")
    forget_below: float = Field(0.05, ge=0, le=1, description="Beliefs below this confidence are forgotten.")
    secondhand: float = Field(0.7, ge=0, le=1, description="Confidence multiplier for something one was told.")
    trust: Optional[str] = Field(None, description="Relation from listener to teller scaling told confidence (value clamped to 0–1).")
    share: bool = Field(False, description="Offer a `<name>_tell` tool: pass one of your beliefs to another holder.")
    view: bool = Field(True, description="Show each holder its own beliefs.")
    view_limit: int = Field(12, ge=1, le=100, description="Beliefs shown, most confident first.")
    phase: Literal["start", "end"] = Field("end", description="When beliefs decay each round.")


def _use(world: Any, source: Optional[str]) -> tuple:
    name = only_use(world, KIND, source)
    return name, config_of(world, name, KIND, BeliefsConfig)


def _map(holder: Entity, name: str) -> Dict[str, Any]:
    return props(holder).get(name) or {}


def _key(value: Any, where: Optional[str]) -> str:
    if isinstance(value, Entity):
        return value.id
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ExprError(f"a belief key is text, got {value!r}", where)
    key = value if isinstance(value, str) else format_value(value)
    if not key or len(key) > MAX_KEY_LEN:
        raise ExprError(f"a belief key is 1 to {MAX_KEY_LEN} characters", where)
    return key


def _holder(world: Any, config: BeliefsConfig, value: Any, where: str) -> Entity:
    return entity(world, value, where, config.holders)


def believe(world: Any, name: str, config: BeliefsConfig, holder: Entity, key: str, value: Any, confidence: float,
            source: str, teller: Optional[str]) -> bool:
    """Record a belief if it wins over what ``holder`` already holds. True when something changed."""
    confidence = max(0.0, min(1.0, float(confidence)))
    if confidence < config.forget_below:
        return False
    beliefs = dict(_map(holder, name))
    held = beliefs.get(key)
    new = {"value": value, "confidence": round(confidence, 6), "source": source, "told_by": teller, "round": world.round}
    if held is not None:
        if held["value"] == value and held["confidence"] >= confidence:
            return False
        if held["value"] != value and held["confidence"] > confidence:
            return False
    beliefs[key] = new
    world.set_prop(holder, name, beliefs)
    return True


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


def _agent(call: Call) -> Entity:
    world: Any = call.scope.world
    found = world.entity(eid(call.arg(0), call.source))
    if found is None:
        raise ExprError(f"${call.name}: no entity {call.arg(0)!r}", call.source)
    return found  # type: ignore[no-any-return]


@function("believes(agent, key, value?)", "True when the agent holds a belief about key (and, given a value, believes "
          "exactly that) (beliefs mechanism).", min_args=2, max_args=3)
def _believes_fn(call: Call) -> bool:
    name, _ = _use(call.scope.world, call.source)
    held = _map(_agent(call), name).get(_key(call.arg(1), call.source))
    if held is None:
        return False
    return len(call) < 3 or held["value"] == _plain(call.arg(2))


@function("belief(agent, key)", "The agent's belief about key: {value, confidence, source, told_by, round}, or null.",
          min_args=2, max_args=2)
def _belief_fn(call: Call) -> Optional[Dict[str, Any]]:
    name, _ = _use(call.scope.world, call.source)
    held = _map(_agent(call), name).get(_key(call.arg(1), call.source))
    return dict(held) if held is not None else None


@function("confidence(agent, key)", "How sure the agent is about key (0 when it holds no belief).", min_args=2, max_args=2)
def _confidence_fn(call: Call) -> float:
    name, _ = _use(call.scope.world, call.source)
    held = _map(_agent(call), name).get(_key(call.arg(1), call.source))
    return float(held["confidence"]) if held is not None else 0.0


@function("beliefs_of(agent)", "The agent's beliefs, most confident first: [{key, value, confidence, source, told_by, round}].",
          min_args=1, max_args=1)
def _beliefs_of_fn(call: Call) -> List[Dict[str, Any]]:
    name, _ = _use(call.scope.world, call.source)
    rows = [{"key": key, **held} for key, held in _map(_agent(call), name).items()]
    rows.sort(key=lambda r: -r["confidence"])
    return rows


def _plain(value: Any) -> Any:
    return value.id if isinstance(value, Entity) else value


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


@effect_op("learn", keys=("who", "value", "confidence", "source", "from"),
           example='{"learn": "wolf", "who": "$actor", "value": "$params.suspect.id", "confidence": 0.9}  '
                   "(who comes to believe key = value; source direct unless given; `from` names who it came from)")
def _learn_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name, config = _use(world, where)
    try:
        key = _key(runner.eval(effect["learn"], vars), where)
        holders = ids(runner.eval(effect.get("who", "$actor"), vars), where)
        value = _plain(runner.eval(effect.get("value", True), vars))
        confidence = runner.eval(effect.get("confidence", 1), vars)
        source = runner.eval(effect.get("source", "direct"), vars)
        teller = runner.eval(effect["from"], vars) if "from" in effect else None
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    _confidence_number(confidence, where)
    for holder_id in holders:
        holder = _holder(world, config, holder_id, where)
        believe(world, name, config, holder, key, value, confidence, str(source),
                eid(teller, where) if teller is not None else None)


@effect_op("tell", keys=("from", "to", "value", "confidence", "say"), templates=("say",),
           example='{"tell": "wolf", "from": "$actor", "to": "$params.listener"}  (pass a belief on at secondhand '
                   "confidence; `value` to tell something else, `say` for the listener's notice)")
def _tell_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name, config = _use(world, where)
    try:
        key = _key(runner.eval(effect["tell"], vars), where)
        teller = _holder(world, config, runner.eval(effect.get("from", "$actor"), vars), where)
        listeners = ids(runner.eval(effect.get("to"), vars), where)
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    held = _map(teller, name).get(key)
    if "value" in effect:
        value, base = _plain(runner.eval(effect["value"], vars)), runner.eval(effect.get("confidence", 1), vars)
    elif held is None:
        raise Abort(f"{teller.name} holds no belief about {key} to pass on.")
    else:
        value, base = held["value"], runner.eval(effect.get("confidence", held["confidence"]), vars)
    _confidence_number(base, where)
    if listeners == [teller.id]:
        raise Abort("You cannot tell yourself; choose someone else.")
    for listener_id in listeners:
        if listener_id == teller.id:
            continue
        listener = _holder(world, config, listener_id, where)
        factor = config.secondhand
        if config.trust is not None:
            trust = world.relation(listener, teller, config.trust)
            factor *= max(0.0, min(1.0, float(trust))) if trust is not None else 1.0
        changed = believe(world, name, config, listener, key, value, float(base) * factor, "told", teller.id)
        text = runner.text(effect["say"], {**vars, "listener": listener}) if "say" in effect else \
            f"{teller.name} told you: {key} is {format_value(value)}."
        if text:
            world.emit("told", text + ("" if changed else " (It did not change what you believe.)"),
                       actor=teller.id, to=(listener.id,), data={"mechanism": name, "key": key})


def _confidence_number(value: Any, where: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise RunError(f"confidence must be a number from 0 to 1, got {value!r}", where)


@effect_op("forget", keys=("who",), example='{"forget": "wolf", "who": "$actor"}  (drop a belief)')
def _forget_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name, config = _use(world, where)
    try:
        key = _key(runner.eval(effect["forget"], vars), where)
        holders = ids(runner.eval(effect.get("who", "$actor"), vars), where)
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    for holder_id in holders:
        holder = _holder(world, config, holder_id, where)
        beliefs = _map(holder, name)
        if key in beliefs:
            world.set_prop(holder, name, {k: v for k, v in beliefs.items() if k != key})


@effect_op("decay_beliefs", keys=(), literal=("decay_beliefs",), check=literal_name_check(KIND, "decay_beliefs"),
           example='{"decay_beliefs": "memory"}  (one round of confidence decay; generated for you each round)')
def _decay_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["decay_beliefs"]
    config = config_of(world, name, KIND, BeliefsConfig)
    if config.decay == 0:
        return
    for holder in world.entities_of(config.holders):
        beliefs = _map(holder, name)
        if not beliefs:
            continue
        kept: Dict[str, Any] = {}
        for key, held in beliefs.items():
            c = held["confidence"] * (1 - config.decay) if config.mode == "exponential" else held["confidence"] - config.decay
            if c >= config.forget_below:
                kept[key] = {**held, "confidence": round(c, 6)}
        world.set_prop(holder, name, kept)


# ---------------------------------------------------------------------------
# The mechanism
# ---------------------------------------------------------------------------


@mechanism(KIND, BeliefsConfig,
           "A private world model per agent: beliefs {key: {value, confidence, source, told_by, round}} in the private prop "
           "`<name>`, changed by the `learn`, `tell` and `forget` ops, decaying every round. Told beliefs arrive at "
           "secondhand confidence (scaled by trust). Read with $believes(agent, key, value?), $belief(agent, key), "
           "$confidence(agent, key), $beliefs_of(agent).",
           example={"kind": "beliefs", "holders": "villager", "decay": 0.1, "secondhand": 0.6, "share": True})
def _expand(name: str, config: BeliefsConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    single_use_check(KIND, contract)
    require_type(contract, config.holders, "holders")
    if config.trust is not None and config.trust not in (contract.get("relations") or {}):
        raise MechanismError(f"trust '{config.trust}' is not a declared relation", "declare it under relations", "trust")
    fragment: Dict[str, Any] = {
        "types": {config.holders: {"props": {name: {"type": "map", "default": {}, "private": True,
                                                    "description": "What this agent believes."}}}},
        "events": [{"name": f"{name}_decay", "phase": config.phase, "do": [{"decay_beliefs": name}]}],
    }
    if config.view:
        fragment["views"] = {name: {"for": config.holders, "title": "What you believe", "of": "$beliefs_of($actor)",
                                    "limit": config.view_limit, "empty": "You hold no beliefs yet.",
                                    "show": "{key}: {value} ({confidence|pct} sure, {$'seen yourself' if $it.source == 'direct' "
                                            "else 'told by ' + $text($entity($it.told_by))})"}}
    if config.share:
        fragment["actions"] = {f"{name}_tell": {
            "by": config.holders, "description": "Tell another agent one of your beliefs; they hold it less surely than you.",
            "params": {"about": {"type": "enum", "values": f"$keys($actor.{name})", "description": "Which belief."},
                       "to": {"type": "entity", "of": config.holders, "description": "Who you tell."}},
            "when": [{"expr": f"$len($keys($actor.{name})) > 0", "why": "You hold no beliefs to share."}],
            "do": [{"tell": "$params.about", "from": "$actor", "to": "$params.to"}],
            "outcome": "You told {$params.to.name} about {$params.about}.", "private": True}}
    return fragment
