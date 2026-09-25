"""A loaded environment: the run's public face, which owns its state and its gate and wires its parts together.

What the run changes as it plays is one value, :class:`~fg_env.runtime.state.RunState`. The parts are services over
it: :class:`~fg_env.runtime.rules.Rules` evaluates and commits world logic, :class:`~fg_env.runtime.schedule.Schedule`
says when everything happens and who acts in what order, the :class:`~fg_env.runtime.driving.Driver` plays each turn's
participant, and :class:`~fg_env.information.core.Information` renders what agents and spectators read. The parts hold
no state of their own, so a copy of the run (:meth:`Env.copy`) is a copy of its state with the parts built around it.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from .. import mechanisms  # noqa: F401  (loading it registers every mechanism's ops, kinds and functions a run calls)
from ..actions.book import ActionBook
from ..assets.store import AssetStore
from ..contract import MAX_ROUNDS, Contract
from ..copying.previews import Preview, Previews
from ..copying.snapshot import SNAPSHOT_VERSION, restore_env, take_snapshot
from ..effects.runner import EffectRunner
from ..errors import RunError
from ..expr import ExprError
from ..expr.objects import Entity
from ..host.hosts import count_host_tokens
from ..host.tape import tape_of
from ..information.core import Information
from ..information.exposure import recording
from ..sampling.seeds import SeedTree
from ..world.build import build_world
from ..world.store import World
from ..world.values import plain_value
from .budget import Budget, is_seconds
from .diagnostics import diagnose
from .driving import Driver, run_on_worker
from .end_state import end_state
from .facts import Facts
from .forgetting import reads_log
from .gate import Gate
from .measure import RunResult
from .returns import measured
from .rules import Rules
from .schedule import Schedule
from .state import Memory, RunState
from .turn import Turn, entity_dict

__all__ = ["Env", "Origin", "SNAPSHOT_VERSION"]

_E = TypeVar("_E", bound="Env")


class Origin:
    """Where a run comes from: its contract before its arm was applied (forks switch arms from it), and — for a run
    that continues a fork — the snapshot its recording replays from (None otherwise; see
    :func:`~fg_env.copying.snapshot.recording_start`)."""

    __slots__ = ("unarmed", "start")

    def __init__(self, unarmed: Contract, start: dict[str, Any] | None = None):
        self.unarmed = unarmed
        self.start = start


class Env:
    """A loaded environment. Create with :func:`fg_env.load`; run with :meth:`run`; copy with :meth:`clone`
    and :meth:`fork`."""

    # The run's parts (built around its state by _assemble).
    world: World
    effects: EffectRunner
    actions: ActionBook
    information: Information
    facts: Facts
    rules: Rules
    driver: Driver
    previews: Previews
    schedule: Schedule
    gate: Gate
    _running: threading.Lock

    def __init__(self, contract: Contract, inputs: dict[str, Any], seed: int, arm: str | None = None,
                 parallel: int = 8, exposures: bool = False, assets: AssetStore | None = None, events: bool = True):
        self.contract = contract
        self.inputs = inputs
        self.seed = seed
        self.arm = arm
        self.parallel = max(1, parallel)
        self.seeds = SeedTree(seed)
        if exposures and not events:
            raise ValueError("events=False keeps no event log, but exposures=True records what every agent was shown "
                             "to replay against it: drop one of them")
        world = build_world(contract, inputs, self.seeds, arm, assets)
        world.evaluation.cache_defs()
        world.exposures = Information.exposure_log(contract, exposures)
        #: Everything the run changes as it plays (see runtime/state.py). Results carry the event log unless
        #: ``events`` is false; then the run forgets what nothing can read (see forgetting.py).
        self.state = RunState(world, keep_events=events)
        self.state.forgets = not events and not reads_log(contract)
        #: Wall-clock seconds each agent has for a turn (None: no limit).
        self.time_limit: float | None = None
        self.origin = Origin(contract)
        self._assemble(None)
        self.rules.check_invariants("build", "build")

    def _assemble(self, like: Env | None) -> None:
        """Build the run's parts around its state: when it is loaded, and around the state of a copy (see :meth:`copy`),
        sharing what ``like``, the run it copies, read from the contract."""
        contract, world, state = self.contract, self.state.world, self.state
        self.world = world
        #: The one lock every change to the run is made holding (see runtime/gate.py).
        self.gate = Gate()
        self._running = threading.Lock()
        self.effects = EffectRunner(world)
        world.joined = self._joined
        self.actions = ActionBook(contract, world, self.effects)
        self.information = Information(contract, world, self.actions, state, self.gate,
                                       like.information if like is not None else None)
        #: Where everything that happens is told: the statistics and the diagnosis in the state are its folds.
        self.facts = world.facts = Facts(state)
        self.rules = Rules(contract, world, self.effects, self.actions, self.information, state, self.facts,
                           self.gate)
        self.driver = self._new_driver()
        self.previews = Previews(self)
        self.schedule = Schedule(self)
        self.rules.react = self.schedule.react

    def _new_driver(self) -> Driver:
        """What plays this kind of run's turns."""
        return Driver(self)

    def copy(self, kind: type[_E] | None = None, *, waiting: Turn | None = None) -> _E:
        """An independent copy of the run now, continuing exactly as it would, as a run of ``kind`` (default: its
        own). The one way a run is copied: its state is copied (:meth:`RunState.copy`) and its parts are built around
        the copy; a round in progress resumes where it is. Take it between blocks of logic: between rounds, at a safe
        point, or while turns wait for a decision — ``waiting``, the one the copy continues from (the turns of the
        stage already under way end there; see :meth:`~fg_env.runtime.driving.Driver.drive_steps`). The copy keeps the
        run's participants, time limit and hosts."""
        made: type[Any] = kind or type(self)
        env = object.__new__(made)
        env.contract, env.inputs, env.seed, env.arm = self.contract, self.inputs, self.seed, self.arm
        env.parallel, env.seeds, env.time_limit = self.parallel, self.seeds, self.time_limit
        env.origin = Origin(self.origin.unarmed, self.origin.start)
        env.state = self.state.copy()
        env._assemble(self)
        env.state.adopt(env)
        if waiting is not None:
            env.state.cursor.waiting = waiting.number
        env.driver.spec = dict(self.driver.spec)
        from ..copying.branch import keep_chance
        from ..host.hosts import bind, hosts_for

        hosts = hosts_for(self.world)
        if hosts is not None:
            bind(env, hosts)
        keep_chance(self, env)
        return env

    # -- the run's standing (held in its state) ------------------------------------

    @property
    def status(self) -> str:
        return self.state.status

    @status.setter
    def status(self, value: str) -> None:
        self.state.status = value

    @property
    def ended_by(self) -> str | None:
        return self.state.ended_by

    @ended_by.setter
    def ended_by(self, value: str | None) -> None:
        self.state.ended_by = value

    @property
    def error(self) -> str | None:
        return self.state.error

    @error.setter
    def error(self, value: str | None) -> None:
        self.state.error = value

    @property
    def budget(self) -> Budget | None:
        return self.state.budget

    @budget.setter
    def budget(self, value: Budget | None) -> None:
        self.state.budget = value

    # -- public API ----------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.status in ("completed", "ended", "failed")

    @property
    def round(self) -> int:
        return self.world.round

    @property
    def stage(self) -> str | None:
        """The name of the stage being played, or None between stages (with ``run(stop=...)``, stop as one begins)."""
        return self.world.stage

    def run(self, participants: Any = None, *, rounds: int | None = None,
            stop: Callable[[Env], bool] | None = None,
            on_event: Callable[[dict[str, Any]], None] | None = None,
            raise_errors: bool = False, hosts: Any = None, time_limit: float | None = None,
            budget: Mapping[str, Any] | None = None) -> RunResult:
        """Run to the end, or for ``rounds`` more rounds, or until ``stop(env)`` is true.

        ``participants`` is a callable for every agent, or a mapping from entity id, type or ``"*"`` to a participant (a
        callable — plain or ``async def`` — ``"random"``, ``"idle"``, ``"policy:<name>"``). Agents without one use their
        type's ``policy`` or ``"random"``. Every participant is offered the contract's in-turn host tools; ``hosts``
        binds the run to host adapters first. ``time_limit`` sets :attr:`time_limit`, the wall-clock seconds each
        agent's turn may take; ``budget`` caps the run (:mod:`fg_env.runtime.budget`). In an event loop, use
        :meth:`arun`.

        ``stop`` is checked before every round, stage, pass and sequential turn. A stopped run
        continues exactly where it stopped on the next call; finishing a round that was
        stopped part-way counts as one of ``rounds``.
        """
        return self._run(participants, rounds, stop, on_event, raise_errors, hosts, time_limit, budget, None)

    async def arun(self, participants: Any = None, *, rounds: int | None = None,
                   stop: Callable[[Env], bool] | None = None,
                   on_event: Callable[[dict[str, Any]], None] | None = None,
                   raise_errors: bool = False, hosts: Any = None, time_limit: float | None = None,
                   budget: Mapping[str, Any] | None = None) -> RunResult:
        """:meth:`run` as a coroutine, for use inside a running event loop.

        Async participants run on this loop — so clients bound to it work — and a simultaneous
        stage's async participants run concurrently. The engine itself runs in a worker thread, so
        the loop stays free while it plays; ``stop`` and ``on_event`` are called from that thread.
        Cancelling the call stops the run at its next safe point.
        """
        def play(loop: asyncio.AbstractEventLoop, halt: Callable[[Env], bool]) -> RunResult:
            return self._run(participants, rounds, halt, on_event, raise_errors, hosts, time_limit, budget, loop)

        result: RunResult = await run_on_worker(play, stop)
        return result

    def _run(self, participants: Any, rounds: int | None, stop: Callable[[Env], bool] | None,
             on_event: Callable[[dict[str, Any]], None] | None, raise_errors: bool, hosts: Any,
             time_limit: float | None, budget: Any, loop: asyncio.AbstractEventLoop | None) -> RunResult:
        if rounds is not None and (isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0):
            raise ValueError(f"rounds must be a whole number ≥ 0, got {rounds!r}")
        if rounds is not None and rounds > MAX_ROUNDS:
            raise ValueError(f"rounds must be at most {MAX_ROUNDS:,}, got {rounds:,}")
        if time_limit is not None and not is_seconds(time_limit):
            raise ValueError(f"time_limit must be a number of seconds > 0, got {time_limit!r}")
        if not self._running.acquire(blocking=False):
            raise RuntimeError("this environment is already running; run() cannot be called again until it returns")
        try:
            if hosts is not None:
                from ..host.hosts import bind

                bind(self, hosts)
            self.driver.bind(participants)
            if time_limit is not None:
                self.time_limit = float(time_limit)
            self.budget = Budget.begin(budget, self.budget)
            self.driver.loop = loop
            self.schedule.on_event = on_event
            try:
                self.schedule.play(rounds, stop)
            except (RunError, ExprError) as exc:
                self.schedule.fail(str(exc))
                if raise_errors:
                    self.schedule.flush()
                    raise
            except BaseException as exc:
                # A participant callback, stop/on_event callback or engine defect: surfaced, never swallowed.
                self.schedule.fail(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
                raise
            self.schedule.flush()
            return self.result()
        finally:
            self.schedule.on_event = None
            self.driver.loop = None
            self._running.release()

    def step(self, participants: Any = None) -> RunResult:
        """Run exactly one round (or finish the round a stopped run is in)."""
        return self.run(participants, rounds=1)

    def entity(self, entity_id: str) -> dict[str, Any] | None:
        """A copy of one entity: ``{id, name, type, alive, at, props}``, or None."""
        found = self.world.entities.get(entity_id)
        return entity_dict(found) if found is not None else None

    def entities(self, type_name: str | None = None, alive: bool = True) -> list[dict[str, Any]]:
        """Copies of entities, optionally of one type (subtypes included) and only alive ones."""
        kinds = set(self.contract.subtypes(type_name)) if type_name else None
        return [entity_dict(e) for e in self.world.entities.values()
                if (kinds is None or e.entity_type in kinds) and (e.alive or not alive)]

    def records(self, name: str) -> list[dict[str, Any]]:
        """A detached, JSON-safe copy of a declared record stream.

        Host applications use records to render engine-native timelines,
        transcripts, market bars, and reports without reaching into the
        runtime's internal world object. Private record visibility remains a
        participant-view concern; this host-level method returns the complete
        authoritative stream.
        """
        return [plain_value(dict(entry)) for entry in self.world.records(name)]

    @property
    def props(self) -> dict[str, Any]:
        """A copy of the world's global properties."""
        return plain_value(dict(self.world.props))

    def result(self) -> RunResult:
        count_host_tokens(self)
        outputs: dict[str, Any] = {}
        issues: list[dict[str, Any]] = []
        returns: dict[str, float] = {}
        if self.status != "failed":  # unfinished runs get provisional outputs and returns
            outputs, problems, returns = measured(self.contract, self.world, self.finished)
            issues = [p.to_dict() for p in problems]
        end = self.world.end_request or {}
        return RunResult(
            status=self.status, ended_by=self.ended_by, rounds=self.world.round, seed=self.seed, arm=self.arm,
            inputs=self.inputs, outputs=outputs, metrics=dict(self.world.metrics),
            series={k: list(v) for k, v in self.world.series.items()}, winner=end.get("winner"),
            error=self.error, output_issues=issues, stats=self.state.stats.to_dict(),
            agent_stats={key: agent.to_dict() for key, agent in sorted(self.state.agent_stats.items())},
            events=self.state.event_rows() if self.state.keep_events else [],
            exposures=recording(self),
            frames=[dict(frame) for frame in self.state.frames], returns=returns,
            host_tape=tape_of(self) if self.world.exposures is not None else {}, budget=Budget.report(self),
            formats={name: spec.format for name, spec in self.contract.outputs.items() if spec.format},
            diagnostics=diagnose(self, outputs, issues),
            clock={"unit": self.contract.clock.unit, "step": self.contract.clock.step, "start": self.world.start},
            assets=self.world.assets.to_dict() if len(self.world.assets) else {},
            state=end_state(self.contract, self.world),
        )

    @property
    def frames(self) -> list[dict[str, Any]]:
        """Spectator frames so far: ``[{round, views: {name: text}, final?}]``."""
        return self.state.frames

    def spectate(self) -> dict[str, str]:
        """Every spectator view (``"for": "spectator"``) rendered against the world now, by name. Changes
        nothing: views that draw randomness use a stream of their own."""
        return self.information.spectate()

    def preview(self, entity_id: str, stage: str | None = None, participants: Any = None) -> Preview:
        """What the agent would receive on its next turn: brief, update, tools and time limit — a mapping that prints
        as ``fg-env preview`` shows it (``print(env.preview("ann"))``). Changes nothing.

        Between rounds this plays the next round on a copy up to the agent's turn — scheduled
        effects, start events, physics and the turns of agents before it — so the preview shows the
        turn as the agent will get it. ``participants`` (as for :meth:`run`) plays those earlier turns; by default
        the run's own participants do. Only free ones play: random, idle, a policy, or a callable you pass here. A
        named LLM or search algorithm is never called — its agent plays its default (its type's policy, else random)
        — and a run's own callables are not called either. A turn an `auto` stage plays without waking the agent is
        skipped, as the run skips it.
        """
        return self.previews.preview(entity_id, stage, participants)

    def snapshot(self) -> dict[str, Any]:
        """Everything needed to continue this run later, as JSON-safe data: between rounds, or stopped part-way
        through a round (``run(stop=...)``)."""
        return take_snapshot(self)

    @classmethod
    def restore(cls, contract: Any, snapshot: Mapping[str, Any], parallel: int = 8, hosts: Any = None,
                data_dir: Any = None) -> Env:
        """Continue a run from :meth:`snapshot`. ``contract`` is the contract it was taken with
        (a :class:`Contract`, dict, path or JSON text; the snapshot's arm is applied if needed).
        ``hosts`` answers host judgment; answers already recorded in the snapshot are never asked again.
        The contract's files are found again in its folder (or ``data_dir``) and checked against the recorded hashes."""
        from ..api import default_data_dir
        from ..assets.catalog import locate

        env = restore_env(cls, contract, snapshot, parallel)
        locate(env.world.assets, default_data_dir(contract, data_dir))
        if hosts is not None:
            from ..host.hosts import bind

            bind(env, hosts)
        return env

    def clone(self) -> Env:
        """An independent copy of this run now, continuing exactly as it would.

        Between rounds, or stopped part-way through a round (``run(stop=...)``) — then the copy is stopped at the same
        point. Inside a turn, use ``wake.clone()``.
        """
        from ..copying.branch import clone_env

        return clone_env(self)

    def fork(self, **changes: Any) -> Env:
        """A new run continuing this one from now under changes, leaving this run untouched: another ``arm``
        (``None`` for none), ``inputs``, a contract ``patch`` or a whole replacement ``contract``, a ``seed`` for
        the luck from here on, and intervention ``effects`` applied at the fork (logged as a `fork` event,
        invariants checked). Without changes it is :meth:`clone`; like a clone, it keeps this run's participants.

        Changes apply between rounds. Whatever the changed contract cannot hold of the current state is refused
        with a :class:`~fg_env.ContractError` listing each problem and its fix. See :func:`fg_env.fork`.
        """
        from ..copying.forks import fork_env

        return fork_env(self, **changes)

    # -- helpers --------------------------------------------------------------------------------

    def _joined(self, entity: Entity) -> None:
        """An entity created during the run: an agent's news starts from its arrival."""
        if self.contract.is_agent(entity.entity_type):
            memory = self.state.memories[entity.id] = Memory()
            memory.cursor = self.world.log[-1].seq if self.world.log else 0
