"""What an expression reads: the :class:`World` it queries and the :class:`Scope` of named roots it runs in."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .base import ExprError, _held

__all__ = ["World", "Scope"]


class World:
    """What expressions may read from the running environment.

    The runtime supplies a subclass bound to the live world. The base class is an
    empty world, which is what static checks and pure evaluations use.
    """

    rng: Any = None
    #: Every property name some type declares private: reading any other name needs no visibility check or count.
    private_names: frozenset[str] = frozenset()
    #: Metrics worked out from agents' private properties: what an agent is shown may not read them.
    private_metrics: frozenset[str] = frozenset()
    #: How many times game logic read a private property of an entity other than the one acting: a refused action
    #: that read one could tell its agent something hidden, so it costs the action.
    hidden_reads: int = 0

    def entities_of(self, type_name: str) -> list[Any]:
        raise ExprError(f"no entities of type '{type_name}' exist in this context")

    def alive_of(self, type_name: str) -> Sequence[Any]:
        """The living entities of a type, possibly as a shared list that callers only read."""
        return self.entities_of(type_name)

    def entity(self, entity_id: str) -> Any:
        return None

    def is_private(self, type_name: str, prop: str) -> bool:
        """Whether entities of ``type_name`` keep ``prop`` private: only the entity itself may be shown it."""
        return False

    def is_hidden(self, type_name: str, prop: str) -> bool:
        """Whether entities of ``type_name`` declare ``prop`` private (an agent type or any other)."""
        return False

    def records(self, name: str) -> list[Any]:
        raise ExprError(f"no record '{name}' exists in this context")

    def visible_records(self, name: str, viewer: Any) -> list[Any]:
        return self.records(name)

    def events(self, name: str | None, viewer: Any = None) -> list[Any]:
        return []

    def relation(self, a: Any, b: Any, kind: str) -> float | None:
        return None

    def neighbors(self, entity: Any, kind: str) -> list[Any]:
        return []

    def link_view(self, a: Any, b: Any, kind: str) -> Any:
        return None

    def links_of(self, entity: Any, kind: str) -> list[Any]:
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

    def call_def(self, name: str, args: list[Any], source: str, viewer: Any = None) -> Any:
        """Call a contract-defined function (``defs``), which sees the caller's ``viewer``. The empty world has none."""
        from .calls import FUNCTIONS, suggest_function

        hint = suggest_function(name, list(FUNCTIONS))
        raise ExprError(f"unknown function ${name}" + (f" — did you mean {hint}?" if hint else ""), source)


_EMPTY_WORLD = World()


class Scope:
    """Values visible to an expression: named roots plus the world to query.

    ``vars`` is read, never changed: a child scope is a new mapping holding the parent's roots and its own."""

    __slots__ = ("vars", "world")

    def __init__(self, vars: Mapping[str, Any] | None = None, world: World = _EMPTY_WORLD):
        self.vars: Mapping[str, Any] = {} if vars is None else vars
        self.world = world

    def child(self, **values: Any) -> Scope:
        return Scope({**self.vars, **values}, self.world)

    def root(self, name: str, source: str) -> Any:
        try:
            return self.vars[name]
        except KeyError:
            if self.world.has_def(name):  # a def without arguments reads like a value: $negotiating
                return _held(lambda: self.world.call_def(name, [], source, self.vars.get("viewer")))
            available = ", ".join(f"${k}" for k in sorted(self.vars)) or "none"
            raise ExprError(f"${name} is not available here (available: {available})", source) from None
