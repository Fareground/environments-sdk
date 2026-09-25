"""Parsing, validating and compiling expressions: :func:`compile_expr` turns text into an :class:`Expr`.

The parser uses Python's ``ast`` module purely as a grammar: only whitelisted node types pass validation, and
:mod:`.codegen` compiles the validated tree to Python functions that contain none of the expression's text.
"""
from __future__ import annotations

import ast
import keyword
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .base import _BUDGET, EVAL_BUDGET, EXPRESSION_WORDS, ExprError, charge, nested_free
from .calls import _NO_KEY, EqualityGuard, Evaluator
from .codegen import _FUNC_PREFIX, _LITERAL_NAMES, _ROOT_PREFIX, Codegen
from .scope import Scope
from .syntax_hints import syntax_message

__all__ = ["Expr", "call_roots", "compile_expr", "filter_parts", "item_conditions"]

_MAX_SOURCE = 8_192
_MAX_NODES = 2_048

_ALLOWED = (
    ast.Expression, ast.Constant, ast.Name, ast.Load, ast.Attribute, ast.Subscript,
    ast.Call, ast.List, ast.Tuple, ast.Dict, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.IfExp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
    ast.Gt, ast.GtE, ast.In, ast.NotIn,
)
#: Marks a bare word or field that is a Python keyword (`class`, `$it.from`) so Python's parser takes it as a name.
_WORD_PREFIX = "__w_"


