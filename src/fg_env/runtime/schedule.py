"""The schedule of a run: the one place ordering lives.

A run plays rounds. A round starts — scheduled effects, feeds, `round.start` events, physics — plays its stages in
order, and ends — `round.end` events, patterns, metrics — or ends the run. A stage that runs fires `stage.X.start`,
plays its passes — each waking the agents its `who` wakes, in its `order`, until its `until` holds — and fires
`stage.X.end`; after each turn it fires `stage.X.turn`. Sealed choices commit in the stage's order, or in a random one
(the first to commit wins a contested item; see :mod:`.sealed`). Agents asked to react get their turn once the change
that asked has committed (:meth:`Schedule.react`). The rules evaluate and commit everything (:mod:`.rules`); the
schedule only says when.

A round advances through safe points — before each stage, each pass and each sequential turn — so a run can stop at
any of them and continue exactly where it left off. The round is a generator that keeps the run's
:class:`~fg_env.runtime.state.Cursor` current as it plays, and resumes from the cursor alone: a copy of the run taken
while a turn waits for a decision continues the round from there (see :mod:`fg_env.copying.direct`).
"""
from __future__ import annotations

import bisect
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..actions.book import announces, stage_actions
from ..contract import MAX_STAGE_PASSES, StageSpec
from ..errors import RunError
from ..expr import EVERYONE, ExprError, PrivateRead, compile_expr, truthy
from ..expr.objects import Entity
from ..world.build import whole_setting
from .diagnosis import SealedWrites
from .driving import WAITING
from .feeds import run_feeds
from .forgetting import forget
from .measure import sample_metrics
from .sealed import commit_sealed
from .state import Cursor
from .turn import Turn

if TYPE_CHECKING:
    from .env import Env

__all__ = ["Schedule", "SafePoint", "Steps"]


@dataclass
class SafePoint:
    """A safe point in a round. ``reasons`` names the agents about to be woken, and why."""

    stage: StageSpec | None = None
    reasons: dict[str, str] = field(default_factory=dict)


#: A round's steps: its safe points (:class:`SafePoint`), and ``WAITING`` while a turn waits for a decision.
Steps = Generator[Any, None, None]


