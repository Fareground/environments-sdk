"""Where asset bytes come from: a content-addressed registry shared by every run in this process.

Run state, snapshots and recordings name assets by content hash and never hold their bytes. The bytes are
found here by hash: files a contract's catalog read (by path, re-read and re-checked on use; every path seen
holding the bytes is kept, so editing one copy never strands another), files
participants submitted (kept in memory), and folders handed over with :func:`provide` (a saved run's asset
folder, a store directory on another machine). Bytes whose hash no longer matches are refused, so a file
changed after a run started can never pass for the one the run recorded.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path

from .kinds import HARD_MAX_BYTES

__all__ = ["HASH_DIGITS", "digest", "keep_bytes", "keep_path", "provide", "read", "available", "BlobMissing"]

#: Hex digits kept from a file's SHA-256.
HASH_DIGITS = 32

_LOCK = threading.Lock()
_BYTES: dict[str, bytes] = {}
#: Every file seen holding the bytes with a hash, oldest first; a path whose bytes changed is dropped when read.
_PATHS: dict[str, list[Path]] = {}
#: Folders handed over with provide(), scanned by hash on first need.
_FOLDERS: list[Path] = []


class BlobMissing(LookupError):
    """No bytes with this hash are available in this process."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:HASH_DIGITS]


def keep_bytes(data: bytes) -> str:
    """Hold ``data`` in memory under its hash (submitted files); returns the hash."""
    key = digest(data)
    with _LOCK:
        _BYTES.setdefault(key, bytes(data))
    return key


def keep_path(key: str, path: Path) -> None:
    """Remember that the file at ``path`` holds the bytes with hash ``key`` (checked again when read)."""
    with _LOCK:
        paths = _PATHS.setdefault(key, [])
        if path not in paths:
            paths.append(path)


def provide(folder: str | os.PathLike[str]) -> int:
    """Make every file in ``folder`` (not its subfolders) available by content hash, e.g. the asset folder of a run
    saved on another machine. Returns how many files it holds."""
    base = Path(folder)
    if not base.is_dir():
        raise FileNotFoundError(f"'{base}' is not a folder of asset files")
    files = [path for path in sorted(base.iterdir()) if path.is_file() and not path.is_symlink()]
    with _LOCK:
        if base.resolve() not in _FOLDERS:
            _FOLDERS.append(base.resolve())
    return len(files)


def available(key: str) -> bool:
    with _LOCK:
        return key in _BYTES or bool(_PATHS.get(key))


def read(key: str) -> bytes:
    """The bytes with hash ``key``; raises :class:`BlobMissing` when none are available or they changed."""
    with _LOCK:
        held = _BYTES.get(key)
        paths = list(_PATHS.get(key, ()))
        folders = list(_FOLDERS)
    if held is not None:
        return held
    for path in paths:
        data = _read_file(path)
        if data is not None and digest(data) == key:
            return data
        _forget(key, path)
    for folder in folders:
        found = _scan(folder, key)
        if found is not None:
            return found
    if paths:
        raise BlobMissing(f"the file '{paths[0]}' changed after it was read (its content no longer has hash {key})")
    raise BlobMissing(f"no file with hash {key} is available in this process")


def _forget(key: str, path: Path) -> None:
    with _LOCK:
        paths = _PATHS.get(key, [])
        if path in paths:
            paths.remove(path)
        if not paths:
            _PATHS.pop(key, None)


def _scan(folder: Path, key: str) -> bytes | None:
    named = [path for path in folder.glob(f"{key}*") if path.is_file() and not path.is_symlink()]
    for path in named + [p for p in sorted(folder.iterdir()) if p not in named]:
        if not path.is_file() or path.is_symlink():
            continue
        data = _read_file(path)
        if data is not None:
            found = digest(data)
            keep_path(found, path)
            if found == key:
                return data
    return None


def _read_file(path: Path) -> bytes | None:
    try:
        if path.stat().st_size > HARD_MAX_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None
