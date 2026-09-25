"""The two world objects the language reads natively: entities and the ``$world`` view.

They live here, below the world, because compiled expressions test them by exact type (the fast path for
``$it.cash`` and ``$world.price``); the world builds and holds them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .base import ExprError

if TYPE_CHECKING:
    from ..world.store import World

__all__ = ["Entity", "PropertyValue", "PropsView"]

# Runtime value for a property
PropertyValue = float | int | str | bool | list[str] | None


@dataclass
class Entity:
    """
    A concrete entity within the world state.
    Properties are stored as a mutable dict keyed by property name.
    """
    id: str
    name: str
    entity_type: str
    properties: dict[str, PropertyValue] = field(default_factory=dict)
    location_id: str | None = None
    alive: bool = True
    #: What its luck is keyed by, when not its id: an entity created during a run is keyed by the block that created
    #: it (see ``Randomness.birth``), since its id comes from a counter every creator shares.
    luck: str | None = None

    def get(self, prop_name: str, default: PropertyValue = None) -> PropertyValue:
        """Get a property value."""
        return self.properties.get(prop_name, default)

    def set(self, prop_name: str, value: PropertyValue):
        """Set a property value."""
        self.properties[prop_name] = value

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "entity_type": self.entity_type,
            "properties": dict(self.properties),
            "location_id": self.location_id,
            "alive": self.alive,
        }


class PropsView:
    """``$world`` — global properties, readable and assignable."""

    #: The root it is: a view of the run's live state, never a value to keep or show.
    ROOT = "world"

    def __init__(self, world: World):
        self._world = world

    def expr_attr(self, name: str, source: str | None) -> Any:
        values = self._world.props
        if name not in values:
            known = ", ".join(sorted(values)) or "none declared"
            raise ExprError(f"world has no property '{name}' (declared: {known})", source)
        return values[name]
