"""Two-sided stable matching, the ``groups.matching`` mode: students to schools, doctors to hospitals, workers to firms.

Both sides rank the other in ``<name>_prefs`` (a private list of ids, best first; anyone left off is unacceptable).
Agents submit their ranking with the generated ``<name>_rank`` tool; coded entities carry it as a prop the author
sets. When the matching stage ends, deferred acceptance (Gale–Shapley) runs with ``who`` proposing: each proposer
applies down its list, each receiver holds its best ``seats`` applicants so far and rejects the rest. The result is
stable (no proposer and receiver both prefer each other to what they got) and the best stable result for every
proposer. It is written to ``<name>_match`` (a proposer's receiver id, or '') and ``<name>_matches`` (a receiver's
proposer ids, best first).
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from ..errors import RunError
from ..registry import MechanismError, family_action, mode
from ._common import is_agent_type
from ._social import check_expr, config_of, props, require_type
from .common import number

__all__ = ["MatchingConfig", "stable_match"]

KEY = "groups.matching"


class MatchingConfig(BaseModel):
    """A two-sided matching market cleared by deferred acceptance."""

    model_config = ConfigDict(extra="forbid")

    who: str = Field(..., description="Type that proposes and is matched to at most one receiver (students, doctors).")
    to: str = Field(..., description="Type that receives proposals (schools, hospitals).")
    seats: Union[int, str] = Field(1, description="Proposers one receiver accepts: a number or an expression over $it "
                                                  "(the receiver), e.g. \"$it.capacity\".")
    stage: Optional[str] = Field(None, description="Rank during this declared stage and match when it ends (every time); "
                                                "default: a stage named after the mechanism, once.")


def stable_match(proposers: Mapping[str, List[str]], receivers: Mapping[str, List[str]],
                 seats: Mapping[str, int]) -> Dict[str, List[str]]:
    """Proposer-optimal stable matching: each receiver's accepted proposers, best first.

    A pair is acceptable only when each lists the other. The result does not depend on the order proposals are
    made in, so no randomness is involved."""
    rank = {r: {p: i for i, p in enumerate(prefs)} for r, prefs in receivers.items()}
    held: Dict[str, List[str]] = {r: [] for r in receivers}
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


def _ranking(value: Any) -> List[str]:
    """A prefs prop as ids (it may hold entities or ids), first mention kept."""
    items = value if isinstance(value, list) else []
    return list(dict.fromkeys(getattr(item, "id", item) for item in items if isinstance(item, str) or hasattr(item, "id")))


def clear(world: Any, name: str) -> None:
    config = config_of(world, name, KEY, MatchingConfig)
    proposers = list(world.entities_of(config.who))
    receivers = list(world.entities_of(config.to))
    where = f"mechanisms.{name}.seats"
    seats: Dict[str, int] = {}
    for receiver in receivers:
        count = number(world, config.seats, where, it=receiver)
        if count < 0 or count != int(count):
            raise RunError(f"{receiver.id} has {count!r} seats; seats must be a whole number ≥ 0", where)
        seats[receiver.id] = int(count)
    held = stable_match({p.id: _ranking(props(p).get(f"{name}_prefs")) for p in proposers},
                        {r.id: _ranking(props(r).get(f"{name}_prefs")) for r in receivers}, seats)
    matched = {p: r for r, accepted in held.items() for p in accepted}
    for proposer in proposers:
        world.set_prop(proposer, f"{name}_match", matched.get(proposer.id, ""))
    for receiver in receivers:
        world.set_prop(receiver, f"{name}_matches", held[receiver.id])
    world.set_world(f"{name}_cleared", True)
    world.emit(f"{name}_matched", f"The {name} matching is done: {len(matched)} of {len(proposers)} matched.")


@family_action("groups", ("matching",), "clear", internal=True,
               example='{"groups": "admissions", "action": "clear"}  (run deferred acceptance on the submitted rankings)')
def _clear_op(runner: Any, effect: Dict[str, Any], vars: Dict[str, Any], where: str) -> None:
    try:
        clear(runner.world, effect["groups"])
    except RunError as exc:
        raise RunError(str(exc), exc.path or where) from None


def _rank_action(name: str, by: str, other: str) -> Dict[str, Any]:
    return {"by": by, "description": f"Rank the {other}s you would accept, best first; anyone you leave off is "
                                     "unacceptable. Submitting again replaces your ranking.",
            "params": {"ranking": {"type": "list", "of": other, "description": f"{other}s, best first."}},
            "do": [f"$actor.{name}_prefs = $map($params.ranking, $it.id)"],
            "outcome": "Your ranking is in: {$join($map($params.ranking, $it.name), ', ') or 'nobody'}.",
            "private": True, "terminal": True}


@mode("groups", "matching", MatchingConfig,
      "Two-sided stable matching (deferred acceptance, Gale–Shapley): `who` proposes to `to`, each receiver takes up "
      "to `seats`. Both sides rank the other in the private list prop `<name>_prefs` (ids, best first; unlisted = "
      "unacceptable): agent types get a `<name>_rank` tool (`<name>_rank_<type>` when both sides are agents), coded "
      "entities carry the prop. When the stage ends the match is stable and the best stable one for every proposer; read it as $it.<name>_match (a proposer's "
      "receiver id, '' when unmatched) and $it.<name>_matches (a receiver's proposer ids). Output `<name>_matched`.",
      example={"who": "student", "to": "school", "seats": "$it.capacity"})
def _expand_matching(name: str, config: MatchingConfig, contract: Mapping[str, Any]) -> Dict[str, Any]:
    require_type(contract, config.who, "who")
    require_type(contract, config.to, "to")
    if config.who == config.to:
        raise MechanismError("`who` and `to` must be different types: one side proposes, the other receives",
                             "e.g. who: student, to: school", "to")
    check_expr(config.seats, "seats", ["it"])
    prefs = {"type": "list", "default": [], "private": True, "description": f"Who this entity would accept in {name}, best first."}
    agents = [t for t in (config.who, config.to) if is_agent_type(contract, t)]
    actions = {f"{name}_rank" if len(agents) == 1 else f"{name}_rank_{by}": _rank_action(name, by, other)
               for by, other in ((config.who, config.to), (config.to, config.who)) if by in agents}
    fragment: Dict[str, Any] = {
        "types": {config.who: {"props": {f"{name}_prefs": prefs, f"{name}_match": {
                      "type": "text", "default": "", "description": f"The {config.to} matched to in {name} ('' when unmatched)."}}},
                  config.to: {"props": {f"{name}_prefs": prefs, f"{name}_matches": {
                      "type": "list", "default": [], "description": f"The {config.who}s matched in {name}, best first."}}}},
        "world": {f"{name}_cleared": {"type": "bool", "default": False}},
        "actions": actions,
        "outputs": {f"{name}_matched": {"expr": f"$count($filter({config.who}, $it.{name}_match != ''))", "type": "int",
                                        "description": f"{config.who}s matched."}},
    }
    clearing = [{"groups": name, "action": "clear"}]
    if config.stage is not None:
        fragment["stage_hooks"] = {config.stage: {"actions": list(actions), "on_exit": clearing}}
    elif actions:
        fragment["stages"] = [{"name": name, "turns": "simultaneous", "actions": list(actions),
                               "when": f"not $world.{name}_cleared", "on_exit": clearing,
                               "brief": "Rank who you would accept, best first. When everyone has ranked, deferred "
                                        "acceptance makes a stable match."}]
    else:
        fragment["events"] = [{"name": f"{name}_clear", "at": 1, "do": clearing}]
    return fragment
