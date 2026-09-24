"""Entity instances: the things in a running world."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

# Runtime value for a property
PropertyValue = Union[float, int, str, bool, List[str], None]


@dataclass
class Entity:
    """
    A concrete entity within the world state.
    Properties are stored as a mutable dict keyed by property name.
    """
    id: str
    name: str
    entity_type: str
    properties: Dict[str, PropertyValue] = field(default_factory=dict)
    location_id: Optional[str] = None
    alive: bool = True

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
