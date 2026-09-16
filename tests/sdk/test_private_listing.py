"""A view listing every entity of a type with a private property warns: each reader would see everyone's."""
import fg_env


def _contract(view):
    return {"name": "Hands", "clock": {"rounds": 1},
            "types": {"player": {"agent": True, "props": {"hand": {"type": "text", "default": "", "private": True},
                                                          "chips": 100}}},
            "entities": {"ann": {"type": "player"}, "bo": {"type": "player"}},
            "views": {"table": {"for": "player", "of": "player", **view}},
            "stages": [{"name": "play", "turns": "sequential"}]}


def _warnings(view):
    return [str(i) for i in fg_env.check(_contract(view)) if i.severity == "warning" and "private" in str(i)]


def test_listing_everyones_private_property_warns_through_any_spelling():
    assert _warnings({"show": "{name}: {hand}"})
    assert _warnings({"show": "{name}: {$it.hand}"})


def test_scoped_or_public_listings_do_not_warn():
    assert not _warnings({"show": "{name}: {hand}", "where": "$it.id == $actor.id"})
    assert not _warnings({"show": "{name}: {chips} chips"})
