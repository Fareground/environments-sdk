"""Tests for fg_env/world/entity.py"""
from fg_env.expr.objects import Entity


class TestEntity:
    def test_create_entity(self):
        e = Entity(id="e1", name="Warrior Bob", entity_type="warrior", properties={"health": 100.0, "strength": 15})
        assert e.id == "e1"
        assert e.name == "Warrior Bob"
        assert e.alive is True

    def test_get_set(self):
        e = Entity(id="e1", name="Bob", entity_type="warrior", properties={"health": 100.0})
        assert e.get("health") == 100.0
        assert e.get("nonexistent", 0) == 0
        e.set("health", 50.0)
        assert e.get("health") == 50.0

    def test_to_dict(self):
        e = Entity(id="e1", name="Bob", entity_type="warrior", properties={"health": 100.0}, location_id="town")
        d = e.to_dict()
        assert d["id"] == "e1"
        assert d["name"] == "Bob"
        assert d["properties"]["health"] == 100.0
        assert d["location_id"] == "town"
        assert d["alive"] is True


def test_an_entity_s_repr_names_it_and_shows_none_of_its_properties():
    """audit 13 L1: a message that shows an entity (a `$fmt` error in an author's diagnostics) never prints its
    properties, private ones included."""
    import fg_env

    entity = Entity(id="e1", name="Bob", entity_type="warrior", properties={"card": 7_654_321})
    assert repr(entity) == "<entity e1>"
    contract = {"name": "F", "clock": {"rounds": 1},
                "types": {"p": {"agent": True, "props": {"card": {"default": 7654321, "private": True}}}},
                "entities": {"a": {"type": "p"}}, "outputs": {"x": "$fmt(1, $entity(a))"}}
    assert not any("7654321" in str(issue) for issue in fg_env.check(contract))
