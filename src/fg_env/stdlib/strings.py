"""Text functions. Text derived from participant text stays marked as participant text."""
from __future__ import annotations

import re
from typing import Any, List, Optional

from ..expr import MAX_TEXT_LEN, Call, check_size, charge, derived, function
from ._args import fail, int_arg, optional_text, text_arg
from .regex import PatternError, compile_pattern, search

#: Longest text `$similar` compares (its work grows with the product of both lengths).
MAX_SIMILAR_LEN = 1_000
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")  # linear: every repetition consumes a letter or digit


def _pieces(parts: List[str], *sources: Any) -> List[str]:
    return check_size([derived(part, *sources) for part in parts], None)


@function("split(text, separator?)", "Text cut into a list at each `separator` (default: runs of whitespace, ends trimmed).",
          min_args=1, max_args=2)
def _split(call: Call) -> List[str]:
    text = text_arg(call, 0)
    if len(call) < 2 or call.arg(1) is None:
        return _pieces(text.split(), text)
    separator = text_arg(call, 1, "the separator text")
    if not separator:
        raise fail(call, "the separator cannot be empty; use $chars(text) for single characters")
    return _pieces(text.split(separator), text)


@function("chars(text)", "The characters of text as a list.", min_args=1, max_args=1)
def _chars(call: Call) -> List[str]:
    text = text_arg(call, 0)
    return _pieces(list(text), text)


@function("words(text)", "The words in text (letters and digits, keeping inner apostrophes and hyphens), punctuation dropped.",
          min_args=1, max_args=1)
def _words(call: Call) -> List[str]:
    text = text_arg(call, 0)
    return _pieces(_WORD.findall(text), text)


@function("upper(text)", "Upper-case text.", min_args=1, max_args=1)
def _upper(call: Call) -> str:
    text = text_arg(call, 0)
    return derived(check_size(text.upper(), call.source), text)


@function("title(text)", "Text with each word capitalised.", min_args=1, max_args=1)
def _title(call: Call) -> str:
    text = text_arg(call, 0)
    return derived(check_size(text.title(), call.source), text)


@function("trim(text)", "Text without leading and trailing whitespace.", min_args=1, max_args=1)
def _trim(call: Call) -> str:
    text = text_arg(call, 0)
    return derived(text.strip(), text)


@function("replace(text, old, new)", "Text with every `old` replaced by `new` (case-sensitive).", min_args=3, max_args=3)
def _replace(call: Call) -> str:
    text, old, new = text_arg(call, 0), text_arg(call, 1, "the text to find"), text_arg(call, 2, "the replacement text")
    if not old:
        raise fail(call, "the text to find cannot be empty")
    count = text.count(old)
    size = len(text) + count * (len(new) - len(old))
    if size > MAX_TEXT_LEN:
        raise fail(call, f"the result would be {size:,} characters; the limit is {MAX_TEXT_LEN:,}")
    charge(size, call.source)
    return derived(text.replace(old, new), text, new)


def _position(call: Call, index: int, length: int, default: Optional[int]) -> Optional[int]:
    if index >= len(call) or call.arg(index) is None:
        return default
    return int_arg(call, index, what="a character position (negative counts from the end)")


@function("substr(text, start, end?)", "Characters from `start` up to (not including) `end`; negative positions count from the end.",
          min_args=2, max_args=3)
def _substr(call: Call) -> str:
    text = text_arg(call, 0)
    start = _position(call, 1, len(text), 0)
    end = _position(call, 2, len(text), None)
    return derived(text[start:end], text)


@function("char_at(text, index)", "The character at `index` (negative counts from the end); an error when out of range.",
          min_args=2, max_args=2)
def _char_at(call: Call) -> str:
    text = text_arg(call, 0)
    index = int_arg(call, 1, what="a character position")
    if not -len(text) <= index < len(text):
        raise fail(call, f"position {index} is out of range for text of {len(text)} characters")
    return derived(text[index], text)


