"""The environment contract — one JSON document describing any environment.

Every section is optional except ``name`` and at least one agent type. Section
reference, field by field, is generated from these models (see ``fg_env.guide()``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator
from pydantic_core import PydanticCustomError

__all__ = [
    "CONTRACT_VERSION",
    "Contract",
    "InputSpec",
    "Brief",
    "Clock",
    "Space",
    "PropSpec",
    "TypeSpec",
    "EntitySpec",
    "PopulationSpec",
    "RelationSpec",
    "LinkSpec",
    "PhysicsSpec",
    "PhysicsVar",
    "RecordSpec",
    "ParamSpec",
    "Condition",
    "ActionSpec",
    "StageSpec",
    "ViewSpec",
    "EventSpec",
    "PolicyRule",
    "PolicySpec",
    "MetricSpec",
    "OutputSpec",
    "EndSpec",
    "ArmSpec",
    "DefSpec",
    "BlockSpec",
    "InvariantSpec",
    "INPUT_TYPES",
    "PROP_TYPES",
    "PARAM_TYPES",
    "OUTPUT_TYPES",
    "MAX_ROUNDS",
    "MAX_STAGE_PASSES",
    "MAX_TURN_CALLS",
    "MAX_TURN_ACTIONS",
    "MAX_POPULATION",
    "MAX_CREATE",
    "MAX_SUBSTEPS",
]

CONTRACT_VERSION = "1"

INPUT_TYPES = ("number", "int", "bool", "text", "enum", "list", "table", "map", "date", "any")
PROP_TYPES = ("number", "int", "bool", "text", "enum", "list", "map", "any")
PARAM_TYPES = ("number", "int", "bool", "text", "enum", "entity", "list")
#: Most items a list argument may hold.
MAX_LIST_ITEMS = 1_000
OUTPUT_TYPES = ("number", "int", "bool", "text", "list", "map", "any")

Effects = List[Any]

# Ceilings: generous for any real environment, low enough that a typo cannot make a run
# effectively infinite or exhaust memory.

#: Most rounds a run may last.
MAX_ROUNDS = 100_000
#: Most passes a stage may make through its agents in one round.
MAX_STAGE_PASSES = 10_000
#: Most tool calls (including looks) one turn may allow.
MAX_TURN_CALLS = 1_000
#: Most actions one turn may allow.
MAX_TURN_ACTIONS = 1_000
#: Most entities one population group may generate.
MAX_POPULATION = 1_000_000
#: Most entities one ``create`` effect may make (checked where the count is a literal).
MAX_CREATE = 100_000
#: Most physics sub-steps per round.
MAX_SUBSTEPS = 10_000


def _ceiling(value: Any, limit: int, fix: str) -> Any:
    """Reject a literal whole number above ``limit``."""
    if isinstance(value, int) and not isinstance(value, bool) and value > limit:
        raise PydanticCustomError("ceiling", "is {value}, above the ceiling of {limit}",
                                  {"value": f"{value:,}", "limit": f"{limit:,}", "fix": fix})
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _ExprShorthand(_Model):
    """Accept a bare expression text in place of the object (``"$x > 0"`` → ``{"expr": "$x > 0"}``)."""

    @model_validator(mode="before")
    @classmethod
    def _expand(cls, data: Any) -> Any:
        return data if isinstance(data, dict) else {"expr": data}


# ---------------------------------------------------------------------------
# Inputs, brief, clock, space
# ---------------------------------------------------------------------------


class InputSpec(_Model):
    """A typed value supplied when the environment is loaded (``fg_env.load(..., inputs=)``)."""

    type: str = Field("number", description="One of: " + ", ".join(INPUT_TYPES))
    default: Any = Field(None, description="Used when the caller supplies nothing.")
    required: bool = Field(False, description="The caller must supply it (no default).")
    min: Optional[float] = None
    max: Optional[float] = None
    values: Optional[List[Any]] = Field(None, description="Allowed values (type enum).")
    columns: Optional[Dict[str, str]] = Field(None, description="Column types (type table): {name: type}.")
    description: str = ""
    unit: str = ""


class Brief(_Model):
    """Static text every agent receives once per wake, before anything dynamic (cacheable)."""

    situation: str = Field("", description="What this world is and what is going on (template; {$inputs.x} works).")
    rules: str = Field("", description="How it works: what agents can do and what happens (template).")
    roles: Dict[str, str] = Field(default_factory=dict, description="Extra brief per agent type (template over $actor).")


class Clock(_Model):
    """How long a run lasts and how rounds are labelled."""

    rounds: Union[int, str] = Field(20, description="Round budget (number or expression over $inputs).")
    unit: str = Field("round", description="Name of one round: day, week, turn, hour …")
    start: Optional[str] = Field(None, description="ISO date of round 1 (adds a calendar date).")
    step: int = Field(1, description="Units per round (e.g. 7 with unit 'day' = weekly rounds).")
    mode: str = Field("rounds", description="rounds (every round is one step) | continuous (time is a number: actions take `duration`, `scheduled` stages wake agents when their time comes).")
    tick: float = Field(1.0, gt=0, description="Continuous: how far time moves when nothing is due sooner.")
    jump: bool = Field(True, description="Continuous: jump straight to the next moment something is due (an agent's turn or an `after` effect) instead of moving by `tick`.")
    horizon: Union[float, str, None] = Field(None, description="Continuous: the run completes when time would pass this (number or expression over $inputs).")

    @field_validator("rounds")
    @classmethod
    def _rounds_ceiling(cls, value: Any) -> Any:
        return _ceiling(value, MAX_ROUNDS, "a run that long is almost certainly a typo; use fewer rounds")


class GridSpace(_Model):
    """A rows × cols board; positions are [row, col]. Distance is steps (diagonal: king moves)."""

    rows: int
    cols: int
    diagonal: bool = False


class GraphSpace(_Model):
    """Named places joined by edges; positions are place names. Distance is the shortest path."""

    nodes: List[str]
    edges: List[Any] = Field(default_factory=list, description="[a, b] or {from, to, weight}.")


class PlaneSpace(_Model):
    """A width × height area; positions are [x, y]. Distance is straight-line."""

    width: float
    height: float


class Space(_Model):
    """Where entities are (``at``). Declare exactly one of grid, graph, plane."""

    grid: Optional[GridSpace] = None
    graph: Optional[GraphSpace] = None
    plane: Optional[PlaneSpace] = None


# ---------------------------------------------------------------------------
# Types, entities, population, relations
# ---------------------------------------------------------------------------

_PROP_KEYS = {"type", "default", "min", "max", "values", "private", "description", "unit"}


class PropSpec(_Model):
    """One property. Shorthand: a bare value is the default (``"cash": 100``).

    In a type that ``extends`` another, a property the parent declares is overridden field by
    field: only the fields written here change (a bare value changes only the default), so the
    parent's ``private``, ``type``, ``min``, ``max`` and ``values`` still apply."""

    type: Optional[str] = Field(None, description="One of: " + ", ".join(PROP_TYPES) + " (inferred from default).")
    default: Any = Field(None, description="Literal or expression (evaluated when the entity is created).")
    min: Optional[float] = None
    max: Optional[float] = None
    values: Optional[List[Any]] = None
    private: bool = Field(False, description="Hidden from other agents' inspect tool.")
    description: str = ""
    unit: str = ""

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        # An object is a spec when it names `type` or `default` (other keys must then be
        # valid spec keys); a map-valued default is written {"type": "map", "default": {...}}.
        if isinstance(data, dict) and data and ("type" in data or "default" in data or set(data) <= _PROP_KEYS):
            return data
        return {"default": data}


