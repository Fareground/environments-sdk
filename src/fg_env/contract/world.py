"""Contract sections of the world model: inputs, brief, clock and space; types, entities (named or generated) and
relations; physics; and feeds."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .base import (
    INPUT_TYPES,
    MAX_POPULATION,
    MAX_ROUNDS,
    MAX_SUBSTEPS,
    PROP_TYPES,
    SPELLINGS,
    Effects,
    TypeName,
    _ceiling,
    _Model,
)
from .rules import PolicySpec

__all__ = ["InputSpec", "Brief", "Clock", "LAYER_TYPES", "GridSpace", "GraphSpace", "PlaneSpace", "LayerSpec", "Space",
           "PropSpec", "TypeSpec", "EntitySpec",
           "RelationSpec", "LinkSpec", "PhysicsVar", "EntityVar", "EntityDynamics", "PhysicsSpec", "FeedSpec"]
# ---------------------------------------------------------------------------
# Inputs, brief, clock, space
# ---------------------------------------------------------------------------


class InputSpec(_Model):
    """A typed value supplied when the environment is loaded (``fg_env.load(..., inputs=)``)."""

    type: TypeName = Field("number", description="One of: " + ", ".join(INPUT_TYPES) + SPELLINGS)
    default: Any = Field(None, description="Used when the caller supplies nothing.")
    required: bool = Field(False, description="The caller must supply it (no default).")
    min: float | None = None
    max: float | None = None
    multiple_of: float | None = Field(None, gt=0, allow_inf_nan=False,
                                      description="Require a multiple of this positive numeric increment, measured "
                                                  "from zero; e.g. 0.01 for cents. Unlike step, validates supplied "
                                                  "data.")
    values: list[Any] | None = Field(None, description="Allowed values (type enum).")
    columns: dict[str, str] | None = Field(None, description="Column types (type table): {name: type}.")
    source: str | None = Field(None,
                               description="Load the value from a data file (.csv → table, .json, .jsonl) inside the "
                                           "data directory: the contract file's folder, or `data_dir=` at load. "
                                           "Undeclared CSV columns stay text.")
    description: str = ""
    unit: str = ""
    label: str = Field("", description="Human-readable input label; defaults to the input name in a host UI.")
    display: Literal["number", "text", "textarea", "select", "toggle", "date", "slider", "knob", "table", "object",
                     "list", "json"] | None = Field(
        None, description="Optional host UI control. Presentation only: does not change simulation semantics.")
    step: float | None = Field(None, gt=0, allow_inf_nan=False,
                               description="Suggested numeric control increment; min/max still validate supplied "
                                           "values.")
    fields: dict[str, InputSpec] | None = Field(None,
                                                description="Typed configurable fields of a map object or each table "
                                                            "row; supports nested objects, defaults and control hints.")
    items: InputSpec | None = Field(None, description="Typed elements of a list input.")

    @model_validator(mode="after")
    def _input_presentation(self) -> InputSpec:
        compatible = {"number": {"number", "int"}, "text": {"text"}, "textarea": {"text"},
                      "select": {"enum"}, "toggle": {"bool"}, "date": {"date"},
                      "slider": {"number", "int"}, "knob": {"number", "int"},
                      "table": {"table"}, "object": {"map"}, "list": {"list"}}
        if self.display in compatible and self.type not in compatible[self.display]:
            raise ValueError(f"display '{self.display}' does not support type '{self.type}'")
        if self.display in {"slider", "knob"} and (self.min is None or self.max is None or self.min >= self.max):
            raise ValueError("slider and knob controls require min < max; use display=number for an input without "
                             "justified finite bounds")
        if self.multiple_of is not None and self.type not in {"number", "int"}:
            raise ValueError("multiple_of only applies to numeric inputs")
        if self.step is not None and self.type not in {"number", "int"}:
            raise ValueError("step only applies to numeric inputs")
        if self.fields is not None and self.type not in {"map", "table"}:
            raise ValueError("fields only apply to map or table inputs")
        if self.items is not None and self.type != "list":
            raise ValueError("items only applies to list inputs")
        for name, child in (self.fields or {}).items():
            if child.source is not None:
                raise ValueError(f"field '{name}': declare data sources on the containing input")
            if name in (self.columns or {}) and (self.columns or {})[name] != child.type:
                raise ValueError(f"field '{name}' conflicts with its column type")
        if self.items is not None and self.items.source is not None:
            raise ValueError("declare data sources on the containing input, not list items")
        return self


class Brief(_Model):
    """Static text every agent receives once per wake, before anything dynamic (cacheable)."""

    situation: str = Field("", description="What this world is and what is going on (template; {$inputs.x} works).")
    rules: str = Field("", description="How it works: what agents can do and what happens (template).")
    roles: dict[str, str] = Field(default_factory=dict,
                                  description="Extra brief per agent type (template over $actor).")
    attach: str | None = Field(None,
                               description="Assets every agent receives with its brief: an expression over $actor "
                                           "giving an asset id, a list of them, or null.")


class Clock(_Model):
    """How long a run lasts and how rounds are labelled."""

    rounds: int | str = Field(20, description="Round budget (number or expression over $inputs).")
    unit: str = Field("round", description="Name of one round: day, week, turn, hour …")
    start: str | None = Field(None,
                              description="ISO date of round 1 (adds a calendar date), or an expression over $inputs "
                                          "giving one (`\"$inputs.start\"`).")
    step: int = Field(1, description="Units per round (e.g. 7 with unit 'day' = weekly rounds).")
    mode: str = Field("rounds",
                      description="rounds (every round is one step) | continuous (time is a number: actions take "
                                  "`duration`, `scheduled` stages wake agents when their time comes).")
    tick: float = Field(1.0, gt=0, description="Continuous: how far time moves when nothing is due sooner.")
    jump: bool = Field(True,
                       description="Continuous: jump straight to the next moment something is due (an agent's turn or "
                                   "an `after` effect) instead of moving by `tick`.")
    horizon: float | str | None = Field(None,
                                        description="Continuous: the run completes when time would pass this (number "
                                                    "or expression over $inputs).")

    @field_validator("rounds")
    @classmethod
    def _rounds_ceiling(cls, value: Any) -> Any:
        return _ceiling(value, MAX_ROUNDS, "a run that long is almost certainly a typo; use fewer rounds")


LAYER_TYPES = ("number", "int", "bool")


class GridSpace(_Model):
    """A rows × cols board; positions are [row, col]."""

    rows: int | str = Field(..., description="Number of rows (number or expression over $inputs).")
    cols: int | str = Field(..., description="Number of columns (number or expression over $inputs).")
    neighborhood: str = Field("von_neumann",
                              description="von_neumann (4 neighbours; distance counts steps along rows and columns) | "
                                          "moore (8 neighbours; distance counts king moves) | hex (6 neighbours: a "
                                          "rhombus of hexagons in axial coordinates [r, q], whose neighbours are [r, "
                                          "q±1], [r±1, q], [r-1, q+1] and [r+1, q-1]).")
    torus: bool = Field(False,
                        description="The edges wrap around: a position off one side comes back on the other, and "
                                    "distances take the short way.")


class GraphSpace(_Model):
    """Named places joined by edges; positions are place names. Distance is the shortest path."""

    nodes: list[str] | str = Field(..., description="Place names, or an expression over $inputs giving them.")
    edges: list[Any] | str = Field(default_factory=list,
                                   description="[a, b] or {from, to, weight}; or an expression over $inputs giving "
                                               "them.")


class PlaneSpace(_Model):
    """A width × height area; positions are [x, y]. Distance is straight-line."""

    width: float | str = Field(..., description="Number or expression over $inputs.")
    height: float | str = Field(..., description="Number or expression over $inputs.")
    torus: bool = Field(False, description="The edges wrap around (positions and distances, as on a grid).")


class LayerSpec(_Model):
    """A value on every cell (grid) or place (graph) without an entity per cell: sugar, pheromone, alive.
    Read with ``$layer(name, position)``; changed by the ``layer`` effect."""

    type: TypeName = Field("number", description="One of: " + ", ".join(LAYER_TYPES))
    default: Any = Field(0,
                         description="Every cell's starting value: a literal, or an expression over $cell (its "
                                     "position) and $inputs.")
    min: float | None = Field(None,
                              description="Lowest allowed value: a set below it is refused, never clamped (saturate "
                                          "with $clamp); diffuse and decay stay within it.")
    max: float | None = Field(None,
                              description="Highest allowed value: a set above it is refused, never clamped (saturate "
                                          "with $clamp); diffuse and decay stay within it.")
    description: str = ""


class Space(_Model):
    """Where entities are (``at``). Declare exactly one of grid, graph, plane."""

    grid: GridSpace | None = None
    graph: GraphSpace | None = None
    plane: PlaneSpace | None = None
    capacity: int | str | dict[str, int | str] | None = Field(None,
                                                              description="Most entities one cell (grid) or place "
                                                                          "(graph) holds: a number for every entity, "
                                                                          "or {type: number} (subtypes count). "
                                                                          "Creating or moving an entity into a full "
                                                                          "cell is refused. Numbers or expressions "
                                                                          "over $inputs.")
    layers: dict[str, LayerSpec] = Field(default_factory=dict,
                                         description="{name: LayerSpec}: values stored on every cell (grid) or place "
                                                     "(graph).")


# ---------------------------------------------------------------------------
# Types, entities, population, relations
# ---------------------------------------------------------------------------

_PROP_KEYS = {"type", "default", "min", "max", "values", "private", "description", "unit"}


class PropSpec(_Model):
    """One property. Shorthand: a bare value is the default (``"cash": 100``).

    In a type that ``extends`` another, a property the parent declares is overridden field by
    field: only the fields written here change (a bare value changes only the default), so the
    parent's ``private``, ``type``, ``min``, ``max`` and ``values`` still apply."""

    type: TypeName | None = Field(None,
                                  description="One of: " + ", ".join(PROP_TYPES) + " (inferred from default)"
                                  + SPELLINGS + ".")
    default: Any = Field(None, description="Literal or expression (evaluated when the entity is created).")
    min: float | None = Field(None,
                              description="Lowest allowed value: a write below it is refused, never clamped (saturate "
                                          "with $clamp).")
    max: float | None = Field(None,
                              description="Highest allowed value: a write above it is refused, never clamped (saturate "
                                          "with $clamp).")
    values: list[Any] | None = None
    private: bool = Field(False,
                          description="Hidden from every agent but its owner: an agent owns its own; the world's and "
                                      "any other entity's are hidden from all, except to the reader a view's or entity "
                                      "choice's `where` picks them for (`$it.owner == $actor.id`). Reading one in what "
                                      "an agent is shown or offered, or in text sent to several, is an error at run "
                                      "time; a refusal whose rules read one spends the action.")
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
    its props, its agent flag, its lifecycle hooks and membership (``$count(trader)`` counts
    every kind of trader)."""

    agent: bool = False
    extends: str | None = Field(None, description="Parent type whose props and role this type inherits.")
    description: str = ""
    props: dict[str, PropSpec] = Field(default_factory=dict)
    policies: dict[str, PolicySpec] = Field(default_factory=dict,
                                            description="Coded participants for agents of this type (and its "
                                                        "subtypes), played as `policy:<name>`: crowds and baselines.")
    policy: str | None = Field(None, description="The policy agents of this type play when a run names none.")
    inspect: bool | str = Field(False,
                                description="Whether agents may inspect these entities (each agent may always inspect "
                                            "itself): false (default), true, or an expression over $viewer and $it. "
                                            "Inspect shows every property that is not private.")
    on_create: Effects = Field(default_factory=list,
                               description="Effects run for every entity of this type (subtypes too) the moment it is "
                                           "created ($it), atomically with whatever created it; an ancestor's hooks "
                                           "run first.")
    on_remove: Effects = Field(default_factory=list,
                               description="Effects run for every entity of this type (subtypes too) the moment it is "
                                           "removed ($it, already no longer alive), atomically with the removal.")
    on_create_at_build: bool = Field(True,
                                     description="Also run on_create for entities made when the world is built (once "
                                                 "the whole world exists, in creation order); false runs it only for "
                                                 "entities created during the run. The nearest declaration in the "
                                                 "type's lineage wins.")


class EntitySpec(_Model):
    """A starting entity, whose id is its key. With ``count`` or ``from`` it generates many instead (households from
    a table, a crowd of traders): their ids are ``<key>_<n>`` (a row's own ``id``, or the ``id`` template, when given),
    and an id already taken is an error. Entities are built in the order they are declared."""

    type: str
    name: str | None = Field(None, description="Its name (default: the id). Generated: a template over $row and $i "
                                               "(default: the type's title and the number).")
    props: dict[str, Any] = Field(default_factory=dict,
                                  description="Values or expressions ($row, $i, $normal(...) when generated).")
    at: Any = None
    brief: str | None = Field(None, description="Private text added to this entity's own brief (template; over $row "
                                                "and $i when generated).")
    count: int | str | None = Field(None,
                                    description="Generate this many (number or expression). Omit with `from` = one "
                                                "per row.")
    from_: str | None = Field(None, alias="from", description="Generate from rows: an expression giving them "
                                                              "(e.g. $inputs.households).")
    where: str | None = Field(None, description="Generated: row filter ($row).")
    weight: str | None = Field(None, description="Generated: row sampling weight ($row); sampled without "
                                                 "replacement.")
    replace: bool = Field(False, description="Generated: sample rows with replacement.")
    id: str | None = Field(None, description="Generated: id template ({$i}, {$row.x}); default <key>_<n>.")

    @field_validator("count")
    @classmethod
    def _count_ceiling(cls, value: Any) -> Any:
        return _ceiling(value, MAX_POPULATION, "generate fewer entities; this many is almost certainly a typo")

    @model_validator(mode="after")
    def _generator_fields(self) -> EntitySpec:
        if self.count is None and self.from_ is None:
            used = [key for key in ("where", "weight", "replace", "id") if key in self.model_fields_set]
            if used:
                raise ValueError(f"`{used[0]}` applies to generated entities: add `count` or `from`, or remove it")
        return self

    @property
    def generates(self) -> bool:
        """Whether this entry generates entities (``count`` or ``from``) rather than naming one."""
        return self.count is not None or self.from_ is not None


class LinkSpec(_Model):
    """Starting links of a relation: one explicit link, links from data rows, or a generated network among a
    type."""

    from_: str | None = Field(None, alias="from")
    to: str | None = None
    value: Any = 1
    among: str | None = Field(None, description="Generate links among entities of this type.")
    graph: str | None = Field(None,
                              description="complete | ring | random | small_world | scale_free | blocks | lattice | "
                                          "star | bipartite")
    m: int | str | None = Field(None, description="scale_free: links each new member makes (preferential attachment).")
    block: str | None = Field(None,
                              description="blocks: expression over $it giving each member's group; `p` applies within "
                                          "a group, `p_between` across.")
    p_between: float | str | None = Field(None, description="blocks: link probability between groups.")
    with_: str | None = Field(None, alias="with", description="bipartite: the other type (links run among → with).")
    hub: str | None = Field(None, description="star: expression giving the hub entity (default: the first member).")
    rows: str | None = Field(None,
                             description="Edges from data: an expression giving rows with `from`, `to` and optional "
                                         "`value`.")
    degree: int | str | None = Field(None, description="Mean neighbours per member (number or expression); on a "
                                                       "one-way random graph a link either way makes a neighbour.")
    p: float | str | None = Field(None,
                                  description="Link probability (random) or rewiring probability (small_world). For "
                                              "random it may depend on the pair: '0.1 if $to.influencer else 0.02'.")
    props: dict[str, Any] = Field(default_factory=dict,
                                  description="Link field values or expressions over $from and $to ($row too with "
                                              "`rows`, whose columns named like a field fill it).")
    where: str | None = None


class RelationSpec(_Model):
    """A kind of link between entities (follows, trusts, owns …). Every link carries a number
    (``value``) and, with ``props``, typed fields of its own (``since``, ``channel``, ``strength``)."""

    symmetric: bool = False
    default: float | None = Field(None, description="Value of a link made without one (default 1).")
    min: float | None = Field(None,
                              description="Lowest allowed link value: a write below it is refused, never clamped "
                                          "(saturate with $clamp).")
    max: float | None = Field(None,
                              description="Highest allowed link value: a write above it is refused, never clamped "
                                          "(saturate with $clamp).")
    props: dict[str, PropSpec] = Field(default_factory=dict,
                                       description="Typed fields every link carries, read as $link(a, b, kind).field; "
                                                   "defaults may be expressions over $from and $to.")
    links: list[LinkSpec] = Field(default_factory=list,
                                  description="Links made when the world is built, after the entities.")
    description: str = ""


# ---------------------------------------------------------------------------
# Physics
# ---------------------------------------------------------------------------


class PhysicsVar(_Model):
    """A continuous variable. With ``rate`` it is integrated (RK4): d(var)/dt = rate."""

    start: Any = Field(0, description="Initial value (number or expression).")
    rate: str | None = Field(None, description="Math over variable/param names: 'beta*S*I/N'.")
    noise: str | None = Field(None,
                              description="Stochastic term (Euler–Maruyama): d(var) = rate·dt + noise·dW, drawn from "
                                          "the run's seed; e.g. 'sigma*price'. Needs a rate; the var's min/max then "
                                          "hold at every sub-step.")
    min: float | None = None
    max: float | None = None


class EntityVar(_Model):
    """How one number property changes by itself on every entity. Shorthand: the rate text."""

    rate: str = Field(...,
                      description="d(prop)/dt as math over names: the entity's own number props, this type's params "
                                  "and reads, world physics variables and params, and t.")
    noise: str | None = Field(None,
                              description="Stochastic term (Euler–Maruyama), drawn from the run's seed: d(prop) = "
                                          "rate·dt + noise·dW.")

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        return {"rate": data} if isinstance(data, str) else data


class EntityDynamics(_Model):
    """Continuous dynamics of entities, jointly integrated when state is coupled.
    Variables are number props visible to views, effects and snapshots; min/max
    bounds apply at physical substeps."""

    where: str | None = Field(None,
                              description="Which entities integrate this step ($it); the others keep their values.")
    params: dict[str, Any] = Field(default_factory=dict,
                                   description="Constants for this type (numbers or expressions over $inputs, $world).")
    read: dict[str, str] = Field(default_factory=dict,
                                 description="Names read from shared intermediate entity state: "
                                             "{exposure: '$count($neighbors($it, contact), $it.sick)'}.")
    vars: dict[str, EntityVar] = Field(default_factory=dict,
                                       description="{number prop: EntityVar | rate}: the props integrated.")
    write: dict[str, str] = Field(default_factory=dict,
                                  description="After each step, other props of the entity from math: "
                                              "{'sick': 'viral_load > 5'}.")


class PhysicsSpec(_Model):
    """Continuous dynamics advanced every round before agents act. Deterministic."""

    dt: float = Field(1.0, description="Time integrated per round.")
    substeps: int = Field(4,
                          description="Maximum drift step and noise interval: the round duration divided by this "
                                      "count. Drift refines further for accuracy.")
    noise_rtol: float = Field(0.01, gt=0, le=1, allow_inf_nan=False,
                              description="Relative timestep convergence target for general noisy dynamics. Refinement "
                                          "reuses the same Brownian path.")
    rtol: float = Field(1e-7, gt=0, le=1, allow_inf_nan=False,
                        description="Relative local error tolerance for continuous drift; smaller values request more "
                                    "precision.")
    atol: float = Field(1e-10, gt=0, allow_inf_nan=False,
                        description="Absolute local drift error tolerance in variable units, important near zero.")
    params: dict[str, Any] = Field(default_factory=dict, description="Constants (numbers or expressions over $inputs).")
    vars: dict[str, PhysicsVar] = Field(default_factory=dict)
    read: dict[str, str] = Field(default_factory=dict,
                                 description="Names read from the world during integration: {N: '$count(person)'}.")
    write: dict[str, str] = Field(default_factory=dict,
                                  description="After each step: {'world.price': 'P', 'person.risk': 'I/N'}.")
    per: dict[str, EntityDynamics] = Field(default_factory=dict,
                                           description="{type: EntityDynamics}: entity dynamics, including coupling "
                                                       "through reads (viral load, firm capital, habit strength).")

    @field_validator("substeps")
    @classmethod
    def _substeps_ceiling(cls, value: int) -> int:
        return _ceiling(value, MAX_SUBSTEPS, "use fewer sub-steps or a smaller dt")


class FeedSpec(_Model):
    """External data written into the world — live or historical prices, news, weather — answered
    by a host adapter (``fetch(request)``) at the start of a round, before events and physics.
    Every answer is recorded on the host tape, so snapshots, restores and replays never ask again;
    text from a host is marked untrusted."""

    host: str = Field(..., description="Name of the host adapter that answers (a Feed).")
    into: str = Field(...,
                      description="'world.<prop>' (the answer is the new value) or 'records.<record>' (the answer is "
                                  "one entry's fields, or a list of entries).")
    query: Any = Field(None,
                       description="What to ask for: data whose texts may be expressions or templates over the world "
                                   "($world, $clock, $round, $inputs).")
    every: int = Field(1, ge=1, description="Fetch every N rounds, from round 1.")
    when: str | None = Field(None, description="Fetch only when true.")
    fallback: Any = Field(None,
                          description="The value (or entries) used when no host is bound: a literal or an expression, "
                                      "whose random draws come from the run's seed. Without one, a run with no host "
                                      "stops and names the host it needs.")
    description: str = ""
