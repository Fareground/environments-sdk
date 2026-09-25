"""What one model call cost, for every caller that counts it — participants, hosts and the authoring loop.

A provider's reply says what the call spent (Anthropic's ``input_tokens``…, OpenAI's ``prompt_tokens``…). A reply that
says nothing, or not as whole numbers, still cost something: the prompt's rough size stands in for it and the call is
flagged as unreported, so a token budget still binds and the run (or the session) says its counts are estimates.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["CallUsage", "call_usage", "rough_tokens"]

#: Characters per token when a size has to be estimated.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class CallUsage:
    """One call's tokens: fresh input (not read from the prompt cache), output, cache reads and writes, and whether
    the provider left them unreported (the input is then an estimate)."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    unreported: bool = False

    def counts(self) -> dict[str, int]:
        """The four token counts by name."""
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens, "cache_write_tokens": self.cache_write_tokens}


def rough_tokens(chars: int) -> int:
    """Roughly how many tokens ``chars`` characters of text hold."""
    return chars // _CHARS_PER_TOKEN


def call_usage(usage: Any, provider: str, prompt_tokens: int) -> CallUsage:
    """What a call whose reply carried ``usage`` (a response's ``usage``, from ``provider``: ``"anthropic"`` or
    ``"openai"``) spent; ``prompt_tokens`` (a rough count of what was sent) stands in when the reply reports none."""
    if provider == "anthropic":
        fresh, output = _whole(usage, "input_tokens"), _whole(usage, "output_tokens")
        read, written = _whole(usage, "cache_read_input_tokens", 0), _whole(usage, "cache_creation_input_tokens", 0)
    else:
        prompt, output = _whole(usage, "prompt_tokens"), _whole(usage, "completion_tokens")
        read, written = _whole(_field(usage, "prompt_tokens_details"), "cached_tokens", 0), 0
        fresh = None if prompt is None or read is None else max(0, prompt - read)
    if fresh is None or output is None or read is None or written is None:
        return CallUsage(input_tokens=prompt_tokens, unreported=True)
    return CallUsage(fresh, output, read, written)


def _whole(owner: Any, name: str, missing: int | None = None) -> int | None:
    """``owner``'s ``name`` when it is a whole number ≥ 0; ``missing`` when it is absent (None: it must be there);
    None when it is anything else."""
    value = _field(owner, name)
    if value is None:
        return missing
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _field(owner: Any, name: str) -> Any:
    """``owner``'s ``name``: an attribute of a client's response object, or a key of the plain dict some clients and
    proxies return instead."""
    if owner is None:
        return None
    return owner.get(name) if isinstance(owner, Mapping) else getattr(owner, name, None)

