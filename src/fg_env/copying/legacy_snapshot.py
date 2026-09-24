"""Reading snapshots of the previous format (version 4), frozen: delete it one minor release after the current one.

A version-4 snapshot taken between rounds holds what a version-5 one does. One taken part-way through a round holds no
cursor: it holds the snapshot the run was rebuilt from (its base) and the tape of every participant step since, so it
is restored as it was then, by playing the tape back into the rebuilt base up to the safe point it was taken at (the
base holds the host answers recorded so far, so the playback never asks a host again). This is the last use of tape
playback for snapshots (see :mod:`.replay`).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from ..contract import Contract
from ..errors import SnapshotError
from ..runtime.turn_tools import wrap
from .replay import Playback, Tape

if TYPE_CHECKING:
    from ..runtime.env import Env

__all__ = ["LEGACY_VERSION", "part_way"]

LEGACY_VERSION = 4

_E = TypeVar("_E", bound="Env")


def part_way(cls: type[_E], contract: Contract, snapshot: Mapping[str, Any], parallel: int) -> _E:
    """The run a version-4 snapshot taken part-way through a round holds, restored into ``contract`` (matched to it)."""
    from .snapshot import check_snapshot, decode, restore_state

    try:
        held = snapshot["part_way"]
        base = held["base"]
        check_snapshot(base)
        env = restore_state(cls, contract, base, parallel)
        tape = Tape()
        tape.turns = {int(number): (actor, [tuple(decode(entry)) for entry in entries])
                      for number, actor, entries in held["tape"]}
        points, round_ = int(held["points"]), int(snapshot["round"])
        playback = Playback(tape, int(held["turns"]))
    except SnapshotError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SnapshotError(f"the snapshot is incomplete or corrupted ({type(exc).__name__}: {exc})") from None
    passed = [0]

    def stop(env: Env) -> bool:  # the safe points passed since the base, counted as the snapshot counted them
        if not env.state.in_round:
            return False
        passed[0] += 1
        return env.world.round == round_ and passed[0] >= points

    env.run(wrap(env, lambda wake: playback.play(wake)), stop=stop)
    env.driver.bind({})
    if env.status != "stopped" or env.world.round != round_ or passed[0] != points:
        why = f" ({env.error})" if env.error else ""
        raise SnapshotError(f"the snapshot did not replay to where it was taken{why}; restore it into the contract "
                            "it was taken with, unedited")
    return env
