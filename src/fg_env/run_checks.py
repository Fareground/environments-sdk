"""The checks a run makes as it goes: invariants that must hold, and `end` conditions that stop it.

Both are checked at set moments. An invariant's `check` and an end condition's `check` choose how often:
``action`` also checks the moment anything commits (an action, a sealed choice, an effect block).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from .errors import InvariantViolation, RunError
from .expr import EVERYONE, ExprError, Scope, compile_expr, truthy
from .template import compile_template
from .world import _plain

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["RunChecks"]

#: The moments each `invariants[].check` setting is checked at.
_INVARIANT_MOMENTS = {"action": ("build", "action", "round"), "round": ("build", "round"), "end": ("end",)}


class RunChecks:
    """Invariants and end conditions of a run (mixed into :class:`~fg_env.runtime.Env`)."""

    _invariant_held: Dict[int, Any]

    def _check_invariants(self: "Env", path: str, moment: str = "action") -> None:  # type: ignore[misc]
        """Check the invariants due at ``moment``: build, action (after a change), round or end. An
        invariant already found to hold in exactly this state — without drawing randomness — holds again,
        so it is not evaluated again."""
        if not self.contract.invariants:
            return
        world = self.world
        scope = world.scope()
        for index, invariant in enumerate(self.contract.invariants):
            if moment not in _INVARIANT_MOMENTS[invariant.check]:
                continue
            state = world.state_version()
            if moment == "action" and self._invariant_held.get(index) == state:
                continue
            drawn = world.draws()
            try:
                holds = truthy(compile_expr(invariant.expr)(scope))
            except ExprError as exc:
                raise RunError(str(exc), f"invariants[{index}]") from None
            if not holds:
                why = _why(invariant.why, scope, f"invariants[{index}].why")
                raise InvariantViolation(f"invariant `{invariant.expr}` no longer holds after {path}"
                                         f"{f' ({why})' if why else ''}", f"invariants[{index}]", why)
            fresh = world.draws() == drawn and world.state_version() == state
            self._invariant_held[index] = state if fresh else None

    def _check_end(self: "Env", moment: str = "stage") -> None:  # type: ignore[misc]
        """Request the end of the run when an end condition holds. ``moment`` is ``stage`` (the round's set
        points: every condition) or ``action`` (something just committed: the conditions with `check: action`)."""
        world = self.world
        if world.end_request is not None or world.round == 0:
            return
        scope = None
        for index, end in enumerate(self.contract.end):
            if moment == "action" and end.check != "action":
                continue
            path = f"end[{index}]"
            scope = scope or world.scope()
            try:
                if not truthy(compile_expr(end.when)(scope)):
                    continue
                winner = _plain(compile_expr(end.winner)(scope)) if end.winner else None
                text = compile_template(end.say, None).render(scope) if end.say else ""
            except ExprError as exc:
                raise RunError(str(exc), path) from None
            world.request_end(end.name or f"end_{index}", winner, text)
            return


def _why(template: str, scope: Scope, path: str) -> str:
    """An invariant's `why`, rendered: the agent whose action broke it is told, so it may read no agent's private
    property (whose action it is, the invariant does not know)."""
    if not template:
        return ""
    try:
        return compile_template(template, None).render(scope.child(viewer=EVERYONE))
    except ExprError as exc:
        raise RunError(str(exc), path) from None
