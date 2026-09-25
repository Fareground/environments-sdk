"""What is hidden from whom: the one definition every channel an agent reads enforces.

A property declared `private` is readable by the entity itself, by the entity's owner — the agent whose id the
property its type names as `owner` holds (``"owner": "seller"``; a list of ids names several) — and by the agent
types its `private` lists (``"private": ["chair"]``, subtypes included). Nothing else widens it: whose an entity is
is a stated fact, read from the entity's own value each time it is read, never inferred from how a view or a choice
is written, so the property naming the owner is public. The world's private properties have no owner: only the listed
types read them.

Record entries and events are hidden the same way: an entry sent `to` others, or one its record's `visible` rule keeps
from the reader; an event addressed to others, or one carrying such an entry. What game logic reads of them counts as
hidden by what the contract says — a record that may hold an entry some agent cannot see, a kind of event that may be
kept from one — never by what the log holds now: whether a read counts reveals nothing itself.

Everything an agent is shown or offered — views, tools and their bounds, choices and defaults, `who`,
announcements, news, outcomes, refusals, inspect — refuses a hidden value (see ``expr/values.py``), and a refusal
whose rules read one spends the action (see ``runtime/turn.py``), so no hidden value can be probed for free. A
`where` that picks items for a reader (a view's, an entity choice's) reads them as the reader does: it widens
nothing, and one that reads a hidden value is refused like any other read.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..contract import Contract

__all__ = ["Hidden", "readers"]


class Hidden:
    """The private properties a contract declares, and whom each is hidden from: per property, the agent types that
    read it besides the entity and its owner (none for ``private: true``); per type, the property naming its owner."""

    __slots__ = ("types", "agents", "world", "names", "lineage", "owners", "records", "open_events")

    def __init__(self, contract: Contract):
        self.types = {kind: {prop: readers(spec.private) for prop, spec in contract.props_of(kind).items()
                             if spec.private} for kind in contract.types}
        self.agents = frozenset(contract.agent_types())
        self.world = {prop: readers(spec.private) for prop, spec in contract.world.items() if spec.private}
        #: Every name declared private anywhere: reading any other name needs no check.
        self.names = frozenset(self.world).union(*(frozenset(props) for props in self.types.values()))
        #: Each type and the types it extends: an agent of any of them is one.
        self.lineage = {kind: frozenset(contract.lineage(kind)) for kind in contract.types}
        #: Each type with an owner, and the property naming it.
        self.owners = {kind: owner for kind in contract.types if (owner := contract.owner_of(kind)) is not None}
        #: The records that may hold an entry some agent may not see.
        self.records = contract.hiding_records()
        #: The kinds of event every agent sees every one of: actions, when none is silent and none posts an entry that
        #: may be hidden; record news, when no record may hide an entry. Any other kind may be addressed to some.
        opened = set() if self.records else {"record"}
        if not self.records and not any(spec.silent for spec in contract.actions.values()):
            opened.add("action")
        self.open_events = frozenset(opened)

    def entity_hides(self, entity: Any, prop: str, agent: Any) -> bool:
        """Whether ``entity``'s ``prop`` is hidden from ``agent`` (an entity; anything else — everyone, or game logic
        with no actor — is nobody's owner and no reader)."""
        allowed = self.types.get(entity.entity_type, {}).get(prop)
        if allowed is None:
            return False
        reader = getattr(agent, "id", None)
        return reader != entity.id and not self.owns(entity, reader) and not self._reads(agent, allowed)

    def owns(self, entity: Any, reader: Any) -> bool:
        """Whether the agent with id ``reader`` owns ``entity``: its type's `owner` property holds that id (or a list
        holding it)."""
        key = self.owners.get(entity.entity_type)
        if key is None or not isinstance(reader, str):
            return False
        value = entity.properties.get(key)
        return value == reader or (type(value) is list and reader in value)

    def world_hides(self, prop: str, agent: Any) -> bool:
        """Whether the world's ``prop`` is hidden from ``agent``."""
        allowed = self.world.get(prop)
        return allowed is not None and not self._reads(agent, allowed)

    def events_hide(self, kind: Any) -> bool:
        """Whether the events of ``kind`` (None: every kind) may include one some agent may not know of."""
        return kind not in self.open_events

    def _reads(self, agent: Any, allowed: frozenset[str]) -> bool:
        kind = getattr(agent, "entity_type", None)
        return bool(allowed) and isinstance(kind, str) and bool(self.lineage.get(kind, frozenset()) & allowed)


def readers(private: bool | list[str]) -> frozenset[str]:
    """The agent types a `private` property is shown to besides the entity and its owner (``true``: none)."""
    return frozenset(private) if isinstance(private, list) else frozenset()

