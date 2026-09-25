"""A run's state in one value: the world and the bookkeeping the run keeps beside it, with one canonical form.

:class:`RunState` holds everything a run changes as it plays that is not configuration or a service: the world — its
store and the rules' bookkeeping journaled with it (the events that fired or are armed, each agent's uses of each
action this round) — and beside it turn numbers, what the engine remembers of each agent, the briefs, the statistics,
the run's status and budget, where the round in progress is (:class:`Cursor`) and the turns being played there.

:meth:`RunState.copy` is the one way a run is copied — a clone, a fork's start, a preview, a game state, a copy taken
inside a turn: the copy's parts are built afresh around the copied state (they hold none of their own), and a round in
progress resumes from the cursor. :meth:`RunState.encode` is its canonical form — JSON-safe data, what a snapshot
stores and what a copy must reproduce — and :meth:`RunState.decode` puts it back into a freshly built run.

What an undo brings back and what it does not is one rule, :data:`UNDONE`: everything the world journals comes back
(see world/journal.py); what is spent for good (luck drawn — the random stream and each site's ``firings`` — and turn
numbers) and what only records the run (memories, briefs, statistics, exposures, metrics) do not.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..assets.store import AssetStore
from ..errors import RunError, SnapshotError
from ..expr.objects import Entity
from ..information.exposure import ExposureLog
from ..world.abort import Abort
from ..world.copies import copy_world
from ..world.parts import Entry, LogEvent
from .diagnosis import Diagnosis
from .facts import Stats

if TYPE_CHECKING:
    from ..world.store import World
    from .budget import Budget
    from .env import Env
    from .turn import Turn

__all__ = ["Cursor", "Memory", "RunState", "UNDONE"]

#: The parts of :meth:`RunState.encode` an undo (a refused action, an atomic turn that is not allowed, a rolled-back
#: trial) brings back as they were. The rest is spent for good or only records the run.
UNDONE = frozenset({
    "round", "stage", "entities", "entity_briefs", "props", "links", "records", "record_seq", "log", "seq", "physics",
    "scheduled", "schedule_seq", "wake_requests", "reactions", "counters", "end_request", "layers", "fired_once",
    "armed", "used_round"})


class Memory:
    """What the engine remembers per agent between turns: where its news starts, how many turns it had, and what its
    last action of its last turn returned (its next update opens with it)."""

    __slots__ = ("cursor", "turns", "last")

    def __init__(self) -> None:
        self.cursor = 0
        self.turns = 0
        self.last: str | None = None

    def copy(self) -> Memory:
        memory = Memory()
        memory.cursor, memory.turns, memory.last = self.cursor, self.turns, self.last
        return memory


#: Where in a stage the round in progress is (:attr:`Cursor.at`).
POINTS = ("stage", "pass", "turn", "turns", "playing")


@dataclass
class Cursor:
    """Where the round in progress is, kept current as the schedule plays it: the round resumes from it alone — in a
    copy of the run, or in a run restored from a snapshot taken part-way through the round (see
    :mod:`fg_env.runtime.schedule`)."""

    #: The stage being played (its index among the contract's stages).
    stage: int = 0
    #: Where in it: at the safe point before it starts (``stage``), before its pass :attr:`pass_index` chooses its
    #: agents (``pass``), before the sequential turn at :attr:`position` or the pass's sealed turns, each agent told its
    #: :attr:`reasons` (``turn``, ``turns``), or playing them (``playing``: :attr:`turn`, or the run's
    #: :attr:`RunState.staged`).
    at: str = "stage"
    pass_index: int = 0
    #: The agents of the pass being played, in turn order.
    agents: list[Entity] = field(default_factory=list)
    #: The sequential turn's place in ``agents``.
    position: int = 0
    #: Why each agent about to be woken is woken.
    reasons: dict[str, str] = field(default_factory=dict)
    #: The sequential turn being played.
    turn: Turn | None = None
    #: The number of the turn a copy was taken waiting in: the turns of the stage already started continue only there
    #: (see :meth:`~fg_env.runtime.driving.Driver.drive_steps`).
    waiting: int | None = None

    def copy(self, entities: Mapping[str, Entity]) -> Cursor:
        """This cursor over the copied ``entities`` (the turn in play is copied with the run's turns)."""
        return Cursor(self.stage, self.at, self.pass_index, [entities[agent.id] for agent in self.agents],
                      self.position, dict(self.reasons), self.turn, self.waiting)

    def encode(self) -> dict[str, Any]:
        """The cursor as data (the turns in play are not: a run is saved at a safe point, between them)."""
        return {"stage": self.stage, "at": self.at, "pass": self.pass_index, "agents": [a.id for a in self.agents],
                "position": self.position, "reasons": dict(self.reasons)}

    @classmethod
    def decode(cls, data: Mapping[str, Any], entities: Mapping[str, Entity]) -> Cursor:
        at = data["at"]
        if at not in POINTS or at == "playing":
            raise SnapshotError(f"the snapshot's round is at an unknown point {at!r}")
        return cls(int(data["stage"]), at, int(data["pass"]), [entities[agent] for agent in data["agents"]],
                   int(data["position"]), {str(k): str(v) for k, v in data["reasons"].items()})


class RunState:
    """Everything a run changes as it plays (see the module docstring). ``keep_events``: results carry the event
    log, whose rows are then converted once each (:meth:`event_rows`)."""

    def __init__(self, world: World, keep_events: bool = True):
        self.world = world
        self.keep_events = keep_events
        #: Whether the run forgets the events no agent's news can reach any more: it keeps no event log and its rules
        #: read none older than an agent's news (see runtime/forgetting.py).
        self.forgets = False
        #: Turns numbered so far (numbers are assigned in a fixed order, before any turn runs concurrently).
        self.turn_count = 0
        self.memories: dict[str, Memory] = {}
        #: Each agent's brief, rendered once, and the assets it attaches.
        self.briefs: dict[str, str] = {}
        self.brief_assets: dict[str, list[str]] = {}
        #: How the run stands: ready, running, stopped (at a safe point), or finished — completed, ended or failed, the
        #: end's name (``ended_by``) or the error that failed it.
        self.status = "ready"
        self.ended_by: str | None = None
        self.error: str | None = None
        #: The run's budget, and what it has counted (see runtime/budget.py).
        self.budget: Budget | None = None
        #: Whether a round is being played, where in it the run is, and the sealed turns of the stage being played.
        self.in_round = False
        self.cursor = Cursor()
        self.staged: list[Turn] = []
        #: How many of the log's events have been handed to the run's ``on_event`` callback.
        self.emitted = 0
        #: Spectator views rendered at the end of every round, the last one marked final (see information/core.py).
        self.frames: list[dict[str, Any]] = []
        #: The run's statistics and what it notices about its own rules: folds over its facts (see runtime/facts.py).
        self.stats = Stats()
        #: The same numbers per agent entity id (a tournament bills each entrant for its own turns).
        self.agent_stats: dict[str, Stats] = {}
        #: Saved in snapshots beside the canonical form (see copying/snapshot.py), so a resumed run diagnoses exactly
        #: what a straight run does.
        self.diagnosis = Diagnosis(world.written)
        #: A cache, not state: the world state each invariant was last found to hold in (see runtime/rules.py).
        self.invariant_held: dict[int, Any] = {}
        #: A cache, not state: the log as plain data, converted once per event (see :meth:`event_rows`).
        self.rows: list[dict[str, Any]] = []
        self.rows_last: Any = None

    def copy(self) -> RunState:
        """A copy of the state that shares nothing that changes (see the module docstring): the world
        (:func:`~fg_env.world.copies.copy_world`) and every piece of bookkeeping beside it. Its turns in play are copied
        by :meth:`adopt`, into the run built around the copy; caches start empty."""
        world = copy_world(self.world)
        state = RunState.__new__(RunState)
        state.__dict__.update(
            world=world, keep_events=self.keep_events, forgets=self.forgets, turn_count=self.turn_count,
            memories={key: memory.copy() for key, memory in self.memories.items()}, briefs=dict(self.briefs),
            brief_assets={key: list(ids) for key, ids in self.brief_assets.items()},
            status=self.status, ended_by=self.ended_by, error=self.error,
            budget=None if self.budget is None else self.budget.copy(), in_round=self.in_round,
            cursor=self.cursor.copy(world.entities), staged=list(self.staged), emitted=self.emitted,
            frames=list(self.frames), stats=self.stats.copy(),
            agent_stats={key: stats.copy() for key, stats in self.agent_stats.items()},
            diagnosis=self.diagnosis.copy(world.written), invariant_held={}, rows=list(self.rows),
            rows_last=self.rows_last)
        return state

    def adopt(self, env: Env) -> None:
        """Copy the turns in play into ``env``, the run just built around this copied state."""
        self.staged = [turn.copy(env) for turn in self.staged]
        if self.cursor.turn is not None:
            self.cursor.turn = self.cursor.turn.copy(env)

    def memory(self, entity_id: str) -> Memory:
        """What the engine remembers of ``entity_id``, from now on."""
        memory = self.memories.get(entity_id)
        if memory is None:
            memory = self.memories[entity_id] = Memory()
        return memory

    def event_rows(self) -> list[dict[str, Any]]:
        """The log as plain data, each event converted once, so a result costs the same late in a run as early.
        Results share the converted events; the log only grows at its end or loses events a rollback undid, so the
        rows are rebuilt only when their last event is no longer where it was."""
        log, rows = self.world.log, self.rows
        if not self.keep_events:  # a snapshot's rows: converted for it alone, never kept
            return [event.to_dict() for event in log]
        if rows and (len(rows) > len(log) or log[len(rows) - 1] is not self.rows_last):
            rows.clear()
        rows.extend(event.to_dict() for event in log[len(rows):])
        self.rows_last = log[-1] if log else None
        return list(rows)

    # -- the canonical form --------------------------------------------------------------------------------------

    def encode(self) -> dict[str, Any]:
        """The state as JSON-safe data: between rounds, or at a safe point part-way through one (:attr:`cursor`)."""
        from ..copying.snapshot import encode

        w = self.world
        rng = w.luck.main.getstate()  # the main stream itself (reading `rng` counts as a draw)
        return {
            "round": w.round, "rounds": w.rounds, "stage": w.stage,
            "entities": [{"id": e.id, "type": e.entity_type, "name": e.name, "props": encode(e.properties),
                          "alive": e.alive, "at": e.location_id, **({"luck": e.luck} if e.luck else {})}
                         for e in w.entities.values()],
            "entity_briefs": encode(w.entity_briefs),
            "briefs": encode(self.briefs),
            "props": encode(w.props),
            "links": {kind: [[a, b, v, encode(w.link_fields[kind][(a, b)])] if (a, b) in w.link_fields[kind]
                             else [a, b, v] for (a, b), v in edges.items()] for kind, edges in w.links.items()},
            "records": {name: [encode(dict(row)) for row in rows] for name, rows in w.records_store.items()},
            "record_seq": w.record_seq,
            "log": [encode(row) for row in self.event_rows()], "seq": w.event_seq,
            "physics": w.physics.to_dict() if w.physics else None,
            "metrics": encode(w.metrics), "series": encode(w.series),
            "scheduled": [[due, order, encode(item)] for due, order, item in w.scheduled],
            "schedule_seq": w.schedule_seq,
            "wake_requests": encode(w.wake_requests),
            "reactions": encode(w.reactions),
            "counters": dict(w.counters), "firings": dict(w.luck.firings),
            "births": dict(w.luck.births), "end_request": encode(w.end_request),
            "fired_once": sorted(w.fired_once),
            "turn_count": self.turn_count,
            "armed": {str(k): v for k, v in w.armed.items()},
            "used_round": {actor: dict(used) for actor, used in w.used_round.items()},
            "memory": {k: {"cursor": m.cursor, "turns": m.turns, **({"last": m.last} if m.last else {})}
                       for k, m in self.memories.items()},
            "rng": [rng[0], list(rng[1]), rng[2]],
            "stats": self.stats.to_dict(),
            "agent_stats": {key: self.agent_stats[key].to_dict() for key in sorted(self.agent_stats)},
            "exposures": w.exposures.to_dict() if w.exposures is not None else None,
            "layers": w.space.state() if w.space is not None else {},
            **({"assets": {**w.assets.to_dict(), "briefs": dict(self.brief_assets)}} if len(w.assets) else {}),
            **({"cursor": self.cursor.encode()} if self.in_round else {}),
        }

    def decode(self, data: Mapping[str, Any]) -> None:
        """Put :meth:`encode`'s data back into this state, which belongs to a run freshly built from the same
        contract, inputs and seed. Every property is checked against its declaration, so data edited by hand or
        damaged is refused (:class:`~fg_env.errors.SnapshotError`) here, not wherever the run next reads it."""
        from ..copying.snapshot import decode
        from ..physics.model import PhysicsModel

        w = self.world
        w.entities = {}
        for row in data["entities"]:
            w.entities[row["id"]] = Entity(id=row["id"], name=row["name"], entity_type=row["type"],
                                           properties=decode(row["props"]), location_id=row.get("at"),
                                           alive=row["alive"], luck=row.get("luck"))
        w.rebuild_index()
        if w.space is not None:
            w.space.restore(data.get("layers") or {})
        w.props = decode(data["props"])
        w.entity_briefs = decode(data["entity_briefs"])
        self.briefs = decode(data["briefs"])
        w.links = {kind: {(row[0], row[1]): row[2] for row in edges} for kind, edges in data["links"].items()}
        w.link_fields = {kind: {(row[0], row[1]): decode(row[3]) for row in edges if len(row) > 3}
                         for kind, edges in data["links"].items()}
        w.rebuild_adjacency()
        w.records_store = {}
        w.entry_by_seq = {}
        for name, rows in data["records"].items():
            entries = []
            for row in rows:
                entry = Entry(decode(row))
                entry.world = w
                entries.append(entry)
                w.entry_by_seq[entry["seq"]] = entry
            w.records_store[name] = entries
        w.rebuild_record_index()
        w.record_seq = data["record_seq"]
        w.log = []
        for raw in data["log"]:
            e = decode(raw)
            w.log.append(LogEvent(e["seq"], e["round"], e["kind"], e.get("text", ""), e.get("actor"),
                                  tuple(e["to"]) if e.get("to") is not None else None, e.get("data", {}),
                                  e.get("stage")))
        w.rebuild_event_index()
        w.event_seq = data["seq"]
        if data.get("physics") and w.physics is not None:
            restored = PhysicsModel.from_dict(data["physics"])
            w.physics.params, w.physics.time = restored.params, restored.time
            for name, var in restored.variables.items():
                w.physics.variables[name].value = var.value
        w.metrics = decode(data["metrics"])
        w.series = decode(data["series"])
        w.scheduled = [(due, order, decode(item)) for due, order, item in data["scheduled"]]
        w.schedule_seq = data["schedule_seq"]
        w.wake_requests = decode(data["wake_requests"])
        w.reactions = [(entity_id, why, actions) for entity_id, why, actions in decode(data.get("reactions") or [])]
        w.counters = dict(data["counters"])
        w.luck.firings = {str(k): int(v) for k, v in data["firings"].items()}
        w.luck.births = {str(k): int(v) for k, v in (data.get("births") or {}).items()}
        w.end_request = decode(data.get("end_request"))
        w.round, w.rounds, w.stage = data["round"], data["rounds"], data.get("stage")
        state = data["rng"]
        w.luck.main.setstate((state[0], tuple(state[1]), state[2]))
        w.fired_once = set(data["fired_once"])
        self.turn_count = int(data["turn_count"])
        w.armed = {int(k): bool(v) for k, v in data["armed"].items()}
        w.used_round = {str(actor): {str(name): int(n) for name, n in used.items()}
                        for actor, used in (data.get("used_round") or {}).items()}
        for key, m in data["memory"].items():
            memory = self.memory(key)
            memory.cursor, memory.turns, memory.last = m["cursor"], m["turns"], m.get("last")
        for name in Stats.__dataclass_fields__:
            setattr(self.stats, name, data["stats"].get(name, 0))
        self.agent_stats = {key: Stats(**{name: counts.get(name, 0) for name in Stats.__dataclass_fields__})
                            for key, counts in data.get("agent_stats", {}).items()}
        if data.get("exposures") is not None:
            w.exposures = ExposureLog.from_dict(data["exposures"])
        if data.get("assets"):
            w.assets = AssetStore.from_dict(data["assets"])
            self.brief_assets = {key: list(ids) for key, ids in (data["assets"].get("briefs") or {}).items()}
        if data.get("cursor") is not None:
            self.in_round, self.cursor = True, Cursor.decode(data["cursor"], w.entities)
        _check_props(w)
        w.commit()
        w.touch()  # the state was replaced wholesale: nothing cached before holds


def _check_props(w: World) -> None:
    """Every restored property as its declaration stores it — type, values and bounds."""
    def checked(spec: Any, value: Any, where: str, owner: str = "") -> Any:
        try:
            return w.coerce(spec, value, where, owner)
        except RunError as exc:
            problem = str(exc)
        except Abort as exc:  # out of bounds
            problem = f"{where}: {exc.reason.rstrip('.')}"
        raise SnapshotError(f"the snapshot holds a value its contract does not allow ({problem}); restore an unedited "
                            "snapshot")

    for entity in w.entities.values():
        for prop, spec in w.type_props.get(entity.entity_type, {}).items():
            if prop in entity.properties:
                entity.properties[prop] = checked(spec, entity.properties[prop], f"entities.{entity.id}.props.{prop}",
                                                  entity.name)
    for prop, spec in w.contract.world.items():
        if prop in w.props:
            w.props[prop] = checked(spec, w.props[prop], f"world.{prop}")
