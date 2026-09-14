"""Voting and social choice: ``$tally_votes`` for any ballot, the ``tally`` op, and the ``ballot`` mechanism."""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from ..expr import Call, ExprError, function
from ..registry import MechanismError, effect_op, mechanism

__all__ = ["tally", "METHODS"]

METHODS = ("plurality", "majority", "supermajority", "approval", "ranked", "borda", "score", "condorcet")
#: Methods whose ballot is a single choice; the others take a list (or a map for score).
SINGLE = ("plurality", "majority", "supermajority")
ABSTAIN = "abstain"


def _key(value: Any) -> str:
    return value.id if hasattr(value, "entity_type") else str(value)


def tally(method: str, ballots: Any, options: Optional[Sequence[Any]] = None, threshold: Optional[float] = None,
          ties: str = "random", rng: Any = None, eligible: Optional[int] = None, quorum: Optional[float] = None) -> Dict[str, Any]:
    """Count ballots. ``ballots`` is ``{voter: ballot}`` or a list of ballots.

    A ballot is one option (plurality, majority, supermajority), a list of options (approval: every
    option approved; ranked and borda: most preferred first; condorcet: a ranking) or a map
    ``{option: score}`` (score). ``"abstain"`` and empty ballots count toward turnout only.
    Returns ``winner`` (None when nobody wins), ``passed``, ``counts``, ``ranking``, ``votes``,
    ``turnout``, ``tie``, ``tied`` and, for ranked, the elimination ``rounds``.
    """
    if method not in METHODS:
        raise ValueError(f"method must be one of {', '.join(METHODS)}, got {method!r}")
    if ties not in ("random", "none", "first"):
        raise ValueError(f"ties must be random, none or first, got {ties!r}")
    cast = list(ballots.values()) if isinstance(ballots, Mapping) else list(ballots or [])
    order = [_key(o) for o in options] if options is not None else []
    valid = [b for b in cast if not _abstained(b)]
    result: Dict[str, Any] = {"method": method, "votes": len(valid), "cast": len(cast), "winner": None,
                              "passed": False, "tie": False, "tied": [], "counts": {}, "ranking": []}
    if eligible:
        result["turnout"] = len(cast) / eligible
    if quorum is not None and eligible is not None and len(cast) < quorum * eligible:
        result["reason"] = "no quorum"
        return result
    if method in SINGLE:
        scores = _count(order, [[_key(b)] for b in valid], lambda ballot, i: 1.0 if i == 0 else 0.0)
    elif method == "approval":
        scores = _count(order, [_as_list(b, method) for b in valid], lambda ballot, i: 1.0)
    elif method == "borda":
        size = max([len(order)] + [len(_as_list(b, method)) for b in valid])
        scores = _count(order, [_as_list(b, method) for b in valid], lambda ballot, i: float(size - 1 - i))
    elif method == "score":
        scores = {o: 0.0 for o in order}
        for b in valid:
            if not isinstance(b, Mapping):
                raise ValueError(f"a score ballot is a map of option → score, got {b!r}")
            for option, value in b.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"scores must be numbers, got {value!r} for {option}")
                scores[_key(option)] = scores.get(_key(option), 0.0) + value
    elif method == "ranked":
        return _instant_runoff(result, order, [[_key(o) for o in _as_list(b, method)] for b in valid], ties, rng)
    else:
        return _condorcet(result, order, [[_key(o) for o in _as_list(b, method)] for b in valid], ties, rng)
    return _decide(result, scores, method, threshold, ties, rng)


def _abstained(ballot: Any) -> bool:
    return ballot is None or ballot == ABSTAIN or (isinstance(ballot, (list, tuple, Mapping)) and not ballot)


def _as_list(ballot: Any, method: str) -> List[Any]:
    if isinstance(ballot, (list, tuple)):
        return list(ballot)
    if isinstance(ballot, str) or hasattr(ballot, "entity_type"):
        return [ballot]
    raise ValueError(f"a {method} ballot is a list of options, got {ballot!r}")


def _count(order: List[str], ballots: List[List[Any]], weight: Any) -> Dict[str, float]:
    scores = {o: 0.0 for o in order}
    for ballot in ballots:
        seen = set()
        for i, option in enumerate(ballot):
            key = _key(option)
            if key == ABSTAIN or key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + weight(ballot, i)
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
            ties: str, rng: Any) -> Dict[str, Any]:
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
        share = top / result["votes"]
        result["share"] = share
        strictly = method == "majority" and threshold is None
        passed = share > need if strictly else share >= need
        if not passed or (len(tied) > 1 and ties == "none"):
            result["reason"] = f"no option reached {'more than ' if strictly else ''}{need:.0%}"
            return result
    result["winner"] = winner
    result["passed"] = winner is not None
    return result


