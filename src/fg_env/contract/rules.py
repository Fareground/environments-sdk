"""Contract sections of the rules: records, actions and their parameters, stages, views, events and policies."""
from __future__ import annotations

import re
from difflib import get_close_matches
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

__all__ = ["RecordSpec", "ParamSpec", "Condition", "ActionSpec", "StageSpec", "ViewSpec", "EventSpec", "ANCHORS",
           "anchor_roots", "PolicyRule", "PolicySpec"]
# ---------------------------------------------------------------------------
# Records, actions, stages, views, events, policies
# ---------------------------------------------------------------------------


class RecordSpec(_Model):
    """An append-only log (chat, reviews, bids, transcript). New entries reach agents as news."""

    fields: dict[str, TypeName] = Field(default_factory=lambda: {"text": "text"},
                                        description="{field: type}; a field holds what a property of its type "
                                                    "holds, by the same rules (a number field refuses text, a "
                                                    "text field a number). Text fields written by agents are "
                                                    "marked untrusted.")
    show: str | None = Field(None, description="How one entry reads: '{author}: {text}'.")
    visible: str = Field("all", description="'all' or an expression over $viewer and $it (the entry). It filters "
                         "what agents are shown or offered; game logic reads every entry. An entry's `seq` counts "
                         "from 1 in what each reader sees of the record (game logic: every entry of every record), "
                         "so it never tells a reader of entries it cannot see.")
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
    do: Effects = Field(default_factory=list, description="Effects applied atomically.")
    outcome: str | None = Field(None, description="What the actor is told (template over $actor, $params).")
    announce: str | Literal[False] | None = Field(
        None,
        description="What everyone else is told: omitted, a default line (a simultaneous stage's leaves out the "
                    "arguments); a template; or false: nobody else learns this action happened.")
    terminal: bool | str = Field(False,
                                 description="Taking it ends the agent's turn: true, or an expression checked after it "
                                             "applies ($actor, $params).")
    per_turn: int | None = Field(None, description="Max uses per turn.")
    per_round: int | None = Field(None, description="Max uses per round.")
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

    @property
    def silent(self) -> bool:
        """Whether nobody but the actor learns this action happened (``announce: false``)."""
        return self.announce is False


class StageSpec(_Model):
    """One step of every round. Stages run in order; each wakes agents to take turns. What happens around a stage (a
    resolution when it ends, a default move for an agent that did not act) is an event on the stage's anchors."""

    name: str
    when: str | None = Field(None, description="Run this stage only when true (e.g. $round == 1, $round % 7 == 0).")
    actions: str | list[str] | dict[str, list[str]] = Field("all", description="'all', a list, or {type: [actions]}.")
    turns: str = Field("sequential",
                       description="sequential (one after another, effects immediate) | simultaneous (everyone "
                                   "chooses from the same picture; the sealed choices then commit one agent after "
                                   "another, in `order` or else a random order — resolve them jointly in an event "
                                   "on `stage.<name>.end`).")
    order: str | None = Field(None,
                              description="seat | random | expression over $it (lowest first): the order agents take "
                                          "turns in, and a simultaneous stage's choices commit in. Every agent sees "
                                          "it, so it may read no agent's private property. Default: seat; a "
                                          "simultaneous stage's choices then commit in a random order drawn anew each "
                                          "time, so no seat always wins a contested item.")
    who: str | None = Field(None, description="Which agents are woken ($it); e.g. $it.alive and $chance(0.3).")
    until: str | None = Field(None, description="Repeat turns within the round until true.")
    passes: int | str | None = Field(None,
                                     description="Max passes through the agents (default 1, or 10 with until): a "
                                                 "number ≥ 1 or an expression over $inputs.")
    quiet: str = Field("wake", description="wake | skip — skip agents with nothing new since their last turn.")
    max_actions: int | str = Field(1,
                                   description="Actions an agent may take per turn: a number or an expression over "
                                               "$inputs (default 1; left out on a stage mechanisms attach to, the sum "
                                               "of what each allows, and one more for the stage's own actions).")
    max_calls: int | str = Field(8,
                                 description="Tool calls (including looks) per turn: a number or an expression over "
                                             "$inputs.")
    brief: str = Field("", description="Instruction shown during this stage (template).")
    must_act: bool = Field(False, description="While an action is available, the agent cannot just end its turn.")
    valid: Annotated[list[Condition], BeforeValidator(one_or_many)] = Field(
        default_factory=list,
        description="Conditions the whole turn must meet when it ends, after the `change` events it sets off "
                    "($actor, $pending); if one fails, every action of the turn is undone and the agent is told "
                    "`why` and plays the turn again. The turn's actions apply together or not at all: events, "
                    "reactions and invariants wait until it ends, and an action's `outcome` (and attached files) is "
                    "shown once the turn commits. `\"true\"` makes the turn atomic with no condition. An action that "
                    "draws randomness settles the turn so far at once (a failure then undoes the turn and ends it), so "
                    "no later action can undo its luck.")

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
    title: str = ""
    of: str | None = Field(None, description="Entity type or expression giving items; omit for a single line.")
    where: str | None = Field(None, description="Filter ($it, $actor).")
    sort: str | None = Field(None, description="Sort key ($it).")
    desc: bool = False
    limit: int | None = None
    show: str = Field(..., description="Template for one item (or the single line).")
    empty: str | None = Field(None, description="Text when no items match (omit to hide the view).")
    when: str | None = Field(None, description="Show it only when true ($actor, $stage): e.g. "
                                               "\"$stage in ['trade']\".")
    look: bool = Field(False,
                       description="Offer it on demand as look(view) instead of always including it. Randomness a view "
                                   "draws is fixed for the turn: looking again shows the same text.")
    bullet: bool = Field(True, description="Prefix each item with '- ' (false for boards and tables).")
    attach: str | None = Field(None,
                               description="Assets delivered with the view: an expression giving an asset id, a list "
                                           "or null — per listed item ($it) with `of`, else once ($actor).")


