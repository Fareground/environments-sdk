"""Word-game rules: Wordle feedback, hangman masks, anagrams."""
from __future__ import annotations

from collections import Counter
from typing import Any

from ..expr import Call, derived, function
from ._args import fail, optional_text, text_arg

GREEN, YELLOW, GRAY = "green", "yellow", "gray"


def wordle_feedback(guess: str, answer: str) -> list[str]:
    """Per-letter feedback. Greens are placed first; a yellow is given only while the answer still has an
    unmatched copy of that letter, so repeated letters are never over-reported."""
    marks = [GRAY] * len(guess)
    unmatched: Counter = Counter()
    for position, (g, a) in enumerate(zip(guess, answer)):
        if g == a:
            marks[position] = GREEN
        else:
            unmatched[a] += 1
    for position, g in enumerate(guess):
        if marks[position] != GREEN and unmatched[g] > 0:
            marks[position] = YELLOW
            unmatched[g] -= 1
    return marks


@function("wordle_feedback(guess, answer)",
          "Wordle marks per letter of `guess` against `answer` (same length, case-insensitive): green (right place), "
          "yellow (elsewhere, respecting repeated letters), gray.",
          min_args=2, max_args=2)
def _wordle_feedback(call: Call) -> list[str]:
    guess, answer = text_arg(call, 0, "the guess text").lower(), text_arg(call, 1, "the answer text").lower()
    if len(guess) != len(answer):
        raise fail(call,
                   f"the guess has {len(guess)} letters but the answer has {len(answer)}; validate the guess length "
                   "first")
    return wordle_feedback(guess, answer)  # fixed labels, never participant text


def _letters(call: Call, value: Any) -> set:
    if value is None:
        return set()
    if isinstance(value, str):
        return {ch.lower() for ch in value}
    if isinstance(value, (list, tuple)):
        out: set = set()
        for item in value:
            if not isinstance(item, str):
                raise fail(call, f"revealed letters must be text, got {item!r}")
            out.update(ch.lower() for ch in item)
        return out
    raise fail(call, "revealed letters must be a list of letters or a text of letters")


@function("mask(word, revealed, hidden?)",
          "Hangman view of `word`: letters in `revealed` (a list or text, case-insensitive) shown, other letters and "
          "digits replaced by `hidden` (default _); spaces and punctuation always shown.",
          min_args=2, max_args=3)
def _mask(call: Call) -> str:
    word = text_arg(call, 0, "the secret word")
    revealed = call.arg(1)
    shown = _letters(call, revealed)
    hidden = optional_text(call, 2, "_", "one hiding character")
    if len(hidden) != 1:
        raise fail(call, f"the hiding character must be exactly one character, got {len(hidden)}")
    out = "".join(ch if (not ch.isalnum() or ch.lower() in shown) else hidden for ch in word)
    return derived(out, word, hidden)


@function("anagram(a, b)",
          "True when the two texts use exactly the same letters (case, spaces and punctuation ignored).",
          min_args=2, max_args=2)
def _anagram(call: Call) -> bool:
    def letters(text: str) -> Counter:
        return Counter(ch.lower() for ch in text if ch.isalnum())

    return letters(text_arg(call, 0)) == letters(text_arg(call, 1))
