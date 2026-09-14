"""Information diffusion over a relation: independent cascade and linear threshold models.

.. code-block:: json

    "mechanisms": {"rumor": {"kind": "diffusion", "population": "account", "over": "net_follows",
                             "flow": "against", "model": "cascade", "p": "0.05 + 0.2 * $to.credulity",
                             "seeds": {"moon_base": ["u1"]}}}

Each item (a rumor, a product, a belief) has, per agent of ``population``, a state: unaware,
exposed (with a count of exposures), adopted or rejected. ``flow`` says which way an item
travels along a link: ``along`` (from → to), ``against`` (to → from: a follower hears what the
followed account adopted) or ``both``.

* cascade — each new adopter gets one chance, at the next step, to convince each unaware or
  exposed neighbour with probability ``p`` (an expression over ``$from``, ``$to``, ``$item``).
* threshold — an agent adopts when the (link-weighted, with ``weighted``) share of its
  informing neighbours who adopted reaches its threshold (a number, an expression over ``$it``
  and ``$item``, or ``"random"``: drawn once per agent and item from the run's seed).

Items step every round in ``step`` phase (or only through ``{"spread": name, "act": "step"}``).
Randomness comes from the world's seeded stream; all state is the world prop ``<name>``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from ...entity import Entity
from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function, tainted
from ..registry import MechanismError, effect_op, mechanism
from ._social import (NAME, check_expr, config_of, edges, eid, ids, literal_name_check, require_type, seat_order,
                      uses_of)

__all__ = ["DiffusionConfig"]

KIND = "diffusion"
_ACTS = ("seed", "step", "adopt", "reject", "expose")


class DiffusionConfig(BaseModel):
    """How items spread among agents over one relation."""

    model_config = ConfigDict(extra="forbid")

    population: str = Field(..., description="Entity type the items spread among (subtypes included).")
    over: str = Field(..., description="Relation the items travel along (e.g. follows).")
    flow: Literal["along", "against", "both"] = Field("both", description="along: from → to; against: to → from (followers hear the followed); both.")
    model: Literal["cascade", "threshold"] = Field("cascade", description="cascade (independent cascade) | threshold (linear threshold).")
    p: Union[float, str] = Field(0.1, description="Cascade: chance one adopter convinces one neighbour (number or expression over $from, $to, $item).")
    threshold: Union[float, str] = Field(0.5, description="Threshold: share of informing neighbours needed (number, expression over $it and $item, or \"random\").")
    weighted: bool = Field(False, description="Threshold: weigh neighbours by link value.")
    seeds: Dict[str, List[str]] = Field(default_factory=dict, description="Items adopted from the start: {item: [ids]}.")
    step: Optional[Literal["start", "end"]] = Field("end", description="Spread every round in this phase; null: only through the spread op.")
    steps: int = Field(1, ge=1, le=100, description="Spread steps per round.")
    on_adopt: List[Any] = Field(default_factory=list, description="Effects for each new adopter ($it, $item, and $from for a cascade).")


def _fresh(adopted: Optional[List[str]] = None) -> Dict[str, Any]:
    seeds = list(dict.fromkeys(adopted or []))
    return {"adopted": {s: 0 for s in seeds}, "exposed": {}, "rejected": {}, "frontier": seeds, "thresholds": {}}


def _items(world: Any, name: str) -> Dict[str, Any]:
    return world.props.get(name) or {}


def _find(world: Any, item: str) -> Optional[Dict[str, Any]]:
    """The state of ``item`` in whichever diffusion mechanism holds it."""
    for name in uses_of(world.contract.mechanisms, KIND):
        state = _items(world, name).get(item)
        if state is not None:
            return state  # type: ignore[no-any-return]
    return None


def _item_key(value: Any, source: Optional[str]) -> str:
    if isinstance(value, Entity):
        return value.id
    if not isinstance(value, str) or not value or tainted(value):
        raise ExprError(f"an item is a name or an entity (never participant text), got {value!r}", source)
    return value


def state_of(state: Optional[Mapping[str, Any]], agent_id: str) -> str:
    if state is None:
        return "unaware"
    if agent_id in state["rejected"]:
        return "rejected"
    if agent_id in state["adopted"]:
        return "adopted"
    return "exposed" if agent_id in state["exposed"] else "unaware"


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


@function("reach(item)", "Distinct agents an item has reached so far: exposed, adopted or rejected (diffusion).",
          min_args=1, max_args=1)
def _reach_fn(call: Call) -> int:
    state = _find(call.scope.world, _item_key(call.arg(0), call.source))
    if state is None:
        return 0
    return len(set(state["adopted"]) | set(state["exposed"]) | set(state["rejected"]))


@function("adopters(item)", "How many agents currently hold an item (adopted, not rejected).", min_args=1, max_args=1)
def _adopters_fn(call: Call) -> int:
    state = _find(call.scope.world, _item_key(call.arg(0), call.source))
    return len(state["adopted"]) if state is not None else 0


@function("spread_state(agent, item)", "unaware | exposed | adopted | rejected.", min_args=2, max_args=2)
def _state_fn(call: Call) -> str:
    return state_of(_find(call.scope.world, _item_key(call.arg(1), call.source)), eid(call.arg(0), call.source))


@function("exposures(agent, item)", "How many times an agent was exposed to an item.", min_args=2, max_args=2)
def _exposures_fn(call: Call) -> int:
    state = _find(call.scope.world, _item_key(call.arg(1), call.source))
    return int(state["exposed"].get(eid(call.arg(0), call.source), 0)) if state is not None else 0


@function("heard(agent, mechanism?)", "Items an agent is aware of: [{item, state, exposures}], in the order they started.",
          min_args=1, max_args=2)
def _heard_fn(call: Call) -> List[Dict[str, Any]]:
    world: Any = call.scope.world
    agent = eid(call.arg(0), call.source)
    names = [str(call.arg(1))] if len(call) > 1 else uses_of(world.contract.mechanisms, KIND)
    out: List[Dict[str, Any]] = []
    for name in names:
        for item, state in _items(world, name).items():
            current = state_of(state, agent)
            if current != "unaware":
                out.append({"item": item, "state": current, "exposures": int(state["exposed"].get(agent, 0))})
    return out


# ---------------------------------------------------------------------------
# The spread op
# ---------------------------------------------------------------------------


@effect_op("spread", keys=("act", "item", "who"), literal=("spread", "act"), required=("act",),
           check=literal_name_check(KIND, "spread"),
           example='{"spread": "rumor", "act": "step"}  (acts: step [item?], seed/adopt/reject/expose with item and who)')
def _spread_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    name = effect["spread"]
    config = config_of(world, name, KIND, DiffusionConfig)
    act = effect["act"]
    if act not in _ACTS:
        raise RunError(f"act must be one of {', '.join(_ACTS)}, got {act!r}", f"{where}.act")
    if world.contract.relations.get(config.over) is None:
        raise RunError(f"diffusion {name}: '{config.over}' is not a declared relation", f"mechanisms.{name}.over")
    items = {k: _copy(v) for k, v in _items(world, name).items()}
    try:
        item = _item_key(runner.eval(effect["item"], vars), where) if "item" in effect else None
        if act == "step":
            for key in ([item] if item is not None else list(items)):
                if key in items:
                    _step(runner, name, config, items, key, where)
        else:
            if item is None:
                raise RunError(f"act {act} needs `item`", where)
            if item not in items:
                if _find(world, item) is not None:
                    raise RunError(f"item '{item}' already spreads in another diffusion mechanism", where)
                items[item] = _fresh()
            agents = [a for a in ids(runner.eval(effect.get("who"), vars), where) if _eligible(world, config, a)]
            _apply(runner, name, config, items, item, act, agents, where)
    except ExprError as exc:
        raise RunError(str(exc), where) from None
    world.set_world(name, items)


def _copy(state: Mapping[str, Any]) -> Dict[str, Any]:
    return {"adopted": dict(state["adopted"]), "exposed": dict(state["exposed"]), "rejected": dict(state["rejected"]),
            "frontier": list(state["frontier"]), "thresholds": dict(state["thresholds"])}


def _eligible(world: Any, config: DiffusionConfig, agent_id: str) -> bool:
    found = world.entities.get(agent_id)
    return found is not None and found.alive and world.is_a(found.entity_type, config.population)


def _apply(runner: Any, name: str, config: DiffusionConfig, items: Dict[str, Any], item: str, act: str,
           agents: List[str], where: str) -> None:
    state = items[item]
    world = runner.world
    for agent in agents:
        if act == "expose":
            if agent not in state["adopted"]:
                state["exposed"][agent] = state["exposed"].get(agent, 0) + 1
        elif act == "reject":
            state["adopted"].pop(agent, None)
            state["frontier"] = [a for a in state["frontier"] if a != agent]
            state["rejected"][agent] = world.round
        elif agent not in state["adopted"] and (act == "seed" or agent not in state["rejected"]):
            state["rejected"].pop(agent, None)
            state["adopted"][agent] = world.round
            state["frontier"].append(agent)
            if act == "adopt":
                _adopted(runner, name, config, item, agent, None, where)


def _adopted(runner: Any, name: str, config: DiffusionConfig, item: str, agent: str, source: Optional[str], where: str) -> None:
    if not config.on_adopt:
        return
    world = runner.world
    local: Dict[str, Any] = {"it": world.entities[agent], "item": item}
    if source is not None:
        local["from"] = world.entities.get(source)
    runner.run(config.on_adopt, local, f"mechanisms.{name}.on_adopt")


def _informers(world: Any, config: DiffusionConfig, agent: str, towards: bool) -> List[str]:
    """Agents ``agent`` passes items to (``towards``) or hears items from, in seat order."""
    out, into = edges(world, config.over)
    along = out.get(agent, []) if towards else into.get(agent, [])
    against = into.get(agent, []) if towards else out.get(agent, [])
    chosen = along if config.flow == "along" else against if config.flow == "against" else along + against
    order = seat_order(world)
    return sorted((a for a in dict.fromkeys(chosen) if _eligible(world, config, a)), key=lambda a: order.get(a, 0))


def _number(value: Any, what: str, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise RunError(f"{what} must be a number from 0 to 1, got {value!r}", where)
    return float(value)


def _step(runner: Any, name: str, config: DiffusionConfig, items: Dict[str, Any], item: str, where: str) -> None:
    for _ in range(config.steps):
        if config.model == "cascade":
            _cascade(runner, name, config, items[item], item, where)
        else:
            _threshold(runner, name, config, items[item], item, where)


def _cascade(runner: Any, name: str, config: DiffusionConfig, state: Dict[str, Any], item: str, where: str) -> None:
    world = runner.world
    p_expr = compile_expr(config.p) if isinstance(config.p, str) else None
    convinced: Dict[str, str] = {}
    for source in state["frontier"]:
        if source not in state["adopted"] or not _eligible(world, config, source):
            continue
        for target in _informers(world, config, source, towards=True):
            if target in state["adopted"] or target in state["rejected"] or target in convinced:
                continue
            state["exposed"][target] = state["exposed"].get(target, 0) + 1
            p = config.p if p_expr is None else p_expr(world.scope(**{"from": world.entities[source],
                                                                      "to": world.entities[target], "item": item}))
            if world.rng.random() < _number(p, f"diffusion {name}: p", f"mechanisms.{name}.p"):
                convinced[target] = source
    state["frontier"] = list(convinced)
    for target, source in convinced.items():
        state["adopted"][target] = world.round
    for target, source in convinced.items():
        _adopted(runner, name, config, item, target, source, where)


def _threshold(runner: Any, name: str, config: DiffusionConfig, state: Dict[str, Any], item: str, where: str) -> None:
    world = runner.world
    adopted = state["adopted"]
    candidates = dict.fromkeys(t for a in adopted if _eligible(world, config, a)
                               for t in _informers(world, config, a, towards=True))
    order = seat_order(world)
    joining: List[str] = []
    for agent in sorted(candidates, key=lambda a: order.get(a, 0)):
        if agent in adopted or agent in state["rejected"]:
            continue
        sources = _informers(world, config, agent, towards=False)
        total = active = 0.0
        count = 0
        for source in sources:
            weight = _weight(world, config, source, agent) if config.weighted else 1.0
            total += weight
            if source in adopted:
                active += weight
                count += 1
        state["exposed"][agent] = count
        if total > 0 and active / total >= _threshold_of(world, name, config, state, item, agent):
            joining.append(agent)
    for agent in joining:
        adopted[agent] = world.round
    state["frontier"] = joining
    for agent in joining:
        _adopted(runner, name, config, item, agent, None, where)


def _weight(world: Any, config: DiffusionConfig, source: str, target: str) -> float:
    forward = world.relation(source, target, config.over)
    value = forward if forward is not None else world.relation(target, source, config.over)
    return max(0.0, float(value or 0.0))


def _threshold_of(world: Any, name: str, config: DiffusionConfig, state: Dict[str, Any], item: str, agent: str) -> float:
    if agent in state["thresholds"]:
        return float(state["thresholds"][agent])
    if config.threshold == "random":
        value = world.rng.random()
        state["thresholds"][agent] = value
        return value
    if isinstance(config.threshold, str):
        raw = compile_expr(config.threshold)(world.scope(it=world.entities[agent], item=item))
        return _number(raw, f"diffusion {name}: threshold", f"mechanisms.{name}.threshold")
    return float(config.threshold)


# ---------------------------------------------------------------------------
# The mechanism
# ---------------------------------------------------------------------------


@mechanism(KIND, DiffusionConfig,
           "Items (rumors, ideas, products) spreading over a relation by independent cascade or linear threshold, with "
           "per-agent states (unaware, exposed, adopted, rejected) and exposure counts in the world prop `<name>`. Steps "
           "every round (`step`) or on demand with the `spread` op; `on_adopt` effects run per adopter. Read it with "
           "$reach(item), $adopters(item), $spread_state(agent, item), $exposures(agent, item), $heard(agent).",
           example={"kind": "diffusion", "population": "account", "over": "follows", "flow": "against",
                    "model": "cascade", "p": 0.1, "seeds": {"rumor": ["u1"]}})
def _expand(name: str, config: DiffusionConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    require_type(contract, config.population, "population")
    check_expr(config.p, "p", ("from", "to", "item"))
    if config.threshold != "random":
        check_expr(config.threshold, "threshold", ("it", "item"))
    for value, field in ((config.p, "p"), (config.threshold, "threshold")):
        if not isinstance(value, str) and not 0 <= value <= 1:
            raise MechanismError(f"{field} must be from 0 to 1, got {value}", None, field)
    for item in config.seeds:
        if not NAME.match(item):
            raise MechanismError(f"'{item}' is not a valid item name", "use letters, digits and _", "seeds")
    fragment: Dict[str, Any] = {
        "world": {name: {"type": "map", "default": {item: _fresh(seeds) for item, seeds in config.seeds.items()},
                         "description": "Spread state per item: adopted, exposed, rejected, frontier, thresholds."}},
    }
    if config.step is not None:
        fragment["events"] = [{"name": f"{name}_spread", "phase": config.step, "do": [{"spread": name, "act": "step"}]}]
    return fragment
