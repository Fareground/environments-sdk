"""Keyed table lookups: the rows whose field holds a value, found through an index built once per run."""
import pytest

import fg_env
from fg_env.sdk.stdlib import tables

SALES = [{"sku": "a", "week": 1, "units": 3}, {"sku": "b", "week": 1, "units": 5},
         {"sku": "a", "week": 2, "units": 4}, {"sku": 7, "week": 2, "units": 1}]


def _shop(outputs, events=None, world=None):
    return {"name": "Shop", "clock": {"rounds": 1},
            "inputs": {"sales": {"type": "table", "default": SALES},
                       "skus": {"type": "table", "default": [{"sku": "a"}, {"sku": "b"}, {"sku": "z"}]}},
            "types": {"sku": {"props": {"base": 0}}},
            "population": [{"type": "sku", "from": "$inputs.skus", "id": "{$row.sku}",
                            "props": {"base": "$avg($lookup($inputs.sales, sku, $row.sku), $it.units) or 0"}}],
            "world": world or {}, "events": events or [], "outputs": outputs}


def test_lookup_finds_the_rows_of_a_key_in_table_order_and_none_for_a_missing_key():
    result = fg_env.run(_shop({"a": {"type": "list", "expr": "$lookup($inputs.sales, sku, 'a')"},
                               "missing": {"type": "list", "expr": "$lookup($inputs.sales, sku, 'q')"},
                               "number": {"type": "list", "expr": "$lookup($inputs.sales, sku, 7)"},
                               "bases": {"type": "map", "expr": "$dict(sku, $it.id, $it.base)"}}), seed=1)
    assert [row["units"] for row in result.outputs["a"]] == [3, 4]
    assert result.outputs["missing"] == [] and result.outputs["number"] == [SALES[3]]
    assert result.outputs["bases"] == {"a": 3.5, "b": 5, "z": 0}


def test_lookup_on_several_fields_takes_a_list_of_values_and_lookup_one_a_default():
    result = fg_env.run(_shop({"cell": {"type": "list", "expr": "$lookup($inputs.sales, [sku, week], ['a', 2])"},
                               "one": {"type": "any", "expr": "$lookup_one($inputs.sales, sku, 'b').units"},
                               "none": {"type": "any", "expr": "$lookup_one($inputs.sales, sku, 'q', 'no sale')"}}),
                        seed=1)
    assert result.outputs == {"cell": [SALES[2]], "one": 5, "none": "no sale"}


def test_an_input_table_is_indexed_once_per_run_and_other_lists_every_call():
    env = fg_env.load(_shop({"n": "$len($lookup($inputs.sales, sku, 'a')) + $len($lookup($inputs.sales, sku, 'b'))"}),
                      seed=1)
    assert len(tables._INDEXES[env.world]) == 1
    result = env.run()
    assert result.outputs["n"] == 3 and len(tables._INDEXES[env.world]) == 1


def test_a_world_list_that_changes_is_never_served_from_a_stale_index():
    contract = _shop({"after": {"type": "list", "expr": "$lookup($world.rows, k, 1)"}},
                     events=[{"do": ["$first = $lookup($world.rows, k, 1)", "$world.rows += [{k: 1, v: 'new'}]"]}],
                     world={"rows": {"type": "list", "default": [{"k": 1, "v": "old"}]}})
    assert [row["v"] for row in fg_env.run(contract, seed=1).outputs["after"]] == ["old", "new"]


@pytest.mark.parametrize("source, message", [
    ("$lookup(sku, base, 1)", "must be a table (a list of rows), got str 'sku'; for entities use $filter"),
    ("$lookup($inputs.sales, 3, 'a')", "must be a field name or a list of field names"),
    ("$lookup($inputs.sales, price, 'a')", "row 0 has no field 'price' (fields: sku, week, units)"),
    ("$lookup($inputs.sales, [sku, week], 'a')", "2 fields need a list of 2 key values"),
    ("$lookup([1, 2], sku, 1)", "row 0 is int 1, not a row with fields"),
])
def test_lookup_mistakes_say_what_to_pass(source, message):
    result = fg_env.run(_shop({}, events=[{"do": [f"$found = {source}"]}]), seed=1)
    assert result.status == "failed" and message in result.error
