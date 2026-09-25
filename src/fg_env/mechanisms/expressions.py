"""The expression fields of a mechanism's config, found from its model's types.

A field holds an expression when its type admits a number (or true/false) beside text — ``Number``, ``int | str`` —
or is marked :data:`Expr`: text that is always an expression (``fair_value``, a ``when``), worked out as one whether or
not it has a `$` (``"false"`` is false). Many of them are read only by the mechanism's own code as a run plays, never
by the contract's sections, so nothing else would check them before then: expansion compiles every one (a syntax error
is reported at its field) and ``check`` checks what each reads.
"""
from __future__ import annotations

import types
import typing
from collections.abc import Iterator
from typing import Annotated, Any

from pydantic import BaseModel

from ..expr import is_expr

__all__ = ["Expr", "EXPRESSION", "expression_fields", "bare_words"]

#: Marks text that is always an expression (``Annotated[str, EXPRESSION]``).
EXPRESSION = "expression"
#: Text that is always an expression.
Expr = Annotated[str, EXPRESSION]
_SCALARS = frozenset({int, float, bool})


def expression_fields(config: BaseModel, path: str = "") -> Iterator[tuple[str, str]]:
    """``(path, source)`` of every expression written in ``config``, nested models, maps and lists included."""
    for name, field in type(config).model_fields.items():
        # a field typed `Expr` alone keeps its mark in the field's metadata; in a union, in its annotation
        annotation = Annotated[field.annotation, EXPRESSION] if EXPRESSION in field.metadata else field.annotation
        yield from _written(getattr(config, name), annotation, f"{path}{field.alias or name}")


def bare_words(config: BaseModel, path: str = "") -> Iterator[tuple[str, str]]:
    """``(path, word)`` of every field that is always an expression holding one bare word other than its default
    (``"perm"``): the expression is that word as text, which is what a forgotten ``$it.`` looks like."""
    for name, field in type(config).model_fields.items():
        value, where = getattr(config, name), f"{path}{field.alias or name}"
        if isinstance(value, BaseModel):
            yield from bare_words(value, f"{where}.")
        elif (isinstance(value, str) and value != field.default and value.strip().isidentifier()
              and value.strip() not in ("true", "false", "null")
              and _holds_expression(Annotated[field.annotation, EXPRESSION] if EXPRESSION in field.metadata
                                    else field.annotation, marked_only=True)):
            yield where, value.strip()


def _written(value: Any, annotation: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, BaseModel):
        yield from expression_fields(value, f"{path}.")
    elif isinstance(value, str):
        if is_expr(value) and _holds_expression(annotation):
            yield path, value
    elif isinstance(value, dict):
        inner = _item(annotation, dict)
        for key, item in value.items():
            yield from _written(item, inner, f"{path}.{key}")
    elif isinstance(value, list):
        inner = _item(annotation, list)
        for index, item in enumerate(value):
            yield from _written(item, inner, f"{path}[{index}]")


def _members(annotation: Any) -> Iterator[Any]:
    """The members of a union (``annotation`` itself when it is not one)."""
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            yield from _members(arg)
    else:
        yield annotation


def _holds_expression(annotation: Any, marked_only: bool = False) -> bool:
    bases, marked = set(), False
    for member in _members(annotation):
        if typing.get_origin(member) is Annotated:
            marked = marked or EXPRESSION in member.__metadata__
            member = typing.get_args(member)[0]
        bases.add(member)
    return marked or (not marked_only and str in bases and bool(bases & _SCALARS))


def _item(annotation: Any, container: type) -> Any:
    """What a ``container`` (dict or list) member of ``annotation`` holds (Any when none says)."""
    for member in _members(annotation):
        if typing.get_origin(member) is container:
            args = typing.get_args(member)
            return args[-1] if args else Any
    return Any
