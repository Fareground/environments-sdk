"""Snapshots: a run between rounds as JSON-safe data, restored so it continues exactly.

Participant-text provenance survives the round trip: :class:`Untrusted` text is written as
``{"$untrusted": ...}``, and maps whose keys cannot be plain JSON keys (untrusted or non-text
keys) are written as ``{"$map": [[key, value], ...]}``.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Dict, Mapping, Type, TypeVar

from ..entity import Entity
from .contract import Contract
from .errors import ContractError, SnapshotError
from .expr import Untrusted
from .measure import Stats
from .world import Entry, LogEvent

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["SNAPSHOT_VERSION", "contract_hash", "encode", "decode", "take_snapshot", "restore_env"]

SNAPSHOT_VERSION = 2

_E = TypeVar("_E", bound="Env")


def contract_hash(contract: Contract) -> str:
    text = json.dumps(contract.model_dump(by_alias=True, exclude_defaults=True), sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def encode(value: Any) -> Any:
    """A JSON-safe copy that keeps participant-text provenance."""
    if isinstance(value, Untrusted):
        return {"$untrusted": str.__str__(value)}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        if any(type(key) is not str for key in value) or "$untrusted" in value or "$map" in value:
            return {"$map": [[encode(k), encode(v)] for k, v in value.items()]}
        return {k: encode(v) for k, v in value.items()}
    return value


def decode(value: Any) -> Any:
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if len(value) == 1 and isinstance(value.get("$untrusted"), str):
            return Untrusted(value["$untrusted"])
        if len(value) == 1 and isinstance(value.get("$map"), list):
            return {_key(decode(k)): decode(v) for k, v in value["$map"]}
        return {k: decode(v) for k, v in value.items()}
    return value


def _key(value: Any) -> Any:
    return tuple(_key(v) for v in value) if isinstance(value, list) else value


def take_snapshot(env: "Env") -> Dict[str, Any]:
    w = env.world
    if env._in_round:
        if env.status == "failed":
            raise SnapshotError(f"the run failed during round {w.round}, so its state is incomplete; "
                                "use a snapshot taken before the failure")
        raise SnapshotError(f"the run is stopped in the middle of round {w.round}; snapshots are taken between "
                            "rounds — finish the round with env.run(rounds=1) first")
    state = w.rng.getstate()
    return {
        "fg_env_snapshot": SNAPSHOT_VERSION,
        "contract": contract_hash(env.contract),
        "seed": env.seed, "arm": env.arm, "inputs": encode(env.inputs),
        "status": env.status, "ended_by": env.ended_by, "error": env.error,
        "round": w.round, "rounds": w.rounds,
        "entities": [{"id": e.id, "type": e.entity_type, "name": e.name, "props": encode(e.properties),
                      "alive": e.alive, "at": e.location_id} for e in w.entities.values()],
        "entity_briefs": encode(w.entity_briefs),
        "briefs": encode(env._briefs),
        "props": encode(w.props),
        "links": {kind: [[a, b, v] for (a, b), v in edges.items()] for kind, edges in w.links.items()},
        "records": {name: [encode(dict(row)) for row in rows] for name, rows in w.records_store.items()},
        "record_seq": w._record_seq,
        "log": [encode(e.to_dict()) for e in w.log], "seq": w._seq,
        "physics": w.physics.to_dict() if w.physics else None,
        "metrics": encode(w.metrics), "series": encode(w.series),
        "scheduled": [[due, order, encode(item)] for due, order, item in w.scheduled],
        "schedule_seq": w._schedule_seq,
        "wake_requests": encode(w.wake_requests),
        "counters": dict(w.counters), "end_request": encode(w.end_request),
        "fired_once": sorted(env._fired_once),
        "turn_count": env._turn_count,
        "memory": {k: {"cursor": m.cursor, "views": encode(m.views), "turns": m.turns} for k, m in env._memories.items()},
        "rng": [state[0], list(state[1]), state[2]],
        "stats": env.stats.to_dict(),
    }


def _matching_contract(contract: Any, snapshot: Mapping[str, Any]) -> Contract:
    """The contract the snapshot was taken with: as given, or with the snapshot's arm applied."""
    from .api import apply_arm, parse

    base = contract if isinstance(contract, Contract) else parse(contract)
    if snapshot.get("contract") == contract_hash(base):
        return base
    arm = snapshot.get("arm")
    if isinstance(arm, str) and arm in base.arms:
        try:
            patched = apply_arm(base, arm)
        except ContractError:
            patched = None
        if patched is not None and snapshot.get("contract") == contract_hash(patched):
            return patched
    raise SnapshotError("the snapshot was taken with a different contract")