class TypeSpec(_Model):
    """A kind of entity. ``agent: true`` types take turns. ``extends`` inherits another type:
    its props, its agent flag, and membership (``$count(trader)`` counts every kind of trader)."""

    agent: bool = False
    extends: Optional[str] = Field(None, description="Parent type whose props and role this type inherits.")
    description: str = ""
    props: Dict[str, PropSpec] = Field(default_factory=dict)
    policy: Optional[str] = Field(None, description="Default coded policy for agents of this type.")
    inspect: Union[bool, str] = Field(True, description="Whether agents may inspect these entities: true, false, or an expression over $viewer and $it.")


class EntitySpec(_Model):
    """A named starting entity."""

    type: str
    name: Optional[str] = None
    props: Dict[str, Any] = Field(default_factory=dict)
    at: Any = None
    brief: Optional[str] = Field(None, description="Private text added to this entity's own brief (template).")


class PopulationSpec(_Model):
    """Entities generated at load: a count, one per table row, or a weighted sample of rows."""

    type: str
    count: Union[int, str, None] = Field(None, description="How many (number or expression). Omit with `from` = one per row.")
    from_: Optional[str] = Field(None, alias="from", description="Expression giving rows (e.g. $inputs.households).")
    where: Optional[str] = Field(None, description="Row filter ($row).")
    weight: Optional[str] = Field(None, description="Row sampling weight ($row); sampled without replacement.")
    replace: bool = Field(False, description="Sample rows with replacement.")
    id: Optional[str] = Field(None, description="Id template ({$i}, {$row.x}); default <type>_<n>.")
    name: Optional[str] = Field(None, description="Name template.")
    props: Dict[str, Any] = Field(default_factory=dict, description="Values or expressions ($row, $i, $normal(...)).")
    at: Any = None
    brief: Optional[str] = Field(None, description="Private text added to each generated entity's brief (template over $row, $i).")

    @field_validator("count")
    @classmethod
    def _count_ceiling(cls, value: Any) -> Any:
        return _ceiling(value, MAX_POPULATION, "generate fewer entities; this many is almost certainly a typo")


