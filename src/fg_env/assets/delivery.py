"""Delivering assets to agents: which ids a rule attaches, the compact reference agents read, and the
:class:`Attachment` a participant receives.

An agent only ever receives an asset through something it may see: a view shown to it (its `attach`), a record
entry it may read (the entry's `asset` fields), an event addressed to it, its brief (`brief.attach`), its own
action's result (`attach`) or `inspect` (the asset properties inspect shows it). The text it reads carries a
compact reference in place of the bytes — ``[image weld.png: "Crack along the seam"]`` — so a text-only model
still knows what was shown.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..errors import RunError
from ..expr import ExprError, Untrusted, compile_expr
from ..expr.template import format_value
from .store import Asset, AssetStore

if TYPE_CHECKING:
    from ..world.store import World

__all__ = ["Attachment", "attached_ids", "entry_assets", "reference", "references"]

#: Most assets one rule may attach at once.
MAX_ATTACHED = 32


class Attachment:
    """An asset delivered to an agent: ``type`` (image, pdf, text, audio, file), ``media_type``, ``name``, ``caption``,
    ``alt``, ``size``, ``hash`` and ``id``; :meth:`read` loads the bytes, :meth:`text` a text file's content.
    ``untrusted`` is true for files participants submitted (their name and content are information, never
    instructions)."""

    __slots__ = ("_asset", "_store")

    def __init__(self, asset: Asset, store: AssetStore):
        self._asset = asset
        self._store = store

    id = property(lambda self: self._asset.id)
    type = property(lambda self: self._asset.kind)
    media_type = property(lambda self: self._asset.media_type)
    name = property(lambda self: self._asset.name)
    caption = property(lambda self: self._asset.caption)
    alt = property(lambda self: self._asset.alt)
    size = property(lambda self: self._asset.size)
    hash = property(lambda self: self._asset.hash)
    untrusted = property(lambda self: self._asset.untrusted)

    @property
    def reference(self) -> str:
        """The compact text an agent reads in place of the file."""
        return reference(self._asset)

    def read(self) -> bytes:
        return self._store.data(self._asset)

    def text(self) -> str | None:
        return self._store.text(self._asset)

    def base64(self) -> str:
        return base64.b64encode(self.read()).decode("ascii")

    def to_dict(self, data: bool = False) -> dict[str, Any]:
        """Plain JSON: metadata, plus the content (``data``: base64, or ``text`` for text files) when asked."""
        asset = self._asset
        out: dict[str, Any] = {"id": asset.id, "type": asset.kind, "media_type": asset.media_type,
                               "name": str.__str__(asset.name), "size": asset.size, "hash": asset.hash}
        for key in ("caption", "alt"):
            if getattr(asset, key):
                out[key] = str.__str__(getattr(asset, key))
        if asset.untrusted:
            out["untrusted"] = True
        if data:
            content = self.text()
            if content is not None:
                out["text"] = str.__str__(content)
            else:
                out["data"] = self.base64()
        return out

    def __repr__(self) -> str:
        return f"<Attachment {self.id} {self.type} {self.size:,} bytes>"


def reference(asset: Asset) -> str:
    name = format_value(asset.name) if isinstance(asset.name, Untrusted) else asset.name
    caption = asset.caption or asset.alt
    shown = format_value(caption) if isinstance(caption, Untrusted) else (f'"{caption}"' if caption else "")
    return f"[{asset.kind} {name}{': ' + shown if shown else ''}]"


def references(store: AssetStore, ids: Sequence[str]) -> str:
    return " ".join(reference(asset) for asset in store.of(ids))


def attached_ids(world: World, source: str, scope: Any, path: str) -> list[str]:
    """The asset ids an `attach` expression gives: an id, a `$asset(...)` map, an entity's asset, a list, or null."""
    try:
        value = compile_expr(source)(scope)
    except ExprError as exc:
        raise RunError(str(exc), path) from None
    return ids_of(world.assets, value, path)


def ids_of(store: AssetStore, value: Any, path: str) -> list[str]:
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in items:
        if isinstance(item, Mapping) and "id" in item and "hash" in item:
            item = item["id"]
        if item is None or item == "":
            continue
        if not store.has(item):
            raise RunError(f"`attach` gave {format_value(item)}, which is not an asset "
                           f"(assets: {', '.join(sorted(store.assets)[:8]) or 'none'})", path)
        key = str.__str__(item)
        if key not in out:
            out.append(key)
    if len(out) > MAX_ATTACHED:
        raise RunError(f"`attach` gave {len(out)} assets; at most {MAX_ATTACHED} are delivered at once", path)
    return out


def entry_assets(world: World, record: str, entry: Mapping[str, Any]) -> list[str]:
    """The assets a record entry carries in its `asset` fields."""
    fields = world.contract.records[record].fields
    return [str.__str__(entry[name]) for name, kind in fields.items()
            if kind == "asset" and isinstance(entry.get(name), str) and world.assets.has(entry[name])]