def restore_env(cls: Type[_E], contract: Any, snapshot: Mapping[str, Any], parallel: int = 8) -> _E:
    if not isinstance(snapshot, Mapping):
        raise SnapshotError(f"a snapshot is a mapping (from env.snapshot()), got {type(snapshot).__name__}")
    version = snapshot.get("fg_env_snapshot")
    if version != SNAPSHOT_VERSION:
        raise SnapshotError(f"unsupported snapshot version {version!r} (this engine reads version {SNAPSHOT_VERSION})")
    matched = _matching_contract(contract, snapshot)
    try:
        return _restore(cls, matched, snapshot, parallel)
    except SnapshotError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise SnapshotError(f"the snapshot is incomplete or corrupted ({type(exc).__name__}: {exc})") from None


def _restore(cls: Type[_E], contract: Contract, snapshot: Mapping[str, Any], parallel: int) -> _E:
    from ..physics import PhysicsModel

    env = cls(contract, decode(snapshot["inputs"]), int(snapshot["seed"]), snapshot.get("arm"), parallel)
    w = env.world
    w.entities = {}
    for row in snapshot["entities"]:
        w.entities[row["id"]] = Entity(id=row["id"], name=row["name"], entity_type=row["type"],
                                       properties=decode(row["props"]), location_id=row.get("at"),
                                       alive=row["alive"])
    w.props = decode(snapshot["props"])
    w.entity_briefs = decode(snapshot["entity_briefs"])
    env._briefs = decode(snapshot["briefs"])
    w.links = {kind: {(a, b): v for a, b, v in edges} for kind, edges in snapshot["links"].items()}
    w.rebuild_adjacency()
    w.records_store = {}
    w.entry_by_seq = {}
    for name, rows in snapshot["records"].items():
        entries = []
        for row in rows:
            entry = Entry(decode(row))
            entry.world = w
            entries.append(entry)
            w.entry_by_seq[entry["seq"]] = entry
        w.records_store[name] = entries
    w._record_seq = snapshot["record_seq"]
    w.log = []
    for raw in snapshot["log"]:
        e = decode(raw)
        w.log.append(LogEvent(e["seq"], e["round"], e["kind"], e.get("text", ""), e.get("actor"),
                              tuple(e["to"]) if e.get("to") is not None else None, e.get("data", {}), e.get("stage")))
    w._seq = snapshot["seq"]
    if snapshot.get("physics") and w.physics is not None:
        restored = PhysicsModel.from_dict(snapshot["physics"])
        w.physics.params, w.physics.time = restored.params, restored.time
        for name, var in restored.variables.items():
            w.physics.variables[name].value = var.value
    w.metrics = decode(snapshot["metrics"])
    w.series = decode(snapshot["series"])
    w.scheduled = [(due, order, decode(item)) for due, order, item in snapshot["scheduled"]]
    w._schedule_seq = snapshot["schedule_seq"]
    w.wake_requests = decode(snapshot["wake_requests"])
    w.counters = dict(snapshot["counters"])
    w.end_request = decode(snapshot.get("end_request"))
    w.round, w.rounds = snapshot["round"], snapshot["rounds"]
    w.stage = None
    state = snapshot["rng"]
    w.rng.setstate((state[0], tuple(state[1]), state[2]))
    env._fired_once = set(snapshot["fired_once"])
    env._turn_count = int(snapshot["turn_count"])
    for key, m in snapshot["memory"].items():
        memory = env._memory(key)
        memory.cursor, memory.views, memory.turns = m["cursor"], decode(m["views"]), m["turns"]
    status = snapshot["status"]
    env.status = status if status != "stopped" else ("running" if w.round else "ready")
    env.ended_by, env.error = snapshot.get("ended_by"), snapshot.get("error")
    for name in Stats.__dataclass_fields__:
        setattr(env.stats, name, snapshot["stats"].get(name, 0))
    w.journal.clear()
    w.touch()  # the state was replaced wholesale: nothing cached before holds
    env._emitted = len(w.log)
    return env
