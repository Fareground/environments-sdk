"""The rules `guide('authoring')` states for what first-time authors found unclear hold as written."""
import copy

import fg_env

#: Two stands; `n` is declared by its default only.
STANDS = {
    "name": "Stands",
    "types": {"stand": {"agent": True, "props": {"n": 2}}},
    "entities": {"a": {"type": "stand"}, "b": {"type": "stand"}},
    "actions": {"go": {"by": "stand", "do": ["$actor.n = 1.5"]}},
}


def _with(**sections):
    contract = copy.deepcopy(STANDS)
    contract.update(sections)
    return contract


def _go(wake):
    wake.call("go", {})
    wake.end()


def test_min_max_sum_and_avg_take_a_collection_and_value_a_list_or_numbers():
    page = fg_env.guide("authoring")
    assert "`$min(stand, $it.price)`" in page and "`$min(3, $x)`" in page
    outputs = {"of_items": "$min(stand, $it.n)", "of_list": "$max($map(stand, $it.n))", "of_numbers": "$min(4, 3)",
               "sum_list": "$sum([1, 2])", "avg_items": "$avg(stand, $it.n)"}
    result = fg_env.run(_with(outputs=outputs), "idle", rounds=1, seed=1)
    assert result.outputs == {"of_items": 2, "of_list": 2, "of_numbers": 3, "sum_list": 3, "avg_items": 2.0}


def test_a_view_lists_the_viewer_unless_its_where_leaves_it_out():
    assert '`where: "$it.id != $actor.id"`' in fg_env.guide("authoring")
    views = {"everyone": {"title": "Everyone", "of": "stand", "show": "{name}"},
             "rivals": {"title": "Rivals", "of": "stand", "where": "$it.id != $actor.id", "show": "{name}"}}
    update = fg_env.load(_with(views=views)).preview("a")["update"]
    everyone, rivals = update.split("Rivals")
    assert "- a" in everyone and "- b" in everyone
    assert "- b" in rivals and "- a" not in rivals


def test_a_local_lasts_through_its_nested_effects_but_not_into_the_next_action():
    nested = {"go": {"by": "stand", "do": ["$t = 5", {"each": "stand", "do": ["$it.n = $t"]},
                                            {"if": "$t == 5", "then": ["$u = 7"]}, "$actor.n = $u"],
                     "outcome": "Set {$t}."}}
    told = []

    def go(wake):
        told.append(wake.call("go", {}).text)
        wake.end()

    result = fg_env.run(_with(actions=nested, outputs={"n": "$dict(stand, $it.id, $it.n)"}), go, rounds=1, seed=1)
    assert sorted(result.outputs["n"].values()) == [5, 7]
    assert all("Set 5." in text for text in told)
    later = dict(nested, later={"by": "stand", "do": ["$actor.n = $t"]})
    issues = [i for i in fg_env.check(_with(actions=later), rounds=0) if i.path == "actions.later.do[0]"]
    assert issues and "$t is not available here" in issues[0].message


def test_inspect_is_offered_only_when_a_type_opens_it():
    def offered(contract):
        tools = {tool["name"]: tool for tool in fg_env.load(contract).preview("a")["tools"]}
        return tools["inspect"]["input_schema"]["properties"]["id"]["enum"] if "inspect" in tools else None

    assert offered(STANDS) is None  # its only choice would be the agent itself
    opened = copy.deepcopy(STANDS)
    opened["types"]["stand"]["inspect"] = True
    assert offered(opened) == ["a", "b"]


def test_a_number_default_makes_a_number_and_type_int_keeps_whole_numbers():
    assert fg_env.run(STANDS, _go, rounds=1, seed=1).status != "failed"
    whole = copy.deepcopy(STANDS)
    whole["types"]["stand"]["props"]["n"] = {"type": "int", "default": 2}
    whole["outputs"] = {"n": "$sum(stand, $it.n)"}
    result = fg_env.run(whole, _go, rounds=1, seed=1)
    assert result.outputs == {"n": 4} and "whole number" in " ".join(str(d) for d in result.diagnostics)
