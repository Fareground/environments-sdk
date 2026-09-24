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
