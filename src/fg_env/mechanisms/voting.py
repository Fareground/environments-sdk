"""Voting and social choice: ``$tally_votes`` for any ballot, and the decision family's ``ballot`` mode with its
``tally`` action."""
from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import EVERYONE, Call, ExprError, function, truthy
from ..registry import MechanismError, family_action, mechanism_config, mode
from . import _common
from .expressions import EachWho, Expr

__all__ = ["tally", "METHODS"]

METHODS = ("plurality", "majority", "supermajority", "approval", "ranked", "borda", "score", "condorcet")
#: Methods whose ballot is a single choice; the others take a list (or a map for score).
SINGLE = ("plurality", "majority", "supermajority")
ABSTAIN = "abstain"
KEY = "decision.ballot"


def _key(value: Any) -> str:
    return value.id if hasattr(value, "entity_type") else str(value)


def tally(method: str, ballots: Any, options: Sequence[Any] | None = None, threshold: float | None = None,
          ties: str = "random", rng: Any = None, eligible: float | None = None, quorum: float | None = None,
          weights: Mapping[str, float] | None = None, base: float | None = None,
          vetoers: Collection[str] | None = None) -> dict[str, Any]:
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
    first option won: list a motion's "yes" first), ``counts``, ``ranking`` (the winner first),
    ``votes``, ``turnout``, ``tie`` (a tie-break decided the winner or, in a ranked count, an
    elimination), ``tied`` (the options it chose among), ``vetoed`` (the voters whose veto defeated
    it) and, for ranked, the elimination ``rounds``. ``ties="first"`` favours the first-declared
    option in every method: it wins a tie at the top and survives a tie for last.
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
    result: dict[str, Any] = {"method": method, "votes": _clean(sum(w for _, w in valid)), "cast": _clean(cast),
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


def _weight(weights: Mapping[str, float] | None, voter: Any) -> float:
    return 1.0 if weights is None or voter is None else float(weights.get(_key(voter), 1.0))


def _on_ballot(option: Any, order: list[str]) -> str:
    key = _key(option)
    if order and key != ABSTAIN and key not in order:
        raise ValueError(f"{key!r} is not on the ballot (options: {', '.join(order)})")
    return key


def _scores(method: str, order: list[str], valid: list[tuple[Any, float]]) -> dict[str, float]:
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


def _rankings(valid: list[tuple[Any, float]], order: list[str], method: str) -> list[tuple[list[str], float]]:
    return [([_on_ballot(o, order) for o in _as_list(b, method)], w) for b, w in valid]


def _veto(result: dict[str, Any], order: list[str], pairs: list[tuple[Any, Any]], vetoers: Collection[str]) -> None:
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


def _as_list(ballot: Any, method: str) -> list[Any]:
    if isinstance(ballot, (list, tuple)):
        return list(ballot)
    if isinstance(ballot, str) or hasattr(ballot, "entity_type"):
        return [ballot]
    raise ValueError(f"a {method} ballot is a list of options, got {ballot!r}")


def _count(order: list[str], ballots: list[tuple[list[Any], float]], points: Any) -> dict[str, float]:
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


def _clean(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _break_tie(tied: list[str], ties: str, rng: Any) -> str | None:
    if len(tied) == 1:
        return tied[0]
    if ties == "none":
        return None
    if ties == "first":
        return tied[0]
    if rng is None:
        raise ValueError("breaking a tie at random needs randomness")
    return tied[rng.randrange(len(tied))]


def _drop(tied: list[str], ties: str, rng: Any) -> str:
    """The option eliminated from a tie for last: the mirror of ``_break_tie`` (``first`` keeps the first-declared)."""
    if len(tied) == 1 or ties == "first":
        return tied[-1]
    if rng is None:
        raise ValueError("breaking a tie at random needs randomness")
    return tied[rng.randrange(len(tied))]


def _lead(ranking: list[str], winner: str | None) -> list[str]:
    """The ranking with the winner first (a tie-break may have picked one listed after it)."""
    return ranking if winner is None else [winner, *(o for o in ranking if o != winner)]


def _decide(result: dict[str, Any], scores: dict[str, float], method: str, threshold: float | None,
            base: float | None, ties: str, rng: Any) -> dict[str, Any]:
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
            result["reason"] = f"no option reached {'more than ' if strictly else ''}{_common.pct(need)}{of}"
            return result
        if len(tied) > 1 and ties != "first":  # a threshold is met by one option, not by a draw between several
            result["reason"] = f"{' and '.join(tied)} tied at {_common.pct(share)}"
            return result
    result["winner"], result["ranking"] = winner, _lead(ranking, winner)
    return result


def _instant_runoff(result: dict[str, Any], order: list[str], ballots: list[tuple[list[str], float]], ties: str,
                    rng: Any) -> dict[str, Any]:
    remaining = list(dict.fromkeys(order + [o for b, _ in ballots for o in b if o != ABSTAIN]))
    rounds: list[dict[str, Any]] = []
    broken: list[str] = []  # the last tie a tie-break decided, if any
    while remaining:
        counts: dict[str, float] = {o: 0 for o in remaining}
        active: float = 0
        for ballot, weight in ballots:
            choice = next((o for o in ballot if o in counts), None)
            if choice is not None:
                counts[choice] += weight
                active += weight
        rounds.append({"counts": {o: _clean(c) for o, c in counts.items()}, "active": _clean(active)})
        leader = max(counts.values()) if counts else 0
        low = min(counts.values()) if counts else 0
        losers = [o for o in remaining if counts[o] == low]
        finished = active and (leader * 2 > active or len(remaining) == 1)
        if finished or (active and len(losers) == len(remaining)):  # a majority, or everyone tied: decide among them
            tied = [o for o in remaining if counts[o] == leader]
            winner = _break_tie(tied, ties, rng)
            broken = tied if len(tied) > 1 else broken
            result.update(counts=rounds[0]["counts"], rounds=rounds, winner=winner, tie=bool(broken), tied=broken,
                          ranking=_lead(sorted(remaining, key=lambda o: -counts[o]), winner))
            return result
        if not active:
            break
        if ties == "none":  # no draw: every option tied for last is eliminated together
            remaining = [o for o in remaining if o not in losers]
            continue
        if len(losers) > 1:
            broken = losers
        remaining.remove(_drop(losers, ties, rng))
    result.update(counts=rounds[0]["counts"] if rounds else {}, rounds=rounds)
    return result


def _condorcet(result: dict[str, Any], order: list[str], ballots: list[tuple[list[str], float]], ties: str,
               rng: Any) -> dict[str, Any]:
    """Copeland: each option scores a point per head-to-head win (half per draw); an option beating
    every other is the Condorcet winner."""
    options = list(dict.fromkeys(order + [o for b, _ in ballots for o in b if o != ABSTAIN]))
    position = [({o: i for i, o in enumerate(b)}, w) for b, w in ballots]
    wins: dict[str, float] = {o: 0.0 for o in options}
    pairwise: dict[str, dict[str, int | float]] = {o: {} for o in options}
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
def _tally_function(call: Call) -> dict[str, Any]:
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
    options: list[Any] | Expr = Field(...,
                                     description="The choices: a list, or an expression giving a list (e.g. "
                                                 "\"$map(candidate, $it.id)\").")
    method: Literal["plurality", "majority", "supermajority", "approval", "ranked", "borda", "condorcet"] = Field(
        "plurality", description="plurality (most votes) | majority (more than half) | supermajority (threshold, "
                                 "default 2/3) | approval (approve any number) | ranked (instant runoff) | borda | "
                                 "condorcet (Copeland); the last four take a list ballot. (score ballots are a map: "
                                 "count them with $tally_votes.)")
    threshold: float | None = Field(None, ge=0, le=1,
                                    description="Share of votes needed to pass (majority/supermajority).")
    threshold_of: Literal["votes", "members"] = Field(
        "votes", description="What the threshold is a share of: the votes cast (abstentions aside), or all members "
                             "still in the game (e.g. cloture at 3/5 of the senate).")
    weight: EachWho | None = Field(None, description="Votes each voter casts, an expression over the voter $it (e.g. "
                                                 "\"$it.shares\"); default 1. Turnout and quorum count weight too.")
    veto: EachWho | None = Field(None, description="Who holds a veto, an expression over the voter $it (e.g. "
                                               "\"$it.permanent\"): one of them voting for the second option defeats "
                                               "the first. Needs exactly two options, the motion first.")
    quorum: float | None = Field(None, ge=0, le=1,
                                 description="Share of eligible voters who must cast a ballot (abstentions count).")
    abstain: bool = Field(True, description="Voters may abstain.")
    private: bool = Field(True, description="Ballots stay private; only the result is announced.")
    ties: Literal["random", "none", "first"] = Field("random",
                                                     description="How a tie is decided (random uses the run's seed; "
                                                                 "first favours the first-declared option; none leaves "
                                                                 "it undecided, and in a ranked count eliminates every "
                                                                 "option tied for last together). A "
                                                                 "majority or supermajority tied at the top fails "
                                                                 "unless ties is first (a casting vote for the first "
                                                                 "option).")
    stage: str | None = Field(None,
                              description="Vote during this declared stage (tally at its end); default: a simultaneous "
                                          "stage named after the vote.")
    when: Expr | None = Field(None, description="Hold the vote only when true (e.g. \"$round == 3\").")
    question: str = Field("", description="What is being decided, shown with the ballot.")
    announce: str = Field("",
                          description="Result text (template over $result); default names the winner or says it "
                                      "failed.")


def _options(runner: Any, config: BallotConfig, vars: dict[str, Any]) -> list[Any]:
    value = runner.eval(config.options, vars) if isinstance(config.options, str) else config.options
    if not isinstance(value, list):
        raise ExprError(f"ballot options must be a list, got {value!r}", str(config.options))
    return [_key(v) for v in value]


@family_action("decision", ("ballot",), "tally",
               example='{"decision": "election", "action": "tally"}  (count the ballot now: sets '
                       '$world.election_result, announces it, opens a fresh ballot)')
def _tally_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    name = effect["decision"]
    world = runner.world
    config = mechanism_config(world, name, KEY, BallotConfig, where)
    prop = f"{name}_ballot"
    cast = [voter for voter in world.entities_of(config.who) if voter.properties.get(prop) is not None]
    ballots = {voter.id: voter.properties[prop] for voter in cast}
    voters = _voters_in_game(world, config.who)
    weights = ({v.id: _weight_of(runner, config.weight, v, f"mechanisms.{name}.weight") for v in voters}
               if config.weight else None)
    eligible = sum(weights.values()) if weights is not None else len(voters)
    vetoers = [v.id for v in voters if config.veto and truthy(runner.expression(config.veto, {"it": v}))]
    try:
        result = tally(config.method, ballots, _options(runner, config, vars), config.threshold, config.ties,
                       world.rng, eligible, config.quorum, weights,
                       eligible if config.threshold_of == "members" else None, vetoers)
    except ValueError as exc:
        raise RunError(f"tally {name}: {exc}", where) from None
    result["round"] = world.round
    world.set_world(f"{name}_result", result)
    for voter in cast:  # a fresh ballot for the next vote
        world.set_prop(voter, prop, None)
    text = (runner.text(config.announce, {**vars, "result": result}, EVERYONE) if config.announce
            else _announcement(world, config, result))
    world.emit(name, text, data={"mechanism": "ballot", "result": result})


@family_action("decision", ("ballot",), "close", internal=True,
               example='{"decision": "election", "action": "close"}  (the end of a declared stage: count the ballot '
                       'when its `when` holds for a voter now, else leave the last result)')
def _close_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    """Count a ballot hooked into a declared stage at the stage's end — only when it is open now: its `when` (a
    requirement of each voter's vote, so it may read `$actor`) holds for some voter still in the game."""
    name = effect["decision"]
    world = runner.world
    config = mechanism_config(world, name, KEY, BallotConfig, where)
    when = config.when
    if when and not any(truthy(runner.eval(when, {"actor": voter})) for voter in _voters_in_game(world, config.who)):
        return
    _tally_op(runner, {"decision": name, "action": "tally"}, vars, where)


def _weight_of(runner: Any, expr: str, voter: Any, where: str) -> float:
    value = runner.expression(expr, {"it": voter})
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise RunError(f"a voter's weight must be a number ≥ 0, got {value!r} for {voter.id}", where)
    return float(value)


def _voters_in_game(world: Any, who: str) -> list[Any]:
    """Voters still in the game: every living `who`, less those a roles mechanism on the same type has put out."""
    out_props = [raw.get("alive") or "living" for _, raw in _common.uses(world.contract, "groups.roles")
                 if raw.get("who") == who]
    return [voter for voter in world.entities_of(who) if all(voter.properties.get(prop, True) for prop in out_props)]


def _announcement(world: Any, config: BallotConfig, result: dict[str, Any]) -> str:
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
    if result["winner"] is None and not any(result["counts"].values()):
        return f"{subject}: no decision (no votes were cast)."
    if result["winner"] is None:
        return f"{subject}: no decision ({result.get('reason') or 'tie'}; {counts})."
    tie = " after a tie" if result["tie"] else ""
    return f"{subject}: {label(result['winner'])} wins{tie} ({counts})."


@mode("decision", "ballot", BallotConfig,
           "A vote among agents: a `<name>_vote` tool (and `<name>_abstain`), counted by plurality, majority or "
           "supermajority with an optional quorum when the vote's stage ends — after the contract's own events on its "
           "end, so read the result in a later stage or event, not in an event on the vote stage's end. The "
           "result is in $world.<name>_result "
           "({winner, decided, passed, counts, ranking, votes, turnout, tie, vetoed}; an empty map until the first "
           "count) and is announced, options that are entity ids named: `decided` is true when there is a winner, "
           "`passed` when the first option won, so list a motion's yes first. Turnout counts the voters still in the "
           "game. `weight` gives shareholder-style votes, `threshold_of: members` measures the threshold over every "
           "member (cloture), `veto` lets some voters defeat a motion alone (a security council).",
           example={"who": "member", "options": ["approve", "reject"], "method": "majority", "quorum": 0.5,
                    "question": "Adopt the budget?"})
def _expand_ballot(name: str, config: BallotConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    types = contract.get("types") or {}
    if config.who not in types:
        raise MechanismError(f"who '{config.who}' is not a declared type", f"types: {', '.join(types) or 'none'}",
                             "who")
    if config.veto and (config.method not in SINGLE or (isinstance(config.options, list) and len(config.options) != 2)):
        raise MechanismError("a veto needs a single-choice ballot of exactly two options", 'the motion first, e.g. '
                             '"options": ["adopt", "reject"], "method": "majority"', "veto")
    if config.threshold_of == "members" and config.method not in ("majority", "supermajority"):
        raise MechanismError("threshold_of: members needs a threshold, so method majority or supermajority",
                             'set "method": "supermajority"', "threshold_of")
    # Each voter's ballot is its own property, so casting one costs the same however many have voted.
    ballot, result = f"{name}_ballot", f"{name}_result"
    vote, abstain = f"{name}_vote", f"{name}_abstain"
    # A tool's description is plain text: a question that is a template (`{$world.bill}`) is shown in the stage's brief
    # and the result, rendered, and left out here rather than shown with its braces.
    plain = config.question.strip() if "{" not in config.question else ""
    question = f" on: {plain.rstrip('.')}" if plain else ""
    open_ballot = f"$actor.{ballot} == null"
    # In a declared stage the vote is one of the turn's moves, not its end: the stage gives each mechanism hooked into
    # it its share of the turn, and voting first must not forfeit the rest.
    shared = config.stage is not None
    ballot_param: dict[str, Any]
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
    actions: dict[str, Any] = {
        vote: {"by": config.who, "description": _sentence(f"{how}{question}"),
               "params": ballot_param,
               "when": [{"expr": open_ballot, "why": "You have already voted."}],
               "do": [f"$actor.{ballot} = {cast}"],
               "outcome": told if config.private else None,
               "private": config.private, "terminal": not shared},
    }
    if config.abstain:
        actions[abstain] = {"by": config.who, "description": _sentence(f"Abstain{question}"),
                            "when": [{"expr": open_ballot, "why": "You have already voted."}],
                            "do": [f"$actor.{ballot} = '{ABSTAIN}'"], "outcome": "You abstained.",
                            "private": config.private, "terminal": not shared}
    for action in actions.values():
        if action.get("outcome") is None:
            action.pop("outcome", None)
    fragment: dict[str, Any] = {
        "world": {result: {"type": "map", "default": {}}},
        "types": {config.who: {"props": {ballot: {"type": "any", "default": None, "private": config.private}}}},
        "actions": actions,
    }
    names = list(actions)
    if config.stage is None:
        stage: dict[str, Any] = {"name": name, "turns": "simultaneous", "actions": names, "brief": config.question}
        if config.when:
            stage["when"] = config.when
        fragment["stages"] = [stage]
    else:
        if config.when:
            for action in actions.values():
                action["when"].append({"expr": config.when, "why": "The vote is not open now."})
        fragment["stage_hooks"] = {config.stage: {"actions": names}}
    # In a declared stage the count obeys `when` too (`close`): a stage that repeats would otherwise count a closed
    # ballot again at every end and overwrite its result with an empty one.
    count = "close" if config.stage is not None and config.when else "tally"
    fragment["events"] = [_common.stage_event(config.stage or name, "end", [{"decision": name, "action": count}])]
    return fragment


def _sentence(text: str) -> str:
    """``text`` ending as a sentence does: a question keeps its question mark, anything else gets a full stop."""
    return text if text.endswith(("?", "!", ".")) else f"{text}."

