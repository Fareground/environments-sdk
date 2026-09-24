"""Unbalanced brackets in an expression are reported where they are, with what to add or remove."""
import pytest

import fg_env

BASE = {"name": "x", "types": {"p": {"agent": True, "props": {"c": 1}}}, "entities": {"a": {"type": "p"}},
        "actions": {"go": {"by": "p", "do": "$actor.c += 1"}}}


def _error(expression: str) -> str:
    return next(str(i) for i in fg_env.check({**BASE, "outputs": {"o": expression}}, rounds=0) if i.severity == "error")


@pytest.mark.parametrize("expression, expected", [
    ("$max(($entity(a).c, 2)",
     "the `(` at character 5 (after `$max(`) is never closed — add `)` where what it holds ends"),
    ("$entity(a).c + 1)", "the `)` at character 17 (after `$entity(a).c + 1)`) closes nothing — remove it, "
                          "or add the `(` it was meant to close"),
    ("[$entity(a).c, 2)", "the `[` at character 1 (after `[`) is closed by `)` at character 17 — close it with `]`"),
])
def test_an_unbalanced_bracket_names_its_position_and_the_fix(expression, expected):
    assert _error(expression) == f"outputs.o: syntax error: {expected} → expression: {expression}"


def test_brackets_inside_quoted_text_are_not_counted():
    assert "`$max(`" in _error("$max(('(', 2)")


def test_a_single_equals_still_suggests_double_equals():
    assert "compare with `==`" in _error("$entity(a).c = 2")