class RelationSpec(_Model):
    """A kind of link between entities (follows, trusts, owns …)."""

    symmetric: bool = False
    default: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    description: str = ""


class LinkSpec(_Model):
    """Starting links: one explicit link, or a generated network among a type."""

    relation: str
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    value: Any = 1
    among: Optional[str] = Field(None, description="Generate links among entities of this type.")
    graph: Optional[str] = Field(None, description="complete | ring | random | small_world")
    degree: Union[int, str, None] = Field(None, description="Links per member (number or expression).")
    p: Union[float, str, None] = Field(None, description="Link probability (random) or rewiring probability (small_world). For random it may depend on the pair: '0.1 if $to.influencer else 0.02'.")
    where: Optional[str] = None


# ---------------------------------------------------------------------------
# Physics
# ---------------------------------------------------------------------------


class PhysicsVar(_Model):
    """A continuous variable. With ``rate`` it is integrated (RK4): d(var)/dt = rate."""

    start: Any = Field(0, description="Initial value (number or expression).")
    rate: Optional[str] = Field(None, description="Math over variable/param names: 'beta*S*I/N'.")
    min: Optional[float] = None
    max: Optional[float] = None


class PhysicsSpec(_Model):
    """Continuous dynamics advanced every round before agents act. Deterministic."""

    dt: float = Field(1.0, description="Time integrated per round.")
    substeps: int = Field(4, description="RK4 sub-steps per round.")
    params: Dict[str, Any] = Field(default_factory=dict, description="Constants (numbers or expressions over $inputs).")
    vars: Dict[str, PhysicsVar] = Field(default_factory=dict)
    read: Dict[str, str] = Field(default_factory=dict, description="Names refreshed from the world before each step: {N: '$count(person)'}.")
    write: Dict[str, str] = Field(default_factory=dict, description="After each step: {'world.price': 'P', 'person.risk': 'I/N'}.")

    @field_validator("substeps")
    @classmethod
    def _substeps_ceiling(cls, value: int) -> int:
        return _ceiling(value, MAX_SUBSTEPS, "use fewer sub-steps or a smaller dt")


# ---------------------------------------------------------------------------
# Records, actions, stages, views, events, policies
# ---------------------------------------------------------------------------


class RecordSpec(_Model):
    """An append-only log (chat, reviews, bids, transcript). New entries reach agents as news."""

    fields: Dict[str, str] = Field(default_factory=lambda: {"text": "text"}, description="{field: type}; text fields written by agents are marked untrusted.")
    show: Optional[str] = Field(None, description="How one entry reads: '{author}: {text}'.")
    visible: str = Field("all", description="'all' or an expression over $viewer and $it (the entry).")
    keep: Optional[int] = Field(None, description="Keep only the latest N entries.")
    notify: bool = Field(True, description="Deliver new entries to agents in 'since your last turn'.")
    description: str = ""


