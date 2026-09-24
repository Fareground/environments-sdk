"""The round loop: a round's safe points, starting a round (scheduled effects, feeds, `round.start` events, physics),
playing its stages in order, and ending the round or the run."""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..contract import StageSpec
from .feeds import run_feeds
from .measure import sample_metrics
from .state import Where

if TYPE_CHECKING:
    from .env import Env

__all__ = ["RunRounds"]


@dataclass
class _Point:
    """A safe point in a round. ``reasons`` names the agents about to be woken, and why."""

    stage: StageSpec | None = None
    reasons: dict[str, str] = field(default_factory=dict)


#: A round's steps: its safe points (:class:`_Point`), and ``WAITING`` while a turn waits for a decision.
_Steps = Generator[Any, None, None]


class RunRounds:
    """The round loop of a run (mixed into :class:`~fg_env.runtime.env.Env`)."""

    ended_by: str | None

    def _begin_round(self: Env) -> bool:  # type: ignore[misc]
        """Start the next round: scheduled effects, feeds, `round.start` events, physics. False if the run ended."""
        world = self.world
        self.state.in_round = True
        if self.status in ("ready", "stopped"):
            self.status = "running"
        world.round += 1
        world.luck.firings.clear()  # counts of this round's luck, and of its uses of actions: never undone
        world.used_round.clear()
        world.stage = None
        self.rules.run_scheduled()
        run_feeds(self.rules)
        self.rules.fire("round.start")
        self.rules.check_end()
        if self.rules.ended():
            self._finish()
            return False
        with self._lock:
            world.step_physics()
            world.journal.clear()
        self.rules.check_invariants("physics")
        self.rules.check_changes("physics")
        if self.rules.ended():
            self._finish()
            return False
        return True

    def _round(self: Env, resumed: bool = False) -> _Steps:  # type: ignore[misc]
        """A round, from its start — or, ``resumed``, from the waiting turn a copy of the run was taken in (see
        :class:`~fg_env.runtime.state.Where`)."""
        world = self.world
        if not resumed:
            if not self._begin_round():
                return
            self.state.where = Where()
        stages = self.contract.stage_list()
        for index in range(self.state.where.stage, len(stages)):
            stage = stages[index]
            if resumed:
                resumed = False
                yield from self._run_stage(stage, resumed=True)
            else:
                self.state.where.stage = index
                yield _Point(stage)
                yield from self._run_stage(stage)
            self.rules.check_end()
            if self.rules.ended():
                self._finish()
                return
        world.stage = None
        self.rules.fire("round.end")
        world.patterns.commit()
        sample_metrics(self.contract, world)
        self.rules.check_changes("round end")
        self.rules.check_invariants("round", "round")
        self.rules.check_end()
        self._flush_events()
        if self.rules.ended():
            self._finish()
            return
        self.state.in_round = False
        if world.round >= world.rounds:
            self.ended_by = "rounds"
            self.status = "completed"
            self._final_event()
        else:
            self.previews.frame(final=False)

    def _finish(self: Env) -> None:  # type: ignore[misc]
        world = self.world
        world.stage = None
        if not world.series or len(next(iter(world.series.values()), [])) < world.round:
            sample_metrics(self.contract, world)
        end = world.end_request or {}
        self.ended_by = end.get("name") or "end"
        self.status = "ended"
        self.state.in_round = False
        self._final_event()

    def _final_event(self: Env) -> None:  # type: ignore[misc]
        self.rules.check_invariants("the run", "end")
        end = self.world.end_request or {}
        text = end.get("text") or (f"The run ended: {self.ended_by}." if self.ended_by != "rounds" else "Time is up.")
        self.world.emit("end", text, data={"ended_by": self.ended_by, "winner": end.get("winner")})
        self.world.journal.clear()
        self.previews.frame(final=True)
        self._flush_events()
