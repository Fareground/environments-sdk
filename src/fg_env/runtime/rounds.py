"""The round loop: a round's safe points, starting a round (scheduled effects, feeds, start events, physics),
playing its stages in order, ending the round or the run, and atomic effect blocks."""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..actions.book import ACTION_BUDGET
from ..contract import StageSpec
from ..errors import RunError
from ..expr import shared_budget
from ..expr.objects import Entity
from ..world.clock_math import advance_time
from ..world.live import Abort, OutOfBounds
from .feeds import run_feeds
from .measure import sample_metrics
from .turn import Turn

if TYPE_CHECKING:
    from .env import Env

__all__ = ["RunRounds"]


@dataclass
class _Point:
    """A safe point in a round. ``reasons`` names the agents about to be woken, and why."""

    stage: StageSpec | None = None
    reasons: dict[str, str] = field(default_factory=dict)


@dataclass
class _Where:
    """Where the round in progress is, kept current as it plays, so a copy of the run taken while a turn waits for a
    decision continues that round from the same place (see :mod:`fg_env.copying.stepping`)."""

    stage: int = 0
    pass_index: int = 0
    #: The agents of the pass being played, in turn order.
    agents: list[Entity] = field(default_factory=list)
    #: The waiting turn's place: in ``agents`` (sequential), or among the stage's sealed turns (simultaneous).
    position: int = 0
    #: The waiting turn itself (set on a copy only).
    turn: Turn | None = None


#: A round's steps: its safe points (:class:`_Point`), and ``WAITING`` while a turn waits for a decision.
_Steps = Generator[Any, None, None]


