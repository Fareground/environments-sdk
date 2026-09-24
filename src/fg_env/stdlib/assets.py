"""`$asset(ref)`: an asset's metadata in expressions."""
from __future__ import annotations

from typing import Any

from ..assets.store import SUBMITTED
from ..expr import Call, ExprError, _describe, function

__all__ = ["asset_info"]


@function("asset(ref)",
          "An asset's details, or null for null: `id name type media_type size hash caption alt tags text submitted` "
          "(`text` is a text file's content or the text a describe host extracted; `caption` falls back to the "
          "described one). `ref` is an asset id or an `asset` property.", min_args=1, max_args=1)
def _asset(call: Call) -> Any:
    ref = call.arg(0)
    if ref is None:
        return None
    return asset_info(call.scope.world, ref, call.source)


def asset_info(world: Any, ref: Any, source: str | None) -> dict[str, Any]:
    store: Any = getattr(world, "assets", None)
    if isinstance(ref, dict) and "id" in ref:
        ref = ref["id"]
    asset = store.get(ref) if store is not None else None
    if asset is None:
        known = ", ".join(sorted(store.assets)[:8]) if store is not None else ""
        raise ExprError(f"$asset: {_describe(ref)} is not an asset (assets: {known or 'none'})", source)
    from ..assets.describe import described

    found = described(world, asset) if asset.describe else {}
    text = store.text(asset)
    return {"id": asset.id, "name": asset.name, "type": asset.kind, "media_type": asset.media_type,
            "size": asset.size, "hash": asset.hash, "caption": asset.caption or found.get("caption") or "",
            "alt": asset.alt, "tags": list(asset.tags), "text": text if text is not None else found.get("text"),
            "submitted": asset.id.startswith(SUBMITTED)}
