"""Checks for how turns run and who sees what: time limits, atomic turns, spectator views."""
from __future__ import annotations

from typing import TYPE_CHECKING, AbstractSet

from .. import contract as C
from ..runtime.perception import SPECTATOR

if TYPE_CHECKING:
    from . import _Checker

__all__ = ["check_stage_turns", "check_spectator_view", "spectator_audience_issues"]


def check_stage_turns(checker: "_Checker", stage: C.StageSpec, path: str, base: AbstractSet[str]) -> None:
    """`time_limit`, `on_timeout`, `atomic` and `valid` of one stage."""
    agents = {"actor": set(checker.agents)}
    limit = stage.time_limit
    if isinstance(limit, bool) or (isinstance(limit, (int, float)) and not limit > 0):
        checker.error(f"{path}.time_limit", f"is {limit!r}; a time limit is a number of seconds above 0",
                      "e.g. 30, or an expression over $actor such as '120 if $actor.human else 20'")
    elif isinstance(limit, str):
        if "$" not in limit:
            checker.error(f"{path}.time_limit", f"'{limit}' is text, not a number or an expression",
                          "write the number of seconds without quotes, or an expression over $actor")
        checker.expr(limit, f"{path}.time_limit", base | {"actor"}, agents)
    checker.effects(stage.on_timeout, f"{path}.on_timeout", set(base) | {"actor"}, dict(agents))
    for index, condition in enumerate(stage.valid):
        where = f"{path}.valid[{index}]"
        checker.condition(condition.expr, where, base | {"actor"}, agents)
        checker.template(condition.why or None, f"{where}.why", None, base | {"actor"}, agents)
    if (stage.atomic or stage.valid) and stage.actions == []:
        checker.warn(f"{path}.atomic", "the stage wakes nobody, so there is no turn to make atomic")


def check_spectator_view(checker: "_Checker", name: str, view: C.ViewSpec, base: AbstractSet[str]) -> None:
    """A view for spectators: no reader, so no $actor; rendered once per round, never as a look."""
    path = f"views.{name}"
    for key, used in (("look", view.look), ("only_changes", view.only_changes), ("stages", view.stages is not None)):
        if used:
            checker.error(f"{path}.{key}", "does not apply to a spectator view",
                          "spectator views are rendered once per round (result.frames) and by env.spectate()")
    checker.condition(view.when, f"{path}.when", base)
    if view.of is None:
        checker.template(view.show, f"{path}.show", None, base)
        return
    types: dict = {}
    if view.of in checker.c.types:
        types["it"] = {view.of}
    elif view.of not in checker.c.records:
        checker.expr(view.of, f"{path}.of", base)
    item_roots = base | {"it", "i"}
    checker.condition(view.where, f"{path}.where", item_roots, types)
    checker.expr(view.sort, f"{path}.sort", item_roots, types)
    checker.template(view.show, f"{path}.show", "it", item_roots, types)
    if view.limit is not None and view.limit < 1:
        checker.error(f"{path}.limit", "must be at least 1")


def spectator_audience_issues(checker: "_Checker", name: str, view: C.ViewSpec) -> bool:
    """Report `for` lists that mix spectators with agents; True when the view is a spectator view."""
    if view.for_ == SPECTATOR:
        return True
    if isinstance(view.for_, list) and SPECTATOR in view.for_:
        checker.error(f"views.{name}.for", "a spectator view is for spectators alone",
                      'give it "for": "spectator" and declare the agents\' view separately')
    return False