class Schedule:
    """When everything in one run happens, and who acts in what order."""

    #: How deep reactions may set off further reactions (deeper reactions wait for the agent's next turn).
    REACTION_DEPTH = 4

    def __init__(self, env: Env):
        self.env = env
        self.rules = env.rules
        #: The round in progress while the run is stopped or waiting inside it: a generator over the cursor.
        self._round: Steps | None = None
        self._reaction_depth = 0
        #: Called with every event as it is logged, once it has committed (``run(on_event=...)``).
        self.on_event: Callable[[dict[str, Any]], None] | None = None

    # -- the run ---------------------------------------------------------------------------------------------------

    @property
    def playing(self) -> bool:
        """Whether a round is in progress: the run stopped or waits inside it."""
        return self._round is not None

    def play(self, rounds: int | None, stop: Callable[[Env], bool] | None) -> None:
        """Play the run on: to its end, for ``rounds`` more rounds (finishing a round stopped part-way counts as one),
        or until ``stop(env)`` is true at a safe point or its budget runs out."""
        env = self.env
        completed = 0
        while not env.finished:
            if self._round is None:
                if (rounds is not None and completed >= rounds) or (
                        env.budget is not None and env.budget.enforce(env)):
                    return
                if stop is not None and stop(env):
                    env.status = "stopped"
                    return
                if not env.state.keep_events:
                    forget(env)
                env.origin.round_start(env)
                self._round = self.steps()
            elif env.status == "stopped":
                env.status = "running"
            for point in self._round:
                if point is WAITING:  # a turn waits for a decision: the run pauses here, its round kept
                    return
                env.origin.tape.points += 1
                if (env.budget is not None and env.budget.enforce(env)) or (stop is not None and stop(env)):
                    env.status = env.status if env.finished else "stopped"
                    return
            self._round = None
            completed += 1

    def fail(self, message: str) -> None:
        """The run failed with ``message``: the round in progress is dropped."""
        self.abandon()
        self.env.status, self.env.error = "failed", message

    def abandon(self) -> None:
        """Drop the round in progress, closing its generator (nothing in it runs on)."""
        if self._round is not None:
            self._round.close()
            self._round = None

    def resume(self) -> None:
        """Continue the round at the run's cursor: a copy taken while a turn waited in it (see copying/direct.py)."""
        self._round = self.steps(resumed=True)

    def throw(self, error: BaseException) -> None:
        """Raise ``error`` inside the round in progress, where it waits (its turns close as a failure raised inside
        them would); the caller catches it, or the ``StopIteration`` of a round that handled it."""
        if self._round is not None:
            self._round.throw(error)

    def flush(self) -> None:
        """Hand the events logged since the last flush to :attr:`on_event`."""
        log, state = self.env.world.log, self.env.state
        if self.on_event is None:
            state.emitted = len(log)
            return
        while state.emitted < len(log):
            event = log[state.emitted]
            state.emitted += 1
            self.on_event(event.to_dict())

    # -- rounds ----------------------------------------------------------------------------------------------------

    def begin_round(self) -> bool:
        """Start the next round: scheduled effects, feeds, `round.start` events, physics. False if the run ended."""
        env, rules, world = self.env, self.rules, self.env.world
        env.state.in_round = True
        if env.status in ("ready", "stopped"):
            env.status = "running"
        world.round += 1
        world.luck.firings.clear()  # counts of this round's luck, and of its uses of actions: never undone
        world.used_round.clear()
        world.stage = None
        rules.run_scheduled()
        run_feeds(rules)
        rules.fire("round.start")
        rules.check_end()
        if rules.ended():
            self.finish()
            return False
        with rules.lock:
            world.step_physics()
            world.journal.clear()
        rules.check_invariants("physics")
        rules.check_changes("physics")
        if rules.ended():
            self.finish()
            return False
        return True

    def steps(self, resumed: bool = False) -> Steps:
        """A round, from its start — or, ``resumed``, from the waiting turn the run's cursor is at."""
        env, rules, world, state = self.env, self.rules, self.env.world, self.env.state
        if not resumed:
            if not self.begin_round():
                return
            state.cursor = Cursor()
        stages = env.contract.stage_list()
        for index in range(state.cursor.stage, len(stages)):
            stage = stages[index]
            if resumed:
                resumed = False
                yield from self.run_stage(stage, resumed=True)
            else:
                state.cursor.stage = index
                yield SafePoint(stage)
                yield from self.run_stage(stage)
            rules.check_end()
            if rules.ended():
                self.finish()
                return
        world.stage = None
        rules.fire("round.end")
        world.patterns.commit()
        sample_metrics(env.contract, world)
        rules.check_changes("round end")
        rules.check_invariants("round", "round")
        rules.check_end()
        self.flush()
        if rules.ended():
            self.finish()
            return
        state.in_round = False
        if world.round >= world.rounds:
            env.ended_by = "rounds"
            env.status = "completed"
            self._final_event()
        else:
            env.previews.frame(final=False)

    def finish(self) -> None:
        """End the run where it is, as its end request says."""
        env, world = self.env, self.env.world
        world.stage = None
        if not world.series or len(next(iter(world.series.values()), [])) < world.round:
            sample_metrics(env.contract, world)
        end = world.end_request or {}
        env.ended_by = end.get("name") or "end"
        env.status = "ended"
        env.state.in_round = False
        self._final_event()

    def _final_event(self) -> None:
        env, world = self.env, self.env.world
        self.rules.check_invariants("the run", "end")
        end = world.end_request or {}
        text = end.get("text") or (f"The run ended: {env.ended_by}." if env.ended_by != "rounds" else "Time is up.")
        world.emit("end", text, data={"ended_by": env.ended_by, "winner": end.get("winner")})
        world.journal.clear()
        env.previews.frame(final=True)
        self.flush()

    # -- stages ----------------------------------------------------------------------------------------------------

    def run_stage(self, stage: StageSpec, resumed: bool = False) -> Steps:
        """One visit of ``stage``: its start events, its passes and its end events."""
        env, rules, world, cursor = self.env, self.rules, self.env.world, self.env.state.cursor
        path = f"stages.{stage.name}"
        if not resumed:
            runs = self.stage_runs(stage)
            env.diagnosis.stage(stage.name, reached=1, ran=int(runs))
            if not runs:
                return
            world.stage = stage.name
            rules.fire(f"stage.{stage.name}.start")
            if rules.ended():
                return
            cursor.pass_index = 0
        passes = whole_setting(world, stage.passes, f"{path}.passes", MAX_STAGE_PASSES) or (10 if stage.until else 1)
        for pass_index in range(cursor.pass_index, passes):
            if resumed:
                agents = cursor.agents
            else:
                if pass_index:
                    yield SafePoint(stage)
                agents = self.eligible(stage, pass_index=pass_index)
                cursor.pass_index, cursor.agents = pass_index, agents
                env.diagnosis.stage(stage.name, woke=len(agents))
            if stage.turns == "simultaneous":
                yield from self._simultaneous(stage, agents, pass_index, resumed)
            else:
                yield from self._sequential(stage, agents, pass_index, resumed)
            resumed = False
            if rules.ended():
                return
            if stage.until is not None:
                try:
                    if truthy(compile_expr(stage.until)(world.scope())):
                        break
                except ExprError as exc:
                    raise RunError(str(exc), f"{path}.until") from None
        else:
            if stage.until is not None:
                env.diagnosis.stage(stage.name, capped=1)  # every pass ran and `until` still did not hold
        rules.fire(f"stage.{stage.name}.end")

    def stage_runs(self, stage: StageSpec) -> bool:
        """Whether ``stage`` runs now (its `when`)."""
        if stage.when is None:
            return True
        try:
            return truthy(compile_expr(stage.when)(self.env.world.scope()))
        except ExprError as exc:
            raise RunError(str(exc), f"stages.{stage.name}.when") from None

    def eligible(self, stage: StageSpec, ordered: bool = True, pass_index: int = 0) -> list[Entity]:
        """Agents woken in ``stage`` (in its pass ``pass_index``), in turn order. ``ordered=False`` skips ordering (no
        random draws)."""
        contract, world = self.env.contract, self.env.world
        agent_types = set(contract.agent_types())  # includes types that inherit `agent`
        acting = {kind: bool(stage_actions(contract, stage, kind)) for kind in agent_types}
        agents = [e for e in world.entities.values() if e.alive and acting.get(e.entity_type)]
        path = f"stages.{stage.name}"
        try:
            if stage.who is not None:
                agents = self._woken(stage, agents, pass_index)
        except PrivateRead as exc:
            raise RunError(f"{exc.detail.partition(', and ')[0]}, and every agent learns who acts in {stage.name} (its "
                           "actions are announced), so waking by it would reveal it: wake by what is not private, or "
                           "give the stage's actions `announce: false`", f"{path}.who") from None
        except ExprError as exc:
            raise RunError(str(exc), path) from None
        try:
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

    def _woken(self, stage: StageSpec, agents: list[Entity], pass_index: int) -> list[Entity]:
        """The ``agents`` that the stage's `who` wakes. Each is decided with luck of its own (the stage, round, pass and
        agent), so who else is alive never shifts it, and asking again (a preview) gives the same answer."""
        world, who = self.env.world, compile_expr(stage.who)
        # When the stage's actions are announced, everyone learns who was woken: `who` reads what everyone may know.
        shown = {"viewer": EVERYONE} if announces(self.env.contract, stage) else {}
        woken = []
        for i, agent in enumerate(agents):
            with world.luck.stream("who", stage.name, world.round, pass_index, agent.id):
                if truthy(who(world.scope(it=agent, i=i, **shown))):
                    woken.append(agent)
        return woken

    def _shuffle(self, stage: StageSpec, agents: list[Any]) -> None:
        """Put ``agents`` (or their turns) in a random order drawn from the stage's own stream."""
        world = self.env.world
        with world.luck.at(f"stages.{stage.name}.order"):
            world.rng.shuffle(agents)
        world.journal.clear()  # the draw is the round's: nothing undoes it, and the run may pause after it

    def _reason(self, actor: Entity, stage: StageSpec, pass_index: int) -> str | None:
        """Why ``actor`` is woken in this pass of ``stage``, or None when it sits the pass out (`quiet: skip`)."""
        env = self.env
        requested = env.world.wake_requests.pop(actor.id, None)
        memory = env.state.memory(actor.id)
        if stage.quiet == "skip" and requested is None and pass_index > 0:
            if not env.perception.news(actor, memory.cursor, 1)[0]:
                return None
        if requested:
            return requested
        if stage.turns == "simultaneous":
            return "Everyone chooses at the same time."
        return "Your turn again." if pass_index and self._turned_here(memory.cursor, stage) else "It is your turn."

    def _turned_here(self, cursor: int, stage: StageSpec) -> bool:
        """Whether the agent whose last turn ended at log position ``cursor`` had it in this visit of ``stage``: the
        latest event then was this round's, in this stage."""
        world = self.env.world
        log = world.log
        at = bisect.bisect_left(log, cursor, key=lambda event: event.seq)
        if cursor <= 0 or at == len(log) or log[at].seq != cursor:
            return False
        return log[at].round == world.round and log[at].stage == stage.name

    # -- turns -----------------------------------------------------------------------------------------------------

    def _sequential(self, stage: StageSpec, agents: list[Entity], pass_index: int, resumed: bool = False) -> Steps:
        env, cursor = self.env, self.env.state.cursor
        for position in range(cursor.position if resumed else 0, len(agents)):
            actor = agents[position]
            if resumed:
                resumed, turn, cursor.turn = False, cursor.turn, None
                assert turn is not None
                yield from env.driver.drive_steps([turn], resume=0)
            else:
                if self.rules.ended():
                    return
                if not actor.alive:  # removed earlier this pass: everyone after it still takes their turn
                    continue
                reason = self._reason(actor, stage, pass_index)
                if reason is None:
                    continue
                cursor.position = position
                yield SafePoint(stage, {actor.id: reason})
                turn = Turn(env, actor, stage, reason, staged=False)
                yield from env.driver.drive_steps([turn])
            self._after_turn(stage, turn, turn.stats.actions > 0)
            self._remember(actor)
            self.flush()

    def _simultaneous(self, stage: StageSpec, agents: list[Entity], pass_index: int, resumed: bool = False) -> Steps:
        env = self.env
        if resumed:
            turns = list(env.origin.staged)
            yield from env.driver.drive_steps(turns, together=True, resume=env.state.cursor.position)
        else:
            reasons: dict[str, str] = {}
            for actor in agents:
                reason = self._reason(actor, stage, pass_index)
                if reason is not None:
                    reasons[actor.id] = reason
            if reasons:
                yield SafePoint(stage, dict(reasons))
            turns = [Turn(env, actor, stage, reasons[actor.id], staged=True) for actor in agents
                     if actor.id in reasons]
            for turn in turns:  # every agent's news starts from before anyone's choices commit
                self._remember(turn.actor)
            yield from env.driver.drive_steps(turns, together=True)
        try:
            self._commit_choices(stage, turns)
        finally:
            for turn in turns:  # in turn order, so `$seen` indexes the same way every run
                if turn.exposure is not None:
                    turn.exposure.close(turn)
        self.flush()

    def _commit_choices(self, stage: StageSpec, turns: list[Turn]) -> None:
        """Commit each agent's sealed choices in turn order — without an `order`, in a random order, since the first to
        commit wins a contested item; atomic stages commit or undo each agent's as a whole."""
        if stage.order is None and len(turns) > 1:
            turns = list(turns)
            self._shuffle(stage, turns)
        world = self.env.world
        writes = world.watched_writes = SealedWrites(stage.name, self.env.diagnosis)
        try:
            for turn in turns:
                acted = commit_sealed(self.rules, turn, writes, atomic=bool(stage.valid))
                if acted is None:
                    return
                self._after_turn(stage, turn, acted, stop_when_ended=True)
        finally:
            world.watched_writes = None

    def _after_turn(self, stage: StageSpec, turn: Turn, acted: bool, stop_when_ended: bool = False) -> None:
        """A played turn is over: record a timeout, or report an agent that had to act and did not; then fire the
        stage's `turn` events for a living agent ($actor, $acted, $timed_out)."""
        rules, actor = self.rules, turn.actor
        timed_out = self._timed_out(turn)
        if stop_when_ended and rules.ended():
            return
        if not (timed_out or acted) and actor.alive and turn.did_not_act:
            with rules.lock:
                rules.world.emit("idle", f"{actor.name} did not act.", actor=actor.id, data={"stage": stage.name})
                rules.world.journal.clear()
        if actor.alive and not rules.ended():
            rules.fire(f"stage.{stage.name}.turn", {"actor": actor, "acted": acted, "timed_out": timed_out},
                       owner=actor)

    def _timed_out(self, turn: Turn) -> bool:
        """Record a turn that ran out of time (a `timeout` event); whether it did."""
        if not turn.timed_out:
            return False
        actor, world = turn.actor, self.rules.world
        with self.rules.lock:
            world.emit("timeout", f"{actor.name} ran out of time.", actor=actor.id,
                       data={"stage": turn.stage.name, "limit": turn.time_limit})
            world.journal.clear()
        return True

    def _remember(self, actor: Entity) -> None:
        """``actor`` has had a turn: its next news starts after what is logged now."""
        log = self.env.world.log
        memory = self.env.state.memory(actor.id)
        memory.cursor = log[-1].seq if log else 0
        memory.turns += 1

    # -- reactions -------------------------------------------------------------------------------------------------

    def react(self, stage: StageSpec | None) -> None:
        """Give every agent asked to react (`wake` with `now`) a turn right away, in the current stage — offered the
        actions the wake names, else the stage's: once the action that woke them has committed, so a reaction answers it
        and cannot undo it. While an agent's action is still committing (and could yet be undone), they wait for it to
        finish. Reactions to reactions nested deeper than :attr:`REACTION_DEPTH` become ordinary wakes: agents that keep
        answering each other never fail the run."""
        env, world = self.env, self.env.world
        if world.journal.holding:
            return
        while world.reactions and not self.rules.ended():
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
                self._timed_out(turn)
            finally:
                self._reaction_depth -= 1
            self._remember(actor)
