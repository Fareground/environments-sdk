"""The engine: rounds, stages, turns, events and ending.

A round advances through safe points — before each stage, each pass and each sequential
turn — so a run can stop at any of them and continue exactly where it left off.

The round loop lives in :mod:`.rounds` and stage and turn running in :mod:`.stages`.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping
from typing import Any

from ..actions.book import ActionBook
from ..actions.reads import inspect_rule
from ..assets.store import AssetStore
from ..contract import MAX_ROUNDS, Contract
from ..copying.previews import Previews
from ..copying.replay import Origin
from ..copying.snapshot import SNAPSHOT_VERSION, restore_env, take_snapshot
from ..effects.runner import EffectRunner
from ..errors import RunError
from ..expr import ExprError
from ..expr.objects import Entity
from ..host.hosts import count_host_tokens
from ..host.tape import tape_of
from ..sampling.seeds import SeedTree
from ..world.build import build_world
from ..world.live import _plain
from .budget import Budget, is_seconds
from .checks import RunChecks
from .diagnosis import Diagnosis
from .diagnostics import diagnose
from .driving import WAITING, Driver, run_on_worker
from .end_state import end_state
from .exposure import ExposureLog, asks_seen, recording
from .forgetting import forget, reads_log
from .happenings import Happenings
from .measure import RunResult, Stats
from .perception import Perception
from .returns import measured
from .rounds import RunRounds, _Steps, _Where
from .stages import RunStages
from .turn import Memory, entity_dict

__all__ = ["Env", "SNAPSHOT_VERSION"]


class Env(RunChecks, RunRounds, RunStages):
    """A loaded environment. Create with :func:`fg_env.load`; run with :meth:`run`; copy with :meth:`clone`
    and :meth:`fork`."""

    #: Declared here because the round and stage mixins are type-checked before ``__init__`` is.
    status: str
    origin: Origin
    diagnosis: Diagnosis
    happenings: Happenings
    driver: Driver
    previews: Previews
    _lock: threading.RLock
    _signal: threading.Condition
    _turn_count: int
    _emitted: int
    _inspectable: bool
    _end_on_action: bool

    def __init__(self, contract: Contract, inputs: dict[str, Any], seed: int, arm: str | None = None,
                 parallel: int = 8, exposures: bool = False, assets: AssetStore | None = None, events: bool = True):
        self.contract = contract
        self.inputs = inputs
        self.seed = seed
        self.arm = arm
        self.parallel = max(1, parallel)
        self.seeds = SeedTree(seed)
        self.world = build_world(contract, inputs, self.seeds, arm, assets)
        self.world.enable_def_cache()
        self.effects = EffectRunner(self.world)
        self.world.joined = self._joined
        self.actions = ActionBook(contract, self.world, self.effects)
        self.perception = Perception(contract, self.world)
        self.stats = Stats()
        #: The same numbers per agent entity id (a tournament bills each entrant for its own turns).
        self.agent_stats: dict[str, Stats] = {}
        self.status = "ready"
        self.ended_by: str | None = None
        self.error: str | None = None
        self._memories: dict[str, Memory] = {}
        self._briefs: dict[str, str] = {}
        #: The assets each agent's brief attaches (fixed with the brief text).
        self._brief_assets: dict[str, list[str]] = {}
        self._used_round: dict[str, dict[str, int]] = {}
        #: Events with `once` that fired, by index; and the last truth value of each `change` event's `when`.
        self._fired_once: set[int] = set()
        self._armed: dict[int, bool] = {}
        self._lock = threading.RLock()
        #: Signalled when a participant's turn lands or a call returns; waiting on it releases the lock.
        self._signal = threading.Condition(self._lock)
        self._running = threading.Lock()
        self.driver = Driver(self)
        #: Wall-clock seconds each agent has for a turn (None: no limit).
        self.time_limit: float | None = None
        self.budget: Budget | None = None
        #: Recorded when asked, or when the contract's rules ask `$seen`.
        self.world.exposures = ExposureLog() if exposures or asks_seen(contract) else None
        if exposures and not events:
            raise ValueError("events=False keeps no event log, but exposures=True records what every agent was shown "
                             "to replay against it: drop one of them")
        #: Whether results carry the event log; without it the run forgets what nothing can read (see forgetting.py).
        self._keep_events = events
        self._reads_log = reads_log(contract) if not events else True
        self.happenings = Happenings(self)
        self.previews = Previews(self)
        self._on_event: Callable[[dict[str, Any]], None] | None = None
        self._emitted = 0
        self._turn_count = 0
        #: The round in progress while a run is stopped inside it, and where in it the run is.
        self._cursor: _Steps | None = None
        self._where = _Where()
        self._in_round = False
        self.origin = Origin(contract)  # what copies of this run replay from (see copying/replay.py)
        #: Whether some type lets agents inspect entities besides themselves (whose [id] handles then show).
        self._inspectable = any(self._inspect_rule(kind) is not False for kind in contract.types)
        #: The state each invariant was last found to hold in (see _check_invariants).
        self._invariant_held: dict[int, Any] = {}
        self._end_on_action = any(end.check == "action" for end in contract.end)
        #: The log as plain data for results, converted once per event (see _event_rows).
        self._rows: list[dict[str, Any]] = []
        self._rows_last: Any = None
        self.diagnosis = self.world.diagnosis = Diagnosis(self.world.written)
        self._check_invariants("build", "build")

    # -- public API ----------------------------------------------------------------

    @property
    def finished(self) -> bool:
        return self.status in ("completed", "ended", "failed")

    @property
    def round(self) -> int:
        return self.world.round

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
            self._on_event = on_event
            try:
                self._play(rounds, stop)
            except (RunError, ExprError) as exc:
                self._fail(str(exc))
                if raise_errors:
                    self._flush_events()
                    raise
            except BaseException as exc:
                # A participant callback, stop/on_event callback or engine defect: surfaced, never swallowed.
                self._fail(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
                raise
            self._flush_events()
            return self.result()
        finally:
            self._on_event = None
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
        return [_plain(dict(entry)) for entry in self.world.records(name)]

    @property
    def props(self) -> dict[str, Any]:
        """A copy of the world's global properties."""
        return _plain(dict(self.world.props))

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
            error=self.error, output_issues=issues, stats=self.stats.to_dict(),
            agent_stats={key: self.agent_stats[key].to_dict() for key in sorted(self.agent_stats)},
            events=self._event_rows() if self._keep_events else [],
            exposures=recording(self),
            frames=[dict(frame) for frame in self.previews.frames], returns=returns,
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
        return self.previews.frames

    def spectate(self) -> dict[str, str]:
        """Every spectator view (``"for": "spectator"``) rendered against the world now, by name. Changes
        nothing: views that draw randomness use a stream of their own."""
        return self.previews.spectate()

    def preview(self, entity_id: str, stage: str | None = None, participants: Any = None) -> dict[str, Any]:
        """What the agent would receive on its next turn: brief, update, tools and time limit. Changes nothing.

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

        Between rounds it is a restored snapshot; a run stopped part-way through a round (``run(stop=...)``) is
        copied by replaying it, so the copy stops at the same point. Inside a turn, use ``wake.clone()``.
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

    # -- driving -----------------------------------------------------------------------

    def _play(self, rounds: int | None, stop: Callable[[Env], bool] | None) -> None:
        completed = 0
        while not self.finished:
            if self._cursor is None:
                if (rounds is not None and completed >= rounds) or (
                        self.budget is not None and self.budget.enforce(self)):
                    return
                if stop is not None and stop(self):
                    self.status = "stopped"
                    return
                if not self._keep_events:
                    forget(self)
                self.origin.round_start(self)
                self._cursor = self._round()
            elif self.status == "stopped":
                self.status = "running"
            for point in self._cursor:
                if point is WAITING:  # a turn waits for a decision: the run pauses here, its round kept
                    return
                self.origin.tape.points += 1
                if (self.budget is not None and self.budget.enforce(self)) or (stop is not None and stop(self)):
                    self.status = self.status if self.finished else "stopped"
                    return
            self._cursor = None
            completed += 1

    def _fail(self, message: str) -> None:
        if self._cursor is not None:
            self._cursor.close()
            self._cursor = None
        self.status, self.error = "failed", message

    # -- checks --------------------------------------------------------------------------------

    def _inspect_rule(self, type_name: str) -> Any:
        """The inspect rule for a type, inherited through `extends`."""
        return inspect_rule(self.contract, type_name)

    # -- helpers --------------------------------------------------------------------------------

    def _joined(self, entity: Entity) -> None:
        """An entity created during the run: an agent's news starts from its arrival."""
        if self.contract.is_agent(entity.entity_type):
            memory = self._memories[entity.id] = Memory()
            memory.cursor = self.world.log[-1].seq if self.world.log else 0

    def _memory(self, entity_id: str) -> Memory:
        memory = self._memories.get(entity_id)
        if memory is None:
            memory = self._memories[entity_id] = Memory()
        return memory

    def _brief(self, actor: Entity) -> str:
        brief = self._briefs.get(actor.id)
        if brief is None:
            attached: list[str] = []
            brief = self._briefs[actor.id] = self.perception.brief(actor, attached)
            if attached:
                self._brief_assets[actor.id] = attached
        return brief

    def _event_rows(self) -> list[dict[str, Any]]:
        """The log as plain data, each event converted once, so a result costs the same late in a run as early.
        Results share the converted events; the log only grows at its end or loses events a rollback undid, so the
        rows are rebuilt only when their last event is no longer where it was."""
        log, rows = self.world.log, self._rows
        if not self._keep_events:  # a snapshot's rows: converted for it alone, never kept
            return [event.to_dict() for event in log]
        if rows and (len(rows) > len(log) or log[len(rows) - 1] is not self._rows_last):
            rows.clear()
        rows.extend(event.to_dict() for event in log[len(rows):])
        self._rows_last = log[-1] if log else None
        return list(rows)

    def _flush_events(self) -> None:
        if self._on_event is None:
            self._emitted = len(self.world.log)
            return
        while self._emitted < len(self.world.log):
            event = self.world.log[self._emitted]
            self._emitted += 1
            self._on_event(event.to_dict())
