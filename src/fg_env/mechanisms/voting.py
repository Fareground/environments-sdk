"""Voting and social choice: ``$tally_votes`` for any ballot, and the decision family's ``ballot`` mode with its
``tally`` action."""
from __future__ import annotations

import math
from typing import Any, Collection, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import Call, ExprError, function, truthy
from ..registry import MechanismError, family_action, mode
from . import _common
from ._common import ToolsSetting, tools_field

__all__ = ["tally", "METHODS"]

METHODS = ("plurality", "majority", "supermajority", "approval", "ranked", "borda", "score", "condorcet")
#: Methods whose ballot is a single choice; the others take a list (or a map for score).
SINGLE = ("plurality", "majority", "supermajority")
ABSTAIN = "abstain"
KEY = "decision.ballot"


def _key(value: Any) -> str:
    return value.id if hasattr(value, "entity_type") else str(value)


def tally(method: str, ballots: Any, options: Optional[Sequence[Any]] = None, threshold: Optional[float] = None,
          ties: str = "random", rng: Any = None, eligible: Optional[float] = None, quorum: Optional[float] = None,
          weights: Optional[Mapping[str, float]] = None, base: Optional[float] = None,
          vetoers: Optional[Collection[str]] = None) -> Dict[str, Any]:
    """Count ballots. ``ballots`` is ``{voter: ballot}`` or a list of ballots.

    A ballot is one option (plurality, majority, supermajority), a list of options (approval: every
    option approved; ranked and borda: most preferred first; condorcet: a ranking) or a map
    ``{option: score}`` (score). ``"abstain"`` and empty ballots count toward turnout only; a ballot
    naming an option that is not among ``options`` is refused.

    ``weights`` gives a voter's votes (default 1 each; ``votes``, ``cast`` and ``counts`` are then
    weighted totals). ``base`` is what a majority or supermajority share is measured against
    (default: the votes cast, abstentions aside; pass the members' total for "3/5 of all members").
    ``vetoers`` hold a veto: on a two-option ballot, one of them voting for the second option
    defeats the first.

    Returns ``winner`` (None when nobody wins), ``decided`` (there is a winner), ``passed`` (the
    first option won: list a motion's "yes" first), ``counts``, ``ranking``, ``votes``, ``turnout``,
    ``tie``, ``tied``, ``vetoed`` (the voters whose veto defeated it) and, for ranked, the
    elimination ``rounds``.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {', '.join(METHODS)}, got {method!r}")
    if ties not in ("random", "none", "first"):
        raise ValueError(f"ties must be random, none or first, got {ties!r}")
    pairs = list(ballots.items()) if isinstance(ballots, Mapping) else [(None, b) for b in ballots or []]
    order = [_key(o) for o in options] if options is not None else []
    weighed = [(b, _weight(weights, voter)) for voter, b in pairs]
    valid = [(b, w) for b, w in weighed if not _abstained(b)]
    cast = sum(w for _, w in weighed)
    result: Dict[str, Any] = {"method": method, "votes": _clean(sum(w for _, w in valid)), "cast": _clean(cast),
                              "winner": None, "decided": False, "passed": False, "tie": False, "tied": [], "counts": {},
                              "ranking": []}
    if eligible:
        result["turnout"] = cast / eligible
    short = quorum is not None and eligible is not None and cast < quorum * eligible
    if short:  # still counted, so the result shows how it stood; nothing is decided (and no tie is drawn)
        ties, rng = "none", None
    if method == "ranked":
        _instant_runoff(result, order, _rankings(valid, order, method), ties, rng)
    elif method == "condorcet":
        _condorcet(result, order, _rankings(valid, order, method), ties, rng)
    else:
        _decide(result, _scores(method, order, valid), method, threshold, base, ties, rng)
    _veto(result, order, pairs, vetoers or ())
    if short:
        result.update(winner=None, reason="no quorum")
    result["decided"] = result["winner"] is not None
    result["passed"] = result["decided"] and bool(order) and result["winner"] == order[0]
    return result


def _weight(weights: Optional[Mapping[str, float]], voter: Any) -> float:
    return 1.0 if weights is None or voter is None else float(weights.get(_key(voter), 1.0))


def _on_ballot(option: Any, order: List[str]) -> str:
    key = _key(option)
    if order and key != ABSTAIN and key not in order:
        raise ValueError(f"{key!r} is not on the ballot (options: {', '.join(order)})")
    return key


def _scores(method: str, order: List[str], valid: List[Tuple[Any, float]]) -> Dict[str, float]:
    if method in SINGLE:
        return _count(order, [([b], w) for b, w in valid], lambda i: 1.0 if i == 0 else 0.0)
    if method == "approval":
        return _count(order, [(_as_list(b, method), w) for b, w in valid], lambda i: 1.0)
    if method == "borda":
        size = max([len(order)] + [len(_as_list(b, method)) for b, _ in valid])
        return _count(order, [(_as_list(b, method), w) for b, w in valid], lambda i: float(size - 1 - i))
    scores = {o: 0.0 for o in order}
    for b, w in valid:
        if not isinstance(b, Mapping):
            raise ValueError(f"a score ballot is a map of option → score, got {b!r}")
        for option, value in b.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"scores must be numbers, got {value!r} for {option}")
            key = _on_ballot(option, order)
            scores[key] = scores.get(key, 0.0) + value * w
    return scores


def _rankings(valid: List[Tuple[Any, float]], order: List[str], method: str) -> List[Tuple[List[str], float]]:
    return [([_on_ballot(o, order) for o in _as_list(b, method)], w) for b, w in valid]


def _veto(result: Dict[str, Any], order: List[str], pairs: List[Tuple[Any, Any]], vetoers: Collection[str]) -> None:
    """A veto-holder voting against (the second of two options) defeats the first."""
    if not vetoers:
        return
    if len(order) != 2:
        raise ValueError(f"a veto needs a ballot of exactly two options (for, against), got {order}")
    vetoed = [_key(voter) for voter, b in pairs if voter is not None and _key(voter) in vetoers and _key(b) == order[1]]
    if vetoed:
        result.update(winner=order[1], vetoed=vetoed, reason=f"vetoed by {', '.join(vetoed)}")


def _abstained(ballot: Any) -> bool:
    return ballot is None or ballot == ABSTAIN or (isinstance(ballot, (list, tuple, Mapping)) and not ballot)


def _as_list(ballot: Any, method: str) -> List[Any]:
    if isinstance(ballot, (list, tuple)):
        return list(ballot)
    if isinstance(ballot, str) or hasattr(ballot, "entity_type"):
        return [ballot]
    raise ValueError(f"a {method} ballot is a list of options, got {ballot!r}")


def _count(order: List[str], ballots: List[Tuple[List[Any], float]], points: Any) -> Dict[str, float]:
    """Each ballot's ``points(position)`` per option, times the voter's weight."""
    scores = {o: 0.0 for o in order}
    for ballot, weight in ballots:
        seen = set()
        for i, option in enumerate(ballot):
            key = _on_ballot(option, order)
            if key == ABSTAIN or key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + points(i) * weight
    return scores


def _clean(value: float) -> Union[int, float]:
    return int(value) if float(value).is_integer() else value


def _break_tie(tied: List[str], ties: str, rng: Any) -> Optional[str]:
    if len(tied) == 1:
        return tied[0]
    if ties == "none":
        return None
    if ties == "first":
        return tied[0]
    if rng is None:
        raise ValueError("breaking a tie at random needs randomness")
    return tied[rng.randrange(len(tied))]


def _decide(result: Dict[str, Any], scores: Dict[str, float], method: str, threshold: Optional[float],
            base: Optional[float], ties: str, rng: Any) -> Dict[str, Any]:
    ranking = sorted(scores, key=lambda o: -scores[o])  # stable: declared order breaks equal scores
    result["counts"] = {o: _clean(scores[o]) for o in ranking}
    result["ranking"] = ranking
    if not ranking or result["votes"] == 0:
        return result
    top = scores[ranking[0]]
    tied = [o for o in ranking if scores[o] == top]
    result["tie"], result["tied"] = len(tied) > 1, tied if len(tied) > 1 else []
    winner = _break_tie(tied, ties, rng)
    if method in ("majority", "supermajority"):
        need = threshold if threshold is not None else (0.5 if method == "majority" else 2 / 3)
        total = result["votes"] if base is None else base
        share = top / total if total > 0 else 0.0
        result["share"] = share
        strictly = method == "majority" and threshold is None
        casting = ties == "first" and len(tied) > 1  # a casting vote carries an exact draw at the threshold
        passed = share > need or (share >= need and (casting or not strictly))
        if not passed:
            of = "" if base is None else " of all members"
            result["reason"] = f"no option reached {'more than ' if strictly else ''}{need:.0%}{of}"
            return result
        if len(tied) > 1 and ties != "first":  # a threshold is met by one option, not by a draw between several
            result["reason"] = f"{' and '.join(tied)} tied at {share:.0%}"
            return result
    result["winner"] = winner
    return result


def _instant_runoff(result: Dict[str, Any], order: List[str], ballots: List[Tuple[List[str], float]], ties: str,
                    rng: Any) -> Dict[str, Any]:
    remaining = list(dict.fromkeys(order + [o for b, _ in ballots for o in b if o != ABSTAIN]))
    rounds: List[Dict[str, Any]] = []
    while remaining:
        counts: Dict[str, float] = {o: 0 for o in remaining}
        active: float = 0
        for ballot, weight in ballots:
            choice = next((o for o in ballot if o in counts), None)
            if choice is not None:
                counts[choice] += weight
                active += weight
        rounds.append({"counts": {o: _clean(c) for o, c in counts.items()}, "active": _clean(active)})
        leader = max(counts.values()) if counts else 0
        if active and (leader * 2 > active or len(remaining) == 1):
            tied = [o for o in remaining if counts[o] == leader]
            result.update(counts=rounds[0]["counts"], rounds=rounds, ranking=sorted(remaining, key=lambda o: -counts[o]),
                          tie=len(tied) > 1, tied=tied if len(tied) > 1 else [])
            result["winner"] = _break_tie(tied, ties, rng)
            return result
        if not active:
            break
        low = min(counts.values())
        losers = [o for o in remaining if counts[o] == low]
        if len(losers) == len(remaining):  # everyone tied: decide among them
            result.update(counts=rounds[0]["counts"], rounds=rounds, ranking=list(remaining), tie=True, tied=losers)
            result["winner"] = _break_tie(losers, ties, rng)
            return result
        if ties == "none":  # no draw: every option tied for last is eliminated together
            remaining = [o for o in remaining if o not in losers]
            continue
        out = losers[0] if len(losers) == 1 or ties == "first" or rng is None else losers[rng.randrange(len(losers))]
        remaining.remove(out)
    result.update(counts=rounds[0]["counts"] if rounds else {}, rounds=rounds)
    return result


def _condorcet(result: Dict[str, Any], order: List[str], ballots: List[Tuple[List[str], float]], ties: str,
               rng: Any) -> Dict[str, Any]:
    """Copeland: each option scores a point per head-to-head win (half per draw); an option beating
    every other is the Condorcet winner."""
    options = list(dict.fromkeys(order + [o for b, _ in ballots for o in b if o != ABSTAIN]))
    position = [({o: i for i, o in enumerate(b)}, w) for b, w in ballots]
    wins: Dict[str, float] = {o: 0.0 for o in options}
    pairwise: Dict[str, Dict[str, Union[int, float]]] = {o: {} for o in options}
    for a in options:
        for b in options:
            if a == b:
                continue
            pairwise[a][b] = _clean(sum(w for p, w in position if p.get(a, math.inf) < p.get(b, math.inf)))
    for a in options:
        for b in options:
            if a < b:
                if pairwise[a][b] > pairwise[b][a]:
                    wins[a] += 1
                elif pairwise[a][b] < pairwise[b][a]:
                    wins[b] += 1
                else:
                    wins[a] += 0.5
                    wins[b] += 0.5
    decided = _decide(result, wins, "condorcet", None, None, ties, rng)
    decided["pairwise"] = pairwise
    beats_all = [o for o in options if all(pairwise[o][x] > pairwise[x][o] for x in options if x != o)]
    decided["condorcet_winner"] = beats_all[0] if beats_all else None
    return decided


@function("tally_votes(method, ballots, options?, threshold?, ties?)",
          "Count ballots with a voting method (plurality, majority, supermajority, approval, ranked, borda, "
          "score, condorcet). ballots: {voter: ballot} or a list; returns {winner, decided, passed, counts, ranking, "
          "votes, tie, tied, share, rounds}: decided = there is a winner, passed = the first option won (list a "
          "motion's yes first). ties: random (seeded) | none | first.",
          min_args=2, max_args=5)
def _tally_function(call: Call) -> Dict[str, Any]:
    method = call.arg(0)
    options = call.arg(2)
    threshold = call.arg(3)
    ties = call.arg(4, "random")
    if options is not None and not isinstance(options, (list, tuple)):
        raise ExprError(f"$tally_votes: options must be a list, got {options!r}", call.source)
    try:
        return tally(str(method), call.arg(1), options, threshold, str(ties), call.scope.world.rng)
    except ValueError as exc:
        raise ExprError(f"$tally_votes: {exc}", call.source) from None


# ---------------------------------------------------------------------------
# The ballot mechanism
# ---------------------------------------------------------------------------


class BallotConfig(BaseModel):
    """A vote among agents of one type."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Agent type that votes (subtypes included).")
    options: Union[List[Any], str] = Field(..., description="The choices: a list, or an expression giving a list (e.g. \"$map(candidate, $it.id)\").")
    method: Literal["plurality", "majority", "supermajority", "approval", "ranked", "borda", "condorcet"] = Field(
        "plurality", description="plurality (most votes) | majority (more than half) | supermajority (threshold, default 2/3) "
                                 "| approval (approve any number) | ranked (instant runoff) | borda | condorcet (Copeland); "
                                 "the last four take a list ballot. (score ballots are a map: count them with $tally_votes.)")
    threshold: Optional[float] = Field(None, ge=0, le=1, description="Share of votes needed to pass (majority/supermajority).")
    threshold_of: Literal["votes", "members"] = Field(
        "votes", description="What the threshold is a share of: the votes cast (abstentions aside), or all members still in "
                             "the game (e.g. cloture at 3/5 of the senate).")
    weight: Optional[str] = Field(None, description="Votes each voter casts, an expression over the voter $it (e.g. "
                                                    "\"$it.shares\"); default 1. Turnout and quorum count weight too.")
    veto: Optional[str] = Field(None, description="Who holds a veto, an expression over the voter $it (e.g. \"$it.permanent\"): "
                                                  "one of them voting for the second option defeats the first. Needs exactly "
                                                  "two options, the motion first.")
    quorum: Optional[float] = Field(None, ge=0, le=1, description="Share of eligible voters who must cast a ballot (abstentions count).")
    abstain: bool = Field(True, description="Voters may abstain.")
    private: bool = Field(True, description="Ballots stay private; only the result is announced.")
    ties: Literal["random", "none", "first"] = Field("random", description="How a tie is decided (random uses the run's seed; none leaves it undecided, and in a ranked count eliminates every option tied for last together). A majority or supermajority tied at the top fails unless ties is first (a casting vote for the first option).")
    stage: Optional[str] = Field(None, description="Vote during this declared stage (tally at its end); default: a simultaneous stage named after the vote.")
    when: Optional[str] = Field(None, description="Hold the vote only when true (e.g. \"$round == 3\").")
    question: str = Field("", description="What is being decided, shown with the ballot.")
    announce: str = Field("", description="Result text (template over $result); default names the winner or says it failed.")
    tools: ToolsSetting = tools_field()


