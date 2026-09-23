"""Inspect choices retain live permissions, inherited privacy and value semantics."""
import fg_env

CONTRACT = {
    "name": "Live inspection choices",
    "clock": {"rounds": 1},
    "world": {"open": True},
    "types": {
        "viewer": {"agent": True, "inspect": True, "props": {"zero": 0}},
        "public": {"inspect": True, "props": {"zero": 0, "flag": False}},
        "blank": {"inspect": True, "props": {"label": ""}},
        "secret": {"inspect": True, "props": {"note": {"type": "text", "default": "hidden", "private": True}}},
        "child": {"extends": "secret", "props": {"note": "still hidden"}},
        "gated": {"inspect": "$world.open and $it.owner == $viewer.id", "props": {"owner": "a"}},
        "hidden": {"agent": True, "inspect": False, "props": {"label": "self only"}},
    },
    "entities": {name: {"type": kind} for name, kind in [
        ("a", "viewer"), ("b", "viewer"), ("p", "public"), ("empty", "blank"),
        ("private_child", "child"), ("g", "gated"), ("h", "hidden")]},
    "actions": {"wait": {"by": "hidden", "do": []}, "change": {"by": "viewer", "do": [
        "$entity(g).owner = 'b'", "$entity(empty).label = 'now visible'", {"remove": "$entity(p)"}]},
        "close": {"by": "viewer", "do": ["$world.open = false"]}},
    "stages": [{"name": "inspect", "turns": "sequential", "max_actions": 3}],
}


def test_choices_change_with_state_and_do_not_expose_inherited_private_details():
    seen = {}

    def ids(wake):
        tool = next(tool for tool in wake.tools if tool.name == "inspect")
        return tool.input_schema["properties"]["id"]["enum"]

    def actor(wake):
        seen[wake.entity_id] = ids(wake)
        if wake.entity_id == "a":
            assert wake.call("change", {}).ok
            seen["after_change"] = ids(wake)
        if wake.entity_id == "b":
            assert wake.call("close", {}).ok
            seen["after_close"] = ids(wake)
        wake.end()

    result = fg_env.load(CONTRACT).run(actor)
    assert result.status == "completed", result.error
    assert seen["a"] == ["a", "b", "p", "g"]  # zero and false are values; inherited secret is private
    assert seen["after_change"] == ["a", "b", "empty"]
    assert seen["b"] == ["a", "b", "empty", "g"]
    assert seen["after_close"] == ["a", "b", "empty"]
    assert seen["h"] == ["a", "b", "empty", "h"]  # itself is always inspectable


def test_shared_listing_invalidates_on_changes_and_rollback_and_keeps_schemas_separate():
    from fg_env.reads import inspect_tool

    contract = {
        "name": "Shared listing invalidation",
        "types": {"person": {"inspect": True, "props": {"zero": 0}},
                  "blank": {"inspect": True, "props": {"label": ""}},
                  "private": {"inspect": True, "props": {"secret": {"default": "private", "private": True}}},
                  "hidden": {"inspect": False, "props": {"label": "hidden"}}},
        "entities": {"a": {"type": "person"}, "b": {"type": "person"}, "empty": {"type": "blank"},
                     "p": {"type": "private"}, "h": {"type": "hidden"}},
    }
    env = fg_env.load(contract)
    world = env.world
    a, b = world.entities["a"], world.entities["b"]

    def listing(viewer):
        tool = inspect_tool(env, viewer, 3)
        assert tool is not None
        return tool.input_schema["properties"]["id"]["enum"]

    offered = listing(a)
    assert offered == ["a", "b"]
    offered.append("injected")
    assert listing(b) == ["a", "b"]
    assert listing(world.entities["p"]) == ["a", "b", "p"]
    assert listing(world.entities["h"]) == ["a", "b", "h"]
    assert listing(a) == ["a", "b"]  # private self choices never contaminate shared choices

    mark = world.journal.mark()
    world.set_prop(world.entities["empty"], "label", "visible")
    world.remove(b)
    assert listing(a) == ["a", "empty"]
    world.journal.rollback(mark)
    assert listing(a) == ["a", "b"]
    assert listing(b) == ["a", "b"]
