"""A run's assets: the contract's catalog and the files its participants submitted, by id.

The store holds metadata only — id, kind, media type, name, size, content hash, caption, tags — and finds
the bytes by hash (:mod:`.blobs`). Its index is part of the run's state: snapshots carry it, so a restored,
cloned or forked run knows the same assets. A submitted file is untrusted: its name is participant text and
its kind is recognised from its bytes. Ids of submitted files derive from their content, so the same file
submitted in a replay gets the same id.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import PurePath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..errors import RunError
from ..expr import Untrusted
from . import blobs
from .kinds import HARD_MAX_BYTES, KINDS, MAX_BYTES, MAX_TEXT_CHARS, sniff

__all__ = ["Asset", "AssetStore", "SUBMITTED", "MAX_NAME_CHARS"]

#: Prefix of the ids of files participants submitted.
SUBMITTED = "upload:"
#: Longest submitted file name kept.
MAX_NAME_CHARS = 120


@dataclass(frozen=True)
class Asset:
    """One file an environment knows. ``owner`` is the agent that submitted it (None for the contract's own)."""

    id: str
    kind: str
    media_type: str
    name: str
    size: int
    hash: str
    caption: str = ""
    alt: str = ""
    tags: Tuple[str, ...] = ()
    path: Optional[str] = None
    describe: Optional[str] = None
    owner: Optional[str] = None

    @property
    def untrusted(self) -> bool:
        return self.owner is not None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        return {key: value for key, value in data.items() if value not in (None, "", [])}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Asset":
        """An asset from :meth:`to_dict`; a submitted file's name is participant text again."""
        owner = data.get("owner")
        name = str.__str__(str(data["name"]))
        return cls(id=str(data["id"]), kind=str(data["kind"]), media_type=str(data["media_type"]),
                   name=Untrusted(name) if owner is not None else name, size=int(data["size"]), hash=str(data["hash"]),
                   caption=str(data.get("caption", "")), alt=str(data.get("alt", "")),
                   tags=tuple(str(tag) for tag in data.get("tags") or ()), path=data.get("path"),
                   describe=data.get("describe"), owner=data.get("owner"))


@dataclass
class AssetStore:
    """Every asset a run knows, by id. ``resolved`` is true once the contract's catalog was read from its folder:
    only then are asset properties checked against it."""

    assets: Dict[str, Asset] = field(default_factory=dict)
    resolved: bool = False
    _texts: Dict[str, str] = field(default_factory=dict, repr=False)

    # -- reading ------------------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.assets)

    def has(self, asset_id: Any) -> bool:
        return isinstance(asset_id, str) and str.__str__(asset_id) in self.assets

    def get(self, asset_id: Any) -> Optional[Asset]:
        return self.assets.get(str.__str__(asset_id)) if isinstance(asset_id, str) else None

    def data(self, asset: Asset) -> bytes:
        """The asset's bytes; raises :class:`~.blobs.BlobMissing` naming the asset when they are not available."""
        try:
            return blobs.read(asset.hash)
        except blobs.BlobMissing as exc:
            where = f"'{asset.path}' beside the contract" if asset.path else "the run's asset folder"
            raise blobs.BlobMissing(f"the bytes of asset '{asset.id}' are not available ({exc}); load the contract from "
                                    f"its folder, or fg_env.sdk.assets.provide() {where}") from None

    def text(self, asset: Asset) -> Optional[str]:
        """A text asset's content (cut at the limit); None for other kinds. Submitted text stays untrusted."""
        if asset.kind != "text":
            return None
        cached = self._texts.get(asset.hash)
        if cached is None:
            cached = self.data(asset).decode("utf-8", "replace")
            if len(cached) > MAX_TEXT_CHARS:
                cached = cached[:MAX_TEXT_CHARS] + f"… (cut at {MAX_TEXT_CHARS:,} characters)"
            self._texts[asset.hash] = cached
        return Untrusted(cached) if asset.untrusted else cached

    def ref(self, value: Any, where: str) -> str:
        """An asset id to store in a property or record field: an id or an `$asset(...)` map, naming an asset the run
        knows (checked once the contract's catalog is read)."""
        if isinstance(value, Mapping) and "id" in value and "hash" in value:
            value = value["id"]
        if not isinstance(value, str) or (self.resolved and not self.has(value)):
            known = ", ".join(sorted(self.assets)[:8]) or "none"
            raise RunError(f"must be an asset id, got {value!r} (assets: {known})", where)
        return str.__str__(value)

    # -- adding ---------------------------------------------------------------------------------

    def add(self, asset: Asset) -> None:
        self.assets[asset.id] = asset

    def submit(self, data: bytes, name: Any, owner: str, kinds: Optional[Sequence[str]] = None,
               max_bytes: Optional[int] = None) -> Tuple[Optional[Asset], Optional[str]]:
        """Store a file a participant hands in: ``(asset, None)`` or ``(None, what to fix)``. The same bytes submitted
        again are the same asset (its first submitter stays its owner)."""
        asset, problem = accepts(data, name, owner, kinds, max_bytes)
        if asset is None:
            return None, problem
        blobs.keep_bytes(data)
        known = self.assets.get(asset.id)
        if known is not None:
            return (known, None) if known.hash == asset.hash else (None, "collides with another submitted file")
        self.assets[asset.id] = asset
        return asset, None

    def adopt(self, meta: Mapping[str, Any]) -> Asset:
        """Add a submitted file recorded on a tape (a copy or replay of the run); its bytes must be available."""
        asset = Asset.from_dict(meta)
        if not blobs.available(asset.hash):
            blobs.read(asset.hash)  # scans provided folders, or raises BlobMissing
        self.assets.setdefault(asset.id, asset)
        return self.assets[asset.id]

    # -- state ----------------------------------------------------------------------------------

    def copy(self) -> "AssetStore":
        """An independent store knowing the same assets (entries are immutable; bytes stay found by hash)."""
        return AssetStore(dict(self.assets), self.resolved, dict(self._texts))

    def catalog(self) -> "AssetStore":
        """A store with the contract's catalog only (a run rebuilt from its start adds submissions as it replays)."""
        return AssetStore({key: asset for key, asset in self.assets.items() if asset.owner is None}, self.resolved)

    def to_dict(self) -> Dict[str, Any]:
        return {"resolved": self.resolved, "assets": [self.assets[key].to_dict() for key in sorted(self.assets)]}

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "AssetStore":
        if not data:
            return cls()
        return cls({row["id"]: Asset.from_dict(row) for row in data.get("assets") or []}, bool(data.get("resolved")))

    def of(self, ids: Iterable[str]) -> List[Asset]:
        return [self.assets[key] for key in ids if key in self.assets]


def accepts(data: bytes, name: Any, owner: str, kinds: Optional[Sequence[str]] = None,
            max_bytes: Optional[int] = None) -> Tuple[Optional[Asset], Optional[str]]:
    """The asset a submitted file would become, or what to fix — decided from its bytes alone, storing nothing."""
    if len(data) == 0:
        return None, "is an empty file"
    kind, media_type = sniff(data)
    if kind == "text" and _markdown(name):
        media_type = "text/markdown"
    allowed = list(kinds) if kinds else list(KINDS)
    if kind not in allowed:
        return None, f"is a {kind} file ({media_type}); accepted: {', '.join(allowed)}"
    limit = min(max_bytes if max_bytes is not None else MAX_BYTES[kind], HARD_MAX_BYTES)
    if len(data) > limit:
        return None, f"is {len(data):,} bytes; the limit is {limit:,}"
    key = blobs.digest(data)
    return Asset(id=f"{SUBMITTED}{key[:16]}", kind=kind, media_type=media_type, name=_file_name(name, kind, media_type),
                 size=len(data), hash=key, owner=owner), None


def _markdown(name: Any) -> bool:
    return isinstance(name, str) and PurePath(str.__str__(name)).suffix.lower() in (".md", ".markdown")


def _file_name(name: Any, kind: str, media_type: str) -> str:
    """A submitted file's shown name: the participant's (untrusted, cut to size) or one from its kind."""
    if isinstance(name, str) and str.__str__(name).strip():
        plain = PurePath(str.__str__(name).strip().replace("\\", "/")).name[:MAX_NAME_CHARS]
        if plain:
            return Untrusted(plain)
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
                 "application/pdf": ".pdf", "text/plain": ".txt", "text/markdown": ".md", "audio/wav": ".wav",
                 "audio/mpeg": ".mp3"}.get(media_type, "")
    return f"{kind}{extension}"
