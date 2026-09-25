"""Effect statements: assignment texts like ``$actor.cash -= 5``, parsed into a target and a compiled value.

Also what a deferred list of statements reads from its scope, so an ``after`` keeps exactly that."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..contract import one_or_many
from ..expr import Expr, ExprError, compile_expr, is_expr
from ..expr.base import RESERVED_ROOTS

__all__ = ["RESERVED_ROOTS", "Statement", "compile_statement", "statement_parts", "split_statement", "capture_roots",
           "structured_capture_roots"]


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


def split_statement(source: str) -> tuple[str, str, str] | None:
    """``(left, operator, right)`` for an assignment text, or None when it is not one."""
    depth, quote, i, n = 0, None, 0, len(source)
    while i < n:
        ch = source[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0:
            for op in ("+=", "-=", "*=", "/="):
                if source.startswith(op, i):
                    return source[:i].strip(), op, source[i + 2:].strip()
            if ch == "=" and not source.startswith("==", i) and (i == 0 or source[i - 1] not in "=!<>"):
                return source[:i].strip(), "=", source[i + 1:].strip()
        i += 1
    return None


def statement_parts(source: str) -> tuple[str | None, tuple[tuple[str, str], ...], str | None, str, str]:
    """``(base, steps, local, op, value)`` of an assignment; raises :class:`ExprError` if malformed.

    ``$total = 3`` → local ``total``. ``$world.board[$r][$c] = x`` → base ``$world`` and steps
    ``(("field", "board"), ("index", "$r"), ("index", "$c"))``. ``$entity(x).bag.apples += 1`` →
    base ``$entity(x)`` and two field steps.
    """
    parts = split_statement(source)
    if parts is None or not parts[0].startswith("$") or not parts[2]:
        raise ExprError(
            "an effect text must be an assignment like `$actor.cash -= 5` or `$total = $params.qty * 2`",
            source,
        )
    left, op, right = parts
    if _NAME.match(left[1:]):
        return None, (), left[1:], op, right
    base, steps = _target_steps(left, source)
    if not any(kind == "field" for kind, _ in steps):
        raise ExprError("the left side must name a property, like `$actor.cash`, `$entity(x).cash` or "
                        "`$world.board[$i][$j]`", source)
    return base, steps, None, op, right


def _target_steps(left: str, source: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*", left)
    if match is None:
        raise ExprError("the left side must start with a root like `$actor` or `$world`", source)
    i = match.end()
    if i < len(left) and left[i] == "(":
        i = _closing(left, i, "(", ")", source) + 1
    base = left[:i]
    steps: list[tuple[str, str]] = []
    while i < len(left):
        ch = left[i]
        if ch == ".":
            field = re.match(r"[A-Za-z_][A-Za-z0-9_]*", left[i + 1:])
            if field is None:
                raise ExprError("a `.` must be followed by a property name", source)
            steps.append(("field", field.group(0)))
            i += 1 + field.end()
        elif ch == "[":
            close = _closing(left, i, "[", "]", source)
            index = left[i + 1:close].strip()
            if not index:
                raise ExprError("an element assignment looks like `$world.board[$i] = x`", source)
            steps.append(("index", index))
            i = close + 1
        elif ch.isspace():
            i += 1
        else:
            raise ExprError(f"unexpected `{ch}` on the left side of the assignment", source)
    return base, tuple(steps)


def _closing(text: str, start: int, opening: str, closing: str, source: str) -> int:
    depth, quote = 0, None
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return i
    raise ExprError(f"`{opening}` is never closed on the left side of the assignment", source)


@dataclass(frozen=True)
class Statement:
    source: str
    base: Expr | None
    #: ``("field", name)`` or ``("index", compiled expression)`` steps after the base.
    steps: tuple[tuple[str, Any], ...]
    local: str | None
    op: str
    value: Expr


@lru_cache(maxsize=8_192)
def compile_statement(source: str) -> Statement:
    base, steps, local, op, right = statement_parts(source)
    value = compile_expr(right)
    if local is not None:
        if local in RESERVED_ROOTS:
            raise ExprError(f"${local} is a reserved name, so a local cannot be called that; rename the local "
                            f"(e.g. ${local}_value) or assign to one of its fields", source)
        return Statement(source, None, (), local, op, value)
    assert base is not None
    compiled = tuple((kind, compile_expr(text) if kind == "index" else text) for kind, text in steps)
    return Statement(source, compile_expr(base), compiled, None, op, value)


@lru_cache(maxsize=8_192)
def capture_roots(sources: tuple[str, ...]) -> frozenset[str] | None:
    """External reads of straight-line assignments; calls may read implicit scope."""
    needed: set[str] = set()
    assigned: set[str] = set()
    try:
        for source in sources:
            statement = compile_statement(source)
            expressions = [statement.value]
            if statement.base is not None:
                expressions.append(statement.base)
            expressions.extend(step for kind, step in statement.steps if kind == "index")
            if any(expr.functions for expr in expressions):
                return None
            reads = set().union(*(expr.roots for expr in expressions))
            if statement.local is not None and statement.op != "=":
                reads.add(statement.local)
            needed.update(reads - assigned)
            if statement.local is not None:
                assigned.add(statement.local)
    except ExprError:
        return None  # Preserve the original error at execution, rather than moving it to scheduling.
    return frozenset(needed)


@lru_cache(maxsize=8_192)
def structured_capture_roots(source: str) -> frozenset[str] | None:
    """Conservative reads across control flow; retain all possibly needed outer locals.

    Unlike the straight-line analysis, no assignments remove dependencies. This
    preserves incoming values on paths where a branch/loop never assigns them.
    Calls and other operations may inspect implicit scope, so keep it in full.
    """
    needed: set[str] = set()

    def expression(raw: Any, *, condition: bool = False) -> None:
        if isinstance(raw, str) and (condition or is_expr(raw)):
            compiled = compile_expr(raw)
            if compiled.functions:
                raise ValueError("implicit call scope")
            needed.update(compiled.roots)
        elif not condition and isinstance(raw, (dict, list)):
            for item in raw.values() if isinstance(raw, dict) else raw:
                expression(item)

    def walk(effects: Any) -> None:
        for effect in one_or_many(effects) or []:
            if isinstance(effect, str):
                roots = capture_roots((effect,))
                if roots is None:
                    raise ValueError("implicit assignment scope")
                needed.update(roots)
            elif isinstance(effect, dict):
                if "if" in effect and set(effect) <= {"if", "then", "else"}:
                    expression(effect["if"], condition=True)
                    walk(effect.get("then"))
                    walk(effect.get("else"))
                elif "each" in effect and set(effect) <= {"each", "as", "where", "do"}:
                    expression(effect["each"])
                    expression(effect.get("where"), condition=True)
                    walk(effect.get("do"))
                elif "repeat" in effect and set(effect) <= {"repeat", "while", "do"}:
                    expression(effect["repeat"])
                    expression(effect.get("while"), condition=True)
                    walk(effect.get("do"))
                elif "after" in effect and set(effect) <= {"after", "do"}:
                    expression(effect["after"])
                    walk(effect.get("do"))
                else:
                    raise ValueError("implicit operation scope")
            else:
                raise ValueError("unknown effect shape")

    try:
        walk(json.loads(source))
    except (ExprError, ValueError, TypeError, RecursionError):
        return None
    return frozenset(needed)
