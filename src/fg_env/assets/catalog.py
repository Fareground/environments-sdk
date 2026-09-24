"""Reading a contract's catalog: the files its `file` inputs and its data inputs' asset columns name.

Only files inside the contract's folder (or ``data_dir=``) are read. Absolute paths, `..`, links that lead
outside it, hidden files, files whose content does not match their extension, oversized files and folders with
too many files are refused, each with the path to fix. Every file is hashed once, so a run records exactly
which bytes it was given.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Union

from ..errors import ContractError, Issue
from . import blobs
from .kinds import (
    HARD_MAX_BYTES,
    MAX_BYTES,
    MAX_CATALOG_BYTES,
    MAX_FOLDER_FILES,
    agrees,
    kind_of_name,
)
from .store import Asset, AssetStore

if TYPE_CHECKING:
    from ..contract import Contract

__all__ = ["resolve_assets", "asset_columns", "file_inputs", "locate"]

Folder = Union[str, "os.PathLike[str]", None]


class _Problem(Exception):
    def __init__(self, path: str, message: str, fix: str | None = None):
        super().__init__(message)
        self.issue = Issue(path, message, fix)


def asset_columns(contract: Contract) -> list[tuple[str, str]]:
    """``(input, column)`` for every table input column of type `asset`."""
    return [(name, column) for name, spec in contract.inputs.items()
            for column, kind in (spec.columns or {}).items() if kind == "asset"]


def file_inputs(contract: Contract) -> dict[str, Any]:
    """The contract's `file` inputs, by name (each one's id in the catalog)."""
    return {name: spec for name, spec in contract.inputs.items() if spec.type == "file"}


def resolve_assets(contract: Contract, inputs: Mapping[str, Any], folder: Folder) -> AssetStore:
    """The run's catalog: each `file` input's file — the one at the path it resolved to (its `source`, or the path
    supplied at load), or every file of that folder as ``<input>/<file name>`` — and the files table cells name.
    Raises :class:`ContractError` listing every problem with its path."""
    columns = asset_columns(contract)
    files = file_inputs(contract)
    store = AssetStore(resolved=True)
    if not files and not columns:
        return store
    if folder is None:
        raise ContractError([Issue("inputs", "the contract's files need a folder to be read from",
                                   "load the contract from its file (its folder is used) or pass data_dir=")])
    base = Path(folder).resolve()
    issues: list[Issue] = []
    total = [0]
    for name, spec in files.items():
        path = f"inputs.{name}.source"
        try:
            relative = _relative(inputs.get(name), path)
            if _inside(base, relative, path).is_dir():
                for asset in _folder(base, name, relative, spec, path, total):
                    _add(store, asset, path, issues)
            else:
                _add(store, _file(base, name, relative, spec, path, total), path, issues)
        except _Problem as problem:
            issues.append(problem.issue)
    for input_name, column in columns:
        for index, row in enumerate(inputs.get(input_name) or []):
            cell = row.get(column) if isinstance(row, Mapping) else None
            if cell in (None, ""):
                continue
            path = f"inputs.{input_name}[{index}].{column}"
            try:
                relative = _relative(cell, path)
                if relative not in store.assets:
                    store.add(_file(base, relative, relative, None, path, total))
            except _Problem as problem:
                issues.append(problem.issue)
    if issues:
        raise ContractError(issues, title="the contract's files cannot be read")
    return store


def _add(store: AssetStore, asset: Asset, path: str, issues: list[Issue]) -> None:
    if asset.id in store.assets:
        issues.append(Issue(path, f"asset id '{asset.id}' is declared twice", "rename one of them"))
    else:
        store.add(asset)


def _relative(raw: Any, path: str) -> str:
    """A file path from a contract or data cell, as a clean relative path inside the folder."""
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise _Problem(path, f"must be a file path, got {raw!r}", "use a path relative to the contract's folder")
    text = raw.strip().replace("\\", "/")
    relative = PurePosixPath(text)
    if relative.is_absolute() or ".." in relative.parts or text.startswith("~"):
        raise _Problem(path, f"'{raw}' must be a path inside the contract's folder", "use a relative path without '..'")
    if any(part.startswith(".") for part in relative.parts):
        raise _Problem(path, f"'{raw}' names a hidden file or folder", "rename it without a leading '.'")
    return str(relative)


def _inside(base: Path, relative: str, path: str) -> Path:
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        raise _Problem(path, f"'{relative}' leads outside the contract's folder",
                       "keep the file inside it (no links out)")
    return target


def _file(base: Path, asset_id: str, raw: str, spec: Any, path: str, total: list[int]) -> Asset:
    relative = _relative(raw, path)
    target = _inside(base, relative, path)
    if not target.is_file():
        raise _Problem(path, f"file not found: '{relative}' in {base}", "check the path and the contract's folder")
    kind, media_type = kind_of_name(relative)
    limit = min(getattr(spec, "max_bytes", None) or MAX_BYTES[kind], HARD_MAX_BYTES)
    size = target.stat().st_size
    if size > limit:
        raise _Problem(path, f"'{relative}' is {size:,} bytes; the limit for {kind} files is {limit:,}",
                       "use a smaller file, or raise `max_bytes`")
    if size == 0:
        raise _Problem(path, f"'{relative}' is empty", "replace it with the real file")
    total[0] += size
    if total[0] > MAX_CATALOG_BYTES:
        raise _Problem(path, f"the contract's files add up to more than {MAX_CATALOG_BYTES:,} bytes",
                       "carry fewer files")
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise _Problem(path, f"cannot read '{relative}': {exc.strerror or exc}",
                       "check that the file is readable") from None
    mismatch = agrees((kind, media_type), data)
    if mismatch:
        raise _Problem(path, f"'{relative}' {mismatch}", "fix the file or its extension")
    key = blobs.digest(data)
    blobs.keep_path(key, target)
    name = PurePosixPath(relative).name
    caption = (spec.caption or "").replace("{name}", name) if spec is not None else ""
    return Asset(id=asset_id, kind=kind, media_type=media_type, name=name, size=size, hash=key, caption=caption,
                 alt=spec.alt if spec is not None else "", tags=tuple(spec.tags) if spec is not None else (),
                 path=relative, describe=spec.describe if spec is not None else None)


def _folder(base: Path, name: str, relative: str, spec: Any, path: str, total: list[int]) -> list[Asset]:
    target = _inside(base, relative, path)
    files = sorted(entry.name for entry in target.iterdir() if entry.is_file() and not entry.name.startswith("."))
    if len(files) > MAX_FOLDER_FILES:
        raise _Problem(path, f"'{relative}' holds {len(files):,} files; the limit is {MAX_FOLDER_FILES:,}",
                       "split it into several inputs")
    if not files:
        raise _Problem(path, f"'{relative}' holds no files", "add files, or name another folder")
    return [_file(base, f"{name}/{file}", f"{relative}/{file}", spec, path, total) for file in files]



def locate(store: AssetStore, folder: Folder) -> None:
    """Find a restored run's catalog files again in the contract's folder, by their recorded paths (their bytes
    are checked against the recorded hashes when read)."""
    if folder is None:
        return
    base = Path(folder).resolve()
    for asset in store.assets.values():
        if asset.path is None or blobs.available(asset.hash):
            continue
        try:
            target = _inside(base, _relative(asset.path, f"assets.{asset.id}"), f"assets.{asset.id}")
        except _Problem:
            continue
        blobs.keep_path(asset.hash, target)
