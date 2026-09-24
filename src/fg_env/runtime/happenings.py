"""Reactions: agents a change asks to answer it (`wake` with `now`) get a turn right away, once it has committed."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..contract import StageSpec
from .turn import Turn

if TYPE_CHECKING:
    from .env import Env

__all__ = ["Happenings"]


class Happenings:
    """The reactions of one run."""

    #: How deep reactions may set off further reactions (deeper reactions wait for the agent's next turn).
    REACTION_DEPTH = 4

    def __init__(self, env: Env):
        self.env = env
        self._reaction_depth = 0

    def react(self, stage: StageSpec | None) -> None:
        """Give every agent asked to react (`wake` with `now`) a turn right away, in the current stage — offered the
        actions the wake names, else the stage's: once the action that woke them has committed, so a reaction answers it
        and cannot undo it. While an agent's action is still committing (and could yet be undone), they wait for it to
        finish. Reactions to reactions nested deeper than :attr:`REACTION_DEPTH` become ordinary wakes: agents that keep
        answering each other never fail the run."""
        env, world = self.env, self.env.world
        if world.journal.holding:
            return
        while world.reactions and not env.rules.ended():
            entity_id, why, actions = world.reactions.pop(0)
            actor = world.entities.get(entity_id)
            if actor is None or not actor.alive or not env.contract.is_agent(actor.entity_type):
                continue
            if self._reaction_depth >= self.REACTION_DEPTH:  # agents answering each other: the rest wait a turn
                world.request_wake(entity_id, why)
                continue
            spec = stage or next(iter(env.contract.stage_list()))
            if actions is not None:  # the answers the wake names, not every action of the stage
                spec = spec.model_copy(update={"actions": list(actions)})
            self._reaction_depth += 1
            try:
                turn = Turn(env, actor, spec, why, staged=False, kind="reaction")
                turn.stats.reactions = 1
                env.driver.drive([turn])
                env._timed_out(turn)
            finally:
                self._reaction_depth -= 1
            memory = env.state.memory(actor.id)
            memory.cursor = world.log[-1].seq if world.log else 0
            memory.turns += 1