#: Where in a run an event is considered (its `on`), with ``<s>`` a stage and ``<t>`` a type.
ANCHORS = ("round.start", "round.end", "stage.<s>.start", "stage.<s>.end", "stage.<s>.turn", "create.<t>",
           "remove.<t>", "change")
_ANCHOR = re.compile(r"(round\.(start|end)|change|stage\.[^\s]+\.(start|end|turn)|(create|remove)\.[^\s.]+)$")

#: What an event binds beyond the roots every expression reads, by its anchor's kind: the names its `when`, `do` and
#: `say` may read. The checker allows exactly these and the run binds exactly these, so the two cannot disagree.
_ANCHOR_ROOTS: dict[str, tuple[str, ...]] = {"turn": ("actor", "acted", "timed_out"), "create": ("it",),
                                             "remove": ("it",)}


def anchor_roots(anchor: str) -> tuple[str, ...]:
    """The names an event on ``anchor`` binds for its `when`, `do` and `say` (beyond the roots read everywhere)."""
    kind, _, rest = anchor.partition(".")
    return _ANCHOR_ROOTS.get(rest.rpartition(".")[2] if kind == "stage" else kind, ())


class EventSpec(_Model):
    """World logic outside agent turns: `on` says when it is considered, `when` whether it fires."""

    name: str | None = None
    on: str = Field("round.start",
                    description="round.start (before the stages) | round.end (after them, before outputs are "
                                "sampled) | stage.<s>.start (when stage s starts) | stage.<s>.end (after it; a "
                                "simultaneous stage's choices have committed) | stage.<s>.turn (after each agent's "
                                "turn in it: $actor, $acted, $timed_out) | create.<t> / remove.<t> (inside the change "
                                "that creates or removes an entity of type t or a subtype: $it) | change (after every "
                                "change, the moment `when` becomes true; it re-arms once it is false again).")
    when: str | None = Field(None,
                             description="Fires only when true: \"$round == 5\", \"$round % 7 == 1\", "
                                         "\"$chance(0.1)\", \"$arm == 'treatment'\".")
    do: Effects = Field(default_factory=list,
                        description="Effects, applied atomically. A `do` that is one `each` loop runs item by item, "
                                    "each item with luck of its own.")
    say: str | None = Field(None, description="Headline agents receive as news.")
    once: bool = Field(False, description="Fire at most once per run.")

    @field_validator("on")
    @classmethod
    def _anchor(cls, value: str) -> str:
        if not _ANCHOR.match(value):
            close = get_close_matches(value, ("round.start", "round.end", "change"), n=1)
            hint = f" — did you mean '{close[0]}'?" if close else ""
            raise ValueError(f"'{value}' is not an anchor{hint}; events go on: {', '.join(ANCHORS)}")
        return value

    @model_validator(mode="after")
    def _change_needs_when(self) -> EventSpec:
        if self.on == "change" and self.when is None:
            raise ValueError("an event `on: change` fires when its `when` becomes true, so it needs a `when`")
        return self


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
