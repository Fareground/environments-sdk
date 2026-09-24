"""Tool descriptions a model can plan with: limits in words and usage caps.

A model counts words, not characters, so a text limit is stated as both (``Up to 400 characters (about 50 words).``);
the word count is deliberately low, because models write longer words than average prose and overran a stated "about
60 words" on almost half their first tries. A per-turn or per-round cap is stated before the model spends a call
learning it. A refusal names what the agent can call.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache

__all__ = ["text_limit", "cut_text", "usage_limits", "compact_ids", "offer_text", "free_reads"]

#: Characters per word in a stated limit: high on purpose, so text written to the word count fits the characters.
CHARS_PER_WORD = 8
#: A compact id listing is cut after this many parts.
_LISTED_IDS = 60
_NUMBERED = re.compile(r"^(.*?)(\d+)$")
#: Where a sentence ends: its closing mark, any closing quotes or brackets, then a space or the end of the text.
_SENTENCE_END = re.compile(r"[.!?…][\"'»”’)\]]*(?=\s|$)")


def text_limit(max_len: int, overflow: str = "refuse") -> str:
    """``Up to 400 characters (about 50 words).`` — the word count at one ratio, rounded down to a multiple of 5
    from 10 words up; with ``overflow="truncate"`` it adds that longer text is cut."""
    words = max_len // CHARS_PER_WORD
    rounded = max(1, words if words < 10 else words // 5 * 5)
    limit = f"Up to {max_len} characters (about {rounded} word{'s' if rounded != 1 else ''})"
    if overflow == "truncate":
        return limit + "; longer text is cut after the last full sentence that fits."
    return limit + "."


def cut_text(text: str, limit: int) -> str:
    """``text`` cut to at most ``limit`` characters: after the last sentence that fits, else at the last word that fits.
    """
    if len(text) <= limit:
        return text
    head = text[:limit + 1]  # one character more: a sentence ending exactly at the limit is followed by a space
    ends = [match.end() for match in _SENTENCE_END.finditer(head) if match.end() <= limit]
    if ends:
        return text[:ends[-1]]
    space = head.rfind(" ")
    return (text[:space] if space > 0 else text[:limit]).rstrip()


def usage_limits(per_turn: int | None, per_round: int | None) -> str:
    """``Once per turn.`` / ``At most 3 times per round.`` — empty when the action has no cap."""
    parts = [f"{_times(count)} per {period}" for count, period in ((per_turn, "turn"), (per_round, "round"))
             if count is not None]
    if not parts:
        return ""
    text = " and ".join(parts)
    return text[0].upper() + text[1:] + "."


def _times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"at most {count} times")


def free_reads(allowance: int) -> str:
    """How many looks and inspects a turn has that use no tool call."""
    return f"Free: up to {allowance} reads (looks and inspects) per turn do not use a tool call."


def offer_text(names: Sequence[str]) -> str:
    """What an agent can call now."""
    return f"Available actions: {', '.join(names)}." if names else "No actions are available now — call end_turn."


def compact_ids(ids: Sequence[str]) -> str:
    """Ids as short text: runs of numbered ids become ranges (``u1–u150``); a very long listing is cut with a count."""
    parts: list[str] = []
    first = last = prefix = ""  # the current run of consecutive numbered ids: its ends, their text and last number
    number = length = 0
    for key in ids:
        stem, digits = _numbered(key)
        if digits and (digits[0] != "0" or digits == "0"):
            value = int(digits)
            if length and stem == prefix and value == number + 1:
                last, number, length = key, value, length + 1
                continue
            _close_run(parts, first, last, length)
            first = last = key
            prefix, number, length = stem, value, 1
            continue
        _close_run(parts, first, last, length)
        length = 0
        parts.append(key)
    _close_run(parts, first, last, length)
    if len(parts) > _LISTED_IDS:
        return ", ".join(parts[:_LISTED_IDS]) + f" and {len(parts) - _LISTED_IDS} more"
    return ", ".join(parts)


def _close_run(parts: list[str], first: str, last: str, length: int) -> None:
    """A run of more than two consecutive ids as a range; a shorter one id by id."""
    if length > 2:
        parts.append(f"{first}–{last}")
    elif length == 2:
        parts += (first, last)
    elif length == 1:
        parts.append(first)


@lru_cache(maxsize=1 << 16)
def _numbered(key: str) -> tuple[str, str]:
    """``key`` split into its text and its trailing digits ("" when it has none) — without a pattern match for the
    usual ASCII digits, and kept per id: every turn's tools list and split the same candidates' ids again."""
    stem = key.rstrip("0123456789")
    if stem and stem[-1].isdecimal():  # digits of another script before or instead of them: the pattern decides
        match = _NUMBERED.match(key)
        return (match.group(1), match.group(2)) if match else (key, "")
    return stem, key[len(stem):]
