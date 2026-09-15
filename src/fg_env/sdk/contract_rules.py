"""Contract sections of the rules: records, actions and their parameters, stages, views, events, triggers and
policies."""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BeforeValidator, Field, field_validator, model_validator

from .contract_base import (
    MAX_STAGE_PASSES,
    MAX_TURN_ACTIONS,
    MAX_TURN_CALLS,
    PARAM_TYPES,
    Effects,
    TypeName,
    _ceiling,
    _ExprShorthand,
    _Model,
    one_or_many,
)

__all__ = ["RecordSpec", "ParamSpec", "Condition", "ActionSpec", "StageSpec", "ViewSpec", "EventSpec", "TriggerSpec",
           "PolicyRule", "PolicySpec"]
# ---------------------------------------------------------------------------
# Records, actions, stages, views, events, policies
# ---------------------------------------------------------------------------


class RecordSpec(_Model):
    """An append-only log (chat, reviews, bids, transcript). New entries reach agents as news."""

    fields: Dict[str, TypeName] = Field(default_factory=lambda: {"text": "text"}, description="{field: type}; text fields written by agents are marked untrusted.")
    show: Optional[str] = Field(None, description="How one entry reads: '{author}: {text}'.")
    visible: str = Field("all", description="'all' or an expression over $viewer and $it (the entry).")
    keep: Optional[int] = Field(None, description="Keep only the latest N entries.")
    notify: bool = Field(True, description="Deliver new entries to agents in 'since your last turn'.")
    description: str = ""


