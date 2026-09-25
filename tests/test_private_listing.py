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


def _haggle(**parts):
    contract = {"name": "Haggle", "clock": {"rounds": 2},
                "types": {"buyer": {"agent": True, "props": {"value": {"type": "number", "default": 50,
                                                                       "private": True}, "paid": 0}}},
                "entities": {"bea": {"type": "buyer"}},
                "actions": {"offer": {"by": "buyer", "params": {"price": "number"},
                                      "do": "$actor.paid = $params.price", "outcome": "You offered {$params.price}."}}}
    for section, items in parts.items():
        contract.setdefault(section, {}).update(items)
    return contract


def _unshown(contract):
    return [i.path for i in fg_env.check(contract) if "can never learn it" in i.message]


def test_an_agents_own_fixed_private_trait_that_nothing_shows_it_is_warned_about():
    """A haggler that never learns its own value cannot play (audit 9 docs M2)."""
    assert _unshown(_haggle()) == ["types.buyer.props.value"]
    assert not _unshown(_haggle(views={"me": {"for": "buyer", "show": "Your value: {value}"}}))
    assert not _unshown(_haggle(brief={"roles": {"buyer": "A vase is worth {$actor.value} to you."}}))
    tally = _haggle()
    tally["actions"]["offer"]["do"] = ["$actor.paid = $params.price", "$actor.value -= 1"]
    assert not _unshown(tally)  # a tally the rules keep is theirs
