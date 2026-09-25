"""Two-sided stable matching, the ``groups.matching`` mode: students to schools, doctors to hospitals, workers to firms.

Both sides rank the other in ``<name>_prefs`` (a private list of ids, best first; anyone left off is unacceptable).
Agents submit their ranking with the generated ``<name>_rank`` tool; coded entities carry it as a prop the author
sets; ``eligible`` restricts both to the partners a pair rule allows. When the matching stage ends (after the
contract's own events on its end), deferred acceptance (Gale–Shapley) runs with ``who`` proposing: each proposer
applies down its list, each receiver holds its best ``seats`` applicants so far and rejects the rest. The result is
stable (no proposer and receiver both prefer each other to what they got) and the best stable result for every
proposer. It is written to ``<name>_match`` (a proposer's receiver id, or '') and ``<name>_matches`` (a receiver's
proposer ids, best first).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..expr import ExprError, compile_expr, truthy
from ..registry import MechanismError, family_action, mechanism_config, mode
from ._common import is_agent_type, number_of, stage_event
from ._social import check_expr, props, require_type
from .expressions import Expr

__all__ = ["MatchingConfig", "stable_match"]

KEY = "groups.matching"


class MatchingConfig(BaseModel):
    """A two-sided matching market cleared by deferred acceptance."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Type that proposes and is matched to at most one receiver (students, doctors).")
    to: str = Field(..., description="Type that receives proposals (schools, hospitals).")
    seats: int | str = Field(1, description="Proposers one receiver accepts: a number or an expression over $it "
                                            "(the receiver), e.g. \"$it.capacity\".")
    eligible: Expr | None = Field(None, description="Which pairs may match: an expression over $proposer and "
                                                    "$receiver, e.g. \"$receiver.id in $proposer.applied\". Rank tools "
                                                    "offer only eligible partners, and rankings are cut to them before "
                                                    "matching.")
    stage: str | None = Field(None, description="Rank during this declared stage and match when it ends, after the "
                                                "contract's own events on its end (every time); default: a stage named "
                                                "after the mechanism, once.")


def stable_match(proposers: Mapping[str, list[str]], receivers: Mapping[str, list[str]],
                 seats: Mapping[str, int]) -> dict[str, list[str]]:
    """Proposer-optimal stable matching: each receiver's accepted proposers, best first.

    A pair is acceptable only when each lists the other. The result does not depend on the order proposals are
    made in, so no randomness is involved."""
    rank = {r: {p: i for i, p in enumerate(prefs)} for r, prefs in receivers.items()}
    held: dict[str, list[str]] = {r: [] for r in receivers}
    tried = {p: 0 for p in proposers}
    free = list(proposers)
    while free:
        p = free.pop()
        prefs = proposers[p]
        while tried[p] < len(prefs):
            r = prefs[tried[p]]
            tried[p] += 1
            if p not in rank.get(r, {}) or seats.get(r, 0) < 1:
                continue
            held[r] = sorted([*held[r], p], key=rank[r].__getitem__)
            if len(held[r]) <= seats[r]:
                break
            rejected = held[r].pop()
            if rejected != p:
                free.append(rejected)
                break
    return held


def _ranking(value: Any) -> list[str]:
    """A prefs prop as ids (it may hold entities or ids), first mention kept."""
    items = value if isinstance(value, list) else []
    ids = (item if isinstance(item, str) else str(item.id) for item in items if isinstance(item, str)
           or hasattr(item, "id"))
    return list(dict.fromkeys(ids))


def clear(world: Any, name: str) -> None:
    config = mechanism_config(world, name, KEY, MatchingConfig)
    proposers = list(world.entities_of(config.who))
    receivers = list(world.entities_of(config.to))
    where = f"mechanisms.{name}.seats"
    seats: dict[str, int] = {}
    for receiver in receivers:
        count = number_of(world, config.seats, where, it=receiver)
        if count < 0 or count != int(count):
            raise RunError(f"{receiver.id} has {count!r} seats; seats must be a whole number ≥ 0", where)
        seats[receiver.id] = int(count)
    held = stable_match({p.id: _eligible(world, name, config, p, _ranking(props(p).get(f"{name}_prefs")))
                         for p in proposers},
                        {r.id: _ranking(props(r).get(f"{name}_prefs")) for r in receivers}, seats)
    matched = {p: r for r, accepted in held.items() for p in accepted}
    for proposer in proposers:
        world.set_prop(proposer, f"{name}_match", matched.get(proposer.id, ""))
    for receiver in receivers:
        world.set_prop(receiver, f"{name}_matches", held[receiver.id])
    world.set_world(f"{name}_cleared", True)
    world.emit(f"{name}_matched", f"The {name} matching is done: {len(matched)} of {len(proposers)} matched.")


def _eligible(world: Any, name: str, config: MatchingConfig, proposer: Any, ranking: list[str]) -> list[str]:
    """A proposer's ranking without the receivers ``eligible`` rules out (a pair needs both lists, so one side is
    enough)."""
    if config.eligible is None:
        return ranking
    where = f"mechanisms.{name}.eligible"
    test = compile_expr(config.eligible)
    kept = []
    for receiver_id in ranking:
        receiver = world.entity(receiver_id)
        try:
            if receiver is not None and truthy(test(world.evaluation.scope(proposer=proposer, receiver=receiver))):
                kept.append(receiver_id)
        except ExprError as exc:
            raise RunError(str(exc), where) from None
    return kept


