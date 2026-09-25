"""What is hidden from whom: the one definition every channel an agent reads enforces.

A property declared `private` is hidden from every agent but its owner; declared with a list of agent types
(``"private": ["chair"]``), agents of those types (and their subtypes) read it too. An agent owns its own properties.
The world's and any other entity's private properties have no owner — hidden from every agent — until a `where` the
author wrote names one: a view's or an entity parameter's `where`, or a policy rule's
``each: $filter(<type>, <condition>)``, that picks items by one of their properties and by the reader
(``$it.owner == $actor.id``, ``$it.side == $actor.side``) makes the reader the owner of the items it picks, so it may
be shown or offered them, private properties and all. (Picking by the item's id — ``$it.id != $actor.id`` —
names no owner; a record's `visible` rule decides who reads each entry the same way.) Everything else an agent is shown
or offered — views, tools and their bounds, choices and defaults, `who`, announcements, news, outcomes, refusals,
inspect — refuses a hidden value (see ``expr/values.py``), and a refusal whose rules read one spends the action (see
``runtime/turn.py``), so no hidden value can be probed for free.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..contract import Contract
    from ..expr import Expr

__all__ = ["Hidden", "REVEALS", "readers", "reveals"]

#: The roots that name the reader of what an agent is shown or offered.
_READERS = frozenset({"actor", "viewer"})
#: The scope entry holding the item a revealing `where` picked: its private properties may be shown to the reader.
#: Not a name an expression can spell, so no contract can reach it.
REVEALS = "@reveals"


class Hidden:
    """The private properties a contract declares, and whom each is hidden from: per property, the agent types that
    read it besides its owner (none for ``private: true``)."""

    __slots__ = ("types", "agents", "world", "names", "lineage")

    def __init__(self, contract: Contract):
        self.types = {kind: {prop: readers(spec.private) for prop, spec in contract.props_of(kind).items()
                             if spec.private} for kind in contract.types}
        self.agents = frozenset(contract.agent_types())
        self.world = {prop: readers(spec.private) for prop, spec in contract.world.items() if spec.private}
        #: Every name declared private anywhere: reading any other name needs no check.
        self.names = frozenset(self.world).union(*(frozenset(props) for props in self.types.values()))
        #: Each type and the types it extends: an agent of any of them is one.
        self.lineage = {kind: frozenset(contract.lineage(kind)) for kind in contract.types}

    def entity_hides(self, entity: Any, prop: str, agent: Any) -> bool:
        """Whether ``entity``'s ``prop`` is hidden from ``agent`` (an entity; anything else — everyone, or game logic
        with no actor — is nobody's owner and no reader)."""
        allowed = self.types.get(entity.entity_type, {}).get(prop)
        return allowed is not None and getattr(agent, "id", None) != entity.id and not self._reads(agent, allowed)

    def world_hides(self, prop: str, agent: Any) -> bool:
        """Whether the world's ``prop`` is hidden from ``agent``."""
        allowed = self.world.get(prop)
        return allowed is not None and not self._reads(agent, allowed)

    def _reads(self, agent: Any, allowed: frozenset[str]) -> bool:
        kind = getattr(agent, "entity_type", None)
        return bool(allowed) and isinstance(kind, str) and bool(self.lineage.get(kind, frozenset()) & allowed)


def readers(private: bool | list[str]) -> frozenset[str]:
    """The agent types a `private` property is shown to besides its owner (``true``: none)."""
    return frozenset(private) if isinstance(private, list) else frozenset()


def reveals(contract: Contract, where: Expr, kind: str | None) -> bool:
    """Whether a `where` over the entities of type ``kind`` names their owner: it reads the reader and one of their
    properties (an agent owns only itself, so it never does)."""
    if kind not in contract.types or contract.is_agent(kind) or not where.roots & _READERS:
        return False
    props = contract.props_of(kind)
    return any(len(chain) > 1 and chain[0] == "it" and chain[1] in props for chain in where.paths)