class ParamSpec(_Model):
    """A tool argument. Shorthand: ``"qty": "int"``."""

    type: str = Field("number", description="One of: " + ", ".join(PARAM_TYPES))
    of: Optional[str] = Field(None, description="Entity type (type entity).")
    where: Optional[str] = Field(None, description="Which entities qualify ($it, $actor, $params for earlier params, $pending).")
    values: Union[List[Any], str, None] = Field(None, description="Allowed values or an expression giving them (type enum).")
    min: Union[float, str, None] = None
    max: Union[float, str, None] = None
    max_len: Optional[int] = Field(None, description="Maximum length (type text).")
    items: Optional["ParamSpec"] = Field(None, description="Type list: the spec every element follows (e.g. {\"type\": \"enum\", \"values\": [...]}). Shorthand: `of` makes entity items, `values` enum items.")
    min_items: Optional[int] = Field(None, ge=0, description="Type list: fewest elements.")
    max_items: Optional[int] = Field(None, ge=0, description="Type list: most elements.")
    unique: bool = Field(True, description="Type list: no element twice (rankings, hands of cards).")
    default: Any = None
    required: Optional[bool] = Field(None, description="Defaults to true unless a default is given.")
    invalid: Optional[str] = Field(None, description="What the agent is told when its value is not valid (template over $actor, $params, $value).")
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        return {"type": data} if isinstance(data, str) else data


ParamSpec.model_rebuild()


class Condition(_ExprShorthand):
    """A requirement: ``"$actor.cash > 0"`` or ``{"expr": ..., "why": "You have no money."}``."""

    expr: str
    why: str = ""



class ActionSpec(_Model):
    """Something an agent can do. Each legal action becomes one typed tool."""

    by: Union[str, List[str]] = Field(..., description="Agent type(s) allowed to take it.")
    description: str = Field("", description="Tool description the agent reads.")
    params: Dict[str, ParamSpec] = Field(default_factory=dict)
    when: List[Condition] = Field(default_factory=list, description="Requirements; the tool is offered only when all hold.")
    chance: Union[float, str, None] = Field(None, description="Probability of success; `do` on success, `otherwise` on failure.")
    do: Effects = Field(default_factory=list, description="Effects applied atomically.")
    otherwise: Effects = Field(default_factory=list, description="Effects when the chance roll fails.")
    outcome: Optional[str] = Field(None, description="What the actor is told (template over $actor, $params).")
    announce: Optional[str] = Field(None, description="What everyone else is told (template).")
    private: bool = Field(False, description="Nobody else learns this action happened.")
    terminal: Union[bool, str] = Field(False, description="Taking it ends the agent's turn: true, or an expression checked after it applies ($actor, $params).")
    per_turn: Optional[int] = Field(None, description="Max uses per turn.")
    per_round: Optional[int] = Field(None, description="Max uses per round.")
    duration: Union[float, str, None] = Field(None, description="Continuous clock: how long it takes (number or expression over $actor, $params); the actor's next scheduled turn comes that much later.")

    @model_validator(mode="before")
    @classmethod
    def _when_list(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("when"), (str, dict)):
            data = {**data, "when": [data["when"]]}
        return data


