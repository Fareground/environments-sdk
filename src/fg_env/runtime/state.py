"""A run's state in one value: the world and the bookkeeping the run keeps beside it, with one canonical form.

:class:`RunState` holds everything a run changes as it plays that is not configuration or a service: the world — its
store and the rules' bookkeeping journaled with it (the events that fired or are armed, each agent's uses of each
action this round) — and beside it turn numbers, what the engine remembers of each agent, the briefs, the statistics,
and where the round in progress is. :meth:`RunState.encode` is its canonical form — JSON-safe data, what a snapshot
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
from ..world.live import Abort, Entry, LogEvent
from .exposure import ExposureLog
from .measure import Stats

if TYPE_CHECKING:
    from ..world.live import SdkWorld
    from .turn import Turn

__all__ = ["Memory", "RunState", "UNDONE", "Where"]

#: The parts of :meth:`RunState.encode` an undo (a refused action, an atomic turn that is not allowed, a rolled-back
#: trial) brings back as they were. The rest is spent for good or only records the run.
UNDONE = frozenset({
    "round", "stage", "entities", "entity_briefs", "props", "links", "records", "record_seq", "log", "seq", "physics",
    "scheduled", "schedule_seq", "wake_requests", "reactions", "counters", "end_request", "layers", "fired_once",
    "armed", "used_round"})


class Memory:
    """What the engine remembers per agent between turns: where its news starts, and how many turns it had."""

    __slots__ = ("cursor", "turns")

    def __init__(self) -> None:
        self.cursor = 0
        self.turns = 0


@dataclass
class Where:
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


class RunState:
    """Everything a run changes as it plays (see the module docstring). ``keep_events``: results carry the event
    log, whose rows are then converted once each (:meth:`event_rows`)."""

    def __init__(self, world: SdkWorld, keep_events: bool = True):
        self.world = world
        self.keep_events = keep_events
        #: Turns numbered so far (numbers are assigned in a fixed order, before any turn runs concurrently).
        self.turn_count = 0
        self.memories: dict[str, Memory] = {}
        #: Each agent's brief, rendered once, and the assets it attaches.
        self.briefs: dict[str, str] = {}
        self.brief_assets: dict[str, list[str]] = {}
        #: Whether a round is being played, and where in it the run is.
        self.in_round = False
        self.where = Where()
        self.stats = Stats()
        #: The same numbers per agent entity id (a tournament bills each entrant for its own turns).
        self.agent_stats: dict[str, Stats] = {}
        #: A cache, not state: the world state each invariant was last found to hold in (see runtime/rules.py).
        self.invariant_held: dict[int, Any] = {}
        #: A cache, not state: the log as plain data, converted once per event (see :meth:`event_rows`).
        self.rows: list[dict[str, Any]] = []
        self.rows_last: Any = None

    def memory(self, entity_id: str) -> Memory:
        """What the engine remembers of ``entity_id``, from now on."""
        memory = self.memories.get(entity_id)
        if memory is None:
            memory = self.memories[entity_id] = Memory()
        return memory

    def tally(self, actor_id: str, stats: Stats) -> None:
        """Add numbers to the run's totals and to the agent's own (callers hold the run's lock)."""
        self.stats.add(stats)
        self.agent_stats.setdefault(actor_id, Stats()).add(stats)

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
        """The state as JSON-safe data. The round in progress is not in it (:attr:`where` holds live turns): a run
        stopped part-way through a round is saved as the snapshot it replays from (see copying/snapshot.py)."""
        from ..copying.snapshot import encode

        w = self.world
        rng = w.luck.main.getstate()  # the main stream itself (reading `rng` counts as a draw)
        return {
            "round": w.round, "rounds": w.rounds, "stage": w.stage,
            "entities": [{"id": e.id, "type": e.entity_type, "name": e.name, "props": encode(e.properties),
                          "alive": e.alive, "at": e.location_id} for e in w.entities.values()],
            "entity_briefs": encode(w.entity_briefs),
            "briefs": encode(self.briefs),
            "props": encode(w.props),
            "links": {kind: [[a, b, v, encode(w.link_fields[kind][(a, b)])] if (a, b) in w.link_fields[kind]
                             else [a, b, v] for (a, b), v in edges.items()] for kind, edges in w.links.items()},
            "records": {name: [encode(dict(row)) for row in rows] for name, rows in w.records_store.items()},
            "record_seq": w._record_seq,
            "log": [encode(row) for row in self.event_rows()], "seq": w._seq,
            "physics": w.physics.to_dict() if w.physics else None,
            "metrics": encode(w.metrics), "series": encode(w.series),
            "scheduled": [[due, order, encode(item)] for due, order, item in w.scheduled],
            "schedule_seq": w._schedule_seq,
            "wake_requests": encode(w.wake_requests),
            "reactions": encode(w.reactions),
            "counters": dict(w.counters), "firings": dict(w.luck.firings), "end_request": encode(w.end_request),
            "fired_once": sorted(w.fired_once),
            "turn_count": self.turn_count,
            "armed": {str(k): v for k, v in w.armed.items()},
            "used_round": {actor: dict(used) for actor, used in w.used_round.items()},
            "memory": {k: {"cursor": m.cursor, "turns": m.turns} for k, m in self.memories.items()},
            "rng": [rng[0], list(rng[1]), rng[2]],
            "stats": self.stats.to_dict(),
            "agent_stats": {key: self.agent_stats[key].to_dict() for key in sorted(self.agent_stats)},
            "exposures": w.exposures.to_dict() if w.exposures is not None else None,
            "layers": w.space.state() if w.space is not None else {},
            **({"assets": {**w.assets.to_dict(), "briefs": dict(self.brief_assets)}} if len(w.assets) else {}),
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
                                           alive=row["alive"])
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
        w._record_seq = data["record_seq"]
        w.log = []
        for raw in data["log"]:
            e = decode(raw)
            w.log.append(LogEvent(e["seq"], e["round"], e["kind"], e.get("text", ""), e.get("actor"),
                                  tuple(e["to"]) if e.get("to") is not None else None, e.get("data", {}),
                                  e.get("stage")))
        w.rebuild_event_index()
        w._seq = data["seq"]
        if data.get("physics") and w.physics is not None:
            restored = PhysicsModel.from_dict(data["physics"])
            w.physics.params, w.physics.time = restored.params, restored.time
            for name, var in restored.variables.items():
                w.physics.variables[name].value = var.value
        w.metrics = decode(data["metrics"])
        w.series = decode(data["series"])
        w.scheduled = [(due, order, decode(item)) for due, order, item in data["scheduled"]]
        w._schedule_seq = data["schedule_seq"]
        w.wake_requests = decode(data["wake_requests"])
        w.reactions = [(entity_id, why, actions) for entity_id, why, actions in decode(data.get("reactions") or [])]
        w.counters = dict(data["counters"])
        w.luck.firings = {str(k): int(v) for k, v in data["firings"].items()}
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
            memory.cursor, memory.turns = m["cursor"], m["turns"]
        for name in Stats.__dataclass_fields__:
            setattr(self.stats, name, data["stats"].get(name, 0))
        self.agent_stats = {key: Stats(**{name: counts.get(name, 0) for name in Stats.__dataclass_fields__})
                            for key, counts in data.get("agent_stats", {}).items()}
        if data.get("exposures") is not None:
            w.exposures = ExposureLog.from_dict(data["exposures"])
        if data.get("assets"):
            w.assets = AssetStore.from_dict(data["assets"])
            self.brief_assets = {key: list(ids) for key, ids in (data["assets"].get("briefs") or {}).items()}
        _check_props(w)
        w.journal.clear()
        w.touch()  # the state was replaced wholesale: nothing cached before holds


def _check_props(w: SdkWorld) -> None:
    """Every restored property as its declaration stores it — type, values and bounds."""
    def checked(spec: Any, value: Any, where: str, owner: str = "") -> Any:
        try:
            return w._coerce(spec, value, where, owner)
        except RunError as exc:
            problem = str(exc)
        except Abort as exc:  # out of bounds
            problem = f"{where}: {exc.reason.rstrip('.')}"
        raise SnapshotError(f"the snapshot holds a value its contract does not allow ({problem}); restore an unedited "
                            "snapshot")

    for entity in w.entities.values():
        for prop, spec in w._type_props.get(entity.entity_type, {}).items():
            if prop in entity.properties:
                entity.properties[prop] = checked(spec, entity.properties[prop], f"entities.{entity.id}.props.{prop}",
                                                  entity.name)
    for prop, spec in w.contract.world.items():
        if prop in w.props:
            w.props[prop] = checked(spec, w.props[prop], f"world.{prop}")
