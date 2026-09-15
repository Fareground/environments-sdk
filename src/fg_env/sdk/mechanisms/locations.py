"""Terrain on the declared space — properties, modifiers to occupants, entry rules, ticks (the ``conditions``
family's ``terrain`` mode).

.. code-block:: json

    "terrain": {"kind": "conditions", "mode": "terrain", "who": "unit", "places": {
        "forest": {"area": [[0, 1], [1, 2]], "props": {"cover": 2}, "modifiers": {"stealth": 2}},
        "lava": {"at": [[2, 2]], "enter": [{"expr": "$it.boots", "why": "You need fire boots."}], "tick": ["$it.hp -= 3"]}}}

A place covers positions of the contract's space: grid cells (``at`` cells, ``area`` corners), graph
nodes (``at`` names) or plane points and rectangles. Where places overlap the first declared wins.
Move with ``{"conditions": "terrain", "action": "enter", "who": entity, "to": position}`` so the entry rules
and ``on_enter`` of every terrain apply; ``move`` ignores them.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple, Union

from pydantic import Field, model_validator

from ...entity import Entity
from ..contract import Condition
from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function, truthy
from ..registry import MechanismError, family_action, mode
from ..world import Abort
from . import _common as common
from ._common import Config, Effects, ModifierSpec, Number

__all__ = ["PlaceDef", "TerrainConfig"]

KEY = "conditions.terrain"


class PlaceDef(Config):
    """One kind of terrain."""

    description: str = ""
    at: List[Any] = Field(default_factory=list, description="Positions: grid cells [row, col], graph node names or plane points [x, y].")
    area: Optional[List[List[Union[int, float]]]] = Field(None, description="A rectangle [[row0, col0], [row1, col1]] (or [[x0, y0], [x1, y1]]), corners included.")
    props: Dict[str, Any] = Field(default_factory=dict, description="Static properties, read with $terrain(position).x.")
    modifiers: Dict[str, Union[Number, ModifierSpec]] = Field(default_factory=dict, description="{prop: add | {add, mul}} for occupants, read with $effective.")
    enter: List[Condition] = Field(default_factory=list, description="Requirements to enter ($it): text or {expr, why}.")
    tick: Effects = Field(default_factory=list, description="Effects on each occupant at the start of every round ($it).")
    on_enter: Effects = Field(default_factory=list, description="Effects when an entity enters with the enter action ($it).")

    @model_validator(mode="after")
    def _shape(self) -> "PlaceDef":
        if not self.at and self.area is None:
            raise ValueError("give `at` positions or an `area`")
        if self.area is not None and (len(self.area) != 2 or any(len(corner) != 2 for corner in self.area)):
            raise ValueError("area is two corners: [[row0, col0], [row1, col1]]")
        return self


class TerrainConfig(Config):
    """Terrain for occupants of some types."""

    who: Union[str, List[str]] = Field(..., description="Type(s) affected by the terrain (subtypes included).")
    places: Dict[str, PlaceDef] = Field(
        ..., description="{name: {at | area, description, props, modifiers, enter, tick, on_enter}}. Modifiers and "
                         "ticks apply to occupants standing there; `enter` rules guard the enter action.")
    views: bool = Field(True, description="Tell agents which terrain they stand on.")


def _occupant_types(cfg: TerrainConfig) -> List[str]:
    return [cfg.who] if isinstance(cfg.who, str) else list(cfg.who)


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


@mode("conditions", "terrain", TerrainConfig,
      "Terrain on the space: places with static props ($terrain(position)), modifiers to occupants ($effective), "
      "entry requirements checked by the `enter` action (and $can_enter), on_enter effects, and tick effects on "
      "occupants each round.",
      example={"who": "unit", "places": {
          "forest": {"area": [[0, 0], [1, 1]], "modifiers": {"armor": 1}},
          "lava": {"at": [[2, 2]], "tick": ["$it.hp -= 3"], "enter": [{"expr": "$it.fireproof", "why": "Too hot."}]}}},
      was="locations")
def _expand(name: str, cfg: TerrainConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    occupants = common.types_in(contract, cfg.who, "who")
    space = contract.get("space")
    if not isinstance(space, Mapping):
        raise MechanismError("terrain needs a `space` (grid, graph or plane)", "declare `space`", "places")
    for place, spec in cfg.places.items():
        if not common.NAME.match(place):
            raise MechanismError(f"place name '{place}' must start with a letter and use letters, digits and _", None,
                                 f"places.{place}")
        for index, position in enumerate(spec.at):
            _valid(space, position, f"places.{place}.at[{index}]")
        if spec.area is not None:
            if "graph" in space:
                raise MechanismError("a graph has no areas; list its nodes in `at`", None, f"places.{place}.area")
            for index, corner in enumerate(spec.area):
                _valid(space, corner, f"places.{place}.area[{index}]")
    # Always generated: the tick action also carries the static check of every place's rules and effects.
    fragment: Dict[str, Any] = {"events": [{"name": f"{name}_tick", "phase": "start",
                                            "do": [{"conditions": name, "action": "tick"}]}]}
    agents = [t for t in occupants if _is_agent(contract, t)]
    if cfg.views and agents:
        here = f"$terrain($actor, '{name}')"
        fragment["views"] = {name: {"for": agents, "title": "Terrain", "when": f"{here} != null",
                                    "show": f"You are on {{{here}.name}}{{$': ' + {here}.description if {here}.description else ''}}."}}
    return fragment


def _is_agent(contract: Mapping[str, Any], type_name: str) -> bool:
    types = contract.get("types") or {}
    seen: List[str] = []
    current: Optional[str] = type_name
    while current is not None and current not in seen and isinstance(types.get(current), Mapping):
        if types[current].get("agent"):
            return True
        seen.append(current)
        current = types[current].get("extends")
    return False


def _valid(space: Mapping[str, Any], position: Any, field: str) -> None:
    grid, graph, plane = space.get("grid"), space.get("graph"), space.get("plane")
    if isinstance(grid, Mapping):
        rows, cols = grid.get("rows"), grid.get("cols")
        if not (isinstance(position, list) and len(position) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in position)):
            raise MechanismError(f"a grid position is [row, col], got {position!r}", None, field)
        if not (0 <= position[0] < rows and 0 <= position[1] < cols):
            raise MechanismError(f"position {position} is off the {rows}x{cols} grid", None, field)
    elif isinstance(graph, Mapping):
        nodes = graph.get("nodes") or []
        if position not in nodes:
            raise MechanismError(f"'{position}' is not a place in the graph", common.suggest(str(position), nodes), field)
    elif isinstance(plane, Mapping):
        if not (isinstance(position, list) and len(position) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in position)):
            raise MechanismError(f"a plane position is [x, y], got {position!r}", None, field)
        if not (0 <= position[0] <= plane.get("width", 0) and 0 <= position[1] <= plane.get("height", 0)):
            raise MechanismError(f"position {position} is outside the plane", None, field)


# ---------------------------------------------------------------------------
# Run time
# ---------------------------------------------------------------------------

_LAYOUTS: Dict[int, Tuple[TerrainConfig, List[Tuple[str, FrozenSet[Any], Optional[Tuple[float, float, float, float]]]]]] = {}


def _key(position: Any) -> Any:
    return tuple(position) if isinstance(position, list) else position


def _layout(cfg: TerrainConfig) -> List[Tuple[str, FrozenSet[Any], Optional[Tuple[float, float, float, float]]]]:
    hit = _LAYOUTS.get(id(cfg))
    if hit is not None and hit[0] is cfg:
        return hit[1]
    layout = []
    for place, spec in cfg.places.items():
        box = None
        if spec.area is not None:
            (a0, b0), (a1, b1) = spec.area
            box = (min(a0, a1), max(a0, a1), min(b0, b1), max(b0, b1))
        layout.append((place, frozenset(_key(p) for p in spec.at), box))
    if len(_LAYOUTS) > 256:
        _LAYOUTS.clear()
    _LAYOUTS[id(cfg)] = (cfg, layout)
    return layout


def place_at(cfg: TerrainConfig, position: Any) -> Optional[str]:
    """The first place covering ``position``, or None."""
    if position is None:
        return None
    key = _key(position)
    for place, points, box in _layout(cfg):
        if key in points:
            return place
        if box is not None and isinstance(key, tuple) and len(key) == 2 \
                and box[0] <= key[0] <= box[1] and box[2] <= key[1] <= box[3]:
            return place
    return None


def _layers(world: Any, entity: Optional[Entity] = None) -> Iterable[Tuple[str, TerrainConfig]]:
    """Terrain mechanisms (only those whose occupants include ``entity``'s type when given)."""
    for mech, raw in common.uses(world.contract, KEY):
        cfg = common.parsed(raw, TerrainConfig)
        if entity is not None and not any(world.is_a(entity.entity_type, t) for t in _occupant_types(cfg)):
            continue
        yield mech, cfg


def _refusal(world: Any, entity: Entity, position: Any, where: str) -> Optional[str]:
    """Why ``entity`` may not enter ``position``, or None."""
    for mech, cfg in _layers(world, entity):
        place = place_at(cfg, position)
        if place is None:
            continue
        for index, rule in enumerate(cfg.places[place].enter):
            try:
                ok = truthy(compile_expr(rule.expr)(world.scope(it=entity)))
            except ExprError as exc:
                raise RunError(str(exc), f"mechanisms.{mech}.places.{place}.enter[{index}]") from None
            if not ok:
                return rule.why or f"{entity.name} cannot enter {place}"
    return None


@family_action("conditions", ("terrain",), "enter", keys=("who", "to"), required=("who", "to"), was=("enter",),
               example='{"conditions": "terrain", "action": "enter", "who": "$actor", "to": "$params.cell"}  (move there '
                       'if every terrain\'s entry rules allow; runs on_enter)')
def _enter_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    position = runner.eval(effect["to"], vars)
    for entity in common.entities_of(world, runner.eval(effect["who"], vars), f"{where}.who"):
        why = _refusal(world, entity, position, where)
        if why is not None:
            raise Abort(why)
        world.move(entity, position, where)
        for mech, cfg in _layers(world, entity):
            place = place_at(cfg, position)
            if place is not None:
                runner.run(cfg.places[place].on_enter, {"it": entity}, f"mechanisms.{mech}.places.{place}.on_enter")


def _check_tick(checker: Any, effect: Dict[str, Any], path: str) -> List[Tuple[str, str, Optional[str]]]:
    name = effect["conditions"]
    cfg = common.parsed(checker.c.mechanisms[name], TerrainConfig)
    occupants = {t for t in _occupant_types(cfg) if t in checker.c.types}
    base = set(common.base_roots())
    roots, types = base | {"it"}, {"it": occupants}
    for place, spec in cfg.places.items():
        at = f"mechanisms.{name}.places.{place}"
        for key in ("tick", "on_enter"):
            checker.effects(getattr(spec, key), f"{at}.{key}", roots, dict(types))
        for index, rule in enumerate(spec.enter):
            checker.expr(rule.expr, f"{at}.enter[{index}]", roots, types)
        for prop, modifier in spec.modifiers.items():
            if not any(prop in checker.type_props.get(t, ()) for t in occupants):
                checker.error(f"{at}.modifiers.{prop}", f"{'/'.join(sorted(occupants))} has no property '{prop}'")
            for term in ([modifier.add, modifier.mul] if isinstance(modifier, ModifierSpec) else [modifier]):
                checker.value(term, f"{at}.modifiers.{prop}", roots, types)
    return []


@family_action("conditions", ("terrain",), "tick", check=_check_tick, internal=True, was=("terrain_tick",),
               example='{"conditions": "terrain", "action": "tick"}  (run tick effects on every occupant; generated at '
                       'the start of each round)')
def _tick_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    world = runner.world
    mech = effect["conditions"]
    cfg = common.config(world, mech, KEY, TerrainConfig, where)
    if not any(spec.tick for spec in cfg.places.values()):
        return
    for entity in common.carriers(world, _occupant_types(cfg)):
        place = place_at(cfg, entity.location_id)
        if place is not None and cfg.places[place].tick and entity.alive:
            runner.run(cfg.places[place].tick, {"it": entity}, f"mechanisms.{mech}.places.{place}.tick")


# ---------------------------------------------------------------------------
# Functions and modifiers
# ---------------------------------------------------------------------------


@function("terrain(position_or_entity, mechanism?)",
          "The place at a position or under an entity as {name, description, ...props}, or null; e.g. $terrain($actor).cover.",
          min_args=1, max_args=2)
def _terrain(call: Call) -> Optional[Dict[str, Any]]:
    world: Any = call.scope.world
    target = call.arg(0)
    only = call.arg(1)
    position = target.location_id if isinstance(target, Entity) else target
    for mech, cfg in _layers(world):
        if only is not None and mech != only:
            continue
        place = place_at(cfg, position)
        if place is not None:
            spec = cfg.places[place]
            return {**spec.props, "name": place, "description": spec.description}
    return None


@function("can_enter(entity, position)", "True when the terrain's entry rules let the entity enter the position.",
          min_args=2, max_args=2)
def _can_enter(call: Call) -> bool:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    if entity is None:
        raise ExprError(f"$can_enter: expected an entity, got {call.arg(0)!r}", call.source)
    try:
        return _refusal(world, entity, call.arg(1), call.source) is None
    except RunError as exc:
        raise ExprError(str(exc), call.source) from None


def _modifiers(world: Any, entity: Entity, prop: str) -> Iterable[Tuple[float, float]]:
    if entity.location_id is None:
        return
    for mech, cfg in _layers(world, entity):
        place = place_at(cfg, entity.location_id)
        if place is not None and prop in cfg.places[place].modifiers:
            yield common.modifier_terms(world, cfg.places[place].modifiers[prop], entity,
                                        f"mechanisms.{mech}.places.{place}.modifiers.{prop}")


common.MODIFIER_SOURCES.append(_modifiers)