def _options(runner: Any, config: BallotConfig, vars: Dict[str, Any]) -> List[Any]:
    value = runner.eval(config.options, vars) if isinstance(config.options, str) else config.options
    if not isinstance(value, list):
        raise ExprError(f"ballot options must be a list, got {value!r}", str(config.options))
    return [_key(v) for v in value]


@family_action("decision", ("ballot",), "tally",
               example='{"decision": "election", "action": "tally"}  (count the ballot now: sets $world.election_result, '
                       'announces it, opens a fresh ballot)')
def _tally_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    name = effect["decision"]
    world = runner.world
    config = _common.config(world, name, KEY, BallotConfig, where)
    ballots = dict(world.props.get(f"{name}_ballots") or {})
    voters = _voters_in_game(world, config.who)
    weights = {v.id: _weight_of(runner, config.weight, v, f"mechanisms.{name}.weight") for v in voters} if config.weight else None
    eligible = sum(weights.values()) if weights is not None else len(voters)
    vetoers = [v.id for v in voters if config.veto and truthy(runner.eval(config.veto, {"it": v}))]
    try:
        result = tally(config.method, ballots, _options(runner, config, vars), config.threshold, config.ties,
                       world.rng, eligible, config.quorum, weights, eligible if config.threshold_of == "members" else None,
                       vetoers)
    except ValueError as exc:
        raise RunError(f"tally {name}: {exc}", where) from None
    result["round"] = world.round
    world.set_world(f"{name}_result", result)
    world.set_world(f"{name}_ballots", {})
    text = runner.text(config.announce, {**vars, "result": result}) if config.announce else _announcement(world, config, result)
    world.emit(name, text, data={"mechanism": "ballot", "result": result})


