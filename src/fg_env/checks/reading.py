"""Test agents that read what they are shown before they play: how `check`'s smoke plays and `fg_env.author`'s test
runs exercise every view and template, not just the rules.

A reading agent reads its brief and update each turn, as a model does, and — beyond the turn's free reads, spending
none of them — every view it may look at and an entity of every type it may inspect (a different one each round), on
every wake. A view or template that fails for any agent's state, on any turn the play reaches, then fails the play,
however many views there are and however few reads a turn allows.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..information.reads import inspect_tool, inspectable

if TYPE_CHECKING:
    from ..runtime.session import Wake
    from ..runtime.turn import Turn

__all__ = ["Reading"]


class Reading:
    """``agent``, reading first each turn (see the module docstring); ``seen`` is told each turn's wake before the
    agent plays it (what it reads: its brief and update)."""

    def __init__(self, agent: Any, seen: Callable[[Wake], None] | None = None) -> None:
        self.agent, self.seen = agent, seen

    def __call__(self, wake: Wake) -> None:
        if self.seen is not None:
            self.seen(wake)
        turn = wake._turn  # read straight from the turn, as its look and inspect tools do, but without their allowance
        with turn.gate:
            for kind, _, args in _reads(turn):
                turn._look(args) if kind == "look" else turn._inspect(args)
        self.agent(wake)


def _reads(turn: Turn) -> list[tuple[str, str, dict[str, Any]]]:
    """``(kind, what, args)`` of a ``look`` at every view ``turn`` offers, and an ``inspect`` of one entity of every
    type it may inspect (a different one each round)."""
    env, actor = turn.env, turn.actor
    reads = [("look", view, {"view": view}) for view in env.information.look_views(actor)]
    if inspect_tool(env.information, actor, turn.ledger.max_calls) is not None:
        members: dict[str, list[str]] = {}
        for entity in inspectable(env.information, actor):
            members.setdefault(entity.entity_type, []).append(entity.id)
        reads += [("inspect", kind, {"id": ids[turn.round % len(ids)]}) for kind, ids in members.items()]
    return reads
