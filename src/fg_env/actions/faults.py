"""An agent's action is one undoable unit: its requirements, arguments, effects, the create, remove and change
events its commit sets off, and the invariants checked after it. A rule that fails anywhere in it, or an invariant it
breaks, is the contract's bug, not the agent's — but it is found through one agent's choice, so that action alone is
refused and undone (:meth:`~fg_env.runtime.rules.Rules.guarded`), the agent is told why in words that reveal nothing
hidden (this module), the run's diagnostics tell the author where and how to fix it, and the run goes on. What an
action schedules with `after` stays that action's when it runs (:meth:`~fg_env.runtime.events.Events.run_scheduled`).
The same failure outside an agent's action (events nothing an agent did set off, physics, the build) still fails the
run — a `fail` or a transfer that does not fit too: world logic has no one to refuse it to
(:func:`world_logic_refused`).
"""
from __future__ import annotations

from ..errors import InvariantViolation, RunError

__all__ = ["refused_text", "fault_reason", "world_logic_refused"]


def refused_text(name: str, reason: str) -> str:
    """What the agent is told when its action was refused because of :meth:`~fg_env.runtime.rules.Rules.guarded`."""
    return (f"Your {name.replace('_', ' ')} was not done: {reason}. Nothing changed; try other arguments or another "
            "action.")


def world_logic_refused(reason: str) -> str:
    """Why world logic (an event) that was refused — a `fail`, a transfer that does not fit —
    fails the run: undoing it quietly would leave a run that looks complete without what its rules said happen."""
    return (f"{reason.rstrip()} World logic cannot be refused: guard it with an `if` so it runs only when it can "
            "succeed (a `fail` or a transfer that does not fit only refuses an agent's action)")


def fault_reason(error: RunError) -> str:
    """Why an action whose rule failed is refused, in words that name no rule and show no value (either could reveal
    hidden state)."""
    if isinstance(error, InvariantViolation):
        return error.why.strip().rstrip(".") or "it would break a rule of this environment"
    text = str(error)
    if "division by zero" in text:
        return "it would divide by zero"
    if "too large" in text or "overflow" in text.lower():
        return "a number would grow too large"
    return "the environment's rules could not be worked out for it"