class ParamSpec(_Model):
    """A tool argument. Shorthand: ``"qty": "int"``."""

    type: TypeName = Field("number", description="One of: " + ", ".join(PARAM_TYPES))
    of: Optional[str] = Field(None, description="Entity type (type entity).")
    where: Optional[str] = Field(None, description="Which entities qualify ($it, $actor, $params for earlier params, $pending).")
    values: Union[List[Any], str, None] = Field(None, description="Allowed values or an expression giving them (type enum).")
    min: Union[float, str, None] = None
    max: Union[float, str, None] = None
    step: Optional[float] = Field(None, gt=0, description="Type number or int: values go in steps of this size from `min` (or 0), which makes the parameter enumerable for games.")
    max_len: Optional[int] = Field(None, description="Maximum length (type text).")
    overflow: Literal["refuse", "truncate"] = Field("refuse", description="Type text: text longer than `max_len` is refused (the agent is told to shorten it), or with `truncate` cut after the last full sentence that fits (the agent is told what was cut).")
    items: Optional["ParamSpec"] = Field(None, description="Type list: the spec every element follows (e.g. {\"type\": \"enum\", \"values\": [...]}). Shorthand: `of` makes entity items, `values` enum items.")
    min_items: Optional[int] = Field(None, ge=0, description="Type list: fewest elements.")
    max_items: Optional[int] = Field(None, ge=0, description="Type list: most elements.")
    unique: bool = Field(True, description="Type list: no element twice (rankings, hands of cards).")
    default: Any = None
    required: Optional[bool] = Field(None, description="Defaults to true unless a default is given.")
    invalid: Optional[str] = Field(None, description="What the agent is told when its value is not valid (template over $actor, $params, $value).")
    kinds: Optional[List[str]] = Field(None, description="Type file: the asset types accepted (image, pdf, text, audio, file; default all).")
    max_bytes: Optional[int] = Field(None, description="Type file: the largest file accepted (default: the largest for its kinds).")
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
    when: Annotated[List[Condition], BeforeValidator(one_or_many)] = Field(default_factory=list, description="Requirements (one or a list). Those over $actor decide whether the tool is offered; those that read $params refuse a call that breaks them, with their `why`.")
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
    tool: Optional[str] = Field(None, description="Offer this action inside one tool of this name, shared by every action naming it: the agent picks the action with the tool's `action` argument, which lists the ones legal now.")
    attach: Optional[str] = Field(None, description="Assets the actor receives with the result (an expression over $actor, $params giving an asset id, a list or null); a sealed choice's arrive with its outcome.")

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
    passes: Union[int, str, None] = Field(None, description="Max passes through the agents (default 1, or 10 with until): a number or an expression over $inputs.")
    quiet: str = Field("wake", description="wake | skip — skip agents with nothing new since their last turn.")
    max_actions: int = Field(1, description="Actions an agent may take per turn.")
    max_calls: int = Field(8, description="Tool calls (including looks) per turn.")
    brief: str = Field("", description="Instruction shown during this stage (template).")
    must_act: bool = Field(False, description="While an action is available, the agent cannot just end its turn.")
    on_idle: Effects = Field(default_factory=list, description="Effects for each agent that ends its turn without acting ($actor): a forfeit, a default move.")
    on_wake: Effects = Field(default_factory=list, description="Effects for each agent just before its turn ($actor), so what it reads reflects them: an upkeep, a draw, marking news as seen.")
    on_turn_end: Effects = Field(default_factory=list, description="Effects for each agent after its turn ($actor), whether or not it acted (simultaneous: after choices are committed).")
    auto: bool = Field(False, description="Play trivial turns without waking the agent: take the only legal action when it has no arguments, skip the turn when nothing is legal.")
    time_limit: Union[float, str, None] = Field(None, description="Wall-clock seconds each agent has for its turn (number, or expression over $actor; null uses the run's `time_limit`). Past it the turn ends, later calls are refused and `on_timeout` runs.")
    on_timeout: Effects = Field(default_factory=list, description="Effects for each agent whose turn ran out of time ($actor), instead of `on_idle`.")
    atomic: bool = Field(False, description="The turn's actions apply together or not at all: triggers, reactions and invariants wait until the turn ends, and a turn that breaks `valid` is undone.")
    valid: Annotated[List[Condition], BeforeValidator(one_or_many)] = Field(default_factory=list, description="Conditions the whole turn must meet when it ends ($actor, $pending); if one fails, every action of the turn is undone and the agent is told `why` and plays the turn again. Makes the stage atomic.")
    on_enter: Effects = Field(default_factory=list)
    on_exit: Effects = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _valid_list(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("valid"), (str, dict)):
            data = {**data, "valid": [data["valid"]]}
        return data

    @field_validator("passes")
    @classmethod
    def _passes_ceiling(cls, value: Any) -> Any:
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

    for_: Union[str, List[str]] = Field("all", alias="for", description="Agent type(s) that see it, or \"spectator\": an omniscient view for UIs and reports, rendered into `result.frames` each round and by `env.spectate()`, never shown to an agent.")
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
    attach: Optional[str] = Field(None, description="Assets delivered with the view: an expression giving an asset id, a list or null — per listed item ($it) with `of`, else once ($actor).")


class EventSpec(_Model):
    """World logic outside agent turns: scheduled, periodic, conditional or random."""

    name: Optional[str] = None
    at: Union[int, List[int], str, None] = Field(None, description="Round(s) it fires.")
    every: Union[int, str, None] = Field(None, description="Fires every N rounds, from round 1: a number or an expression over $inputs.")
    when: Optional[str] = Field(None, description="Fires when true.")
    chance: Union[float, str, None] = Field(None, description="Probability of firing when otherwise due.")
    phase: str = Field("start", description="start (before stages) | end (after stages).")
    each: Optional[str] = Field(None, description="Run `do` once per item ($it): a type or expression.")
    as_: Optional[str] = Field(None, alias="as", description="Name for the item instead of $it.")
    order: Optional[str] = Field(None, description="With `each`: random (shuffled from the run's seed) or an expression over the item (lowest first); default the order `each` gives.")
    sync: bool = Field(False, description="With `each`: every item's rules read the world as it was before the event and all their writes land together (cellular automata, simultaneous updates). Only property and layer-cell assignments are allowed; two items writing different values to one property is an error.")
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
