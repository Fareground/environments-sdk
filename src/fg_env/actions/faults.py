"""An agent's action is one undoable unit: its requirements, arguments, effects, the lifecycle hooks and triggers its
commit sets off, and the invariants checked after it. A rule that fails anywhere in it, or an invariant it breaks, is
the contract's bug, not the agent's — but it is found through one agent's choice, so that action alone is refused and
undone, the agent is told why in words that reveal nothing hidden, the run's diagnostics tell the author where and
how to fix it, and the run goes on. The same failure outside an agent's action (events, triggers nothing an agent did
set off, physics, the build) still fails the run.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional, Tuple, TypeVar

from ..errors import FatalRunError, InvariantViolation, RunError
from ..expr import ExprError

if TYPE_CHECKING:
    from ..runtime.env import Env

__all__ = ["guarded", "refused_text", "fault_reason"]

T = TypeVar("T")


def guarded(env: "Env", work: Callable[[], T], mark: Optional[int] = None,
            action: Optional[str] = None) -> Tuple[Optional[T], Optional[str]]:
    """``(work(), None)``; or, when a rule fails or an invariant breaks inside it, ``(None, reason)`` with everything it
    changed undone — back to ``mark`` when given (where an atomic turn's actions began) — and the failure counted for
    the run's diagnostics, against the contract ``action`` being applied when given. ``reason`` is safe to show the
    agent."""
    world = env.world
    mark = world.journal.mark() if mark is None else mark
    armed, fired = dict(env._trigger_armed), set(env._triggers_fired)
    try:
        with world.journal.held():
            return work(), None
    except FatalRunError:
        raise
    except (RunError, ExprError) as exc:
        world.journal.rollback(mark)
        env._trigger_armed.clear()
        env._trigger_armed.update(armed)
        env._triggers_fired.clear()
        env._triggers_fired.update(fired)
        error = exc if isinstance(exc, RunError) else RunError(str(exc))
        if isinstance(error, InvariantViolation):
            # Already broken before the action (by something no invariant check followed): not the action's doing.
            env._check_invariants("changes made before an agent's action")
        path = error.path or "actions"
        env.diagnosis.faulted(path, str(error).removeprefix(f"{path}: "), action)
        return None, fault_reason(error)


def refused_text(name: str, reason: str) -> str:
    """What the agent is told when its action was refused because of :func:`guarded`."""
    return f"Your {name.replace('_', ' ')} was not done: {reason}. Nothing changed; try other arguments or another action."


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
