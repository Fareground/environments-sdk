"""Host understanding of files: a `describe` host writes a caption and extracted text for an image, PDF or
recording, once, when the world is built.

The answer is recorded on the host tape keyed by the file's content hash, so snapshots, clones, forks and replays
read it and never ask again. Without a bound host the asset's own caption (or alt text) stands in, recorded the
same way. Host text reaches agents untrusted.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Mapping

from ..expr import Untrusted
from ..host.protocols import HostError
from ..host.tape import consult
from .delivery import Attachment
from .multimodal import host_attachments
from .store import Asset

if TYPE_CHECKING:
    from ..runtime import Env

__all__ = ["described", "describe_assets", "MAX_CAPTION_CHARS"]

#: Longest caption a describe host may write.
MAX_CAPTION_CHARS = 1_000
_MAX_TEXT_CHARS = 100_000


def described(world: Any, asset: Asset) -> Dict[str, Any]:
    """``{"caption", "text"}`` the asset's describe host wrote (recorded, replayed, or the fallback)."""
    request = {"task": "describe", "asset": {"id": asset.id, "name": asset.name, "type": asset.kind,
                                              "media_type": asset.media_type, "caption": asset.caption,
                                              "alt": asset.alt, "tags": list(asset.tags)}}

    def ask(adapter: Any) -> Any:
        full = {**request, "attachments": host_attachments([Attachment(asset, world.assets)])}
        return adapter.describe(full)

    answer = consult(world, service=str(asset.describe), method="describe", site=f"assets.{asset.id}",
                     identity={"hash": asset.hash}, ask=ask, validate=_answer, moment=False,
                     fallback=lambda: {"caption": asset.caption or asset.alt, "text": ""})
    return {key: Untrusted(value) if isinstance(value, str) and value else value for key, value in answer.items()}


def _answer(answer: Any) -> Dict[str, str]:
    if not isinstance(answer, Mapping):
        raise HostError('a describe answer is {"caption": text, "text": text}')
    caption, text = answer.get("caption", ""), answer.get("text", "")
    if not isinstance(caption, str) or not isinstance(text, str):
        raise HostError("`caption` and `text` must be text")
    if len(caption) > MAX_CAPTION_CHARS:
        raise HostError(f"the caption is longer than {MAX_CAPTION_CHARS:,} characters")
    if len(text) > _MAX_TEXT_CHARS:
        raise HostError(f"the text is longer than {_MAX_TEXT_CHARS:,} characters")
    return {"caption": caption, "text": text}


def describe_assets(env: "Env") -> None:
    """Describe every asset that names a host, before round 1; copies of the run then start from the answers."""
    store = env.world.assets
    pending = [asset for asset in store.assets.values() if asset.describe]
    if not pending or env.world.round:
        return
    from ..snapshot import take_snapshot

    with env._lock:
        for asset in pending:
            described(env.world, asset)
        env.world.journal.clear()
        env.origin.base = take_snapshot(env)