def _instant_runoff(result: Dict[str, Any], order: List[str], ballots: List[List[str]], ties: str,
                    rng: Any) -> Dict[str, Any]:
    remaining = list(dict.fromkeys(order + [o for b in ballots for o in b if o != ABSTAIN]))
    rounds: List[Dict[str, Any]] = []
    while remaining:
        counts = {o: 0 for o in remaining}
        active = 0
        for ballot in ballots:
            choice = next((o for o in ballot if o in counts), None)
            if choice is not None:
                counts[choice] += 1
                active += 1
        rounds.append({"counts": dict(counts), "active": active})
        leader = max(counts.values()) if counts else 0
        if active and (leader * 2 > active or len(remaining) == 1):
            tied = [o for o in remaining if counts[o] == leader]
            result.update(counts=rounds[0]["counts"], rounds=rounds, ranking=sorted(remaining, key=lambda o: -counts[o]),
                          tie=len(tied) > 1, tied=tied if len(tied) > 1 else [])
            result["winner"] = _break_tie(tied, ties, rng)
            result["passed"] = result["winner"] is not None
            return result
        if not active:
            break
        low = min(counts.values())
        losers = [o for o in remaining if counts[o] == low]
        if len(losers) == len(remaining):  # everyone tied: decide among them
            result.update(counts=rounds[0]["counts"], rounds=rounds, ranking=list(remaining), tie=True, tied=losers)
            result["winner"] = _break_tie(losers, ties, rng)
            result["passed"] = result["winner"] is not None
            return result
        out = losers[0] if len(losers) == 1 or ties == "first" or rng is None else losers[rng.randrange(len(losers))]
        remaining.remove(out)
    result.update(counts=rounds[0]["counts"] if rounds else {}, rounds=rounds)
    return result


def _condorcet(result: Dict[str, Any], order: List[str], ballots: List[List[str]], ties: str, rng: Any) -> Dict[str, Any]:
    """Copeland: each option scores a point per head-to-head win (half per draw); an option beating
    every other is the Condorcet winner."""
    options = list(dict.fromkeys(order + [o for b in ballots for o in b if o != ABSTAIN]))
    position = [{o: i for i, o in enumerate(b)} for b in ballots]
    wins: Dict[str, float] = {o: 0.0 for o in options}
    pairwise: Dict[str, Dict[str, int]] = {o: {} for o in options}
    for a in options:
        for b in options:
            if a == b:
                continue
            prefer = sum(1 for p in position if p.get(a, math.inf) < p.get(b, math.inf))
            pairwise[a][b] = prefer
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
    decided = _decide(result, wins, "condorcet", None, ties, rng)
    decided["pairwise"] = pairwise
    beats_all = [o for o in options if all(pairwise[o][x] > pairwise[x][o] for x in options if x != o)]
    decided["condorcet_winner"] = beats_all[0] if beats_all else None
    return decided


