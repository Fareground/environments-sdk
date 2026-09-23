"""Victory: goals and win conditions as declarative `end` entries, with winner resolution (the ``flow`` family's
``victory`` mode).

.. code-block:: json

    "victory": {"kind": "flow", "mode": "victory", "who": "hero", "alive": "$it.hp > 0", "tiebreak": ["$it.hp"],
                "conditions": [{"first_to": 10, "score": "$it.gold"}, {"last_standing": true},
                               {"most": "$it.gold", "at": 30}]}

Conditions are tried in order wherever the engine checks `end` (after start events, after each
stage, at the end of the round). ``most`` is decided at the end of its round, and ``stable`` counts
rounds at the end of each round. The winner is one id, a list of ids when players share a win, a
team value (``last_team``), or null when nobody wins.

When ``who`` is an agent type the mechanism also fills the contract's ``game`` section where the author
left it unset: the seats are ``who`` and each seat's return is ``$won($actor, <name>)`` — 1 for the
winner, an equal share when several share the win, 0 otherwise. The utility class is left to the author:
a run can end with no winner, so no class holds for every run.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple, Union

from pydantic import Field, model_validator

from ..errors import RunError
from ..expr import Call, ExprError, compile_expr, function
from ..registry import MechanismError, mode
from . import _common as common
from ._common import Config, Number

__all__ = ["VictoryCondition", "VictoryConfig"]

KEY = "flow.victory"
KINDS = ("first_to", "most", "last_standing", "last_team", "win_when", "lose_when", "stable", "eliminate", "objectives")
_NAMES = {"win_when": "win", "lose_when": "loss"}
#: Keys that belong to one kind of condition only.
_PARTS = {"score": "first_to", "at": "most", "rounds": "stable", "where": "eliminate"}


class VictoryCondition(Config):
    """One way the game ends. Give exactly one of the kinds."""

    first_to: Optional[Number] = Field(None, description="A player whose `score` reaches this wins.")
    score: Optional[str] = Field(None, description="The score for first_to ($it).")
    most: Optional[str] = Field(None, description="Highest of this score ($it) wins at round `at`.")
    at: Union[int, str, None] = Field(None, description="Round most is decided (default: the last round).")
    last_standing: Optional[bool] = Field(None, description="The last player still in wins.")
    last_team: Optional[str] = Field(None, description="Team of each player ($it): the last team with players in wins.")
    win_when: Optional[str] = Field(None, description="Cooperative win: every player still in wins when true.")
    lose_when: Optional[str] = Field(None, description="Cooperative loss: nobody wins when true.")
    stable: Optional[str] = Field(None, description="Ends when this holds at the end of `rounds` rounds in a row.")
    rounds: Optional[int] = Field(None, ge=1, description="Rounds in a row for stable.")
    eliminate: Optional[str] = Field(None, description="A type: when none of it is left (see where), the players still in win.")
    where: Optional[str] = Field(None, description="Which of the eliminate type count ($it).")
    objectives: Optional[List[str]] = Field(None, description="A player for whom all these hold ($it) wins.")
    name: Optional[str] = Field(None, description="How the run's ended_by reads (default: the kind).")
    winner: Optional[str] = Field(None, description="Expression naming the winner instead of the default.")
    say: str = Field("", description="Announcement (template); default names the winner.")

    @model_validator(mode="after")
    def _shape(self) -> "VictoryCondition":
        given = [k for k in KINDS if getattr(self, k) is not None and getattr(self, k) is not False]
        if len(given) != 1:
            raise ValueError(f"give exactly one of: {', '.join(KINDS)} (got {', '.join(given) or 'none'})")
        kind = given[0]
        for key, owner in _PARTS.items():
            if getattr(self, key) is not None and owner != kind:
                raise ValueError(f"`{key}` belongs to {owner}")
        if kind == "first_to" and self.score is None:
            raise ValueError("first_to needs `score`")
        if kind == "stable" and self.rounds is None:
            raise ValueError("stable needs `rounds`")
        if kind == "objectives" and not self.objectives:
            raise ValueError("objectives needs at least one expression")
        return self

    @property
    def kind(self) -> str:
        return next(k for k in KINDS if getattr(self, k) is not None and getattr(self, k) is not False)


class VictoryConfig(Config):
    """How players win, lose or end with no winner."""

    who: str = Field(..., description="The type whose members can win (subtypes included).")
    alive: str = Field("true", description="Who is still in ($it), e.g. $it.hp > 0 — any condition, not only the "
                                           "built-in `alive` (false once an entity is removed).")
    conditions: List[VictoryCondition] = Field(
        ..., min_length=1,
        description="Tried in order; each one of {first_to + score}, {most, at}, {last_standing: true}, {last_team}, "
                    "{win_when}, {lose_when}, {stable, rounds}, {eliminate, where}, {objectives}; plus name, winner, say.")
    tiebreak: List[str] = Field(default_factory=list, description="Expressions ($it) that decide ties in order, highest first.")
    ties: Literal["share", "none", "random"] = Field("share", description="A tie left after tiebreaks: share the win, none (nobody wins) or pick at random (seeded).")


def _labelled(cfg: VictoryConfig) -> List[Tuple[str, VictoryCondition]]:
    """Each condition with the name its end reads (repeated names numbered: most, most_2)."""
    used: Dict[str, int] = {}
    out = []
    for condition in cfg.conditions:
        label = condition.name or _NAMES.get(condition.kind, condition.kind)
        used[label] = used.get(label, 0) + 1
        out.append((label if used[label] == 1 else f"{label}_{used[label]}", condition))
    return out


@mode("flow", "victory", VictoryConfig,
      "Win conditions: first to a score, highest score at a round, last one standing, last team, cooperative win or "
      "loss, a condition held for K rounds, eliminating a type, or completing objectives — with tiebreaks and tie "
      "rules. Generates `end` entries (and end-of-round events for `most` and `stable`); $best(items, by, ties) "
      "resolves winners anywhere. For an agent type it fills the `game` section: seats and returns ($won).",
      example={"who": "player", "alive": "not $it.bankrupt",
               "conditions": [{"first_to": 10, "score": "$it.points"}, {"last_standing": True},
                              {"most": "$it.points"}], "tiebreak": ["$it.cash"]}, ends=lambda cfg: True)
def _expand(name: str, cfg: VictoryConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    players = cfg.who
    common.types_in(contract, players, "who")
    alive = f"({cfg.alive})"
    in_play = f"$filter({players}, {alive})"
    breaks = [f"({t})" for t in cfg.tiebreak]
    end: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    world: Dict[str, Any] = {}

    def best_of(items: str, keys: List[str]) -> str:
        ranked = f"$best({items}, [{', '.join(keys) or '0'}]"
        if cfg.ties == "share":  # the one winner, else every player tied for the win (null when nobody is left)
            return f"({ranked}, 'none') or {ranked}, 'all') or null)"
        return f"{ranked}, '{cfg.ties}')"

    for index, (label, c) in enumerate(_labelled(cfg)):
        field = f"conditions[{index}]"
        kind = c.kind
        when: Optional[str] = None
        winner: Optional[str]
        if kind == "first_to":
            reached = f"$filter({players}, {alive} and ({c.score}) >= ({c.first_to}))"
            when, winner = f"$len({reached}) > 0", best_of(reached, [f"({c.score})", *breaks])
        elif kind == "most":
            winner = best_of(in_play, [f"({c.most})", *breaks])
        elif kind == "last_standing":
            when, winner = f"$count({players}, {alive}) <= 1", best_of(in_play, breaks)
        elif kind == "last_team":
            teams = f"$unique($map({in_play}, {c.last_team}))"
            when, winner = f"$len({teams}) <= 1", f"$first({teams})"
        elif kind == "win_when":
            when, winner = str(c.win_when), in_play
        elif kind == "lose_when":
            when, winner = str(c.lose_when), None
        elif kind == "stable":
            streak = f"{name}_{label}_streak"
            world[streak] = {"type": "int", "default": 0, "description": f"Rounds in a row that {label} has held."}
            events.append({"name": streak, "phase": "end", "do": [f"$world.{streak} = $world.{streak} + 1 if ({c.stable}) else 0"]})
            when, winner = f"$world.{streak} >= {c.rounds}", None
        elif kind == "eliminate":
            common.types_in(contract, str(c.eliminate), f"{field}.eliminate")
            left = f"$count({c.eliminate}, {c.where})" if c.where else f"$count({c.eliminate})"
            when, winner = f"{left} == 0", in_play
        else:
            met = " and ".join(f"({o})" for o in c.objectives or [])
            done = f"$filter({players}, {alive} and {met})"
            when, winner = f"$len({done}) > 0", best_of(done, breaks)
        if c.winner is not None:
            winner = c.winner
        say = c.say or _default_say(kind, winner)
        if kind == "most":
            entry = {"end": label, "say": say, **({"winner": winner} if winner else {})}
            events.append({"name": f"{name}_{label}", "phase": "end", "at": c.at if c.at is not None else "$clock.rounds",
                           "do": [entry]})
            continue
        end.append({"name": label, "when": when, "say": say, **({"winner": winner} if winner else {})})
    if not cfg.conditions:
        raise MechanismError("give at least one condition", None, "conditions")
    fragment: Dict[str, Any] = {"end": end, "events": events, **({"world": world} if world else {})}
    if common.is_agent_type(contract, players):
        fragment["game"] = {"players": players, "returns": f"$won($actor, '{name}')"}
    return fragment


def _default_say(kind: str, winner: Optional[str]) -> str:
    if kind == "lose_when":
        return "The players lose."
    if kind == "win_when":
        return "The players win."
    if kind == "last_team":
        return f"Team {{{winner}}} wins." if winner else "The game is over."
    return f"Winner: {{{winner}}}." if winner else "The game is over."


def _key(call: Call, value: Any) -> Any:
    parts = value if isinstance(value, list) else [value]
    for part in parts:
        if isinstance(part, bool):
            part = int(part)
        if not isinstance(part, (int, float, str)):
            raise ExprError(f"$best: a ranking key is a number, text or a list of them, got {part!r}", call.source)
    return tuple(int(p) if isinstance(p, bool) else p for p in parts)


@function("best(items, by, ties?)",
          "The best of `items` by `by` (a value or list of values, highest first): always one item — a tie is broken at "
          "random (seeded) with ties 'random' (default), or gives null with 'none'; null when empty. ties 'all' always "
          "gives a list: every item tied for best ([] when empty).",
          min_args=2, max_args=3, lazy=[1])
def _best(call: Call) -> Any:
    items = call.collection(0)
    ties = call.arg(2, "random")
    if ties not in ("random", "none", "all"):
        raise ExprError(f"$best: ties is random, none or all, got {ties!r}", call.source)
    if not items:
        return [] if ties == "all" else None
    try:
        keyed = [(_key(call, call.each(1, item, i)), item) for i, item in enumerate(items)]
        best = max(key for key, _ in keyed)
    except TypeError:
        raise ExprError("$best: ranking keys must be comparable (all numbers or all text)", call.source) from None
    top = [item for key, item in keyed if key == best]
    if ties == "all":
        return top
    if len(top) == 1:
        return top[0]
    if ties == "none":
        return None
    world: Any = call.scope.world
    ids = sorted(getattr(item, "id", str(item)) for item in top)
    return top[world.seeds.rng("winner", world.round, *ids).randrange(len(top))]


@function("won(entity, victory)",
          "The entity's share of the win once the victory mechanism has ended the run: 1 for the winner (or every player "
          "of the winning team), 1/n when n players share the win, 0 otherwise and while the run goes on; e.g. "
          "$won($actor, 'victory').",
          min_args=2, max_args=2)
def _won(call: Call) -> float:
    world: Any = call.scope.world
    entity = world.entity(call.arg(0))
    if entity is None:
        raise ExprError(f"$won: expected an entity, got {call.arg(0)!r}", call.source)
    try:
        cfg = common.config(world, str(call.arg(1)), KEY, VictoryConfig, call.source)
    except RunError as exc:
        raise ExprError(f"$won: {exc}", call.source) from None
    ended = world.end_request
    condition = dict(_labelled(cfg)).get(ended["name"]) if ended else None
    if condition is None:
        return 0.0
    winner = ended["winner"]
    if condition.kind == "last_team" and condition.winner is None:
        team = compile_expr(str(condition.last_team))(world.scope(it=entity))
        return 1.0 if winner is not None and team == winner else 0.0
    winners = winner if isinstance(winner, list) else ([] if winner is None else [winner])
    return 1.0 / len(winners) if entity.id in winners else 0.0
