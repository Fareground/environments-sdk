"""Snapshots: a run as JSON-safe data — its state's canonical form (``RunState.encode``) — restored so it continues
exactly.

A run is saved between rounds, or stopped at a safe point part-way through one (``env.run(stop=...)``): then the
snapshot holds where the round is (its cursor), and the restored run resumes the round there. Snapshots of the previous
format are still read (:mod:`.legacy_snapshot`).

Participant-text provenance survives the round trip: :class:`Untrusted` text is written as
``{"$untrusted": ...}``, and maps whose keys cannot be plain JSON keys (untrusted or non-text
keys) are written as ``{"$map": [[key, value], ...]}``.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from ..contract import Contract
from ..errors import ContractError, Issue, RunError, SnapshotError
from ..expr import Untrusted
from ..runtime.budget import Budget
from ..sampling.seeds import SeedTree
from ..world.store import World

if TYPE_CHECKING:
    from ..contract import PropSpec
    from ..runtime.env import Env

__all__ = ["SNAPSHOT_VERSION", "KEEP_ARM", "contract_hash", "run_identity", "encode", "decode", "take_snapshot",
           "restore_env", "restore_state", "matching_contract", "check_snapshot", "recording_start", "held_values",
           "refused_values"]

SNAPSHOT_VERSION = 5

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

_FORK_HINT = ("to continue it under changes, use fg_env.fork(original_contract, snapshot, arm=..., inputs=..., "
              "patch=...)")


#: Values encoded and decoded as they are (a subclass, like Untrusted text, is not one of them). Plain values are
#: passed over without a call: a long run's log is mostly them.
_PLAIN = frozenset({str, int, float, bool, type(None)})


def encode(value: Any) -> Any:
    """A JSON-safe copy that keeps participant-text provenance."""
    if type(value) in _PLAIN:
        return value
    if isinstance(value, Untrusted):
        return {"$untrusted": str.__str__(value)}
    if isinstance(value, (list, tuple)):
        return [v if type(v) in _PLAIN else encode(v) for v in value]
    if isinstance(value, dict):
        if any(type(key) is not str for key in value) or "$untrusted" in value or "$map" in value:
            return {"$map": [[encode(k), encode(v)] for k, v in value.items()]}
        return {k: v if type(v) in _PLAIN else encode(v) for k, v in value.items()}
    return value


def decode(value: Any) -> Any:
    if type(value) in _PLAIN:
        return value
    if isinstance(value, list):
        return [v if type(v) in _PLAIN else decode(v) for v in value]
    if isinstance(value, dict):
        if len(value) == 1 and isinstance(value.get("$untrusted"), str):
            return Untrusted(value["$untrusted"])
        if len(value) == 1 and isinstance(value.get("$map"), list):
            return {_key(decode(k)): decode(v) for k, v in value["$map"]}
        return {k: v if type(v) in _PLAIN else decode(v) for k, v in value.items()}
    return value


def _key(value: Any) -> Any:
    return tuple(_key(v) for v in value) if isinstance(value, list) else value


def take_snapshot(env: Env) -> dict[str, Any]:
    w = env.world
    if env.state.in_round:
        if env.status == "failed":
            raise SnapshotError(f"the run failed during round {w.round}, so its state is incomplete; "
                                "use a snapshot taken before the failure")
        if env.status != "stopped":
            raise SnapshotError(f"round {w.round} is being played right now; stop the run at a safe point first "
                                "(env.run(stop=...)), or take the snapshot between rounds")
    return {
        **_identity(env),
        "status": env.status, "ended_by": env.ended_by, "error": env.error,
        **env.state.encode(),
        "frames": encode(env.state.frames),
        "budget": env.budget.to_dict(env) if env.budget is not None else None,
        "start": env.origin.start,
        "diagnosis": env.state.diagnosis.to_dict(),
        **({} if env.state.keep_events else {"events": False}),
    }


def _identity(env: Env) -> dict[str, Any]:
    """What every snapshot of ``env`` starts with: the engine version and the contract and run it belongs to."""
    inputs = encode(env.inputs)
    return {"fg_env_snapshot": SNAPSHOT_VERSION, "contract": contract_hash(env.contract), **_rule_origin(env),
            "run": run_identity(env.seed, env.arm, inputs), "seed": env.seed, "arm": env.arm, "inputs": inputs}


def recording_start(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """``snapshot`` as the start of a recording: its exposure log given as counts, because the recording that
    continues from it holds those first entries (a result never carries its exposures twice)."""
    held = snapshot.get("exposures")
    counts = {"wakes": len(held["wakes"]), "chance": len(held.get("chance") or [])} if held is not None else None
    return {**{key: value for key, value in snapshot.items() if key != "start"}, "exposures": counts}


def matching_contract(contract: Any, snapshot: Mapping[str, Any]) -> tuple[Contract, Contract]:
    """``(taken with, unarmed)``: the contract the snapshot was taken with — as given, or with the snapshot's
    arm applied — and that contract before its arm."""
    from ..api import apply_arm, parse

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



def _rule_origin(env: Env) -> dict[str, Any]:
    """Keep a different rule base only when future variant selection needs it."""
    from ..api import contract_source

    base = env.origin.unarmed
    if base is env.contract:
        return {}
    fingerprint = contract_hash(base)
    if fingerprint == contract_hash(env.contract):
        return {}
    return {"rule_origin": {"hash": fingerprint, "source": contract_source(base)}}


def _restore_rule_origin(snapshot: Mapping[str, Any], fallback: Contract) -> Contract:
    """Optional provenance; old snapshots continue to use their supplied contract."""
    from ..api import located
    from ..checks import parse_contract

    if "rule_origin" not in snapshot:
        return fallback
    held = snapshot["rule_origin"]
    if (not isinstance(held, Mapping) or not isinstance(held.get("source"), Mapping)
        or not isinstance(held.get("hash"), str)):
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
    """Refuse what is not a snapshot of this engine (or of its previous format), or one whose seed, arm or inputs were
    edited."""
    from .legacy_snapshot import LEGACY_VERSION

    if not isinstance(snapshot, Mapping):
        raise SnapshotError(f"a snapshot is a mapping (from env.snapshot()), got {type(snapshot).__name__}")
    version = snapshot.get("fg_env_snapshot")
    if version not in (SNAPSHOT_VERSION, LEGACY_VERSION):
        raise SnapshotError(f"unsupported snapshot version {version!r} (this engine reads versions {LEGACY_VERSION} "
                            f"and {SNAPSHOT_VERSION}); rerun from the snapshot's seed and take a new one")
    if "run" in snapshot and snapshot["run"] != run_identity(snapshot.get("seed"), snapshot.get("arm"),
                                                             snapshot.get("inputs")):
        raise SnapshotError("the snapshot's seed, arm or inputs were changed after it was taken, so it no longer "
                            f"describes one run; restore it unedited — {_FORK_HINT}")


def restore_env(cls: type[_E], contract: Any, snapshot: Mapping[str, Any], parallel: int = 8) -> _E:
    from .legacy_snapshot import part_way

    matched, unarmed = matching_contract(contract, snapshot)
    if isinstance(snapshot.get("part_way"), Mapping):  # the previous format's snapshot of a run stopped in a round
        env = part_way(cls, matched, snapshot, parallel)
    else:
        env = restore_state(cls, matched, snapshot, parallel)
    env.origin.unarmed = unarmed
    return env


def restore_state(cls: type[_E], contract: Contract, snapshot: Mapping[str, Any], parallel: int = 8) -> _E:
    """A run rebuilt from a snapshot into ``contract``, which the caller has matched to it."""
    try:
        return _restore(cls, contract, snapshot, parallel)
    except SnapshotError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
        raise SnapshotError(f"the snapshot is incomplete or corrupted ({type(exc).__name__}: {exc})") from None


def _restore(cls: type[_E], contract: Contract, snapshot: Mapping[str, Any], parallel: int) -> _E:
    refused = refused_values(contract, snapshot)
    if refused:
        raise SnapshotError("the snapshot holds values its contract refuses (was it edited?): "
                            + "; ".join(f"{issue.path}: {issue.message}" for issue in refused[:5])
                            + (f"; and {len(refused) - 5} more" if len(refused) > 5 else ""))
    env = cls(contract, decode(snapshot["inputs"]), int(snapshot["seed"]), snapshot.get("arm"), parallel,
              events=snapshot.get("events", True) is not False)
    env.state.decode(snapshot)
    status = snapshot["status"]
    if status == "stopped" and not env.state.in_round:  # stopped as a round was about to start
        status = "running" if env.world.round else "ready"
    env.status = status
    env.ended_by, env.error = snapshot.get("ended_by"), snapshot.get("error")
    env.state.frames = decode(snapshot.get("frames") or [])
    env.origin.start = snapshot.get("start")
    if snapshot.get("budget") is not None:
        env.budget = Budget.from_dict(snapshot["budget"])
    env.state.diagnosis.load(snapshot.get("diagnosis"))
    env.state.emitted = len(env.world.log)
    return env


def refused_values(contract: Contract, snapshot: Mapping[str, Any]) -> list[Issue]:
    """Every property value in ``snapshot`` that ``contract`` refuses, as :meth:`World.coerce` would refuse it: a
    restore holds the snapshot to its contract as a fork holds it to the new one."""
    probe = World(contract, decode(snapshot["inputs"]), SeedTree(0))
    issues: list[Issue] = []
    for row in snapshot["entities"]:
        if row["type"] in contract.types:
            issues += held_values(contract.props_of(row["type"]), decode(row["props"]), f"types.{row['type']}.props",
                                  row["id"], probe, "its declaration")
    return issues + held_values(contract.world, decode(snapshot["props"]), "world", "the world", probe,
                                "its declaration")


def held_values(specs: Mapping[str, PropSpec], values: Mapping[str, Any], path: str, owner: str, probe: World,
                declaration: str) -> list[Issue]:
    """The values ``owner`` holds (``values``, by property) that their declarations (``specs``) refuse, and the
    properties it holds a value for that are not declared; ``declaration`` names whose they are in the messages."""
    issues = []
    for prop, value in values.items():
        spec = specs.get(prop)
        if spec is None:
            issues.append(Issue(f"{path}.{prop}", f"is gone, but {owner} holds a value for it",
                                "keep the property (the state still has it)"))
            continue
        problem = _refused(probe, spec, value)
        if problem:
            issues.append(Issue(f"{path}.{prop}", f"{owner} holds {_shown(value)}, which {declaration} refuses: "
                                                  f"{problem}", "keep a declaration that accepts the current value"))
    return issues


def _refused(probe: World, spec: PropSpec, value: Any) -> str | None:
    if _non_finite(value):  # no rule can make one, whatever the declaration
        return f"{_shown(value)} is not a finite number"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if spec.min is not None and value < spec.min:
            return f"below the minimum {spec.min:g}"
        if spec.max is not None and value > spec.max:
            return f"above the maximum {spec.max:g}"
    try:
        probe.coerce(spec, value, "restore")
    except RunError as exc:
        return str(exc).split(": ", 1)[-1]
    return None


def _non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, (list, tuple)):
        return any(_non_finite(item) for item in value)
    if isinstance(value, Mapping):
        return any(_non_finite(item) for item in value.values())
    return False


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."