class StageSpec(_Model):
    """One step of every round. Stages run in order; each wakes agents to take turns."""

    name: str
    when: Optional[str] = Field(None, description="Run this stage only when true (e.g. $round == 1).")
    actions: Union[str, List[str], Dict[str, List[str]]] = Field("all", description="'all', a list, or {type: [actions]}.")
    turns: str = Field("sequential", description="sequential (one after another, effects immediate) | simultaneous (same picture, committed together) | scheduled (continuous clock: each agent whose wake time has come, earliest first).")
    interval: Union[float, str, None] = Field(None, description="Scheduled turns: time until an agent that took no timed action is woken again (number or expression over $actor; default clock.tick).")
    first_wake: Union[float, str, None] = Field(None, description="Scheduled turns: each agent's first wake time (number or expression over $it, $i; default 0).")
    order: str = Field("seat", description="seat | random | expression over $it (lowest first).")
    who: Optional[str] = Field(None, description="Which agents are woken ($it); e.g. $it.alive && $chance(0.3).")
    until: Optional[str] = Field(None, description="Repeat turns within the round until true.")
    passes: Optional[int] = Field(None, description="Max passes through the agents (default 1, or 10 with until).")
    quiet: str = Field("wake", description="wake | skip — skip agents with nothing new since their last turn.")
    max_actions: int = Field(1, description="Actions an agent may take per turn.")
    max_calls: int = Field(8, description="Tool calls (including looks) per turn.")
    brief: str = Field("", description="Instruction shown during this stage (template).")
    must_act: bool = Field(False, description="While an action is available, the agent cannot just end its turn.")
    on_idle: Effects = Field(default_factory=list, description="Effects for each agent that ends its turn without acting ($actor): a forfeit, a default move.")
    auto: bool = Field(False, description="Play trivial turns without waking the agent: take the only legal action when it has no arguments, skip the turn when nothing is legal.")
    on_enter: Effects = Field(default_factory=list)
    on_exit: Effects = Field(default_factory=list)

    @field_validator("passes")
    @classmethod
    def _passes_ceiling(cls, value: Optional[int]) -> Optional[int]:
        return _ceiling(value, MAX_STAGE_PASSES, "use fewer passes; a stage that needs this many never settles")

    @field_validator("max_calls")
    @classmethod
    def _calls_ceiling(cls, value: int) -> int:
        return _ceiling(value, MAX_TURN_CALLS, "allow fewer tool calls per turn")

    @field_validator("max_actions")
    @classmethod
    def _actions_ceiling(cls, value: int) -> int:
        return _ceiling(value, MAX_TURN_ACTIONS, "allow fewer actions per turn")


class ViewSpec(_Model):
    """A declared, ranked slice of the world rendered as plain lines for agents."""

    for_: Union[str, List[str]] = Field("all", alias="for", description="Agent type(s) that see it.")
    stages: Optional[List[str]] = None
    title: str = ""
    of: Optional[str] = Field(None, description="Entity type or expression giving items; omit for a single line.")
    where: Optional[str] = Field(None, description="Filter ($it, $actor).")
    sort: Optional[str] = Field(None, description="Sort key ($it).")
    desc: bool = False
    limit: Optional[int] = None
    show: str = Field(..., description="Template for one item (or the single line).")
    empty: Optional[str] = Field(None, description="Text when no items match (omit to hide the view).")
    when: Optional[str] = None
    look: bool = Field(False, description="Offer it on demand as look(view) instead of always including it.")
    bullet: bool = Field(True, description="Prefix each item with '- ' (false for boards and tables).")
    only_changes: bool = Field(False, description="Include it only when it changed since the agent's last turn.")


class EventSpec(_Model):
    """World logic outside agent turns: scheduled, periodic, conditional or random."""

    name: Optional[str] = None
    at: Union[int, List[int], str, None] = Field(None, description="Round(s) it fires.")
    every: Optional[int] = Field(None, description="Fires every N rounds.")
    when: Optional[str] = Field(None, description="Fires when true.")
    chance: Union[float, str, None] = Field(None, description="Probability of firing when otherwise due.")
    phase: str = Field("start", description="start (before stages) | end (after stages).")
    each: Optional[str] = Field(None, description="Run `do` once per item ($it): a type or expression.")
    as_: Optional[str] = Field(None, alias="as", description="Name for the item instead of $it.")
    where: Optional[str] = None
    do: Effects = Field(default_factory=list)
    say: Optional[str] = Field(None, description="Headline agents receive as news.")
    once: bool = False
    arms: Optional[List[str]] = Field(None, description="Only in these experiment arms.")

    @model_validator(mode="before")
    @classmethod
    def _arms_list(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("arms"), str):
            data = {**data, "arms": [data["arms"]]}
        return data


class TriggerSpec(_Model):
    """World logic that reacts the moment a condition becomes true — after any action, effect,
    physics step or round end — instead of waiting for the next event phase."""

    name: Optional[str] = None
    when: str = Field(..., description="Fires when this becomes true (it re-arms once it is false again).")
    do: Effects = Field(default_factory=list)
    say: Optional[str] = Field(None, description="Headline agents receive as news.")
    once: bool = Field(False, description="Fire at most once per run.")
    arms: Optional[List[str]] = Field(None, description="Only in these experiment arms.")

    @model_validator(mode="before")
    @classmethod
    def _arms_list(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("arms"), str):
            data = {**data, "arms": [data["arms"]]}
        return data


