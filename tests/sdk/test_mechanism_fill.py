"""A mechanism fills in what an author-declared type or entity lacks; the author's values win."""
import pytest

from fg_env.sdk.mechanisms import expand_mechanisms
from fg_env.sdk.registry import mode

from family_fixtures import Nothing, scratch_family


@pytest.fixture
def badges():
    with scratch_family("test_badges"):
        mode("test_badges", "gold", Nothing, "Adds a badge to players and to ann.")(
            lambda name, config, contract: {
                "types": {"player": {"description": "Holds a badge.", "agent": False, "props": {"badge": "", "cash": 0}}},
                "entities": {"ann": {"type": "player", "props": {"badge": "gold", "cash": 1}},
                             "vault": {"type": "player", "props": {"badge": "none"}}}})
        yield


def test_declared_types_and_entities_gain_missing_props_and_fields_but_keep_their_own(badges):
    data, issues = expand_mechanisms({
        "types": {"player": {"agent": True, "props": {"cash": 10}}},
        "entities": {"ann": {"type": "player", "props": {"cash": 50}}},
        "mechanisms": {"badges": {"kind": "test_badges", "mode": "gold"}}})
    assert issues == []
    player = data["types"]["player"]
    assert player["agent"] is True and player["description"] == "Holds a badge."
    assert player["props"] == {"cash": 10, "badge": ""}
    assert data["entities"]["ann"]["props"] == {"cash": 50, "badge": "gold"}
    assert data["entities"]["vault"]["props"] == {"badge": "none"}
