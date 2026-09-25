"""Expression syntax errors in plain words: which bracket or quote is unbalanced, where, and what to write."""
from __future__ import annotations

import keyword
import re

__all__ = ["syntax_message"]

_CLOSING = {"(": ")", "[": "]", "{": "}"}
_OPENING = {close: open_ for open_, close in _CLOSING.items()}
#: A word the language keeps for itself written as a map key (``{eager: 3, not: 0}``).
_RESERVED_KEY = re.compile(r"[{,]\s*(" + "|".join(sorted(keyword.kwlist, key=len, reverse=True)) + r")\s*:")
#: Characters of the expression quoted before a bracket to show where it is.
_CONTEXT = 24


def syntax_message(source: str, python_message: str) -> str:
    """The error to report for ``source``, which Python could not parse (``python_message``)."""
    if ("\n" in source or "\r" in source) and "unterminated string" in python_message:
        return ("syntax error: quoted text in the expression holds a line break, which ends it — for a line break in "
                "text write the two characters \\n inside the quotes (in JSON, \"\\\\n\"), or write the text in a "
                "template (outcome, say, show)")
    unbalanced = _unbalanced(source)
    if unbalanced is not None:
        return f"syntax error: {unbalanced}"
    bare = re.sub(r"'[^']*'|\"[^\"]*\"", "''", source)
    word = _RESERVED_KEY.search(bare)
    if word is not None:
        key = word.group(1)
        return (f"syntax error: {python_message} — `{key}` is a word the language itself uses, so as a map key it must "
                f"be quoted: '{key}': …")
    if re.search(r"(?<![=!<>])=(?!=)", bare):
        return f"syntax error: {python_message} — compare with `==` (a single `=` assigns, and only in effects)"
    return f"syntax error: {python_message}"


def _unbalanced(source: str) -> str | None:
    """What is wrong with the brackets or quotes of ``source``, with the fix, or None when they balance."""
    stack: list[tuple[str, int]] = []
    quote: tuple[str, int] | None = None
    index = 0
    while index < len(source):
        char = source[index]
        if quote is not None:
            if char == "\\":
                index += 1
            elif char == quote[0]:
                quote = None
        elif char in "'\"":
            quote = (char, index)
        elif char in _CLOSING:
            stack.append((char, index))
        elif char in _OPENING:
            if not stack:
                return (f"the `{char}` at character {index + 1} ({_at(source, index)}) closes nothing — remove it, "
                        f"or add the `{_OPENING[char]}` it was meant to close")
            opening, at = stack.pop()
            if _CLOSING[opening] != char:
                return (f"the `{opening}` at character {at + 1} ({_at(source, at)}) is closed by `{char}` at character "
                        f"{index + 1} — close it with `{_CLOSING[opening]}`")
        index += 1
    if quote is not None:
        return (f"the quote {quote[0]} at character {quote[1] + 1} ({_at(source, quote[1])}) is never closed — "
                f"add a closing {quote[0]}")
    if stack:
        opening, at = stack[-1]
        return (f"the `{opening}` at character {at + 1} ({_at(source, at)}) is never closed — add "
                f"`{_CLOSING[opening]}` where what it holds ends")
    return None


def _at(source: str, index: int) -> str:
    """The text leading up to and including ``source[index]``, to point at it."""
    start = max(0, index + 1 - _CONTEXT)
    return f"after `{'…' if start else ''}{source[start:index + 1]}`"