def _weight_of(runner: Any, expr: str, voter: Any, where: str) -> float:
    value = runner.eval(expr, {"it": voter})
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise RunError(f"a voter's weight must be a number ≥ 0, got {value!r} for {voter.id}", where)
    return float(value)


def _voters_in_game(world: Any, who: str) -> List[Any]:
    """Voters still in the game: every living `who`, less those a roles mechanism on the same type has put out."""
    out_props = [raw.get("alive") or "living" for _, raw in _common.uses(world.contract, "groups.roles")
                 if raw.get("who") == who]
    return [voter for voter in world.entities_of(who) if all(voter.properties.get(prop, True) for prop in out_props)]


def _announcement(world: Any, config: BallotConfig, result: Dict[str, Any]) -> str:
    """The result in words, options that are entity ids shown by name."""
    def label(option: Any) -> str:
        entity = world.entity(option) if isinstance(option, str) else None
        return str(entity.name or entity.id) if entity is not None else str(option)

    subject = config.question or "The vote"
    counts = ", ".join(f"{label(k)} {v}" for k, v in result["counts"].items())
    if result.get("reason") == "no quorum":
        return f"{subject}: no quorum ({result['cast']} ballot(s) cast)."
    if result.get("vetoed"):
        return f"{subject}: vetoed by {', '.join(label(v) for v in result['vetoed'])} ({counts})."
    if result["winner"] is None:
        return f"{subject}: no decision ({result.get('reason') or 'tie'}; {counts})."
    tie = " after a tie" if result["tie"] else ""
    return f"{subject}: {label(result['winner'])} wins{tie} ({counts})."


