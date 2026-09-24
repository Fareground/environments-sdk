"""Asset kinds: which files an environment may carry, how each is recognised, and how large it may be.

A kind is decided from the file's content, never from its name alone: a contract file's extension must agree
with its bytes, and a file a participant submits is recognised from its bytes only (its name is untrusted).
"""
from __future__ import annotations

from pathlib import PurePath

__all__ = ["KINDS", "EXTENSIONS", "MAX_BYTES", "HARD_MAX_BYTES", "MAX_FOLDER_FILES", "MAX_CATALOG_BYTES",
           "MAX_TEXT_CHARS", "kind_of_name", "sniff", "agrees"]

#: Every asset kind, with what it is for.
KINDS: dict[str, str] = {
    "image": "a picture: png, jpg, webp or gif",
    "pdf": "a PDF document",
    "text": "plain text or markdown (UTF-8)",
    "audio": "a sound recording: wav or mp3",
    "file": "any other file (delivered to agents by reference only)",
}

#: File extension → (kind, media type).
EXTENSIONS: dict[str, tuple[str, str]] = {
    ".png": ("image", "image/png"), ".jpg": ("image", "image/jpeg"), ".jpeg": ("image", "image/jpeg"),
    ".webp": ("image", "image/webp"), ".gif": ("image", "image/gif"), ".pdf": ("pdf", "application/pdf"),
    ".txt": ("text", "text/plain"), ".md": ("text", "text/markdown"), ".markdown": ("text", "text/markdown"),
    ".wav": ("audio", "audio/wav"), ".mp3": ("audio", "audio/mpeg"),
}

#: Largest file of each kind by default (an asset's `max_bytes` or a parameter's may lower or raise it).
MAX_BYTES: dict[str, int] = {"image": 10_000_000, "pdf": 32_000_000, "text": 2_000_000, "audio": 25_000_000,
                             "file": 32_000_000}
#: No single file may be larger than this, whatever a contract declares.
HARD_MAX_BYTES = 100_000_000
#: Most files one `folder` asset may hold.
MAX_FOLDER_FILES = 1_000
#: Most bytes every file of one contract's catalog may add up to.
MAX_CATALOG_BYTES = 1_000_000_000
#: Longest text `$asset(ref).text` gives (longer text is cut, and says so).
MAX_TEXT_CHARS = 100_000


def kind_of_name(name: str) -> tuple[str, str]:
    """``(kind, media type)`` a file name's extension declares; unknown extensions are generic files."""
    return EXTENSIONS.get(PurePath(name).suffix.lower(), ("file", "application/octet-stream"))


def sniff(data: bytes) -> tuple[str, str]:
    """``(kind, media type)`` recognised from a file's first bytes; anything unrecognised is a generic file."""
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image", "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image", "image/webp"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if head.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] in (0xFB, 0xF3, 0xF2)):
        return "audio", "audio/mpeg"
    if _is_text(data):
        return "text", "text/plain"
    return "file", "application/octet-stream"


def _is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def agrees(declared: tuple[str, str], data: bytes) -> str | None:
    """Why a file's bytes do not match the kind its extension declares, or None when they do."""
    kind, media = declared
    if kind == "file":
        return None
    found_kind, found_media = sniff(data)
    if kind == "text":
        return None if found_kind == "text" else "is not UTF-8 text"
    if found_kind != kind or (kind == "image" and found_media != media):
        return f"is not a {media} file (its content looks like {found_media})"
    return None
