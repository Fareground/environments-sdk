"""Attachments as model message parts, per provider, with a text fallback.

Anthropic (Messages API)::

    image → {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "<base64>"}}
    pdf   → {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "<base64>"},
             "title": name, "context": caption}
    text  → {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "<text>"},
             "title": name, "context": caption}

OpenAI-compatible (chat completions)::

    image → {"type": "image_url", "image_url": {"url": "data:image/png;base64,<base64>"}}
    pdf   → {"type": "file", "file": {"filename": name, "file_data": "data:application/pdf;base64,<base64>"}}
    audio → {"type": "input_audio", "input_audio": {"data": "<base64>", "format": "wav" | "mp3"}}
    text  → {"type": "text", "text": "<name>:\\n<text>"}

Every part is preceded by a text part holding the attachment's compact reference, so the model knows which file
is which. An attachment whose type the model does not take (``media``) is not sent as content: the text the agent
reads already carries its reference — the caption and alt text a text-only model reads. Text from submitted files
keeps its «» quotes. Host requests carry attachments as plain JSON (:func:`host_attachments`); :class:`Carried`
reads them back for the reference host adapters.
"""
from __future__ import annotations

from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence

from ..expr.template import format_value
from .delivery import Attachment

__all__ = ["ANTHROPIC_MEDIA", "OPENAI_MEDIA", "anthropic_parts", "openai_parts", "media_set", "host_attachments",
           "Carried", "without_content"]

#: Attachment types each provider's models take by default.
ANTHROPIC_MEDIA = frozenset({"image", "pdf", "text"})
OPENAI_MEDIA = frozenset({"image", "pdf", "audio", "text"})


def media_set(media: Optional[Collection[str]], default: Collection[str], allowed: Collection[str]) -> frozenset:
    """The attachment types a participant sends as real content (``None``: the provider default; empty: text only)."""
    if media is None:
        return frozenset(default)
    chosen = frozenset(media)
    unknown = sorted(chosen - set(allowed))
    if unknown:
        raise ValueError(f"media must name attachment types this provider takes ({', '.join(sorted(allowed))}), "
                         f"got {', '.join(unknown)}")
    return chosen


def _label(item: Any) -> Dict[str, Any]:
    return {"type": "text", "text": f"Attached: {item.reference}"}


def _text_content(item: Any) -> str:
    content = item.text() or ""
    return format_value(content) if item.untrusted else content


def anthropic_parts(attachments: Sequence[Any], media: Collection[str]) -> List[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = []
    for item in attachments:
        if item.type not in media:
            continue
        parts.append(_label(item))
        described = {"title": str.__str__(item.name), **({"context": str.__str__(item.caption)} if item.caption else {})}
        if item.type == "image":
            parts.append({"type": "image", "source": {"type": "base64", "media_type": item.media_type,
                                                      "data": item.base64()}})
        elif item.type == "pdf":
            parts.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                         "data": item.base64()}, **described})
        elif item.type == "text":
            parts.append({"type": "document", "source": {"type": "text", "media_type": "text/plain",
                                                         "data": _text_content(item)}, **described})
    return parts


def openai_parts(attachments: Sequence[Any], media: Collection[str]) -> List[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = []
    for item in attachments:
        if item.type not in media:
            continue
        parts.append(_label(item))
        if item.type == "image":
            parts.append({"type": "image_url", "image_url": {"url": f"data:{item.media_type};base64,{item.base64()}"}})
        elif item.type == "pdf":
            parts.append({"type": "file", "file": {"filename": str.__str__(item.name),
                                                   "file_data": f"data:application/pdf;base64,{item.base64()}"}})
        elif item.type == "audio":
            parts.append({"type": "input_audio", "input_audio": {
                "data": item.base64(), "format": "wav" if item.media_type == "audio/wav" else "mp3"}})
        elif item.type == "text":
            parts.append({"type": "text", "text": f"{str.__str__(item.name)}:\n{_text_content(item)}"})
    return parts


def host_attachments(attachments: Sequence[Attachment]) -> List[Dict[str, Any]]:
    """Attachments inside a host request: metadata with the content (base64 `data`, or `text` for text files)."""
    return [item.to_dict(data=True) for item in attachments]


class Carried:
    """An attachment as a host request carries it (:func:`host_attachments`), read like an :class:`Attachment`."""

    def __init__(self, data: Mapping[str, Any]):
        self._data = data

    type = property(lambda self: str(self._data.get("type", "file")))
    media_type = property(lambda self: str(self._data.get("media_type", "application/octet-stream")))
    name = property(lambda self: str(self._data.get("name", "")))
    caption = property(lambda self: str(self._data.get("caption", "")))
    untrusted = property(lambda self: bool(self._data.get("untrusted")))

    @property
    def reference(self) -> str:
        caption = self._data.get("caption") or self._data.get("alt")
        return f"[{self.type} {self.name}{': ' + repr(caption) if caption else ''}]"

    def base64(self) -> str:
        return str(self._data.get("data", ""))

    def text(self) -> Optional[str]:
        text = self._data.get("text")
        return text if isinstance(text, str) else None


def without_content(attachments: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Attachment metadata without the content, for the JSON text of a host request."""
    return [{key: value for key, value in item.items() if key not in ("data", "text")} for item in attachments]
