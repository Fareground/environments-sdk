"""What an expression reads: the :class:`World` it queries and the :class:`Scope` of named roots it runs in."""
from __future__ import annotations

from typing import Any, List, Mapping, Optional, Sequence

from .base import ExprError, _held

__all__ = ["World", "Scope"]


class World:
    """What expressions may read from the running environment.

    The runtime supplies a subclass bound to the live world. The base class is an
    empty world, which is what static checks and pure evaluations use.
    """

    rng: Any = None

    def entities_of(self, type_name: str) -> List[Any]:
        raise ExprError(f"no entities of type '{type_name}' exist in this context")

    def alive_of(self, type_name: str) -> Sequence[Any]:
        """The living entities of a type, possibly as a shared list that callers only read."""
        return self.entities_of(type_name)

    def entity(self, entity_id: str) -> Any:
        return None

    def records(self, name: str) -> List[Any]:
        raise ExprError(f"no record '{name}' exists in this context")

    def visible_records(self, name: str, viewer: Any) -> List[Any]:
        return self.records(name)

    def events(self, name: Optional[str], viewer: Any = None) -> List[Any]:
        return []

    def relation(self, a: Any, b: Any, kind: str) -> Optional[float]:
        return None

    def neighbors(self, entity: Any, kind: str) -> List[Any]:
        return []

    def link_view(self, a: Any, b: Any, kind: str) -> Any:
        return None

    def links_of(self, entity: Any, kind: str) -> List[Any]:
        return []

    def distance(self, a: Any, b: Any) -> float:
        raise ExprError("this environment declares no space")

    def is_type(self, name: str) -> bool:
        return False

    def is_a(self, type_name: str, ancestor: str) -> bool:
        return type_name == ancestor

    def subtypes_of(self, type_name: str) -> Any:
        """``type_name`` and every type that extends it."""
        return {type_name}

    def has_def(self, name: str) -> bool:
        return False

    def defines(self, name: str) -> bool:
        """Whether the contract declares a def called ``name`` (with or without arguments)."""
        return False

    def call_def(self, name: str, args: List[Any], source: str) -> Any:
        """Call a contract-defined function (``defs``). The empty world has none."""
        from .calls import FUNCTIONS, suggest_function

        hint = suggest_function(name, list(FUNCTIONS))
        raise ExprError(f"unknown function ${name}" + (f" — did you mean ${hint}?" if hint else ""), source)


_EMPTY_WORLD = World()


class Scope:
    """Values visible to an expression: named roots plus the world to query.

    ``vars`` is read, never changed: a child scope is a new mapping holding the parent's roots and its own."""

    __slots__ = ("vars", "world")

    def __init__(self, vars: Optional[Mapping[str, Any]] = None, world: World = _EMPTY_WORLD):
        self.vars: Mapping[str, Any] = {} if vars is None else vars
        self.world = world

    def child(self, **values: Any) -> "Scope":
        return Scope({**self.vars, **values}, self.world)

    def root(self, name: str, source: str) -> Any:
        try:
            return self.vars[name]
        except KeyError:
            if self.world.has_def(name):  # a def without arguments reads like a value: $negotiating
                return _held(lambda: self.world.call_def(name, [], source))
            available = ", ".join(f"${k}" for k in sorted(self.vars)) or "none"
            raise ExprError(f"${name} is not available here (available: {available})", source) from None
