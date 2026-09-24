"""Files participants hand in: `file` parameters and :meth:`Wake.upload`.

A model passes a file in a tool call as ``{"data": "<base64>", "name": "photo.jpg"}``, a document as
``{"text": "...", "name": "memo.md"}``, or a file already submitted in this run as ``{"asset": "upload:…"}``;
coded participants may also pass bytes, or upload a file first (``wake.upload(path)``). Before the call is
recorded, every file is stored in the run's asset store — its kind recognised from its bytes, its size and kind
checked against the parameter — and the argument becomes ``{"asset": id}``, so the engine's tape, the exposure
log and snapshots hold ids and hashes, never bytes. A file that cannot be accepted becomes
``{"invalid": what to fix}``, which the call's validation reports. Paths are never read from tool arguments.
"""
from __future__ import annotations

import base64
import binascii
import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Mapping, Optional, Tuple, Union

from . import blobs
from .kinds import HARD_MAX_BYTES, KINDS, MAX_BYTES
from .store import SUBMITTED, Asset, accepts

if TYPE_CHECKING:
    from ..contract import ParamSpec
    from ..runtime.turn import Turn
    from ..world.live import SdkWorld

__all__ = ["intake", "upload", "file_schema", "file_value"]

_SHAPES = ('{"data": "<base64>", "name": "file name"}, {"text": "<document text>", "name": "notes.md"} '
           'or {"asset": "<id of a file submitted in this run>"}')


def file_schema(param: "ParamSpec") -> Dict[str, Any]:
    kinds = param.kinds or list(KINDS)
    limit = _limit(param)
    return {"type": "object", "additionalProperties": False, "properties": {
        "data": {"type": "string", "description": "The file's bytes, base64-encoded."},
        "text": {"type": "string", "description": "A text or markdown document's content, instead of data."},
        "name": {"type": "string", "maxLength": 120, "description": "The file's name."},
        "asset": {"type": "string", "description": "The id of a file already submitted in this run, instead."}},
        "description": (f"{param.description} " if param.description else "")
        + f"A file ({', '.join(kinds)}; at most {limit:,} bytes)."}


def _limit(param: "ParamSpec") -> int:
    kinds = param.kinds or list(KINDS)
    return min(param.max_bytes if param.max_bytes is not None else max(MAX_BYTES[kind] for kind in kinds), HARD_MAX_BYTES)


def _file_params(turn: "Turn", name: Any, args: Mapping[str, Any]) -> Dict[str, "ParamSpec"]:
    contract, env = turn.env.contract, turn.env
    if not isinstance(name, str):
        return {}
    if name in contract.actions:
        members = [name]
    else:
        members = list(env.actions.groups.get(name, ()))
        picked = args.get("action")
        members = [m for m in members if picked in (m, m.removeprefix(f"{name}_"))] or members
    found: Dict[str, "ParamSpec"] = {}
    for member in members:
        for pname, param in contract.actions[member].params.items():
            if param.type == "file":
                found.setdefault(pname, param)
    return found


def intake(turn: "Turn", name: Any, args: Any) -> Any:
    """``args`` with every submitted file stored and replaced by its id (call under the run's lock)."""
    if not isinstance(args, Mapping):
        return args
    params = _file_params(turn, name, args)
    if not params or not any(key in args for key in params):
        return args
    out = dict(args)
    for pname, param in params.items():
        if pname in out and out[pname] is not None:
            out[pname] = _stored(turn, param, out[pname])
    return out


def _stored(turn: "Turn", param: "ParamSpec", raw: Any) -> Any:
    data, name, problem = _payload(raw)
    if problem is not None:
        return {"invalid": problem}
    if data is None:
        return raw
    asset, problem = turn.env.world.assets.submit(data, name, turn.actor.id, param.kinds, _limit(param))
    if asset is None:
        return {"invalid": f"the file {problem}"}
    _record(turn, asset)
    return {"asset": asset.id}


