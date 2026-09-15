"""Seats and returns: who plays, and what each seat has scored (the contract's `game` section)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..entity import Entity
from .contract import UTILITIES, Contract
from .errors import Issue, RunError
from .expr import ExprError, compile_expr
from .template import format_value

__all__ = ["seat_ids", "seat_returns", "seat_rewards", "measured", "utility_issues", "check_game", "run_result"]

#: How far returns may miss their declared utility class (float noise), relative to their size.
UTILITY_TOLERANCE = 1e-9


def seat_ids(contract: Contract, world: Any) -> List[str]:
    """The seats, in seat order: every entity (alive or not) of the players' types."""
    spec = contract.game
    listed = spec.players if spec is not None and spec.players else contract.agent_types()
    kinds = {kind for name in ([listed] if isinstance(listed, str) else listed) for kind in contract.subtypes(name)}
    members = [entity for entity in world.entities.values() if entity.entity_type in kinds]
    if spec is None or spec.seat is None:
        return [entity.id for entity in members]
    key = compile_expr(spec.seat)
    try:
        keyed = [(key(world.scope(it=entity, i=position)), position, entity) for position, entity in enumerate(members)]
        keyed.sort(key=lambda item: (item[0], item[1]))
    except ExprError as exc:
        raise RunError(str(exc), "game.seat") from None
    except TypeError:
        raise RunError("`seat` must give comparable values (numbers or text)", "game.seat") from None
    return [entity.id for _, _, entity in keyed]


def seat_returns(contract: Contract, world: Any, seats: Optional[Sequence[str]] = None) -> Dict[str, float]:
    """Each seat's return so far, or ``{}`` when the contract declares none."""
    spec = contract.game
    if spec is None or spec.returns is None:
        return {}
    return _per_seat(world, spec.returns, "game.returns", seat_ids(contract, world) if seats is None else seats)


def seat_rewards(contract: Contract, world: Any, seats: Sequence[str]) -> Optional[Dict[str, float]]:
    """Each seat's declared reward now, or None when rewards follow the change in returns."""
    spec = contract.game
    if spec is None or spec.rewards is None:
        return None
    return _per_seat(world, spec.rewards, "game.rewards", seats)


def run_result(world: Any) -> Dict[str, Any]:
    """`$result`: how the run has ended so far. ``winner`` is what an `end` named — its entities as entities
    (removed ones too), several as a list, anything else as it is — or null; ``ended_by`` is the end's name, or null
    while the run goes on or when it ran out of rounds."""
    ending = world.end_request or {}

    def entity(value: Any) -> Any:
        if isinstance(value, list):
            return [entity(item) for item in value]
        found = world.entities.get(value) if isinstance(value, str) else None
        return found if isinstance(found, Entity) else value

    return {"winner": entity(ending.get("winner")), "ended_by": ending.get("name")}


def _per_seat(world: Any, source: str, path: str, seats: Sequence[str]) -> Dict[str, float]:
    expr = compile_expr(source)
    result = run_result(world)
    out: Dict[str, float] = {}
    for seat in seats:
        entity = world.entities.get(seat)
        if not isinstance(entity, Entity):
            raise RunError(f"seat '{seat}' is not an entity of this run", path)
        try:
            value = expr(world.scope(actor=entity, result=result))
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise RunError(f"must give a finite number for {entity.id}, got {format_value(value)}", path)
        out[seat] = float(value)
    return out


def measured(contract: Contract, world: Any, finished: bool) -> Tuple[Dict[str, Any], List[Issue], Dict[str, float]]:
    """A run's outputs, their issues (a finished run's utility problems included) and its returns so far."""
    from .measure import compute_outputs

    outputs, issues = compute_outputs(contract, world)
    returns = seat_returns(contract, world)
    return outputs, issues + (utility_issues(contract, returns) if finished else []), returns


def utility_issues(contract: Contract, returns: Dict[str, float]) -> List[Issue]:
    """Problems with a finished run's returns against the declared utility class."""
    spec = contract.game
    if spec is None or not returns:
        return []
    values = list(returns.values())
    total = sum(values)
    slack = UTILITY_TOLERANCE * max(1.0, sum(abs(v) for v in values))
    fix = "fix game.returns, or declare the utility class these returns really have"
    if spec.utility == "zero_sum" and abs(total) > slack:
        return [Issue("game.utility", f"is zero_sum, but the returns add up to {total:.10g}", fix)]
    if spec.utility == "constant_sum" and spec.total is not None and abs(total - spec.total) > slack:
        return [Issue("game.utility", f"is constant_sum ({spec.total:g}), but the returns add up to {total:.10g}", fix)]
    if spec.utility == "identical" and max(values) - min(values) > slack:
        return [Issue("game.utility", f"is identical, but the returns differ: {returns}", fix)]
    return []


def check_game(checker: Any) -> None:
    """Static checks of the `game` section."""
    from .check import BASE

    spec = checker.c.game
    if spec is None:
        return
    listed = [spec.players] if isinstance(spec.players, str) else (spec.players or checker.agents)
    kinds = {kind for name in listed if checker._type(name, "game.players", agent=True)
             for kind in checker.c.subtypes(name)}
    checker.expr(spec.seat, "game.seat", BASE | {"it", "i"}, {"it": kinds})
    if spec.returns is None:
        checker.error("game.returns", "is required: what each seat has scored",
                      'e.g. "1 if $result.winner == $actor else 0", an expression over $actor and $result')
    for key in ("returns", "rewards"):
        checker.expr(getattr(spec, key), f"game.{key}", BASE | {"actor", "result"}, {"actor": kinds})
    if spec.utility not in UTILITIES:
        checker.error("game.utility", f"unknown utility '{spec.utility}'", checker._suggest(spec.utility, UTILITIES)
                      or ", ".join(UTILITIES))
    if spec.utility == "constant_sum" and spec.total is None:
        checker.error("game", "constant_sum needs `total` (what the returns add up to)")
    elif spec.utility != "constant_sum" and spec.total is not None:
        checker.warn("game.total", "only applies to constant_sum")
    from .describe.claims import CLAIMS, claim_issues
    from .describe.metadata import game_metadata

    claims = {key: getattr(spec, key) for key in CLAIMS if key in spec.model_fields_set}
    if claims and not any(issue.severity == "error" for issue in checker.issues):
        checker.issues.extend(claim_issues(game_metadata(checker.c), claims))


def utility_class(contract: Contract) -> Tuple[str, List[str]]:
    """The utility class the returns have, with the evidence: derived when every seat's return is one constant,
    otherwise the declared class, which every finished run is checked against."""
    spec = contract.game
    if spec is None or spec.returns is None:
        return "unknown", ["the contract declares no per-player returns, so zero-sum or constant-sum cannot be established"]
    try:
        constant: Optional[float] = float(spec.returns)
    except ValueError:
        constant = None
    if constant is not None:
        holds = {"identical", "general_sum", "constant_sum"} | ({"zero_sum"} if constant == 0 else set())
        return (spec.utility if spec.utility in holds else "identical"), [f"every seat's return is the constant {constant:g}"]
    return spec.utility, [f"game.utility declares {spec.utility}; every finished run's returns are checked against it "
                          "(a run that breaks it is not ok)"]
