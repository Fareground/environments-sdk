"""The round loop: a round's safe points, starting a round (scheduled effects, feeds, `round.start` events, physics),
playing its stages in order, ending the round or the run, and atomic effect blocks."""
from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..actions.book import ACTION_BUDGET
from ..actions.faults import world_logic_refused
from ..contract import StageSpec
from ..errors import RunError
from ..expr import shared_budget
from ..world.live import Abort, OutOfBounds
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
        world.firings.clear()  # counts of this round's luck, and of its uses of actions: never undone
        world.used_round.clear()
        world.stage = None
        self.happenings.run_scheduled()
        run_feeds(self)
        self.happenings.fire("round.start")
        self._check_end()
        if self._ended():
            self._finish()
            return False
        with self._lock:
            world.step_physics()
            world.journal.clear()
        self._check_invariants("physics")
        self.happenings.check_changes("physics")
        if self._ended():
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
            self._check_end()
            if self._ended():
                self._finish()
                return
        world.stage = None
        self.happenings.fire("round.end")
        world.patterns.commit()
        sample_metrics(self.contract, world)
        self.happenings.check_changes("round end")
        self._check_invariants("round", "round")
        self._check_end()
        self._flush_events()
        if self._ended():
            self._finish()
            return
        self.state.in_round = False
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
        self.state.in_round = False
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
                check: bool = True, owner: Any = None, luck: str | None = None) -> None:
        """Apply ``effects`` as one undoable block of world logic: a refusal in it (a `fail`, a transfer or write that
        does not fit) fails the run — or, inside an agent's action, refuses that action. ``check=False``: one item of a
        block whose invariants are checked once it is whole (a round event's `each`), unless a `change` event fires or
        an agent reacts first. The block draws from the stream of ``luck`` (default: its path) and ``owner`` (default:
        its $actor), so an entity's luck does not shift when others come or go."""
        if not effects:
            return
        site = luck or path
        with self._lock, self.world.drawing_for(site, vars.get("actor") if owner is None else owner):
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
                raise RunError(world_logic_refused(refusal.reason), path) from None
            except BaseException:
                self.world.journal.rollback(mark)
                raise
            self._after_commit(path, check)
            self.happenings.react(self._stage_spec())

    def _stage_spec(self: Env) -> StageSpec | None:  # type: ignore[misc]
        name = self.world.stage
        return next((s for s in self.contract.stage_list() if s.name == name), None) if name else None

    def _after_commit(self: Env, path: str, check: bool = True) -> None:  # type: ignore[misc]
        if check or self.world.reactions:
            self._check_invariants(path)
        if self._end_on_action:
            self._check_end("action")
        self.world.journal.clear()
        self.happenings.check_changes(path)