@function("tally_votes(method, ballots, options?, threshold?, ties?)",
          "Count ballots with a voting method (plurality, majority, supermajority, approval, ranked, borda, "
          "score, condorcet). ballots: {voter: ballot} or a list; returns {winner, passed, counts, ranking, "
          "votes, tie, tied, share, rounds}. ties: random (seeded) | none | first.",
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

    voters: str = Field(..., description="Agent type that votes (subtypes included).")
    options: Union[List[Any], str] = Field(..., description="The choices: a list, or an expression giving a list (e.g. \"$map(candidate, $it.id)\").")
    method: Literal["plurality", "majority", "supermajority"] = Field(
        "plurality", description="plurality (most votes) | majority (more than half) | supermajority (threshold, default 2/3).")
    threshold: Optional[float] = Field(None, ge=0, le=1, description="Share of votes needed to pass (majority/supermajority).")
    quorum: Optional[float] = Field(None, ge=0, le=1, description="Share of eligible voters who must cast a ballot (abstentions count).")
    abstain: bool = Field(True, description="Voters may abstain.")
    secret: bool = Field(True, description="Ballots stay private; only the result is announced.")
    ties: Literal["random", "none", "first"] = Field("random", description="How a tie is decided (random uses the run's seed).")
    stage: Optional[str] = Field(None, description="Vote during this declared stage (tally at its end); default: a simultaneous stage named after the vote.")
    when: Optional[str] = Field(None, description="Hold the vote only when true (e.g. \"$round == 3\").")
    question: str = Field("", description="What is being decided, shown with the ballot.")
    announce: str = Field("", description="Result text (template over $result); default names the winner or says it failed.")


def _config(world: Any, name: str) -> BallotConfig:
    raw = world.contract.mechanisms.get(name)
    if not isinstance(raw, Mapping) or raw.get("kind") != "ballot":
        raise MechanismError(f"'{name}' is not a declared ballot")
    return _parse_config(_frozen(raw))


@lru_cache(maxsize=256)
def _parse_config(raw: Tuple[Tuple[str, Any], ...]) -> BallotConfig:
    import json

    data = {k: json.loads(v) for k, v in raw if k != "kind"}
    return BallotConfig.model_validate(data)


def _frozen(raw: Mapping[str, Any]) -> Tuple[Tuple[str, Any], ...]:
    import json

    return tuple(sorted((k, json.dumps(v, sort_keys=True, default=str)) for k, v in raw.items()))


def _options(runner: Any, config: BallotConfig, vars: Dict[str, Any]) -> List[Any]:
    value = runner.eval(config.options, vars) if isinstance(config.options, str) else config.options
    if not isinstance(value, list):
        raise ExprError(f"ballot options must be a list, got {value!r}", str(config.options))
    return [_key(v) for v in value]


@effect_op("tally", keys=(), literal=("tally",),
           example='{"tally": "election"}  (count a declared ballot now: sets $world.election_result, announces it, opens a fresh ballot)')
def _tally_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    from ..errors import RunError

    name = effect["tally"]
    world = runner.world
    try:
        config = _config(world, name)
    except MechanismError as exc:
        raise RunError(str(exc), where) from None
    ballots = dict(world.props.get(f"{name}_ballots") or {})
    eligible = len(world.entities_of(config.voters))
    try:
        result = tally(config.method, ballots, _options(runner, config, vars), config.threshold, config.ties,
                       world.rng, eligible, config.quorum)
    except ValueError as exc:
        raise RunError(f"tally {name}: {exc}", where) from None
    result["round"] = world.round
    world.set_world(f"{name}_result", result)
    world.set_world(f"{name}_ballots", {})
    text = runner.text(config.announce, {**vars, "result": result}) if config.announce else _announcement(config, result)
    world.emit(name, text, data={"mechanism": "ballot", "result": result})


def _announcement(config: BallotConfig, result: Dict[str, Any]) -> str:
    subject = config.question or "The vote"
    counts = ", ".join(f"{k} {v}" for k, v in result["counts"].items())
    if result.get("reason") == "no quorum":
        return f"{subject}: no quorum ({result['cast']} ballot(s) cast)."
    if result["winner"] is None:
        return f"{subject}: no decision ({result.get('reason') or 'tie'}; {counts})."
    tie = " after a tie" if result["tie"] else ""
    return f"{subject}: {result['winner']} wins{tie} ({counts})."


@mechanism("ballot", BallotConfig,
           "A vote among agents: a `<name>_vote` tool (and `<name>_abstain`), counted at the end of the vote's "
           "stage by plurality, majority or supermajority with an optional quorum. The result is in "
           "$world.<name>_result ({winner, passed, counts, ranking, votes, turnout, tie}) and is announced.",
           example={"kind": "ballot", "voters": "member", "options": ["approve", "reject"], "method": "majority",
                    "quorum": 0.5, "question": "Adopt the budget?"})
def _expand_ballot(name: str, config: BallotConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    types = contract.get("types") or {}
    if config.voters not in types:
        raise MechanismError(f"voters '{config.voters}' is not a declared type", f"types: {', '.join(types) or 'none'}",
                             "voters")
    ballots, result = f"{name}_ballots", f"{name}_result"
    vote, abstain = f"{name}_vote", f"{name}_abstain"
    question = f" on: {config.question}" if config.question else ""
    open_ballot = f"not ($actor.id in $world.{ballots})"
    actions: Dict[str, Any] = {
        vote: {"by": config.voters, "description": f"Cast your ballot{question}.",
               "params": {"choice": {"type": "enum", "values": config.options, "description": "Your choice."}},
               "when": [{"expr": open_ballot, "why": "You have already voted."}],
               "do": [f"$world.{ballots}[$actor.id] = $params.choice"],
               "outcome": "You voted {$params.choice}." if config.secret else None,
               "private": config.secret, "terminal": True},
    }
    if config.abstain:
        actions[abstain] = {"by": config.voters, "description": f"Abstain{question}.",
                            "when": [{"expr": open_ballot, "why": "You have already voted."}],
                            "do": [f"$world.{ballots}[$actor.id] = '{ABSTAIN}'"],
                            "private": config.secret, "terminal": True}
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
                                 "brief": config.question, "on_exit": [{"tally": name}]}
        if config.when:
            stage["when"] = config.when
        fragment["stages"] = [stage]
    else:
        if config.when:
            for action in actions.values():
                action["when"].append({"expr": config.when, "why": "The vote is not open now."})
        fragment["stage_hooks"] = {config.stage: {"actions": names, "on_exit": [{"tally": name}]}}
    return fragment
