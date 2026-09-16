"""The shared closed-unit-interval contract for Bernoulli probabilities."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .check import _Checker


def is_probability(value: Any) -> bool:
    # The bounds also reject infinities and NaN, without coercing booleans into numbers.
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 1


def check_literal_probability(checker: "_Checker", value: Any, path: str) -> None:
    if value is not None and not isinstance(value, str) and not is_probability(value):
        checker.error(path, f"chance must be a number from 0 to 1, got {value!r}",
                      "use a fraction from 0 to 1 (for example, 0.8 for 80%)")