def _payload(raw: Any) -> Tuple[Optional[bytes], Any, Optional[str]]:
    """``(bytes, name, None)`` for a file to store, ``(None, None, None)`` to leave as it is, or a problem."""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw), None, None
    if isinstance(raw, str) and raw.startswith(SUBMITTED):
        return None, None, None
    if not isinstance(raw, Mapping):
        return None, None, None
    if "path" in raw:
        return None, None, "a tool call cannot name a path; send the file's content as base64 `data`"
    name = raw.get("name")
    if "data" in raw:
        encoded = raw["data"]
        if not isinstance(encoded, str):
            return None, None, "`data` must be base64 text"
        if encoded.startswith("data:") and ";base64," in encoded[:100]:
            encoded = encoded.split(";base64,", 1)[1]
        if len(encoded) > HARD_MAX_BYTES * 4 // 3 + 4:
            return None, None, f"the file is larger than {HARD_MAX_BYTES:,} bytes"
        try:
            return base64.b64decode(encoded, validate=True), name, None
        except (binascii.Error, ValueError):
            return None, None, "`data` is not valid base64"
    if "text" in raw:
        if not isinstance(raw["text"], str):
            return None, None, "`text` must be text"
        return str.__str__(raw["text"]).encode("utf-8"), name if name is not None else "document.txt", None
    return None, None, None


def _record(turn: "Turn", asset: Asset) -> None:
    turn.record("upload", asset.to_dict())


def upload(turn: "Turn", source: Union[bytes, bytearray, str, "os.PathLike[str]"], name: Optional[str] = None) -> str:
    """Store a file for the turn's agent (bytes, or a path the participant's own code chose); returns its id."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif isinstance(source, (str, os.PathLike)):
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"no file at '{path}'")
        if path.stat().st_size > HARD_MAX_BYTES:
            raise ValueError(f"'{path}' is larger than {HARD_MAX_BYTES:,} bytes")
        data, name = path.read_bytes(), name if name is not None else path.name
    else:
        raise TypeError(f"upload takes bytes or a file path, got {type(source).__name__}")
    with turn.env._lock:
        if turn.done:
            raise RuntimeError("this turn is over; upload files during the turn")
        asset, problem = turn.env.world.assets.submit(data, name, turn.actor.id)
        if asset is None:
            raise ValueError(f"the file {problem}")
        _record(turn, asset)
        return asset.id


@contextmanager
def previewed(turn: "Turn", name: Any, args: Any) -> Iterator[Tuple[Any, Optional[str]]]:
    """``(args, problem)`` as :func:`intake` would make them — files replaced by the ids they would get — for a check
    that must change nothing (a game's legality check before the call is made). Accepted files are known to the store
    only inside the block, which then leaves it exactly as it was: nothing is recorded, nothing is kept."""
    params = _file_params(turn, name, args) if isinstance(args, Mapping) else {}
    store = turn.env.world.assets
    added: List[str] = []
    out: Any = dict(args) if params else args
    problem: Optional[str] = None
    try:
        for pname, param in params.items():
            raw = out.get(pname)
            data, file_name, problem = _payload(raw) if raw is not None else (None, None, None)
            if problem is not None:
                break
            if data is None:
                continue
            asset, problem = accepts(data, file_name, turn.actor.id, param.kinds, _limit(param))
            if asset is None:
                problem = f"{pname} the file {problem}"
                break
            if not store.has(asset.id):
                blobs.keep_bytes(data)
                store.add(asset)
                added.append(asset.id)
            out[pname] = {"asset": asset.id}
        yield out, problem
    finally:
        for key in added:
            store.assets.pop(key, None)


def file_value(world: "SdkWorld", param: "ParamSpec", raw: Any) -> Tuple[Any, Optional[str]]:
    """A `file` argument as a submitted asset id, or what to fix."""
    if isinstance(raw, Mapping) and isinstance(raw.get("invalid"), str):
        return None, raw["invalid"]
    key = raw.get("asset") if isinstance(raw, Mapping) else raw if isinstance(raw, str) else None
    asset = world.assets.get(key) if isinstance(key, str) and key.startswith(SUBMITTED) else None
    if asset is None:
        return None, f"must be a file: {_SHAPES}"
    kinds = param.kinds or list(KINDS)
    if asset.kind not in kinds:
        return None, f"is a {asset.kind} file; accepted: {', '.join(kinds)}"
    if asset.size > _limit(param):
        return None, f"is {asset.size:,} bytes; the limit is {_limit(param):,}"
    return asset.id, None
