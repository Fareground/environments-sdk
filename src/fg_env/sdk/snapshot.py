"""Snapshots: a run between rounds as JSON-safe data, restored so it continues exactly.

Participant-text provenance survives the round trip: :class:`Untrusted` text is written as
``{"$untrusted": ...}``, and maps whose keys cannot be plain JSON keys (untrusted or non-text
keys) are written as ``{"$map": [[key, value], ...]}``.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Dict, Mapping, Tuple, Type, TypeVar

from ..entity import Entity
from .assets.store import AssetStore
from .budget import Budget
from .contract import Contract
from .errors import ContractError, SnapshotError
from .expr import Untrusted
from .exposure import ExposureLog
from .measure import Stats
from .world import Entry, LogEvent

if TYPE_CHECKING:
    from .runtime import Env

__all__ = ["SNAPSHOT_VERSION", "KEEP_ARM", "contract_hash", "run_identity", "encode", "decode", "take_snapshot", "restore_env",
           "restore_state", "matching_contract", "check_snapshot", "recording_start"]

SNAPSHOT_VERSION = 2

_E = TypeVar("_E", bound="Env")


def contract_hash(contract: Contract) -> str:
    text = json.dumps(contract.model_dump(by_alias=True, exclude_defaults=True), sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def run_identity(seed: Any, arm: Any, inputs: Any) -> str:
    """A fingerprint of what a run was started with (seed, arm, encoded inputs)."""
    text = json.dumps([seed, arm, inputs], sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class _KeepArm:
    def __repr__(self) -> str:
        return "KEEP_ARM"


#: A fork's default arm: the one the run already has.
KEEP_ARM: Any = _KeepArm()

_FORK_HINT = "to continue it under changes, use fg_env.fork(original_contract, snapshot, arm=..., inputs=..., patch=...)"


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
    inputs = encode(env.inputs)
    return {
        "fg_env_snapshot": SNAPSHOT_VERSION,
        "contract": contract_hash(env.contract),
        **_rule_origin(env),
        "run": run_identity(env.seed, env.arm, inputs),
        "seed": env.seed, "arm": env.arm, "inputs": inputs,
        "status": env.status, "ended_by": env.ended_by, "error": env.error,
        "round": w.round, "rounds": w.rounds,
        "entities": [{"id": e.id, "type": e.entity_type, "name": e.name, "props": encode(e.properties),
                      "alive": e.alive, "at": e.location_id} for e in w.entities.values()],
        "entity_briefs": encode(w.entity_briefs),
        "briefs": encode(env._briefs),
        "props": encode(w.props),
        "links": {kind: [[a, b, v, encode(w.link_fields[kind][(a, b)])] if (a, b) in w.link_fields[kind] else [a, b, v]
                         for (a, b), v in edges.items()] for kind, edges in w.links.items()},
        "records": {name: [encode(dict(row)) for row in rows] for name, rows in w.records_store.items()},
        "record_seq": w._record_seq,
        "log": [encode(e.to_dict()) for e in w.log], "seq": w._seq,
        "physics": w.physics.to_dict() if w.physics else None,
        "metrics": encode(w.metrics), "series": encode(w.series),
        "scheduled": [[due, order, encode(item)] for due, order, item in w.scheduled],
        "schedule_seq": w._schedule_seq,
        "wake_requests": encode(w.wake_requests),
        "time": w.time, "horizon": w.horizon, "wake_at": dict(w.wake_at),
        "counters": dict(w.counters), "end_request": encode(w.end_request),
        "fired_once": sorted(env._fired_once),
        "turn_count": env._turn_count,
        "triggers": {"armed": {str(k): v for k, v in env._trigger_armed.items()}, "fired": sorted(env._triggers_fired)},
        "memory": {k: {"cursor": m.cursor, "views": encode(m.views), "turns": m.turns} for k, m in env._memories.items()},
        "rng": [state[0], list(state[1]), state[2]],
        "stats": env.stats.to_dict(),
        "agent_stats": {key: env.agent_stats[key].to_dict() for key in sorted(env.agent_stats)},
        "exposures": w.exposures.to_dict() if w.exposures is not None else None,
        "layers": w.space.state() if w.space is not None else {},
        "frames": encode(env.previews.frames),
        "budget": env.budget.to_dict(env) if env.budget is not None else None,
        "start": env.origin.start,
        "diagnosis": env.diagnosis.to_dict(),
        **({"assets": {**w.assets.to_dict(), "briefs": dict(env._brief_assets)}} if len(w.assets) else {}),
    }


def recording_start(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    """``snapshot`` as the start of a recording: its exposure log given as counts, because the recording that
    continues from it holds those first entries (a result never carries its exposures twice)."""
    held = snapshot.get("exposures")
    counts = {"wakes": len(held["wakes"]), "chance": len(held.get("chance") or [])} if held is not None else None
    return {**{key: value for key, value in snapshot.items() if key != "start"}, "exposures": counts}


def matching_contract(contract: Any, snapshot: Mapping[str, Any]) -> Tuple[Contract, Contract]:
    """``(taken with, unarmed)``: the contract the snapshot was taken with — as given, or with the snapshot's
    arm applied — and that contract before its arm."""
    from .api import apply_arm, parse

    check_snapshot(snapshot)
    base = contract if isinstance(contract, Contract) else parse(contract)
    if snapshot.get("contract") == contract_hash(base):
        return base, _restore_rule_origin(snapshot, base)
    arm = snapshot.get("arm")
    if isinstance(arm, str) and arm in base.arms:
        try:
            patched = apply_arm(base, arm)
        except ContractError:
            patched = None
        if patched is not None and snapshot.get("contract") == contract_hash(patched):
            return patched, _restore_rule_origin(snapshot, base)
    if arm is not None and arm not in base.arms:
        raise SnapshotError(f"the snapshot's arm '{arm}' is not declared in this contract (arms: "
                            f"{', '.join(base.arms) or 'none'}); restore it into the contract it was taken with")
    raise SnapshotError("the snapshot was taken with a different contract (or this contract was changed since); "
                        f"restore continues a run exactly under its own contract — {_FORK_HINT}")



def _rule_origin(env: "Env") -> Dict[str, Any]:
    """Keep a different rule base only when future variant selection needs it."""
    from .api import contract_source

    base = env.origin.unarmed
    if base is env.contract:
        return {}
    fingerprint = contract_hash(base)
    if fingerprint == contract_hash(env.contract):
        return {}
    return {"rule_origin": {"hash": fingerprint, "source": contract_source(base)}}


def _restore_rule_origin(snapshot: Mapping[str, Any], fallback: Contract) -> Contract:
    """Optional provenance; old snapshots continue to use their supplied contract."""
    from .api import located
    from .check import parse_contract

    if "rule_origin" not in snapshot:
        return fallback
    held = snapshot["rule_origin"]
    if not isinstance(held, Mapping) or not isinstance(held.get("source"), Mapping) or not isinstance(held.get("hash"), str):
        raise SnapshotError("snapshot rule_origin must contain its original contract source and hash")
    try:
        # Sources are already import-resolved. Never read files named inside saved data.
        base = located(parse_contract(held["source"]), fallback._folder)
    except ContractError as exc:
        raise SnapshotError(f"snapshot rule_origin contains an invalid contract: {exc}") from None
    if contract_hash(base) != held["hash"]:
        raise SnapshotError("snapshot rule_origin contract was changed after it was saved")
    return base


def check_snapshot(snapshot: Any) -> None:
    """Refuse what is not a snapshot of this engine, or one whose seed, arm or inputs were edited."""
    if not isinstance(snapshot, Mapping):
        raise SnapshotError(f"a snapshot is a mapping (from env.snapshot()), got {type(snapshot).__name__}")
    version = snapshot.get("fg_env_snapshot")
    if version != SNAPSHOT_VERSION:
        raise SnapshotError(f"unsupported snapshot version {version!r} (this engine reads version {SNAPSHOT_VERSION})")
    if "run" in snapshot and snapshot["run"] != run_identity(snapshot.get("seed"), snapshot.get("arm"),
                                                             snapshot.get("inputs")):
        raise SnapshotError("the snapshot's seed, arm or inputs were changed after it was taken, so it no longer "
                            f"describes one run; restore it unedited — {_FORK_HINT}")


def restore_env(cls: Type[_E], contract: Any, snapshot: Mapping[str, Any], parallel: int = 8) -> _E:
    matched, unarmed = matching_contract(contract, snapshot)
    env = restore_state(cls, matched, snapshot, parallel)
    env.origin.base, env.origin.unarmed = dict(snapshot), unarmed  # copies of the run replay from here
    return env


def restore_state(cls: Type[_E], contract: Contract, snapshot: Mapping[str, Any], parallel: int = 8) -> _E:
    """A run rebuilt from a snapshot into ``contract``, which the caller has matched to it."""
    try:
        return _restore(cls, contract, snapshot, parallel)
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
    w.rebuild_index()
    if w.space is not None:
        w.space.restore(snapshot.get("layers") or {})
    w.props = decode(snapshot["props"])
    w.entity_briefs = decode(snapshot["entity_briefs"])
    env._briefs = decode(snapshot["briefs"])
    w.links = {kind: {(row[0], row[1]): row[2] for row in edges} for kind, edges in snapshot["links"].items()}
    w.link_fields = {kind: {(row[0], row[1]): decode(row[3]) for row in edges if len(row) > 3}
                     for kind, edges in snapshot["links"].items()}
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
    w.rebuild_record_index()
    w._record_seq = snapshot["record_seq"]
    w.log = []
    for raw in snapshot["log"]:
        e = decode(raw)
        w.log.append(LogEvent(e["seq"], e["round"], e["kind"], e.get("text", ""), e.get("actor"),
                              tuple(e["to"]) if e.get("to") is not None else None, e.get("data", {}), e.get("stage"),
                              e.get("time")))
    w.rebuild_event_index()
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
    w.reactions = []  # snapshots are taken between rounds, when no reaction is pending
    w.time, w.horizon = float(snapshot["time"]), snapshot.get("horizon")
    w.wake_at = {str(k): float(v) for k, v in snapshot["wake_at"].items()}
    w.counters = dict(snapshot["counters"])
    w.end_request = decode(snapshot.get("end_request"))
    w.round, w.rounds = snapshot["round"], snapshot["rounds"]
    w.stage = None
    state = snapshot["rng"]
    w.rng.setstate((state[0], tuple(state[1]), state[2]))
    env._fired_once = set(snapshot["fired_once"])
    env._turn_count = int(snapshot["turn_count"])
    env._trigger_armed = {int(k): bool(v) for k, v in snapshot["triggers"]["armed"].items()}
    env._triggers_fired = set(snapshot["triggers"]["fired"])
    for key, m in snapshot["memory"].items():
        memory = env._memory(key)
        memory.cursor, memory.views, memory.turns = m["cursor"], decode(m["views"]), m["turns"]
    status = snapshot["status"]
    env.status = status if status != "stopped" else ("running" if w.round else "ready")
    env.ended_by, env.error = snapshot.get("ended_by"), snapshot.get("error")
    for name in Stats.__dataclass_fields__:
        setattr(env.stats, name, snapshot["stats"].get(name, 0))
    env.agent_stats = {key: Stats(**{name: counts.get(name, 0) for name in Stats.__dataclass_fields__})
                       for key, counts in snapshot.get("agent_stats", {}).items()}
    if snapshot.get("exposures") is not None:
        w.exposures = ExposureLog.from_dict(snapshot["exposures"])
    env.previews.frames = decode(snapshot.get("frames") or [])
    env.origin.start = snapshot.get("start")
    if snapshot.get("assets"):
        w.assets = AssetStore.from_dict(snapshot["assets"])
        env._brief_assets = {key: list(ids) for key, ids in (snapshot["assets"].get("briefs") or {}).items()}
    if snapshot.get("budget") is not None:
        env.budget = Budget.from_dict(snapshot["budget"])
    env.diagnosis.load(snapshot.get("diagnosis"))
    w.journal.clear()
    w.touch()  # the state was replaced wholesale: nothing cached before holds
    env._emitted = len(w.log)
    return env