class RunRounds:
    """The round loop of a run (mixed into :class:`~fg_env.runtime.env.Env`)."""

    ended_by: str | None

    def _begin_round(self: Env) -> bool:  # type: ignore[misc]
        """Start the next round: scheduled effects, feeds, start events, physics. False if the run ended."""
        world = self.world
        self._in_round = True
        if self.status in ("ready", "stopped"):
            self.status = "running"
        elapsed: float | None = None
        if world.continuous:
            elapsed = self._advance_time()
            if elapsed is None:  # the next moment is past the horizon
                self._in_round = False
                self.ended_by, self.status = "horizon", "completed"
                self._final_event()
                return False
        world.round += 1
        world.firings.clear()
        world.stage = None
        self._used_round.clear()
        # Continuous dynamics belong to the interval that just elapsed. Boundary
        # effects must see its final state and affect only subsequent intervals.
        if elapsed is not None and elapsed > 0:
            with self._lock:
                world.step_physics(elapsed)
                world.journal.clear()
            self._check_invariants("physics")
            self.happenings.check_triggers("physics")
            if self._ended():
                self._finish()
                return False
        self.happenings.run_scheduled()
        run_feeds(self)
        self.happenings.run_events("start")
        self._check_end()
        if self._ended():
            self._finish()
            return False
        with self._lock:
            if elapsed is None:
                world.step_physics()
            world.journal.clear()
        if elapsed is None:
            self._check_invariants("physics")
            self.happenings.check_triggers("physics")
        if self._ended():
            self._finish()
            return False
        return True

    def _advance_time(self: Env) -> float | None:  # type: ignore[misc]
        """Move a continuous clock to the next round's moment; the time elapsed, or None past the horizon."""
        world, clock = self.world, self.contract.clock
        if world.round == 0:
            return 0.0
        previous = world.time
        if world.horizon is not None and previous >= world.horizon:
            return None
        target = advance_time(previous, clock.tick, "clock.tick")
        due = self._next_due()
        if due is not None:
            # Even a ticking clock must stop at intervening events. Jump mode
            # may skip empty ticks, but neither mode may skip a due effect.
            target = max(previous, due) if clock.jump else min(target, max(previous, due))
        if world.horizon is not None:
            target = min(target, world.horizon)
        world.time = target
        world.touch()
        return target - previous

    def _next_due(self: Env) -> float | None:  # type: ignore[misc]
        """The earliest moment something is due: a living agent's wake time or a scheduled effect."""
        world = self.world
        times = [at for entity_id, at in world.wake_at.items()
                 if (entity := world.entities.get(entity_id)) is not None and entity.alive]
        if world.scheduled:
            times.append(world.scheduled[0][0])
        return min(times) if times else None

    def _round(self: Env, resumed: bool = False) -> _Steps:  # type: ignore[misc]
        """A round, from its start — or, ``resumed``, from the waiting turn a copy of the run was taken in (see
        :class:`_Where`)."""
        world = self.world
        if not resumed:
            if not self._begin_round():
                return
            self._where = _Where()
        stages = self.contract.stage_list()
        for index in range(self._where.stage, len(stages)):
            stage = stages[index]
            if resumed:
                resumed = False
                yield from self._run_stage(stage, resumed=True)
            else:
                self._where.stage = index
                yield _Point(stage)
                yield from self._run_stage(stage)
            self._check_end()
            if self._ended():
                self._finish()
                return
        world.stage = None
        self.happenings.run_events("end")
        world.patterns.commit()
        sample_metrics(self.contract, world)
        self.happenings.check_triggers("round end")
        self._check_invariants("round", "round")
        self._check_end()
        self._flush_events()
        if self._ended():
            self._finish()
            return
        self._in_round = False
        if world.round >= world.rounds:
            self.ended_by = "rounds"
            self.status = "completed"
            self._final_event()
        else:
            self.previews.frame(final=False)

    def _ended(self: Env) -> bool:  # type: ignore[misc]
        return self.world.end_request is not None

    def _finish(self: Env) -> None:  # type: ignore[misc]
        world = self.world
        world.stage = None
        if not world.series or len(next(iter(world.series.values()), [])) < world.round:
            sample_metrics(self.contract, world)
        end = world.end_request or {}
        self.ended_by = end.get("name") or "end"
        self.status = "ended"
        self._in_round = False
        self._final_event()

    def _final_event(self: Env) -> None:  # type: ignore[misc]
        self._check_invariants("the run", "end")
        end = self.world.end_request or {}
        text = end.get("text") or (f"The run ended: {self.ended_by}." if self.ended_by != "rounds" else "Time is up.")
        self.world.emit("end", text, data={"ended_by": self.ended_by, "winner": end.get("winner")})
        self.world.journal.clear()
        self.previews.frame(final=True)
        self._flush_events()

    def _atomic(self: Env, effects: list[Any], vars: dict[str, Any], path: str,  # type: ignore[misc]
                check: bool = True, owner: Any = None) -> bool:
        """Apply ``effects`` as one undoable block. ``check=False``: one item of a block of world logic whose
        invariants are checked once it is whole (an `each` event), unless a trigger fires or an agent reacts first.
        The block draws from the stream of its path and ``owner`` (default: its $actor), so an entity's luck does not
        shift when others come or go."""
        if not effects:
            return True
        with self._lock, self.world.drawing_for(path, vars.get("actor") if owner is None else owner):
            mark = self.world.journal.mark()
            try:
                with shared_budget(ACTION_BUDGET, path):
                    self.effects.run(effects, dict(vars), path)
            except OutOfBounds as refusal:
                self.world.journal.rollback(mark)
                raise RunError(f"{refusal.reason} Keep it in range where it is written, e.g. with "
                               "$clamp(x, low, high), or guard the write with an `if`", path) from None
            except Abort as refusal:
                self.world.journal.rollback(mark)
                self.world.emit("refused", f"{path} was refused: {refusal.reason}", to=[],
                                data={"path": path, "reason": refusal.reason})
                return False
            except BaseException:
                self.world.journal.rollback(mark)
                raise
            self._after_commit(path, check)
            self.happenings.react(self._stage_spec())
        return True

    def _stage_spec(self: Env) -> StageSpec | None:  # type: ignore[misc]
        name = self.world.stage
        return next((s for s in self.contract.stage_list() if s.name == name), None) if name else None

    def _after_commit(self: Env, path: str, check: bool = True) -> None:  # type: ignore[misc]
        if check or self.world.reactions:
            self._check_invariants(path)
        if self._end_on_action:
            self._check_end("action")
        self.world.journal.clear()
        self.happenings.check_triggers(path)
