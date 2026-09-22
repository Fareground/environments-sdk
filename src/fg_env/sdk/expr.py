"""The fg_env expression language — one small, safe language for every contract field.

Conditions, effects, views, metrics, outputs and physics bindings all use it::

    $actor.cash >= $params.offer.price * $params.qty
    $count(buyer, $it.cash > 0)
    $sum(offer, $it.sold * $it.price)
    $top(offer, $it.rating, 5)
    $params.qty if $actor.vip else 1

Rules:

* ``$name`` is a root supplied by the context (``$actor``, ``$params``, ``$it``,
  ``$inputs``, ``$world``, ``$round`` …); ``$name(...)`` calls a built-in function.
* Bare words are symbols (plain text): ``$actor.status == open``, ``$count(buyer)``.
  ``true``, ``false`` and ``null`` are literals. Quote text that contains spaces.
* Python operator syntax: ``+ - * / // % **``, comparisons, ``and or not``
  (``&& || !`` also accepted), ``x if cond else y``, ``in``, lists ``[1, 2]``.
* Entities expose ``id``, ``name``, ``type``, ``alive``, ``at`` and their properties.
* Evaluation is strict: an unknown property, a missing root or a type error raises
  :class:`ExprError` naming the expression and what to fix — a broken rule never
  silently evaluates to zero.

The language lives in a few modules, all re-exported here: :mod:`.expr_base` (errors, provenance, budgets),
:mod:`.expr_scope` (world and scope), :mod:`.expr_values` (value access and operators), :mod:`.expr_calls`
(built-in function registry) and :mod:`.expr_compile` (parsing and compilation).
"""
from __future__ import annotations

from typing import Any, Optional

# Re-exported: the language's pieces are imported from here (helpers used by function modules included).
from .expr_base import (  # noqa: F401
    EVAL_BUDGET, EXPRESSION_WORDS, MAX_INT_BITS, MAX_LIST_LEN, MAX_RANGE, MAX_TEXT_LEN, ExprError, Untrusted, _held, charge,
    check_size, derived, is_expr, nested_free, shared_budget, tainted, truthy,
)
from .expr_calls import _NO_KEY, FUNCTIONS, Call, EqualityGuard, FunctionSpec, function  # noqa: F401
from .expr_compile import Expr, compile_expr
from .expr_scope import Scope, World
from .expr_values import _describe, _entity_id, _number, _pow, attr  # noqa: F401

__all__ = [
    "EVAL_BUDGET",
    "EXPRESSION_WORDS",
    "MAX_INT_BITS",
    "MAX_LIST_LEN",
    "MAX_RANGE",
    "MAX_TEXT_LEN",
    "Untrusted",
    "tainted",
    "derived",
    "charge",
    "check_size",
    "shared_budget",
    "ExprError",
    "Expr",
    "Scope",
    "World",
    "FunctionSpec",
    "FUNCTIONS",
    "compile_expr",
    "evaluate",
    "resolve",
    "is_expr",
    "truthy",
    "attr",
    "function",
]


def evaluate(source: str, scope: Optional[Scope] = None) -> Any:
    """Compile (cached) and evaluate ``source``."""
    return compile_expr(source)(scope or Scope())


def resolve(value: Any, scope: Scope) -> Any:
    """Resolve a contract value: text with ``{$...}`` renders as a template, expression strings
    evaluate, containers resolve deeply."""
    if isinstance(value, str) and "{$" in value:
        from .template import compile_template

        return compile_template(value, None).render(scope)
    if is_expr(value):
        return compile_expr(value)(scope)
    if isinstance(value, list):
        return [resolve(item, scope) for item in value]
    if isinstance(value, dict):
        return {key: resolve(item, scope) for key, item in value.items()}
    return value


# Built-in functions register themselves on import.
from . import functions as _functions  # noqa: E402,F401
