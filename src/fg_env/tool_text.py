"""Tool descriptions a model can plan with: limits in words, usage caps, and actions that share one tool.

A model counts words, not characters, so a text limit is stated as both (``Up to 400 characters (about 50 words).``);
the word count is deliberately low, because models write longer words than average prose and overran a stated "about
60 words" on almost half their first tries. A per-turn or per-round cap is stated before the model spends a call
learning it. Actions offered inside one shared tool (a mechanism's ``tools: one``) are each described on their own
line with the arguments they take, and an argument several of them take keeps every constraint: one merged schema of
their common type (enums joined, bounds widened), with each action's own range or choices in its description. A
refusal names what the agent can call in the form its tools take (``Use hall with action: speak or yield.``).
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["text_limit", "cut_text", "usage_limits", "shared_description", "shared_param", "compact_ids",
           "offer_text", "free_reads"]

#: Characters per word in a stated limit: high on purpose, so text written to the word count fits the characters.
CHARS_PER_WORD = 8
#: Schema keys that constrain a value, merged across the actions sharing an argument.
_BOUNDS = ("minimum", "maximum", "maxLength", "minItems", "maxItems")
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
    """``text`` cut to at most ``limit`` characters: after the last sentence that fits, else at the last word that fits."""
    if len(text) <= limit:
        return text
    head = text[:limit + 1]  # one character more: a sentence ending exactly at the limit is followed by a space
    ends = [match.end() for match in _SENTENCE_END.finditer(head) if match.end() <= limit]
    if ends:
        return text[:ends[-1]]
    space = head.rfind(" ")
    return (text[:space] if space > 0 else text[:limit]).rstrip()


def usage_limits(per_turn: Optional[int], per_round: Optional[int]) -> str:
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


def offer_text(plain: Sequence[str], shared: Sequence[Tuple[str, Sequence[str]]]) -> str:
    """What an agent can call now, as its tools take it: ``plain`` actions by name, ``shared`` as (tool, choices)."""
    parts = [f"Available actions: {', '.join(plain)}."] if plain else []
    parts += [f"Use {tool} with action: {_or_list(choices)}." for tool, choices in shared]
    return " ".join(parts) or "No actions are available now — call end_turn."


def _or_list(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} or {items[-1]}"


def shared_description(group: str, members: Sequence[Tuple[str, str, Sequence[str]]], staged: bool) -> str:
    """The description of a shared tool: ``members`` are (choice name, description, arguments) of the legal actions."""
    lines = [f"{group.replace('_', ' ').capitalize()}: choose one `action` and pass only the arguments it takes. "
             "Only these actions are available now:"]
    for choice, description, arguments in members:
        takes = f" Arguments: {', '.join(arguments)}." if arguments else " No arguments."
        lines.append(f"- {choice}: {description}{takes}")
    if staged:
        lines.append("(Committed when everyone has chosen.)")
    return "\n".join(lines)


def shared_param(entries: Sequence[Tuple[str, Dict[str, Any]]], total: int) -> Dict[str, Any]:
    """One property of a shared tool from the schemas of the actions taking it (``entries``: choice name, schema)."""
    shapes: List[Dict[str, Any]] = []
    for _, schema in entries:
        bare = {key: value for key, value in schema.items() if key != "description"}
        if bare not in shapes:
            shapes.append(bare)
    merged = dict(shapes[0]) if len(shapes) == 1 else _merged(shapes)
    out: Dict[str, Any] = merged if merged is not None else {"anyOf": shapes}
    described: Dict[str, List[str]] = {}
    for who, schema in entries:
        own = _narrower(schema, out) if len(shapes) > 1 and merged is not None else ""
        text = " ".join(part for part in (schema.get("description", ""), own) if part)
        described.setdefault(text, []).append(who)
    texts = [text for text in described if text]
    if len(described) == 1:
        about = texts[0] if texts else ""
    else:
        about = " ".join(f"For {', '.join(who)}: {text.rstrip('.')}." for text, who in described.items() if text)
    users = "" if len(entries) == total else f"Only for {', '.join(who for who, _ in entries)}."
    description = " ".join(part for part in (users, about) if part)
    if description:
        out["description"] = description
    return out


def _merged(shapes: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """One schema accepting what each of ``shapes`` accepts, when they share a type; None when they do not."""
    kinds = {shape.get("type") for shape in shapes}
    if len(kinds) != 1 or None in kinds:
        return None
    out: Dict[str, Any] = {"type": kinds.pop()}
    if all("enum" in shape for shape in shapes):
        joined: List[Any] = []
        for shape in shapes:
            joined.extend(value for value in shape["enum"] if value not in joined)
        out["enum"] = joined
    for key in _BOUNDS:
        values = [shape[key] for shape in shapes if shape.get(key) is not None]
        if len(values) == len(shapes):
            out[key] = min(values) if key in ("minimum", "minItems") else max(values)
    for key in ("multipleOf", "items", "uniqueItems", "default"):
        values = [shape.get(key) for shape in shapes]
        if values[0] is not None and all(value == values[0] for value in values):
            out[key] = values[0]
    return out


def _narrower(schema: Dict[str, Any], merged: Dict[str, Any]) -> str:
    """How ``schema`` constrains its value more than the merged schema does, in words."""
    parts = []
    enum = schema.get("enum")
    if enum is not None and enum != merged.get("enum"):
        parts.append("One of: " + ", ".join(str(value) for value in enum) + ".")
    low, high = schema.get("minimum"), schema.get("maximum")
    if (low, high) != (merged.get("minimum"), merged.get("maximum")) and (low is not None or high is not None):
        parts.append(f"From {low} to {high}." if low is not None and high is not None else
                     (f"At least {low}." if low is not None else f"At most {high}."))
    if schema.get("maxLength") is not None and schema.get("maxLength") != merged.get("maxLength"):
        parts.append(text_limit(schema["maxLength"]))
    return " ".join(parts)


def compact_ids(ids: Sequence[str]) -> str:
    """Ids as short text: runs of numbered ids become ranges (``u1–u150``); a very long listing is cut with a count."""
    parts: List[str] = []
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


def _close_run(parts: List[str], first: str, last: str, length: int) -> None:
    """A run of more than two consecutive ids as a range; a shorter one id by id."""
    if length > 2:
        parts.append(f"{first}–{last}")
    elif length == 2:
        parts += (first, last)
    elif length == 1:
        parts.append(first)


@lru_cache(maxsize=1 << 16)
def _numbered(key: str) -> Tuple[str, str]:
    """``key`` split into its text and its trailing digits ("" when it has none) — without a pattern match for the
    usual ASCII digits, and kept per id: every turn's tools list and split the same candidates' ids again."""
    stem = key.rstrip("0123456789")
    if stem and stem[-1].isdecimal():  # digits of another script before or instead of them: the pattern decides
        match = _NUMBERED.match(key)
        return (match.group(1), match.group(2)) if match else (key, "")
    return stem, key[len(stem):]
