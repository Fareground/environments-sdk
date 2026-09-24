"""Runs that keep no event log (``load(..., events=False)``): memory stays flat however long they play.

Such a run's result carries no events, so an event is needed only while something in the run can still read it:
an agent's news (what happened since its last turn). Before every round the run forgets the events every living agent
has already been past, and when its replay tape (what participants did since the run's base, see :mod:`replay`) has
grown larger than the world, it moves the base to this round so the tape starts afresh. A contract that reads older
events — `$events`, `$seen`, a game's information sets, the memory and procedure mechanisms — keeps its whole log:
forgetting never changes what a run does, only what it holds.
"""
from __future__ import annotations

import bisect
import json
from typing import TYPE_CHECKING

from ..mechanisms.memory import MEMORY
from ..mechanisms.procedure import KEY as PROCEDURE
from ..registry import use_key

if TYPE_CHECKING:
    from ..contract import Contract
    from .env import Env

__all__ = ["reads_log", "forget"]

#: Turns the replay tape holds at least before its base moves (a small world is not re-based every round).
_LEAST_TAPE = 1_000


def reads_log(contract: Contract) -> bool:
    """Whether the contract's rules read events older than an agent's news, so the run must keep them all."""
    text = json.dumps(contract.model_dump(by_alias=True, exclude_defaults=True), default=str)
    return ("$events" in text or "$seen" in text or contract.scoring() is not None
            or any(use_key(raw) in (MEMORY, PROCEDURE) for raw in contract.mechanisms.values()))


def forget(env: Env) -> None:
    """At a round's start: drop the events no living agent's news can reach any more, and move the replay base here
    when the tape has outgrown the world. Nothing is ever forgotten from a run whose rules read the log."""
    if env._reads_log:
        return
    world = env.world
    memories = env.state.memories
    horizon = min((memories[agent.id].cursor if agent.id in memories else 0
                   for kind in env.contract.agent_types() for agent in world.alive_of(kind)),
                  default=world.log[-1].seq if world.log else 0)
    # An agent's news starts after its cursor, and the cursor's own event tells whether it already played this stage.
    dropped = bisect.bisect_left(world.log, horizon, key=lambda event: event.seq)
    if dropped:
        del world.log[:dropped]
        env.state.emitted = max(0, env.state.emitted - dropped)
        world.rebuild_event_index()
    if len(env.origin.tape.turns) > max(world.types.living, _LEAST_TAPE):
        env.origin.checkpoint_due = True