@function("starts_with(text, prefix)", "True when text begins with `prefix` (case-sensitive).", min_args=2, max_args=2)
def _starts_with(call: Call) -> bool:
    return text_arg(call, 0).startswith(text_arg(call, 1, "the prefix text"))


@function("ends_with(text, suffix)", "True when text ends with `suffix` (case-sensitive).", min_args=2, max_args=2)
def _ends_with(call: Call) -> bool:
    return text_arg(call, 0).endswith(text_arg(call, 1, "the suffix text"))


@function("index_of(text, part)", "Position of the first `part` in text (case-sensitive), or -1.", min_args=2, max_args=2)
def _index_of(call: Call) -> int:
    return text_arg(call, 0).find(text_arg(call, 1, "the text to find"))


@function("count_text(text, part)", "How many times `part` occurs in text, not overlapping (case-sensitive).",
          min_args=2, max_args=2)
def _count_text(call: Call) -> int:
    part = text_arg(call, 1, "the text to count")
    if not part:
        raise fail(call, "the text to count cannot be empty")
    return text_arg(call, 0).count(part)


@function("pad(text, width, fill?, side?)", "Text padded with `fill` (default a space) to `width` characters; `side` left (default), right or both.",
          min_args=2, max_args=4)
def _pad(call: Call) -> str:
    text = text_arg(call, 0)
    width = int_arg(call, 1, low=0, high=MAX_TEXT_LEN, what="the width")
    fill = optional_text(call, 2, " ", "a single fill character")
    if len(fill) != 1:
        raise fail(call, f"the fill must be exactly one character, got {len(fill)}")
    side = optional_text(call, 3, "left", "left, right or both")
    charge(max(0, width - len(text)), call.source)
    if side == "left":
        out = text.rjust(width, fill)
    elif side == "right":
        out = text.ljust(width, fill)
    elif side == "both":
        out = text.center(width, fill)
    else:
        raise fail(call, f"side must be left, right or both, got '{side}'")
    return derived(out, text, fill)


@function("repeat_text(text, n)", "Text repeated `n` times.", min_args=2, max_args=2)
def _repeat_text(call: Call) -> str:
    text = text_arg(call, 0)
    times = int_arg(call, 1, low=0, what="the number of repeats")
    size = len(text) * times
    if size > MAX_TEXT_LEN:
        raise fail(call, f"the result would be {size:,} characters; the limit is {MAX_TEXT_LEN:,}")
    charge(size, call.source)
    return derived(text * times, text)


@function("matches(text, pattern)",
          "True when the regular expression occurs in text. Linear-time subset: . [a-z] [^x] \\d \\w \\s ^ $ ( ) (?: ) | * + ? {m,n}; no backreferences or lookaround.",
          min_args=2, max_args=2)
def _matches(call: Call) -> bool:
    text = text_arg(call, 0)
    pattern = text_arg(call, 1, "a pattern")
    try:
        program = compile_pattern(pattern)
    except PatternError as exc:
        raise fail(call, f"invalid pattern: {exc}") from None
    return search(program, text, lambda steps: charge(steps, call.source))


@function("similar(a, b)", "How alike two texts are, 0–1: 1 minus the edit (Levenshtein) distance over the longer length. Case-sensitive.",
          min_args=2, max_args=2)
def _similar(call: Call) -> float:
    a, b = text_arg(call, 0), text_arg(call, 1)
    if max(len(a), len(b)) > MAX_SIMILAR_LEN:
        raise fail(call, f"compares texts up to {MAX_SIMILAR_LEN:,} characters; got {max(len(a), len(b)):,}")
    if not a and not b:
        return 1.0
    charge((len(a) + 1) * (len(b) + 1), call.source)
    return 1.0 - edit_distance(a, b) / max(len(a), len(b))


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (insertions, deletions, substitutions each cost 1)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]