@mode("decision", "ballot", BallotConfig,
           "A vote among agents: a `<name>_vote` tool (and `<name>_abstain`), counted by plurality, majority or "
           "supermajority with an optional quorum when the vote's stage ends — after that stage's own on_exit effects, so "
           "read the result in a later stage, event or on_enter, not in the vote stage's on_exit. The result is in "
           "$world.<name>_result ({winner, decided, passed, counts, ranking, votes, turnout, tie, vetoed}; an empty map until "
           "the first count) and is announced, options that are entity ids named: `decided` is true when there is a winner, "
           "`passed` when the first option won, so list a motion's yes first. Turnout counts the voters still in the game. "
           "`weight` gives shareholder-style votes, `threshold_of: members` measures the threshold over every member "
           "(cloture), `veto` lets some voters defeat a motion alone (a security council).",
           example={"who": "member", "options": ["approve", "reject"], "method": "majority", "quorum": 0.5,
                    "question": "Adopt the budget?"})
def _expand_ballot(name: str, config: BallotConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    types = contract.get("types") or {}
    if config.who not in types:
        raise MechanismError(f"who '{config.who}' is not a declared type", f"types: {', '.join(types) or 'none'}", "who")
    if config.veto and (config.method not in SINGLE or (isinstance(config.options, list) and len(config.options) != 2)):
        raise MechanismError("a veto needs a single-choice ballot of exactly two options", 'the motion first, e.g. '
                             '"options": ["adopt", "reject"], "method": "majority"', "veto")
    if config.threshold_of == "members" and config.method not in ("majority", "supermajority"):
        raise MechanismError("threshold_of: members needs a threshold, so method majority or supermajority",
                             'set "method": "supermajority"', "threshold_of")
    ballots, result = f"{name}_ballots", f"{name}_result"
    vote, abstain = f"{name}_vote", f"{name}_abstain"
    question = f" on: {config.question}" if config.question else ""
    open_ballot = f"not ($actor.id in $world.{ballots})"
    ballot_param: Dict[str, Any]
    if config.method in SINGLE:
        ballot_param = {"choice": {"type": "enum", "values": config.options, "description": "Your choice."}}
        cast, told, how = "$params.choice", "You voted {$params.choice}.", "Cast your ballot"
    else:
        wording = {"approval": ("every option you approve of", "Approve options"),
                   "ranked": ("the options in order of preference, most preferred first", "Rank the options"),
                   "borda": ("the options in order of preference, most preferred first", "Rank the options"),
                   "condorcet": ("the options in order of preference, most preferred first", "Rank the options")}
        what, how = wording[config.method]
        ballot_param = {"choices": {"type": "list", "values": config.options, "min_items": 1, "unique": True,
                                    "description": f"List {what}."}}
        cast, told = "$params.choices", "Your ballot: {$params.choices}."
    actions: Dict[str, Any] = {
        vote: {"by": config.who, "description": f"{how}{question}.",
               "params": ballot_param,
               "when": [{"expr": open_ballot, "why": "You have already voted."}],
               "do": [f"$world.{ballots}[$actor.id] = {cast}"],
               "outcome": told if config.private else None,
               "private": config.private, "terminal": True},
    }
    if config.abstain:
        actions[abstain] = {"by": config.who, "description": f"Abstain{question}.",
                            "when": [{"expr": open_ballot, "why": "You have already voted."}],
                            "do": [f"$world.{ballots}[$actor.id] = '{ABSTAIN}'"], "outcome": "You abstained.",
                            "private": config.private, "terminal": True}
    for action in actions.values():
        if action.get("outcome") is None:
            action.pop("outcome", None)
    fragment: Dict[str, Any] = {
        "world": {ballots: {"type": "map", "default": {}}, result: {"type": "map", "default": {}}},
        "actions": actions,
    }
    names = list(actions)
    if config.stage is None:
        stage: Dict[str, Any] = {"name": name, "turns": "simultaneous", "actions": names,
                                 "brief": config.question, "on_exit": [{"decision": name, "action": "tally"}]}
        if config.when:
            stage["when"] = config.when
        fragment["stages"] = [stage]
    else:
        if config.when:
            for action in actions.values():
                action["when"].append({"expr": config.when, "why": "The vote is not open now."})
        fragment["stage_hooks"] = {config.stage: {"actions": names, "on_exit": [{"decision": name, "action": "tally"}]}}
    return fragment
