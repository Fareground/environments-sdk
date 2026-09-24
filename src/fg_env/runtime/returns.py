"""Seats and returns: who plays, and what each seat has scored (the `score` of the player types)."""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from ..contract import UTILITIES, Contract
from ..errors import Issue, RunError
from ..expr import ExprError, compile_expr
from ..expr.objects import Entity
from ..expr.template import format_value

__all__ = ["seat_ids", "seat_returns", "measured", "utility_issues", "check_game", "run_result"]

#: How far returns may miss their declared utility class (float noise), relative to their size.
UTILITY_TOLERANCE = 1e-9


def seat_ids(contract: Contract, world: Any) -> list[str]:
    """The seats, in seat order: every entity (alive or not) of a type with a score (every agent when none has)."""
    spec = contract.scoring()
    if spec is None:
        agents = set(contract.agent_types())
        return [entity.id for entity in world.entities.values() if entity.entity_type in agents]
    members = [entity for entity in world.entities.values() if contract.score_of(entity.entity_type) is not None]
    if spec.seat is None:
        return [entity.id for entity in members]
    key = compile_expr(spec.seat)
    path = f"{_scorer(contract, spec)}.seat"
    try:
        keyed = [(key(world.evaluation.scope(it=entity, i=position)), position, entity)
                 for position, entity in enumerate(members)]
        keyed.sort(key=lambda item: (item[0], item[1]))
    except ExprError as exc:
        raise RunError(str(exc), path) from None
    except TypeError:
        raise RunError("`seat` must give comparable values (numbers or text)", path) from None
    return [entity.id for _, _, entity in keyed]


def _scorer(contract: Contract, spec: Any) -> str:
    """The path of a score (``types.<type>.score``)."""
    return next(f"types.{name}.score" for name, kind in contract.types.items() if kind.score is spec)


def seat_returns(contract: Contract, world: Any, seats: Sequence[str] | None = None) -> dict[str, float]:
    """Each seat's return so far (its type's score value), or ``{}`` when no type scores."""
    if contract.scoring() is None:
        return {}
    result = run_result(world)
    out: dict[str, float] = {}
    for seat in seat_ids(contract, world) if seats is None else seats:
        entity = world.entities.get(seat)
        spec = contract.score_of(entity.entity_type) if isinstance(entity, Entity) else None
        if spec is None:
            raise RunError(f"seat '{seat}' is not an entity of a type with a score", "players")
        path = f"{_scorer(contract, spec)}.value"
        try:
            value = compile_expr(spec.value)(world.evaluation.scope(it=entity, result=result))
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise RunError(f"must give a finite number for {entity.id}, got {format_value(value)}", path)
        out[seat] = float(value)
    return out


def run_result(world: Any) -> dict[str, Any]:
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


def measured(contract: Contract, world: Any, finished: bool) -> tuple[dict[str, Any], list[Issue], dict[str, float]]:
    """A run's outputs, their issues (a finished run's utility problems included) and its returns so far."""
    from .measure import compute_outputs

    outputs, issues = compute_outputs(contract, world)
    returns = seat_returns(contract, world)
    return outputs, issues + (utility_issues(contract, returns, world) if finished else []), returns


def utility_issues(contract: Contract, returns: dict[str, float], world: Any = None) -> list[Issue]:
    """Problems with a finished run's returns against the declared utility class and each seat's bounds (``world``
    names each seat's type; without it every seat takes the game's score)."""
    spec = contract.scoring()
    if spec is None or not returns:
        return []
    values = list(returns.values())
    total = sum(values)
    slack = UTILITY_TOLERANCE * max(1.0, sum(abs(v) for v in values))
    path = _scorer(contract, spec)
    fix = "fix the score's value, or declare the utility class these returns really have"
    issues: list[Issue] = []
    if spec.utility == "zero_sum" and abs(total) > slack:
        issues.append(Issue(f"{path}.utility", f"is zero_sum, but the returns add up to {total:.10g}", fix))
    if spec.utility == "identical" and max(values) - min(values) > slack:
        issues.append(Issue(f"{path}.utility", f"is identical, but the returns differ: {returns}", fix))
    bound_fix = "fix the score's value, or widen the declared bound"
    for seat, value in returns.items():
        entity = world.entities.get(seat) if world is not None else None
        own = contract.score_of(entity.entity_type) if isinstance(entity, Entity) else None
        bounds = own or spec
        where = _scorer(contract, bounds)
        if bounds.min is not None and value < bounds.min - slack:
            issues.append(Issue(f"{where}.min", f"is {bounds.min:g}, but {seat} finished with {value:.10g}", bound_fix))
        if bounds.max is not None and value > bounds.max + slack:
            issues.append(Issue(f"{where}.max", f"is {bounds.max:g}, but {seat} finished with {value:.10g}", bound_fix))
    return issues


def check_game(checker: Any) -> None:
    """Static checks of the player types' scores."""
    from ..checks import BASE

    first = checker.c.scoring()
    if first is None:
        return
    seats = {kind for kind in checker.c.types if checker.c.score_of(kind) is not None}
    for name, kind in checker.c.types.items():
        spec = kind.score
        if spec is None:
            continue
        path = f"types.{name}.score"
        if not checker.c.is_agent(name):
            checker.error(path, f"'{name}' is not an agent type, so it has no seats",
                          "declare the score on the agent type that plays")
        own = {kind for kind in checker.c.subtypes(name) if checker.c.score_of(kind) is spec}
        checker.expr(spec.seat, f"{path}.seat", BASE | {"it", "i"}, {"it": seats})
        checker.expr(spec.value, f"{path}.value", BASE | {"it", "result"}, {"it": own})
        if spec.utility not in UTILITIES:
            checker.error(f"{path}.utility", f"unknown utility '{spec.utility}'",
                          checker._suggest(spec.utility, UTILITIES) or ", ".join(UTILITIES))
        for key in ("utility", "seat"):
            if getattr(spec, key) != getattr(first, key):
                checker.error(f"{path}.{key}", f"differs from {_scorer(checker.c, first)}.{key}: the seats play one "
                                               f"game, so every scoring type gives the same {key}",
                              f"write the same {key} on every type with a score")
        if spec.min is not None and spec.max is not None and spec.min > spec.max:
            checker.error(f"{path}.min", f"is {spec.min:g}, above max {spec.max:g}", "swap them, or correct the bound")
    from ..describe.claims import claim_issues
    from ..describe.metadata import game_metadata

    if "utility" in first.model_fields_set and not any(issue.severity == "error" for issue in checker.issues):
        checker.issues.extend(claim_issues(game_metadata(checker.c), {"utility": first.utility},
                                           _scorer(checker.c, first)))


def utility_class(contract: Contract) -> tuple[str, list[str]]:
    """The utility class the returns have, with the evidence: derived when every seat's return is one constant,
    otherwise the declared class, which every finished run is checked against."""
    spec = contract.scoring()
    if spec is None:
        return ("unknown",
                ["the contract declares no per-player returns, so zero-sum or constant-sum cannot be established"])
    values = {kind.score.value for kind in contract.types.values() if kind.score is not None}
    try:
        constants = {float(value) for value in values}
    except ValueError:
        constants = set()
    if len(constants) == 1:
        constant = constants.pop()
        holds = {"identical", "general_sum", "constant_sum"} | ({"zero_sum"} if constant == 0 else set())
        return ((spec.utility if spec.utility in holds else "identical"),
                [f"every seat's return is the constant {constant:g}"])
    return spec.utility, [f"{_scorer(contract, spec)}.utility declares {spec.utility}; every finished run's returns "
                          "are checked against it (a run that breaks it is not ok)"]