@family_action("groups", ("matching",), "clear", internal=True,
               example='{"groups": "admissions", "action": "clear"}  (run deferred acceptance on the submitted '
                       'rankings)')
def _clear_op(runner: Any, effect: dict[str, Any], vars: dict[str, Any], where: str) -> None:
    try:
        clear(runner.world, effect["groups"])
    except RunError as exc:
        raise RunError(str(exc), exc.path or where) from None


def _rank_action(name: str, by: str, other: str, where: str | None) -> dict[str, Any]:
    ranking: dict[str, Any] = {"type": "list", "of": other, "description": f"{other}s, best first."}
    if where is not None:
        ranking["where"] = where
    return {"by": by, "description": f"Rank the {other}s you would accept, best first; anyone you leave off is "
                                     "unacceptable. Submitting again replaces your ranking.",
            "params": {"ranking": ranking},
            "do": [f"$actor.{name}_prefs = $map($params.ranking, $it.id)"],
            "outcome": "Your ranking is in: {$join($map($params.ranking, $it.name), ', ') or 'nobody'}.",
            "private": True, "terminal": True}


@mode("groups", "matching", MatchingConfig,
      "Two-sided stable matching (deferred acceptance, Gale–Shapley): `who` proposes to `to`, each receiver takes up "
      "to `seats`. Both sides rank the other in the private list prop `<name>_prefs` (ids, best first; unlisted = "
      "unacceptable): agent types get a `<name>_rank` tool (`<name>_rank_<type>` when both sides are agents), coded "
      "entities carry the prop. `eligible` (over $proposer and $receiver, e.g. applications) limits the rank tools "
      "and cuts every ranking to eligible partners. When the stage ends (after the contract's own events on its end) "
      "the match is stable and the best stable one for every proposer; read it as $it.<name>_match (a proposer's "
      "receiver id, '' when unmatched) and $it.<name>_matches (a receiver's proposer ids). Output `<name>_matched`.",
      example={"who": "student", "to": "school", "seats": "$it.capacity"},
      context={"types": {"school": {"agent": True, "props": {"capacity": 2}}},
               "entities": {"school": {"type": "school", "count": 2}}})
def _expand_matching(name: str, config: MatchingConfig, contract: Mapping[str, Any]) -> dict[str, Any]:
    require_type(contract, config.who, "who")
    require_type(contract, config.to, "to")
    if config.who == config.to:
        raise MechanismError("`who` and `to` must be different types: one side proposes, the other receives",
                             "e.g. who: student, to: school", "to")
    check_expr(config.seats, "seats", ["it"])
    check_expr(config.eligible, "eligible", ["proposer", "receiver"])
    pair = f"${name}_eligible"  # a def, so each side's tool reads the pair its own way round
    prefs = {"type": "list", "default": [], "private": True,
             "description": f"Who this entity would accept in {name}, best first."}
    agents = [t for t in (config.who, config.to) if is_agent_type(contract, t)]
    wheres = {config.who: f"{pair}($actor, $it)", config.to: f"{pair}($it, $actor)"} if config.eligible else {}
    actions = {f"{name}_rank" if len(agents) == 1
               else f"{name}_rank_{by}": _rank_action(name, by, other, wheres.get(by))
               for by, other in ((config.who, config.to), (config.to, config.who)) if by in agents}
    fragment: dict[str, Any] = {
        "types": {config.who: {"props": {f"{name}_prefs": prefs, f"{name}_match": {
                      "type": "text", "default": "",
                      "description": f"The {config.to} matched to in {name} ('' when unmatched)."}}},
                  config.to: {"props": {f"{name}_prefs": prefs, f"{name}_matches": {
                      "type": "list", "default": [],
                      "description": f"The {config.who}s matched in {name}, best first."}}}},
        "world": {f"{name}_cleared": {"type": "bool", "default": False}},
        "actions": actions,
        **({"defs": {pair[1:]: {"args": ["proposer", "receiver"], "expr": config.eligible,
                                "description": f"Whether a pair may match in {name}."}}} if config.eligible else {}),
        "outputs": {f"{name}_matched": {"expr": f"$count($filter({config.who}, $it.{name}_match != ''))", "type": "int",
                                        "description": f"{config.who}s matched."}},
    }
    clearing = [{"groups": name, "action": "clear"}]
    if config.stage is not None:
        fragment["stage_hooks"] = {config.stage: {"actions": list(actions)}}
        fragment["events"] = [stage_event(config.stage, "end", clearing)]
    elif actions:
        fragment["stages"] = [{"name": name, "turns": "simultaneous", "actions": list(actions),
                               "when": f"not $world.{name}_cleared",
                               "brief": "Rank who you would accept, best first. When everyone has ranked, deferred "
                                        "acceptance makes a stable match."}]
        fragment["events"] = [stage_event(name, "end", clearing)]
    else:
        fragment["events"] = [{"name": f"{name}_clear", "at": 1, "do": clearing}]
    return fragment
