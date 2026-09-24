"""Contract sections of the rules: records, actions and their parameters, stages, views, events, triggers and
policies."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, StrictFloat, field_validator, model_validator

from .base import (
    MAX_STAGE_PASSES,
    MAX_TURN_ACTIONS,
    MAX_TURN_CALLS,
    PARAM_TYPES,
    SPELLINGS,
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

    fields: dict[str, TypeName] = Field(default_factory=lambda: {"text": "text"},
                                        description="{field: type}; text fields written by agents are marked "
                                                    "untrusted.")
    show: str | None = Field(None, description="How one entry reads: '{author}: {text}'.")
    visible: str = Field("all", description="'all' or an expression over $viewer and $it (the entry). It filters "
                         "what agents are shown or offered; game logic reads every entry.")
    keep: int | None = Field(None, description="Keep only the latest N entries.")
    notify: bool = Field(True, description="Deliver new entries to agents in 'since your last turn'.")
    description: str = ""


class ParamSpec(_Model):
    """A tool argument. Shorthand: ``"qty": "int"``."""

    type: TypeName = Field("number", description="One of: " + ", ".join(PARAM_TYPES) + SPELLINGS)
    of: str | None = Field(None, description="Entity type (type entity).")
    where: str | None = Field(None,
                              description="Which entities qualify ($it, $actor, $params for earlier params, $pending).")
    values: list[Any] | str | None = Field(None, description="Allowed values or an expression giving them (type enum).")
    min: float | str | None = None
    max: float | str | None = None
    step: float | None = Field(None, gt=0,
                               description="Type number or int: values go in steps of this size from `min` (or 0), "
                                           "which makes the parameter enumerable for games.")
    max_len: int | None = Field(None, description="Maximum length (type text).")
    overflow: Literal["refuse", "truncate"] = Field("refuse",
                                                    description="Type text: text longer than `max_len` is refused "
                                                                "(the agent is told to shorten it), or with "
                                                                "`truncate` cut after the last full sentence that "
                                                                "fits (the agent is told what was cut).")
    items: ParamSpec | None = Field(None,
                                    description="Type list: the spec every element follows (e.g. "
                                                "{\"type\": \"enum\", \"values\": [...]}). Shorthand: `of` makes "
                                                "entity items, `values` enum items.")
    min_items: Annotated[int, Field(ge=0)] | str | None = Field(None,
                                                                description="Type list: fewest elements (a number or "
                                                                            "an expression, like `min`).")
    max_items: Annotated[int, Field(ge=0)] | str | None = Field(None,
                                                                description="Type list: most elements (a number or an "
                                                                            "expression, like `max`).")
    unique: bool = Field(True, description="Type list: no element twice (rankings, hands of cards).")
    default: Any = None
    required: bool | None = Field(None, description="Defaults to true unless a default is given.")
    invalid: str | None = Field(None,
                                description="What the agent is told when its value is not valid (template over $actor, "
                                            "$params, $value).")
    kinds: list[str] | None = Field(None,
                                    description="Type file: the asset types accepted (image, pdf, text, audio, file; "
                                                "default all).")
    max_bytes: int | None = Field(None,
                                  description="Type file: the largest file accepted (default: the largest for its "
                                              "kinds).")
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

    by: str | list[str] = Field(..., description="Agent type(s) allowed to take it.")
    description: str = Field("", description="Tool description the agent reads.")
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    when: Annotated[list[Condition], BeforeValidator(one_or_many)] = Field(
        default_factory=list,
        description="Requirements (one or a list). Those over $actor decide whether the tool is offered; those that "
                    "read $params refuse a call that breaks them, with their `why`. They may not draw at random (nor "
                    "may parameters' bounds, defaults, values or `where`): a refused call costs nothing, so an agent "
                    "could call again until luck let it through — draw in `do` or `chance`.")
    chance: StrictFloat | str | None = Field(None,
                                             description="Probability of success; `do` on success, `otherwise` on "
                                                         "failure.")
    do: Effects = Field(default_factory=list, description="Effects applied atomically.")
    otherwise: Effects = Field(default_factory=list, description="Effects when the chance roll fails.")
    outcome: str | None = Field(None, description="What the actor is told (template over $actor, $params); with "
                                                  "`chance`, when the roll succeeds (a failed roll is told that the "
                                                  "action did not succeed).")
    announce: str | None = Field(None, description="What everyone else is told (template).")
    private: bool = Field(False, description="Nobody else learns this action happened.")
    terminal: bool | str = Field(False,
                                 description="Taking it ends the agent's turn: true, or an expression checked after it "
                                             "applies ($actor, $params).")
    per_turn: int | None = Field(None, description="Max uses per turn.")
    per_round: int | None = Field(None, description="Max uses per round.")
    duration: float | str | None = Field(None,
                                         description="Continuous clock: how long it takes (number or expression over "
                                                     "$actor, $params); the actor's next scheduled turn comes that "
                                                     "much later.")
    tool: str | None = Field(None,
                             description="Offer this action inside one tool of this name, shared by every action "
                                         "naming it: the agent picks the action with the tool's `action` argument, "
                                         "which lists the ones legal now.")
    attach: str | None = Field(None,
                               description="Assets the actor receives with the result (an expression over $actor, "
                                           "$params giving an asset id, a list or null); a sealed choice's arrive "
                                           "with its outcome.")

    @model_validator(mode="before")
    @classmethod
    def _when_list(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("when"), (str, dict)):
            data = {**data, "when": [data["when"]]}
        return data


class StageSpec(_Model):
    """One step of every round. Stages run in order; each wakes agents to take turns."""

    name: str
    when: str | None = Field(None, description="Run this stage only when true (e.g. $round == 1).")
    actions: str | list[str] | dict[str, list[str]] = Field("all", description="'all', a list, or {type: [actions]}.")
    turns: str = Field("sequential",
                       description="sequential (one after another, effects immediate) | simultaneous (everyone "
                                   "chooses from the same picture; the sealed choices then commit one agent after "
                                   "another, in `order` or else a random order — resolve them jointly in on_exit) | "
                                   "scheduled (continuous clock: each agent whose wake time has come, earliest first).")
    interval: float | str | None = Field(None,
                                         description="Scheduled turns: time until an agent that took no timed action "
                                                     "is woken again (number or expression over $actor; default "
                                                     "clock.tick).")
    first_wake: float | str | None = Field(None,
                                           description="Scheduled turns: each agent's first wake time (number or "
                                                       "expression over $it, $i; default 0).")
    order: str | None = Field(None,
                              description="seat | random | expression over $it (lowest first): the order agents take "
                                          "turns in, and a simultaneous stage's choices commit in. Every agent sees "
                                          "it, so it may read no agent's private property. Default: seat; a "
                                          "simultaneous stage's choices then commit in a random order drawn anew each "
                                          "time, so no seat always wins a contested item.")
    who: str | None = Field(None, description="Which agents are woken ($it); e.g. $it.alive && $chance(0.3).")
    until: str | None = Field(None, description="Repeat turns within the round until true.")
    passes: int | str | None = Field(None,
                                     description="Max passes through the agents (default 1, or 10 with until): a "
                                                 "number ≥ 1 or an expression over $inputs.")
    quiet: str = Field("wake", description="wake | skip — skip agents with nothing new since their last turn.")
    max_actions: int | str = Field(1,
                                   description="Actions an agent may take per turn: a number or an expression over "
                                               "$inputs.")
    max_calls: int | str = Field(8,
                                 description="Tool calls (including looks) per turn: a number or an expression over "
                                             "$inputs.")
    brief: str = Field("", description="Instruction shown during this stage (template).")
    must_act: bool = Field(False, description="While an action is available, the agent cannot just end its turn.")
    on_idle: Effects = Field(default_factory=list,
                             description="Effects for each agent that ends its turn without acting ($actor): a "
                                         "forfeit, a default move.")
    on_wake: Effects = Field(default_factory=list,
                             description="Effects for each agent just before its turn ($actor), so what it reads "
                                         "reflects them: an upkeep, a draw, marking news as seen.")
    on_turn_end: Effects = Field(default_factory=list,
                                 description="Effects for each agent after its turn ($actor), whether or not it acted "
                                             "(simultaneous: after choices are committed).")
    auto: bool = Field(False,
                       description="Play trivial turns without waking the agent: take the only legal action when it "
                                   "has no arguments, skip the turn when nothing is legal.")
    time_limit: float | str | None = Field(None,
                                           description="Wall-clock seconds each agent has for its turn (number, or "
                                                       "expression over $actor; null uses the run's `time_limit`). "
                                                       "Past it the turn ends, later calls are refused and "
                                                       "`on_timeout` runs.")
    on_timeout: Effects = Field(default_factory=list,
                                description="Effects for each agent whose turn ran out of time ($actor), instead of "
                                            "`on_idle`.")
    atomic: bool = Field(False,
                         description="The turn's actions apply together or not at all: triggers, reactions and "
                                     "invariants wait until the turn ends, and a turn that breaks `valid` is undone. "
                                     "An action's own `outcome` text (and attached files) is shown once the turn "
                                     "commits, so an undone turn shows nothing it was not charged for.")
    valid: Annotated[list[Condition], BeforeValidator(one_or_many)] = Field(
        default_factory=list,
        description="Conditions the whole turn must meet when it ends ($actor, $pending); if one fails, every action "
                    "of the turn is undone and the agent is told `why` and plays the turn again. An action that draws "
                    "randomness settles the turn so far at once (a failure then undoes the turn and ends it), so no "
                    "later action can undo its luck. Makes the stage atomic.")
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
    def _calls_ceiling(cls, value: Any) -> Any:
        return _ceiling(value, MAX_TURN_CALLS, "allow fewer tool calls per turn")

    @field_validator("max_actions")
    @classmethod
    def _actions_ceiling(cls, value: Any) -> Any:
        return _ceiling(value, MAX_TURN_ACTIONS, "allow fewer actions per turn")


class ViewSpec(_Model):
    """A declared, ranked slice of the world rendered as plain lines for agents."""

    for_: str | list[str] = Field("all", alias="for",
                                  description="Agent type(s) that see it, or \"spectator\": an omniscient view for "
                                              "UIs and reports, rendered into `result.frames` each round and by "
                                              "`env.spectate()`, never shown to an agent.")
    stages: list[str] | None = None
    title: str = ""
    of: str | None = Field(None, description="Entity type or expression giving items; omit for a single line.")
    where: str | None = Field(None, description="Filter ($it, $actor).")
    sort: str | None = Field(None, description="Sort key ($it).")
    desc: bool = False
    limit: int | None = None
    show: str = Field(..., description="Template for one item (or the single line).")
    empty: str | None = Field(None, description="Text when no items match (omit to hide the view).")
    when: str | None = None
    look: bool = Field(False,
                       description="Offer it on demand as look(view) instead of always including it. Randomness a view "
                                   "draws is fixed for the turn: looking again shows the same text.")
    bullet: bool = Field(True, description="Prefix each item with '- ' (false for boards and tables).")
    only_changes: bool = Field(False,
                               description="Show it in full only when it changed since the agent's last turn; "
                                           "otherwise one line says it is unchanged. For agents that remember their "
                                           "earlier turns: the built-in LLM participants start every turn afresh.")
    attach: str | None = Field(None,
                               description="Assets delivered with the view: an expression giving an asset id, a list "
                                           "or null — per listed item ($it) with `of`, else once ($actor).")


class EventSpec(_Model):
    """World logic outside agent turns: scheduled, periodic, conditional or random."""

    name: str | None = None
    at: int | list[int] | str | None = Field(None, description="Round(s) it fires.")
    every: int | str | None = Field(None,
                                    description="Fires every N rounds, from round 1: a number or an expression over "
                                                "$inputs.")
    when: str | None = Field(None, description="Fires when true; `$chance(0.1)` fires it at random.")
    phase: str = Field("start", description="start (before stages) | end (after stages).")
    each: str | None = Field(None, description="Run `do` once per item ($it): a type or expression.")
    as_: str | None = Field(None, alias="as", description="Name for the item instead of $it.")
    order: str | None = Field(None,
                              description="With `each`: random (shuffled from the run's seed) or an expression over "
                                          "the item (lowest first); default the order `each` gives.")
    sync: bool = Field(False,
                       description="With `each`: every item's rules read the world as it was before the event and all "
                                   "their writes land together (cellular automata, simultaneous updates). Only "
                                   "property and layer-cell assignments are allowed; two items writing different "
                                   "values to one property is an error.")
    where: str | None = None
    do: Effects = Field(default_factory=list)
    say: str | None = Field(None, description="Headline agents receive as news.")
    once: bool = False
    arms: list[str] | None = Field(None, description="Only in these experiment arms.")

    @model_validator(mode="before")
    @classmethod
    def _arms_list(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("arms"), str):
            data = {**data, "arms": [data["arms"]]}
        return data


class TriggerSpec(_Model):
    """World logic that reacts the moment a condition becomes true — after any action, effect,
    physics step or round end — instead of waiting for the next event phase."""

    name: str | None = None
    when: str = Field(..., description="Fires when this becomes true (it re-arms once it is false again).")
    do: Effects = Field(default_factory=list)
    say: str | None = Field(None, description="Headline agents receive as news.")
    once: bool = Field(False, description="Fire at most once per run.")
    arms: list[str] | None = Field(None, description="Only in these experiment arms.")

    @model_validator(mode="before")
    @classmethod
    def _arms_list(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("arms"), str):
            data = {**data, "arms": [data["arms"]]}
        return data


class PolicyRule(_Model):
    """One rule of a coded policy: when `when` holds (and the `chance` roll passes), call `do` with `with`.
    With `each`, the rule is tried once per item ($it): "for each of my armies, hold"."""

    each: str | None = Field(None, description="A type or expression; the rule is tried for every item ($it).")
    when: str | None = None
    do: str = Field(..., description="Action name, or 'pass'.")
    with_: dict[str, Any] = Field(default_factory=dict, alias="with", description="Params as values or expressions.")
    chance: StrictFloat | str | None = None


class PolicySpec(_Model):
    """A coded participant: the first rule whose condition holds and whose action is legal is taken."""

    rules: list[PolicyRule]
    repeat: bool = Field(False, description="Keep applying rules until the turn ends (default: one action).")