def _preprocess(source: str) -> str:
    """Map ``$root`` → ``__r_root``, ``$fn(`` → ``__f_fn(``, C-style boolean operators, and names that are Python
    keywords but not expression words → ``__w_name`` (a field after ``.`` is always a name: ``$it.in``)."""
    out: list[str] = []
    i, n, quote = 0, len(source), None
    while i < n:
        ch = source[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(source[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "$":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            name = source[i + 1:j]
            if not name and i + 1 < n and source[i + 1] in "\"'(":
                i += 1  # `$'text'` and `$(a + b)` mean just the text or the group
                continue
            if not name or name[0].isdigit():
                raise ExprError("'$' must be followed by a name, like $actor or $count(...)", source)
            k = j
            while k < n and source[k] == " ":
                k += 1
            out.append((_FUNC_PREFIX if k < n and source[k] == "(" else _ROOT_PREFIX) + name)
            i = j
            continue
        if (ch.isalpha() or ch == "_") and not (i and (source[i - 1].isalnum() or source[i - 1] == "_")):
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            word = source[i:j]
            field = i and source[i - 1] == "."
            out.append(_WORD_PREFIX + word if keyword.iskeyword(word) and (field or word not in EXPRESSION_WORDS)
                       else word)
            i = j
            continue
        if source.startswith("&&", i):
            out.append(" and ")
            i += 2
            continue
        if source.startswith("||", i):
            out.append(" or ")
            i += 2
            continue
        if ch == "!" and not source.startswith("!=", i):
            out.append(" not ")
            i += 1
            continue
        out.append(ch)
        i += 1
    if quote:
        raise ExprError(syntax_message(source, "unclosed quote"), source)
    return "".join(out)


@dataclass(frozen=True)
class Expr:
    """A compiled expression: evaluate it many times against different scopes."""

    source: str
    run: Evaluator
    roots: frozenset[str]
    functions: frozenset[str]
    symbols: frozenset[str]
    #: ``(root, field, field, ...)`` chains read from roots, e.g. ``("actor", "cash")``.
    paths: frozenset[tuple[str, ...]] = frozenset()
    #: ``(function, first_argument_symbol)`` pairs, e.g. ``("count", "buyer")``.
    calls: frozenset[tuple[str, str | None]] = frozenset()
    #: ``(function, first_argument_symbol, ("it", field, ...))`` — item fields read inside
    #: per-item arguments, e.g. ``("sum", "offer", ("it", "price"))``.
    item_paths: frozenset[tuple[str, str | None, tuple[str, ...]]] = frozenset()
    #: ``(("actor", "status"), "open")`` — a root field compared with a bare word.
    comparisons: frozenset[tuple[tuple[str, ...], str]] = frozenset()
    #: ``(function, first_argument_symbol, ("it", field), word)`` — the same inside per-item arguments.
    item_comparisons: frozenset[tuple[str, str | None, tuple[str, ...], str]] = frozenset()
    #: ``(function, signature)`` — built-in calls with the wrong number of arguments. Valid when the
    #: contract defines its own function of that name (a def shadows a built-in); reported otherwise.
    arity_errors: frozenset[tuple[str, str]] = frozenset()
    #: ``(root, name, argument count)`` — calls of a root's member, e.g. ``$pattern.season($it.sku)``.
    methods: frozenset[tuple[str, str, int]] = frozenset()
    #: ``(function, field, ...)`` chains read from what a function returned, e.g. ``("entity", "cash")`` for
    #: ``$entity(bo).cash``.
    call_paths: frozenset[tuple[str, ...]] = frozenset()

    def __call__(self, scope: Scope) -> Any:
        budget = _BUDGET
        if budget.hold:  # nested inside other work (a def, a record rule, a shared block): charge it
            if budget.shared and budget.hold == 1:  # an expression directly in a shared block: its own ceiling too
                budget.cap = min(budget.limit, budget.used + EVAL_BUDGET)
            budget.used += 1
            if budget.used > budget.limit or budget.used > budget.cap:
                charge(0, self.source)
        else:  # a top-level evaluation starts a fresh budget
            budget.used = 0
            budget.cap = EVAL_BUDGET
        try:
            return self.run(scope)
        except ExprError:
            raise
        except RecursionError:
            raise ExprError("evaluation nested too deeply", self.source) from None
        except (ArithmeticError, IndexError, KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ExprError(f"could not evaluate: {type(exc).__name__}: {str(exc)[:200]}", self.source) from None

    def rules_out(self, scope: Scope) -> Callable[[Any], bool] | None:
        """For evaluating this condition once per item (as ``$it``, each a top-level evaluation) over
        ``scope``: a test that is true for items it certainly does not hold for, so they need not be
        evaluated (see :class:`EqualityGuard`). None when no item can be ruled out that way."""
        return self._differs("guard", scope)

    def rules_in(self, scope: Scope) -> Callable[[Any], bool] | None:
        """For a condition that is only ``$it.field != value``, evaluated like :meth:`rules_out`: a test that is true
        for items it certainly holds for (their field differs, so ``==`` is certainly false), which need not be
        evaluated. None for any other condition."""
        return self._differs("unequal", scope)

    def _differs(self, kind: str, scope: Scope) -> Callable[[Any], bool] | None:
        """A test true for items whose field certainly differs from the value of the ``kind`` guard, if any."""
        guard: EqualityGuard | None = getattr(self.run, kind, None)
        if guard is None or not nested_free():  # nested evaluations charge a budget: evaluate every one
            return None
        key = guard.key(scope)
        if key is _NO_KEY:
            return None
        return lambda item: guard.rules_out(item, key)


@lru_cache(maxsize=16_384)
def compile_expr(source: str) -> Expr:
    """Parse and validate ``source`` once. Raises :class:`ExprError` on bad syntax."""
    if not isinstance(source, str) or not source.strip():
        raise ExprError("expression is empty", str(source))
    source = source.strip()
    if len(source) > _MAX_SOURCE:
        raise ExprError("expression is too long", source[:60] + "…")
    try:
        tree = ast.parse(_preprocess(source).strip(), mode="eval")
    except SyntaxError as exc:
        raise ExprError(syntax_message(source, str(exc.msg)), source) from None
    except (RecursionError, MemoryError):
        raise ExprError("expression is nested too deeply", source) from None
    except ValueError as exc:  # e.g. a NUL character
        raise ExprError(f"syntax error: {exc}", source) from None
    nodes = list(ast.walk(tree))
    if len(nodes) > _MAX_NODES:
        raise ExprError("expression is too large", source)
    _restore_words(nodes)
    for node in nodes:
        if not isinstance(node, _ALLOWED):
            raise ExprError(f"unsupported syntax ({type(node).__name__})", source)
        if isinstance(node, ast.Call) and (node.keywords or not (
            isinstance(node.func, ast.Name) and node.func.id.startswith(_FUNC_PREFIX) or _root_method(node.func)
        )):
            called = node.func.id if isinstance(node.func, ast.Name) and not node.keywords else None
            raise ExprError("only $functions can be called, with positional arguments"
                            + (f": write ${called}(...)" if called else ""), source)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and not node.value.id.startswith((_ROOT_PREFIX, _FUNC_PREFIX)) and node.value.id not in _LITERAL_NAMES:
            raise ExprError(f"'{node.value.id}.{node.attr}' reads a field of plain text: write "
                            f"${node.value.id}.{node.attr}",
                            source)
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExprError(f"private field '{node.attr}' cannot be read", source)
    compiler = Codegen(source)
    try:
        run = compiler.compile(tree.body)
    except (RecursionError, MemoryError):
        raise ExprError("expression is nested too deeply", source) from None
    return Expr(source, run, frozenset(compiler.roots), frozenset(compiler.functions),
                frozenset(compiler.symbols), frozenset(compiler.paths), frozenset(compiler.calls),
                frozenset(compiler.item_paths), frozenset(compiler.comparisons),
                frozenset(compiler.item_comparisons), frozenset(compiler.arity_errors), frozenset(compiler.methods),
                frozenset(compiler.call_paths))


#: What an item's own condition may read besides ``$it``: nothing that changes while a run plays.
_FIXED_ROOTS = frozenset({"it", "inputs"})
#: Entity fields that change without a property write.
_MOVING_FIELDS = frozenset({"at", "alive"})


@lru_cache(maxsize=1_024)
def item_conditions(source: str) -> tuple[tuple[str, Expr], ...] | None:
    """``$all(<type>, <condition>)``, alone or joined by ``and`` with more like it: each term's type word and its
    condition as an expression of ``$it``, when every condition reads nothing but its item's own properties and
    ``$inputs`` — so it can only change for an item whose properties change. None for any other expression (and an
    invalid one raises as :func:`compile_expr` does)."""
    compile_expr(source)
    tree = ast.parse(_preprocess(source.strip()).strip(), mode="eval")
    _restore_words(list(ast.walk(tree)))
    body = tree.body
    terms = body.values if isinstance(body, ast.BoolOp) and isinstance(body.op, ast.And) else [body]
    found = []
    for term in terms:
        if not (isinstance(term, ast.Call) and isinstance(term.func, ast.Name) and term.func.id == f"{_FUNC_PREFIX}all"
                and len(term.args) == 2 and isinstance(term.args[0], ast.Name)
                and not term.args[0].id.startswith((_ROOT_PREFIX, _FUNC_PREFIX))):
            return None
        nodes = list(ast.walk(term.args[1]))
        if any(isinstance(node, ast.Subscript) for node in nodes):
            return None
        for node in nodes:  # back to the language's spelling: the condition is an expression of its own
            if isinstance(node, ast.Name) and node.id.startswith(_ROOT_PREFIX):
                node.id = "$" + node.id[len(_ROOT_PREFIX):]
        condition = compile_expr(ast.unparse(term.args[1]))
        if condition.functions or condition.methods or not condition.roots <= _FIXED_ROOTS or any(
                path[0] == "it" and (len(path) != 2 or path[1] in _MOVING_FIELDS) for path in condition.paths):
            return None
        found.append((term.args[0].id, condition))
    return tuple(found)


@lru_cache(maxsize=1_024)
def filter_parts(source: str) -> tuple[str, Expr] | None:
    """``$filter(<type>, <condition>)``: its type word and its condition as an expression of ``$it``; None for any
    other expression (and an invalid one raises as :func:`compile_expr` does)."""
    compile_expr(source)
    tree = ast.parse(_preprocess(source.strip()).strip(), mode="eval")
    _restore_words(list(ast.walk(tree)))
    body = tree.body
    if not (isinstance(body, ast.Call) and isinstance(body.func, ast.Name) and body.func.id == f"{_FUNC_PREFIX}filter"
            and len(body.args) == 2 and not body.keywords and isinstance(body.args[0], ast.Name)
            and not body.args[0].id.startswith((_ROOT_PREFIX, _FUNC_PREFIX))):
        return None
    for node in ast.walk(body.args[1]):  # back to the language's spelling: the condition is an expression of its own
        if isinstance(node, ast.Name) and node.id.startswith((_ROOT_PREFIX, _FUNC_PREFIX)):
            node.id = "$" + node.id[len(_ROOT_PREFIX):]
    return body.args[0].id, compile_expr(ast.unparse(body.args[1]))


@lru_cache(maxsize=1_024)
def call_roots(source: str) -> tuple[tuple[str, tuple[str | None, ...]], ...]:
    """Every function call in the expression: its name and, for each argument, the root it is when it is a bare root
    (``$actor``), else None — ``$value($actor, $it.terms)`` gives ``("value", ("actor", None))``. An invalid
    expression raises as :func:`compile_expr` does."""
    compile_expr(source)
    tree = ast.parse(_preprocess(source.strip()).strip(), mode="eval")
    return tuple((node.func.id[len(_FUNC_PREFIX):],
                  tuple(arg.id[len(_ROOT_PREFIX):] if isinstance(arg, ast.Name) and arg.id.startswith(_ROOT_PREFIX)
                        else None for arg in node.args))
                 for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                 and node.func.id.startswith(_FUNC_PREFIX))


def _restore_words(nodes: list[ast.AST]) -> None:
    """Give names and fields marked by :func:`_preprocess` their own spelling back (the tree is never run by Python)."""
    for node in nodes:
        if isinstance(node, ast.Name) and node.id.startswith(_WORD_PREFIX):
            node.id = node.id[len(_WORD_PREFIX):]
        elif isinstance(node, ast.Attribute) and node.attr.startswith(_WORD_PREFIX):
            node.attr = node.attr[len(_WORD_PREFIX):]


def _root_method(func: ast.AST) -> bool:
    """``func`` is ``$root.name`` — a member of a root, which may be called like a function."""
    return (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id.startswith(_ROOT_PREFIX))
