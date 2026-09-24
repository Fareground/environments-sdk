"""The expression language's foundations: its error, participant-text provenance, and the work budget and size
caps that bound every evaluation."""
from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from typing import Any

__all__ = [
    "EVAL_BUDGET", "MAX_INT_BITS", "MAX_LIST_LEN", "MAX_RANGE", "MAX_TEXT_LEN", "Untrusted", "tainted", "derived",
    "ExprError", "PrivateRead", "charge", "check_size", "shared_budget", "nested_free", "is_expr", "truthy",
    "EXPRESSION_WORDS",
]

_EXPR_MARK = re.compile(r"\$[A-Za-z_]")

#: Work one top-level evaluation may do: items visited by per-item arguments, collection
#: elements read or built, nested evaluations. Guards against runaway nesting like
#: ``$map($range(100000), $map($range(100000), ...))``.
EVAL_BUDGET = 2_000_000
#: Longest list ``$range`` may produce.
MAX_RANGE = 100_000
#: Longest list any operation may build.
MAX_LIST_LEN = 1_000_000
#: Longest text any operation may build.
MAX_TEXT_LEN = 1_000_000
#: Largest whole number (in bits) ``*`` and ``**`` may produce.
MAX_INT_BITS = 4_096
#: Words the expression language itself uses, so they cannot name a type, entity, property or item: `$count(in)`
#: could not tell the name from the operator. Every other word works, Python keywords included (`$count(class)`).
EXPRESSION_WORDS = frozenset({"and", "or", "not", "in", "if", "else", "true", "false", "null", "True", "False", "None"})


class Untrusted(str):
    """Text written by a participant. It keeps that provenance wherever it is stored and
    renders wrapped in «» and on one line, so other agents read it as information, never instructions or the
    SDK's own layout.

    ``str(value)`` keeps the marker, so code that normalises keys or values with ``str()``
    cannot silently launder participant text; ``str.__str__(value)`` gives the plain text."""

    __slots__ = ()

    def __str__(self) -> str:
        return self


def tainted(value: Any) -> bool:
    """True when ``value`` is, or contains (in list items, map keys or values), participant text."""
    if isinstance(value, Untrusted):
        return True
    if isinstance(value, (str, int, float, bool)) or value is None:
        return False
    if isinstance(value, (list, tuple)):
        return any(tainted(item) for item in value)
    if isinstance(value, Mapping):
        return any(tainted(key) or tainted(item) for key, item in value.items())
    return False


def derived(text: str, *sources: Any) -> str:
    """``text`` marked untrusted when any of the values it was derived from carries participant text."""
    return Untrusted(text) if any(tainted(source) for source in sources) else text


class ExprError(ValueError):
    """An expression is malformed or cannot be evaluated against the current state."""

    def __init__(self, message: str, source: str | None = None):
        self.source = source
        self.detail = message
        super().__init__(f"{message} — in `{source}`" if source else message)


class WrongKind(ExprError):
    """A value of the wrong kind for an operation: text or a bool where a number is needed."""


class PrivateRead(ExprError):
    """What one agent is shown or offered (``$viewer`` is bound) read another agent's private property."""


# ---------------------------------------------------------------------------
# Execution budget
# ---------------------------------------------------------------------------


class _Budget(threading.local):
    """Per-thread work counter. Turns run on worker threads, so each keeps its own.

    ``hold`` counts open nesting points — a def call, a record-visibility rule, a shared
    block: while it is non-zero an evaluation is nested inside other work and charges that
    work's budget; at zero an evaluation is top-level and starts a fresh budget. Only those
    points run evaluations inside evaluations, so plain rules pay one read and one write."""

    hold = 0
    used = 0
    limit = EVAL_BUDGET
    #: The running expression's own ceiling: inside a shared block each expression still gets at most
    #: EVAL_BUDGET steps, so one hostile rule cannot spend the whole block's budget.
    cap = EVAL_BUDGET
    label = ""
    shared = False


_BUDGET = _Budget()


def charge(amount: int, source: str | None = None) -> None:
    """Count ``amount`` units of work against the running evaluation's budget."""
    budget = _BUDGET
    budget.used += amount
    if budget.used > budget.cap and budget.cap < budget.limit:
        raise ExprError(
            f"evaluation exceeded its work budget of {EVAL_BUDGET:,} steps (items visited and elements built); "
            "narrow what it loops over or split the work across rounds", source)
    if budget.used > budget.limit:
        who = f"{budget.label} exceeded its shared" if budget.shared and budget.label else "evaluation exceeded its"
        raise ExprError(
            f"{who} work budget of {budget.limit:,} steps (items visited and elements built); "
            "narrow what it loops over or split the work across rounds", source)


def check_size(value: Any, source: str | None) -> Any:
    """Refuse lists and text longer than :data:`MAX_LIST_LEN` / :data:`MAX_TEXT_LEN`."""
    if isinstance(value, str):
        if len(value) > MAX_TEXT_LEN:
            raise ExprError(f"text would be {len(value):,} characters; the limit is {MAX_TEXT_LEN:,}", source)
    elif isinstance(value, (list, tuple)):
        if len(value) > MAX_LIST_LEN:
            raise ExprError(f"a list would have {len(value):,} items; the limit is {MAX_LIST_LEN:,}", source)
        charge(len(value), source)
    return value


class shared_budget:
    """Make every evaluation inside the block share one budget of ``limit`` steps.

    Code that loops over expressions outside the language (an action's effects, a view's
    items) wraps the loop so the loop as a whole is bounded, not only each evaluation.
    Nested blocks keep the outermost budget. (A class rather than a generator: every action and
    effect block opens one.)"""

    __slots__ = ("limit", "label", "opened")

    def __init__(self, limit: int = EVAL_BUDGET, label: str = ""):
        self.limit, self.label, self.opened = limit, label, False

    def __enter__(self) -> None:
        budget = _BUDGET
        if budget.hold:
            return
        budget.hold, budget.shared, budget.used, budget.limit, budget.label = 1, True, 0, self.limit, self.label
        budget.cap = self.limit
        self.opened = True

    def __exit__(self, *exc: Any) -> None:
        if self.opened:
            budget = _BUDGET
            budget.hold, budget.shared, budget.used, budget.limit, budget.label = 0, False, 0, EVAL_BUDGET, ""
            budget.cap = EVAL_BUDGET


def nested_free() -> bool:
    """True when no work budget is being shared, so each evaluation starts afresh and skipping one is unseen."""
    return not _BUDGET.hold


def _held(run: Callable[[], Any]) -> Any:
    """Run a function call's work as nested work (its evaluations charge the caller's budget)."""
    budget = _BUDGET
    budget.hold += 1
    try:
        return run()
    finally:
        budget.hold -= 1


def is_expr(value: Any) -> bool:
    """True when ``value`` is a string holding a ``$`` reference or function call."""
    return isinstance(value, str) and bool(_EXPR_MARK.search(value))


def truthy(value: Any) -> bool:
    """Truthiness used by conditions: ``None``, ``0``, ``""`` and empty lists are false."""
    return bool(value)
