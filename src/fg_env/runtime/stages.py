"""Running a stage: which agents wake and in what order, sequential, scheduled and simultaneous turns, stage
hooks and time limits, and committing sealed choices."""
from __future__ import annotations

import bisect
from typing import TYPE_CHECKING, Any

from ..actions.book import stage_actions
from ..actions.faults import guarded
from ..contract import MAX_STAGE_PASSES, StageSpec
from ..errors import RunError
from ..expr import EVERYONE, ExprError, PrivateRead, compile_expr, truthy
from ..world.build import whole_setting
from ..world.entity import Entity
from .budget import is_seconds
from .clock_math import advance_time
from .diagnosis import SealedWrites
from .measure import Stats
from .rounds import _Point, _Steps
from .turn import Turn

if TYPE_CHECKING:
    from .env import Env

__all__ = ["RunStages"]


class RunStages:
    """Stage and turn running of a run (mixed into :class:`~fg_env.runtime.env.Env`)."""

    def _run_stage(self: Env, stage: StageSpec, resumed: bool = False) -> _Steps:  # type: ignore[misc]
        world, where = self.world, self._where
        path = f"stages.{stage.name}"
        if not resumed:
            runs = self._stage_runs(stage)
            self.diagnosis.stage(stage.name, reached=1, ran=int(runs))
            if not runs:
                return
            world.stage = stage.name
            self._atomic(stage.on_enter, {}, f"{path}.on_enter")
            if self._ended():
                return
            where.pass_index = 0
        passes = whole_setting(world, stage.passes, f"{path}.passes", MAX_STAGE_PASSES) or (10 if stage.until else 1)
        for pass_index in range(where.pass_index, passes):
            if resumed:
                agents = where.agents
            else:
                if pass_index:
                    yield _Point(stage)
                agents = self._eligible(stage, pass_index=pass_index)
                where.pass_index, where.agents = pass_index, agents
                self.diagnosis.stage(stage.name, woke=len(agents))
            if stage.turns == "simultaneous":
                yield from self._simultaneous(stage, agents, pass_index, resumed)
            elif stage.turns == "scheduled":  # never resumed: copies are not taken in scheduled stages
                yield from self._scheduled(stage, agents, pass_index)
            else:
                yield from self._sequential(stage, agents, pass_index, resumed)
            resumed = False
            if self._ended():
                return
            if stage.until is not None:
                try:
                    if truthy(compile_expr(stage.until)(world.scope())):
                        break
                except ExprError as exc:
                    raise RunError(str(exc), f"{path}.until") from None
        else:
            if stage.until is not None:
                self.diagnosis.stage(stage.name, capped=1)  # every pass ran and `until` still did not hold
        self._atomic(stage.on_exit, {}, f"{path}.on_exit")

    def _stage_runs(self: Env, stage: StageSpec) -> bool:  # type: ignore[misc]
        if stage.when is None:
            return True
        try:
            return truthy(compile_expr(stage.when)(self.world.scope()))
        except ExprError as exc:
            raise RunError(str(exc), f"stages.{stage.name}.when") from None

    def _eligible(self: Env, stage: StageSpec, ordered: bool = True,  # type: ignore[misc]
                  pass_index: int = 0) -> list[Entity]:
        """Agents woken in ``stage`` (in its pass ``pass_index``), in turn order. ``ordered=False`` skips ordering (no
        random draws)."""
        world = self.world
        agent_types = set(self.contract.agent_types())  # includes types that inherit `agent`
        acting = {kind: bool(stage_actions(self.contract, stage, kind)) for kind in agent_types}
        agents = [e for e in world.entities.values() if e.alive and acting.get(e.entity_type)]
        path = f"stages.{stage.name}"
        try:
            if stage.who is not None:
                agents = self._woken(stage, agents, pass_index)
            if not ordered:
                return agents
            if stage.order == "random":
                self._shuffle(stage, agents)
            elif stage.order is not None and stage.order != "seat":
                key = compile_expr(stage.order)  # every agent sees the order: it may read no agent's private property
                keyed = [(key(world.scope(it=a, i=i, viewer=EVERYONE)), i, a) for i, a in enumerate(agents)]
                keyed.sort(key=lambda t: (t[0], t[1]))
                agents = [a for _, _, a in keyed]
        except PrivateRead as exc:
            raise RunError(f"{exc.detail.partition(', and ')[0]}, and every agent sees the turn order, so ordering by "
                           "it would reveal how the agents rank: order by a property that is not private, or `random`",
                           f"{path}.order") from None
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        except TypeError:
            raise RunError("`order` must give comparable values (numbers or text)", f"{path}.order") from None
        return agents

    def _woken(self: Env, stage: StageSpec, agents: list[Entity],  # type: ignore[misc]
               pass_index: int) -> list[Entity]:
        """The ``agents`` that the stage's `who` wakes. Each is decided with luck of its own (the stage, round, pass and
        agent), so who else is alive never shifts it, and asking again (a preview) gives the same answer."""
        world, who = self.world, compile_expr(stage.who)
        woken = []
        for i, agent in enumerate(agents):
            luck = world.seeds.lazy_rng("who", stage.name, world.round, pass_index, agent.id)
            with world.drawing_from(luck):
                if truthy(who(world.scope(it=agent, i=i))):
                    woken.append(agent)
        return woken

    def _shuffle(self: Env, stage: StageSpec, agents: list[Any]) -> None:  # type: ignore[misc]
        """Put ``agents`` (or their turns) in a random order drawn from the stage's own stream."""
        with self.world.drawing_at(f"stages.{stage.name}.order"):
            self.world.rng.shuffle(agents)
        self.world.journal.clear()  # the draw is the round's: nothing undoes it, and the run may pause after it

    def _reason(self: Env, actor: Entity, stage: StageSpec, pass_index: int) -> str | None:  # type: ignore[misc]
        requested = self.world.wake_requests.pop(actor.id, None)
        memory = self._memory(actor.id)
        if stage.quiet == "skip" and requested is None and pass_index > 0:
            if not self.perception.news(actor, memory.cursor, 1)[0]:
                return None
        if requested:
            return requested
        if stage.turns == "simultaneous":
            return "Everyone chooses at the same time."
        return "Your turn again." if pass_index and self._turned_here(memory.cursor, stage) else "It is your turn."

    def _turned_here(self: Env, cursor: int, stage: StageSpec) -> bool:  # type: ignore[misc]
        """Whether the agent whose last turn ended at log position ``cursor`` had it in this visit of ``stage``: the
        latest event then was this round's, in this stage."""
        log = self.world.log
        at = bisect.bisect_left(log, cursor, key=lambda event: event.seq)
        if cursor <= 0 or at == len(log) or log[at].seq != cursor:
            return False
        return log[at].round == self.world.round and log[at].stage == stage.name

    def _sequential(self: Env, stage: StageSpec, agents: list[Entity], pass_index: int,  # type: ignore[misc]
                    resumed: bool = False) -> _Steps:
        where = self._where
        for position in range(where.position if resumed else 0, len(agents)):
            actor = agents[position]
            if resumed:
                resumed, turn, where.turn = False, where.turn, None
                assert turn is not None
                yield from self.driver.drive_steps([turn], resume=0)
            else:
                if self._ended():
                    return
                if not actor.alive:  # removed earlier this pass: everyone after it still takes their turn
                    continue
                reason = self._reason(actor, stage, pass_index)
                if reason is None:
                    continue
                if not self._wake_hook(stage, actor):
                    continue
                where.position = position
                yield _Point(stage, {actor.id: reason})
                turn = Turn(self, actor, stage, reason, staged=False)
                yield from self.driver.drive_steps([turn])
            self._after_turn(stage, turn, turn.stats.actions > 0)
            self._turn_end_hook(stage, actor)
            memory = self._memory(actor.id)
            memory.cursor = self.world.log[-1].seq if self.world.log else 0
            memory.turns += 1
            self._flush_events()

    def _scheduled(self: Env, stage: StageSpec, agents: list[Entity], pass_index: int) -> _Steps:  # type: ignore[misc]
        """Continuous clock: every agent whose wake time has come takes a turn, earliest first. Its
        next wake is now plus the duration of what it did, or the stage interval if it did nothing
        timed — unless something during the turn already scheduled it later."""
        world = self.world
        now = world.time
        due: list[tuple[float, int, Entity]] = []
        for position, actor in enumerate(agents):
            at = world.wake_at.get(actor.id)
            if at is None:
                at = self._stage_time(stage.first_wake, 0.0, f"stages.{stage.name}.first_wake", it=actor, i=position)
                world.set_wake_at(actor.id, at)
            if at <= now:
                due.append((at, position, actor))
        due.sort(key=lambda item: (item[0], item[1]))
        for _, _, actor in due:
            if self._ended():
                return
            if not actor.alive:
                continue
            reason = self._reason(actor, stage, pass_index)
            if reason is None:
                world.set_wake_at(actor.id,
                                  advance_time(now, self._interval(stage, actor), f"stages.{stage.name}.interval"))
                continue
            if not self._wake_hook(stage, actor):
                continue
            yield _Point(stage, {actor.id: reason})
            turn = Turn(self, actor, stage, reason, staged=False)
            yield from self.driver.drive_steps([turn])
            self._after_turn(stage, turn, turn.stats.actions > 0)
            self._turn_end_hook(stage, actor)
            scheduled = world.wake_at.get(actor.id, now)
            if scheduled <= now:
                step = turn.elapsed if turn.elapsed > 0 else self._interval(stage, actor)
                world.set_wake_at(actor.id, advance_time(now, step, f"stages.{stage.name}.interval"))
            memory = self._memory(actor.id)
            memory.cursor = world.log[-1].seq if world.log else 0
            memory.turns += 1
            self._flush_events()

    def _after_turn(self: Env, stage: StageSpec, turn: Turn, acted: bool,  # type: ignore[misc]
                    stop_when_ended: bool = False) -> None:
        """A played turn is over: record a timeout (running `on_timeout`), or — for a living agent that took no
        action — report one that had to act and did not, then run the stage's `on_idle`."""
        actor = turn.actor
        if self._timed_out(turn) or acted or not actor.alive or (stop_when_ended and self._ended()):
            return
        if turn.did_not_act:
            with self._lock:
                self.world.emit("idle", f"{actor.name} did not act.", actor=actor.id, data={"stage": stage.name})
                self.world.journal.clear()
        if stage.on_idle:
            self._atomic(stage.on_idle, {"actor": actor}, f"stages.{stage.name}.on_idle")

    def _timed_out(self: Env, turn: Turn) -> bool:  # type: ignore[misc]
        """Record a turn that ran out of time (a `timeout` event) and run the stage's `on_timeout`.
        True when `on_timeout` took the place of `on_idle`."""
        if not turn.timed_out:
            return False
        stage, actor, world = turn.stage, turn.actor, self.world
        with self._lock:
            world.emit("timeout", f"{actor.name} ran out of time.", actor=actor.id,
                       data={"stage": stage.name, "limit": turn.time_limit})
            world.journal.clear()
        if not stage.on_timeout:
            return False
        if actor.alive and not self._ended():
            self._atomic(stage.on_timeout, {"actor": actor}, f"stages.{stage.name}.on_timeout")
        return True

    def _time_limit(self: Env, stage: StageSpec, actor: Entity) -> float | None:  # type: ignore[misc]
        """Wall-clock seconds ``actor`` has for a turn in ``stage``: the stage's `time_limit`, else the run's."""
        raw = stage.time_limit
        if raw is None:
            return self.time_limit
        path = f"stages.{stage.name}.time_limit"
        try:
            value = compile_expr(raw)(self.world.scope(actor=actor)) if isinstance(raw, str) else raw
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        if value is None:
            return self.time_limit
        if not is_seconds(value):
            raise RunError(f"must be a number of seconds > 0 (or null for the run's limit), got {value!r}", path)
        return float(value)

    def _wake_hook(self: Env, stage: StageSpec, actor: Entity) -> bool:  # type: ignore[misc]
        """Run the stage's ``on_wake`` for ``actor`` before its turn; False when it no longer takes the turn."""
        if stage.on_wake:
            self._atomic(stage.on_wake, {"actor": actor}, f"stages.{stage.name}.on_wake")
        return actor.alive and not self._ended()

    def _turn_end_hook(self: Env, stage: StageSpec, actor: Entity) -> None:  # type: ignore[misc]
        """Run the stage's ``on_turn_end`` for ``actor`` after its turn (and its actions) are done."""
        if stage.on_turn_end and actor.alive and not self._ended():
            self._atomic(stage.on_turn_end, {"actor": actor}, f"stages.{stage.name}.on_turn_end")

    def _interval(self: Env, stage: StageSpec, actor: Entity) -> float:  # type: ignore[misc]
        value = self._stage_time(stage.interval, self.contract.clock.tick, f"stages.{stage.name}.interval", actor=actor)
        if value <= 0:
            raise RunError(f"interval must be greater than 0, got {value}", f"stages.{stage.name}.interval")
        return value

    def _stage_time(self: Env, raw: Any, default: float, path: str, **vars: Any) -> float:  # type: ignore[misc]
        if raw is None:
            return default
        try:
            value = compile_expr(raw)(self.world.scope(**vars)) if isinstance(raw, str) else raw
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
            raise RunError(f"must be a time ≥ 0, got {value!r}", path)
        return float(value)

    def _simultaneous(self: Env, stage: StageSpec, agents: list[Entity], pass_index: int,  # type: ignore[misc]
                      resumed: bool = False) -> _Steps:
        if resumed:
            turns = list(self.origin.staged)
            yield from self.driver.drive_steps(turns, together=True, resume=self._where.position)
        else:
            reasons: dict[str, str] = {}
            for actor in agents:
                reason = self._reason(actor, stage, pass_index)
                if reason is not None:
                    reasons[actor.id] = reason
            for actor in agents:
                if actor.id in reasons and not self._wake_hook(stage, actor):
                    del reasons[actor.id]
            if reasons:
                yield _Point(stage, dict(reasons))
            turns = [Turn(self, actor, stage, reasons[actor.id], staged=True) for actor in agents
                     if actor.id in reasons]
            cursor = self.world.log[-1].seq if self.world.log else 0
            for turn in turns:
                memory = self._memory(turn.actor.id)
                memory.cursor = cursor
                memory.turns += 1
            yield from self.driver.drive_steps(turns, together=True)
        try:
            yield from self._commit_choices(stage, turns)
        finally:
            for turn in turns:  # in turn order, so `$seen` indexes the same way every run
                if turn.exposure is not None:
                    turn.exposure.close(turn)
        self._flush_events()

    def _commit_choices(self: Env, stage: StageSpec, turns: list[Turn]) -> _Steps:  # type: ignore[misc]
        """Commit each agent's sealed choices in turn order — without an `order`, in a random order, since the first to
        commit wins a contested item; atomic stages commit or undo each agent's as a whole."""
        if stage.order is None and len(turns) > 1:
            turns = list(turns)
            self._shuffle(stage, turns)
        atomic = stage.atomic or bool(stage.valid)
        writes = self.world.watched_writes = SealedWrites(stage.name, self.diagnosis)
        try:
            for turn in turns:
                mark = self.world.journal.mark() if atomic else None
                applied = 0
                writes.writer = turn.actor.name or turn.actor.id
                for name, args in turn.intents:
                    if self._ended() and mark is None:
                        return
                    writes.action = name
                    applied += self._commit_intent(turn, name, args, deferred=mark is not None)
                acted = bool(turn.intents)
                if mark is not None:
                    acted = self._settle_choices(turn, mark, applied)
                    if self._ended():
                        return
                self._after_turn(stage, turn, acted, stop_when_ended=True)
                self._turn_end_hook(stage, turn.actor)
        finally:
            self.world.watched_writes = None
        yield from ()

    def _tally(self: Env, actor_id: str, stats: Stats) -> None:  # type: ignore[misc]
        """Add numbers to the run's totals and to the agent's own (callers hold the lock)."""
        self.stats.add(stats)
        self.agent_stats.setdefault(actor_id, Stats()).add(stats)

    def _settle_choices(self: Env, turn: Turn, mark: int, applied: int) -> bool:  # type: ignore[misc]
        """An atomic simultaneous stage: keep one agent's committed choices when they meet `valid`, else undo
        them all and tell the agent why — also when a rule fails or an invariant breaks as they commit. True when
        the agent's choices stand."""
        world, stage = self.world, turn.stage

        def commit() -> str | None:
            with world.turn_context(None, turn.pending):
                why = turn.invalid() if applied else None
            if why is None:
                self._after_commit(f"stages.{stage.name}")
            return why

        with self._lock:
            why, fault = guarded(self, commit, mark)
            if fault is not None:
                why = fault
            if why is None:
                self.happenings.react(stage)
                return bool(turn.intents)
            world.journal.rollback(mark)
            world.emit("outcome", f"Your choices were undone: {why}.", actor=turn.actor.id, to=(turn.actor.id,),
                       data={"ok": False, "undone": True})
            world.journal.clear()
            self._tally(turn.actor.id, Stats(actions=-applied, rejected_actions=applied, undone_turns=1,
                                             faulted_actions=int(fault is not None)))
            turn.stats.undone_turns = 1
        return False

    def _commit_intent(self: Env, turn: Turn, name: str, args: dict[str, Any],  # type: ignore[misc]
                       deferred: bool = False) -> int:
        """Apply one sealed choice; 1 when it applied. ``deferred`` (atomic stages) leaves the commit to the
        whole turn's settling. A rule that fails or an invariant it breaks refuses the choice alone."""
        actor, world = turn.actor, self.world
        with self._lock:
            applied, fault = guarded(self, lambda: self._apply_intent(turn, name, args, deferred), action=name)
            if applied is None:
                assert fault is not None
                world.emit("outcome", f"Your {name.replace('_', ' ')} did not happen: {fault}.",
                           actor=actor.id, to=(actor.id,), data={"action": name, "ok": False})
                self.diagnosis.refused_at_commit(name, fault)
                if not deferred:
                    world.journal.clear()
                self._tally(actor.id, Stats(rejected_actions=1, faulted_actions=1))
                return 0
            if applied and not deferred:
                self.happenings.react(turn.stage)
            return applied

    def _apply_intent(self: Env, turn: Turn, name: str, args: dict[str, Any],  # type: ignore[misc]
                      deferred: bool) -> int:
        actor, world = turn.actor, self.world
        blocked = self.actions.blocked(actor, name, {}, {}) if actor.alive else "you are no longer active"
        params, problem = ({}, blocked) if blocked else self.actions.validate(actor, name, args)
        verb = name.replace("_", " ")
        if problem:
            world.emit("outcome", f"Your {verb} did not happen: {str(problem).rstrip('.')}.",
                       actor=actor.id, to=(actor.id,), data={"action": name, "ok": False})
            self.diagnosis.refused_at_commit(name, str(problem))
            if not deferred:
                world.journal.clear()
            self._tally(actor.id, Stats(rejected_actions=1))
            return 0
        outcome = self.actions.apply(actor, name, params)
        text = outcome.text if outcome.ok else f"Your {verb} failed: {outcome.text}"
        data = {"action": name, "ok": outcome.ok, **({"assets": outcome.assets} if outcome.assets else {})}
        world.emit("outcome", text, actor=actor.id, to=(actor.id,), data=data)
        if not outcome.ok:
            self.diagnosis.refused_at_commit(name, outcome.text)
            self._tally(actor.id, Stats(rejected_actions=1))
            if not deferred:
                world.journal.clear()
            return 0
        if not deferred:
            self._after_commit(f"actions.{name}")
        self.diagnosis.committed(name)
        self._tally(actor.id, Stats(actions=1))
        return 1
