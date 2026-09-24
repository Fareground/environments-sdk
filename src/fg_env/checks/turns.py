"""Checks for how turns run and who sees what: a stage's `valid` rules, spectator views."""
from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import TYPE_CHECKING

from .. import contract as C
from ..information.perception import SPECTATOR

if TYPE_CHECKING:
    from . import _Checker

__all__ = ["check_stage_turns", "check_spectator_view", "spectator_audience_issues"]


def check_stage_turns(checker: _Checker, stage: C.StageSpec, path: str, base: AbstractSet[str]) -> None:
    """The `valid` rules of one stage."""
    agents = {"actor": set(checker.agents)}
    for index, condition in enumerate(stage.valid):
        where = f"{path}.valid[{index}]"
        checker.condition(condition.expr, where, base | {"actor"}, agents)
        checker.template(condition.why or None, f"{where}.why", None, base | {"actor"}, agents)
        checker._actor_text(condition.why, f"{where}.why", {})  # the acting agent is told it
    if stage.valid and stage.actions == []:
        checker.warn(f"{path}.valid", "the stage wakes nobody, so there is no turn to check")


def check_spectator_view(checker: _Checker, name: str, view: C.ViewSpec, base: AbstractSet[str]) -> None:
    """A view for spectators: no reader, so no $actor; rendered once per round, never as a look."""
    path = f"views.{name}"
    if view.look:
        checker.error(f"{path}.look", "does not apply to a spectator view",
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


def spectator_audience_issues(checker: _Checker, name: str, view: C.ViewSpec) -> bool:
    """Report `for` lists that mix spectators with agents; True when the view is a spectator view."""
    if view.for_ == SPECTATOR:
        return True
    if isinstance(view.for_, list) and SPECTATOR in view.for_:
        checker.error(f"views.{name}.for", "a spectator view is for spectators alone",
                      'give it "for": "spectator" and declare the agents\' view separately')
    return False
