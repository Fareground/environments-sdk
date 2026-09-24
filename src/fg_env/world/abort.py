"""Refusals: stopping the change being made, so every write it made is rolled back and its actor told why."""
from __future__ import annotations

from typing import Any

from ..expr.template import format_value

__all__ = ["Abort", "OutOfBounds", "within_bounds"]


class Abort(Exception):
    """Stop the current action; every change it made is rolled back. The text reaches the actor."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class OutOfBounds(Abort):
    """A number past a declared min or max (a property's, a link value's or a layer cell's). An agent's action is
    refused like any :class:`Abort`; world logic (an event no action set off) that does it
    fails the run, because that is a contract bug no agent can fix."""


def within_bounds(spec: Any, value: float, subject: str) -> None:
    """Refuse (:class:`OutOfBounds`) a number past ``spec``'s min or max, naming ``subject`` ("Ann's coins"). Saturating
    is written out: ``$clamp(x, low, high)``. A private property's value stays out of the reason, which the acting
    agent is told."""
    if spec.min is not None and value < spec.min:
        limit = f"cannot go below {format_value(spec.min)}"
    elif spec.max is not None and value > spec.max:
        limit = f"cannot go above {format_value(spec.max)}"
    else:
        return
    if getattr(spec, "private", False):
        raise OutOfBounds(f"{subject} {limit}.")
    raise OutOfBounds(f"{subject} {limit}: it would be {format_value(value)}.")
