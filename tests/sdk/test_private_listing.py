"""A view listing every entity of a type with a private property is a check error: each reader would see everyone's."""
import fg_env


def _contract(view):
    return {"name": "Hands", "clock": {"rounds": 1},
            "types": {"player": {"agent": True, "props": {"hand": {"type": "text", "default": "", "private": True},
                                                          "chips": 100}}},
            "entities": {"ann": {"type": "player"}, "bo": {"type": "player"}},
            "views": {"table": {"for": "player", "of": "player", **view}},
            "stages": [{"name": "play", "turns": "sequential"}]}


def _errors(view):
    return [str(i) for i in fg_env.check(_contract(view)) if i.severity == "error" and "private" in str(i)]


def test_listing_everyones_private_property_is_an_error_through_any_spelling():
    assert _errors({"show": "{name}: {hand}"})
    assert _errors({"show": "{name}: {$it.hand}"})


def test_scoped_or_public_listings_are_fine():
    assert not _errors({"show": "{name}: {hand}", "where": "$it.id == $actor.id"})
    assert not _errors({"show": "{name}: {chips} chips"})
