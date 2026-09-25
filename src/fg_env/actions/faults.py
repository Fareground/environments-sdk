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

from ..contract.base import spoken
from ..errors import InvariantViolation, RunError

__all__ = ["LogicRefused", "RETRY", "refused_text", "fault_reason", "world_logic_refused"]


class LogicRefused(RunError):
    """World logic refused — a `fail`, a transfer or write that does not fit. Inside an agent's action (a `change`
    event its commit set off) it refuses the action, whose agent is told ``why``: the refusal as rendered for it, or
    empty when it was not rendered for one (then nothing of it may be shown). Anywhere else it fails the run."""

    def __init__(self, message: str, path: str | None = None, why: str = ""):
        self.why = why
        super().__init__(message, path)


#: What a refusal that cost nothing advises.
RETRY = " Try other arguments or another action."


def refused_text(name: str, reason: str) -> str:
    """What the agent is told when its action was refused because of :meth:`~fg_env.runtime.rules.Rules.guarded`;
    whether it may simply try again (:data:`RETRY`) or the attempt was spent is the caller's to add."""
    return f"Your {spoken(name)} was not done: {reason}. Nothing changed."


def world_logic_refused(reason: str) -> str:
    """Why world logic (an event) that was refused — a `fail`, a transfer that does not fit —
    fails the run: undoing it quietly would leave a run that looks complete without what its rules said happen."""
    return (f"{reason.rstrip()} World logic cannot be refused: guard it with an `if` so it runs only when it can "
            "succeed (a `fail` or a transfer that does not fit only refuses an agent's action)")


def fault_reason(error: RunError) -> str:
    """Why an action whose rule failed is refused, in words that name no rule and show no value (either could reveal
    hidden state): the author's own words for a broken invariant or a refusal, and one sentence for every other
    failure (what failed — a division by zero, a number too large — could tell a hidden divisor or amount)."""
    if isinstance(error, InvariantViolation):
        return error.why.strip().rstrip(".") or "it would break a rule of this environment"
    if isinstance(error, LogicRefused) and error.why:
        return error.why.strip().rstrip(".")
    return "the environment's rules could not be worked out for it"
