"""The environment contract — one JSON document describing any environment.

Every section is optional except ``name`` and at least one agent type. Section
reference, field by field, is generated from these models (see ``fg_env.guide()``).

The section models live beside it — shared names and ceilings in :mod:`.contract_base`, the world model in
:mod:`.contract_world`, records, actions, stages, views, events and policies in :mod:`.contract_rules`, and
measurement, ending, experiments, invariants and calibration in :mod:`.contract_measure` — and this module
exports every one of them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field, PrivateAttr, model_validator

from .assets.spec import AssetSpec
from .contract_base import (
    CONTRACT_VERSION,
    INPUT_TYPES,
    MAX_CREATE,
    MAX_LIST_ITEMS,
    MAX_POPULATION,
    MAX_ROUNDS,
    MAX_STAGE_PASSES,
    MAX_SUBSTEPS,
    MAX_TURN_ACTIONS,
    MAX_TURN_CALLS,
    OUTPUT_TYPES,
    PARAM_TYPES,
    PROP_TYPES,
    _Model,
    one_or_many,
)
from .contract_measure import (
    END_CHECKS,
    INVARIANT_CHECKS,
    ArmSpec,
    BlockSpec,
    CalibrationSpec,
    DefSpec,
    EndSpec,
    InvariantSpec,
    MetricSpec,
    OutputSpec,
)
from .contract_rules import (
    ActionSpec,
    Condition,
    EventSpec,
    ParamSpec,
    PolicyRule,
    PolicySpec,
    RecordSpec,
    StageSpec,
    TriggerSpec,
    ViewSpec,
)
from .contract_world import (
    LAYER_TYPES,
    Brief,
    Clock,
    EntityDynamics,
    EntitySpec,
    EntityVar,
    FeedSpec,
    GraphSpace,
    GridSpace,
    InputSpec,
    LayerSpec,
    LinkSpec,
    MembersSpec,
    MixSpec,
    PhysicsSpec,
    PhysicsVar,
    PlaneSpace,
    PopulationSpec,
    PropSpec,
    RakingSpec,
    RelationSpec,
    Space,
    TypeSpec,
)
from .game_spec import UTILITIES, GameSpec
from .host.tape import TAPE, tape_prop

__all__ = [
    "CONTRACT_VERSION",
    "Contract",
    "InputSpec",
    "Brief",
    "Clock",
    "Space",
    "GridSpace",
    "GraphSpace",
    "PlaneSpace",
    "LayerSpec",
    "PropSpec",
    "TypeSpec",
    "EntitySpec",
    "PopulationSpec",
    "MixSpec",
    "MembersSpec",
    "RakingSpec",
    "RelationSpec",
    "LinkSpec",
    "PhysicsSpec",
    "PhysicsVar",
    "FeedSpec",
    "EntityVar",
    "EntityDynamics",
    "RecordSpec",
    "ParamSpec",
    "Condition",
    "ActionSpec",
    "StageSpec",
    "ViewSpec",
    "EventSpec",
    "TriggerSpec",
    "PolicyRule",
    "PolicySpec",
    "MetricSpec",
    "OutputSpec",
    "EndSpec",
    "ArmSpec",
    "CalibrationSpec",
    "AssetSpec",
    "GameSpec",
    "UTILITIES",
    "DefSpec",
    "BlockSpec",
    "InvariantSpec",
    "INPUT_TYPES",
    "INVARIANT_CHECKS",
    "END_CHECKS",
    "PROP_TYPES",
    "PARAM_TYPES",
    "OUTPUT_TYPES",
    "LAYER_TYPES",
    "MAX_LIST_ITEMS",
    "MAX_ROUNDS",
    "MAX_STAGE_PASSES",
    "MAX_TURN_CALLS",
    "MAX_TURN_ACTIONS",
    "MAX_POPULATION",
    "MAX_CREATE",
    "MAX_SUBSTEPS",
    "one_or_many",
]


class Contract(_Model):
    """An environment: world, people, rules, what agents see, what is measured."""

    fg_env: str = Field(CONTRACT_VERSION, description="Contract version.")
    name: str
    description: str = ""
    imports: List[str] = Field(default_factory=list, description="Contract files merged into this one (paths relative to this file, inside its folder); this contract's own entries win. Imported files may import others.")
    brief: Brief = Field(default_factory=Brief)
    assets: Dict[str, AssetSpec] = Field(default_factory=dict, description="Files beside the contract (images, PDFs, text, audio) by id; see guide('assets').")
    inputs: Dict[str, InputSpec] = Field(default_factory=dict)
    clock: Clock = Field(default_factory=Clock)
    space: Optional[Space] = None
    world: Dict[str, PropSpec] = Field(default_factory=dict, description="Global properties ($world.x).")
    types: Dict[str, TypeSpec]
    entities: Dict[str, EntitySpec] = Field(default_factory=dict)
    population: List[PopulationSpec] = Field(default_factory=list)
    relations: Dict[str, RelationSpec] = Field(default_factory=dict)
    links: List[LinkSpec] = Field(default_factory=list)
    physics: Optional[PhysicsSpec] = None
    feeds: Dict[str, FeedSpec] = Field(default_factory=dict, description="External data written into world props or records, answered by host adapters.")
    patterns: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Named patterns of the world — trends, seasons, responses, random processes, draws — read as $pattern.<name>; see the guide's patterns part.")
    records: Dict[str, RecordSpec] = Field(default_factory=dict)
    actions: Dict[str, ActionSpec] = Field(default_factory=dict)
    stages: List[StageSpec] = Field(default_factory=list)
    views: Dict[str, ViewSpec] = Field(default_factory=dict)
    events: List[EventSpec] = Field(default_factory=list)
    triggers: List[TriggerSpec] = Field(default_factory=list, description="Reactions that fire the moment a condition becomes true.")
    policies: Dict[str, PolicySpec] = Field(default_factory=dict)
    metrics: Dict[str, MetricSpec] = Field(default_factory=dict)
    outputs: Dict[str, OutputSpec] = Field(default_factory=dict)
    end: List[EndSpec] = Field(default_factory=list)
    arms: Dict[str, ArmSpec] = Field(default_factory=dict)
    calibration: Optional[CalibrationSpec] = Field(None, description="Inputs fitted by short pilot sessions whenever the contract loads.")
    game: Optional[GameSpec] = Field(None, description="Seats, returns and utility for game and learning interfaces.")
    invariants: List[InvariantSpec] = Field(default_factory=list)
    defs: Dict[str, DefSpec] = Field(default_factory=dict, description="Reusable expressions, called as $name(args).")
    blocks: Dict[str, BlockSpec] = Field(default_factory=dict, description="Reusable effect lists, run with {\"block\": name}.")
    mechanisms: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Native building blocks by name: {name: {\"kind\": ..., ...config}}; see the guide's mechanisms part.")

    #: The contract as written, before mechanisms were expanded (re-parse this, not a dump).
    _source: Optional[Dict[str, Any]] = PrivateAttr(default=None)
    #: The folder input data files are read from (the contract file's folder, or ``data_dir=``); ``None`` when unknown.
    _folder: Optional[str] = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _feed_tape(cls, data: Any) -> Any:
        """Feeds and described assets record their answers on the host tape, so such a contract declares it."""
        world = data.get("world") if isinstance(data, dict) else None
        assets = data.get("assets") if isinstance(data, dict) else None
        described = isinstance(assets, dict) and any(isinstance(a, dict) and a.get("describe") for a in assets.values())
        if isinstance(data, dict) and (data.get("feeds") or described) and isinstance(world or {}, dict) and TAPE not in (world or {}):
            data = {**data, "world": {**(world or {}), TAPE: tape_prop()}}
        return data

    # -- type lineage ----------------------------------------------------------

    def lineage(self, type_name: str) -> List[str]:
        """``type_name`` and its ancestors, root first. Stops at unknown types and cycles."""
        chain: List[str] = []
        current: Optional[str] = type_name
        while current is not None and current in self.types and current not in chain:
            chain.append(current)
            current = self.types[current].extends
        return list(reversed(chain))

    def is_a(self, type_name: str, ancestor: str) -> bool:
        return ancestor in self.lineage(type_name)

    def subtypes(self, type_name: str) -> List[str]:
        """``type_name`` and every type that extends it (directly or not)."""
        return [name for name in self.types if self.is_a(name, type_name)]

    def props_of(self, type_name: str) -> Dict[str, PropSpec]:
        """Every property of ``type_name``, inherited ones included. A subtype's override changes
        only the fields it writes, so ``"secret": 5`` over ``{"default": 1, "private": true}``
        stays private."""
        props: Dict[str, PropSpec] = {}
        for name in self.lineage(type_name):
            for prop, spec in self.types[name].props.items():
                inherited = props.get(prop)
                props[prop] = spec if inherited is None else inherited.model_copy(
                    update={key: getattr(spec, key) for key in spec.model_fields_set})
        return props

    def hooks_of(self, type_name: str, hook: str) -> List[Any]:
        """``(type, effects)`` for every type in the lineage (root first) that declares lifecycle ``hook``."""
        return [(name, getattr(self.types[name], hook)) for name in self.lineage(type_name)
                if getattr(self.types[name], hook)]

    def hooks_at_build(self, type_name: str) -> bool:
        """Whether on_create runs for this type's entities made at build (the nearest declaration wins)."""
        for name in reversed(self.lineage(type_name)):
            if "on_create_at_build" in self.types[name].model_fields_set:
                return self.types[name].on_create_at_build
        return True

    def is_agent(self, type_name: str) -> bool:
        return any(self.types[name].agent for name in self.lineage(type_name))

    def agent_types(self) -> List[str]:
        return [name for name in self.types if self.is_agent(name)]

    def stage_list(self) -> List[StageSpec]:
        """Declared stages, or the default single stage where every action is available."""
        return list(self.stages) or [StageSpec(name="play")]