class PolicyRule(_Model):
    """One rule of a coded policy: when `when` holds (and the `chance` roll passes), call `do` with `with`.
    With `each`, the rule is tried once per item ($it): "for each of my armies, hold"."""

    each: Optional[str] = Field(None, description="A type or expression; the rule is tried for every item ($it).")
    when: Optional[str] = None
    do: str = Field(..., description="Action name, or 'pass'.")
    with_: Dict[str, Any] = Field(default_factory=dict, alias="with", description="Params as values or expressions.")
    chance: Union[float, str, None] = None


class PolicySpec(_Model):
    """A coded participant: the first rule whose condition holds and whose action is legal is taken."""

    rules: List[PolicyRule]
    repeat: bool = Field(False, description="Keep applying rules until the turn ends (default: one action).")


# ---------------------------------------------------------------------------
# Measurement, ending, experiment, invariants
# ---------------------------------------------------------------------------


class MetricSpec(_ExprShorthand):
    """A number tracked every round (a series). Shorthand: the expression."""

    expr: str
    description: str = ""
    unit: str = ""



class OutputSpec(_ExprShorthand):
    """A typed field of the run result. ``$metrics.x`` is a metric's final value, ``$series.x`` its history."""

    expr: str
    type: str = Field("any", description="One of: " + ", ".join(OUTPUT_TYPES))
    description: str = ""



class EndSpec(_Model):
    """A condition that ends the run early."""

    when: str
    name: Optional[str] = None
    winner: Optional[str] = Field(None, description="Expression naming the winner(s).")
    say: Optional[str] = None


class DefSpec(_Model):
    """A named, reusable expression called like a built-in: ``$utility($actor, $params.offer)``.
    Shorthand: the expression text (no arguments)."""

    args: List[str] = Field(default_factory=list, description="Argument names; the body reads them as roots ($side).")
    expr: str
    description: str = ""

    @model_validator(mode="before")
    @classmethod
    def _expand(cls, data: Any) -> Any:
        return data if isinstance(data, dict) else {"expr": data}


class BlockSpec(_Model):
    """A named, reusable effect list: ``{"block": "settle", "with": {"buyer": "$actor"}}``.
    The effects see only the arguments (plus $inputs, $world, $round …), never the caller's locals."""

    args: List[str] = Field(default_factory=list)
    do: Effects
    description: str = ""


class ArmSpec(_Model):
    """An experiment variant: input overrides and/or a contract patch."""

    description: str = ""
    inputs: Dict[str, Any] = Field(default_factory=dict)
    patch: Dict[str, Any] = Field(default_factory=dict, description="Deep-merged into the contract (objects merge, lists replace).")


class InvariantSpec(_ExprShorthand):
    """Must always hold; checked after every action and round. A violation fails the run."""

    expr: str
    why: str = ""



# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class Contract(_Model):
    """An environment: world, people, rules, what agents see, what is measured."""

    fg_env: str = Field(CONTRACT_VERSION, description="Contract version.")
    name: str
    description: str = ""
    brief: Brief = Field(default_factory=Brief)
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
    invariants: List[InvariantSpec] = Field(default_factory=list)
    defs: Dict[str, DefSpec] = Field(default_factory=dict, description="Reusable expressions, called as $name(args).")
    blocks: Dict[str, BlockSpec] = Field(default_factory=dict, description="Reusable effect lists, run with {\"block\": name}.")
    mechanisms: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Native building blocks by name: {name: {\"kind\": ..., ...config}}; see the guide's mechanisms part.")

    #: The contract as written, before mechanisms were expanded (re-parse this, not a dump).
    _source: Optional[Dict[str, Any]] = PrivateAttr(default=None)

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

    def is_agent(self, type_name: str) -> bool:
        return any(self.types[name].agent for name in self.lineage(type_name))

    def agent_types(self) -> List[str]:
        return [name for name in self.types if self.is_agent(name)]

    def stage_list(self) -> List[StageSpec]:
        """Declared stages, or the default single stage where every action is available."""
        return list(self.stages) or [StageSpec(name="play")]
