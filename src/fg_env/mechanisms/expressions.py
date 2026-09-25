"""The expression fields of a mechanism's config, found from its model's types.

A field holds an expression when its type admits a number (or true/false) beside text — ``Number``, ``int | str`` —
or is marked :data:`Expr`: text that is always an expression (``fair_value``, a ``when``). Many of them are read only
by the mechanism's own code as a run plays, never by the contract's sections, so nothing else would check them before
then: expansion compiles every one (a syntax error is reported at its field) and ``check`` checks what each reads.
"""
from __future__ import annotations

import types
import typing
from collections.abc import Iterator
from typing import Annotated, Any

from pydantic import BaseModel

from ..expr import is_expr

__all__ = ["Expr", "EXPRESSION", "expression_fields"]

#: Marks text that is always an expression (``Annotated[str, EXPRESSION]``).
EXPRESSION = "expression"
#: Text that is always an expression.
Expr = Annotated[str, EXPRESSION]
_SCALARS = frozenset({int, float, bool})


def expression_fields(config: BaseModel, path: str = "") -> Iterator[tuple[str, str]]:
    """``(path, source)`` of every expression written in ``config``, nested models, maps and lists included."""
    for name, field in type(config).model_fields.items():
        yield from _written(getattr(config, name), field.annotation, f"{path}{field.alias or name}")


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


def _holds_expression(annotation: Any) -> bool:
    bases, marked = set(), False
    for member in _members(annotation):
        if typing.get_origin(member) is Annotated:
            marked = marked or EXPRESSION in member.__metadata__
            member = typing.get_args(member)[0]
        bases.add(member)
    return marked or (str in bases and bool(bases & _SCALARS))


def _item(annotation: Any, container: type) -> Any:
    """What a ``container`` (dict or list) member of ``annotation`` holds (Any when none says)."""
    for member in _members(annotation):
        if typing.get_origin(member) is container:
            args = typing.get_args(member)
            return args[-1] if args else Any
    return Any
