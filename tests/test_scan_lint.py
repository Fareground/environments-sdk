"""The scan lint warns when a population row or an entity re-reads a whole input table, or a block that runs once per
arrival reads every entity of a type (cost growing with the square of their number), says how to fix it, and stays
quiet on once-per-round scans and on per-entity pairing that is the model itself."""
import copy
from pathlib import Path

import pytest

import fg_env

CONTRACTS = Path(__file__).parents[1] / "examples" / "contracts"
EXAMPLES = sorted([*CONTRACTS.glob("*.json"), *(CONTRACTS / "games").glob("*.json"),
                   *(CONTRACTS / "host").glob("*.json")])

#: The study's contact-centre trap: every arrival counts and picks waiting calls by scanning every call ever made.
QUEUE = {
    "name": "Calls", "clock": {"mode": "continuous", "unit": "second", "horizon": 600},
    "types": {"call": {"props": {"arrived": 0.0, "status": {"type": "enum", "values": ["waiting", "served"],
                                                              "default": "waiting"}}}},
    "world": {"busy": 0, "capacity": 2, "queue": {"type": "list", "default": []}},
    "defs": {"waiting": "$count(call, $it.status == waiting)"},
    "blocks": {
        "arrive": {"do": [
            {"create": "call", "props": {"arrived": "$clock.time"}, "as": "made"},
            {"if": "$world.busy < $world.capacity and $waiting == 1",
             "then": ["$made.status = served", "$world.busy += 1"]},
            {"after": "$exponential(0.1)", "do": [{"block": "arrive"}]}]},
        "pull": {"do": [
            {"if": "$world.busy < $world.capacity", "then": [
                "$next = $sort(call, $it.arrived, 1, $it.status == waiting)",
                {"after": 30, "do": ["$world.busy -= 1", {"block": "pull"}]}]}]}},
    "events": [{"at": 1, "do": [{"block": "arrive"}, {"block": "pull"}]}],
    "outputs": {"calls": "$count(call)"},
}

SHOP = {
    "name": "Shop", "clock": {"rounds": 2},
    "inputs": {"skus": {"type": "table", "default": [{"sku": "a"}, {"sku": "b"}]},
               "sales": {"type": "table", "default": [{"sku": "a", "units": 3}, {"sku": "b", "units": 4}]}},
    "types": {"sku": {"props": {"base": 0, "share": 0.0, "recent": 0}}},
    "population": [{"type": "sku", "from": "$inputs.skus", "id": "{$row.sku}",
                    "props": {"base": "$avg($filter($inputs.sales, $it.sku == $row.sku), $it.units)"}}],
    "events": [{"name": "share", "each": "sku", "do": ["$it.share = $it.base / $sum(sku, $it.base)",
                                                        "$it.recent = $sum($filter($inputs.sales, $it.sku == "
                                                        "$outer.id), $it.units)"]},
               {"name": "total", "do": ["$total = $sum($inputs.sales, $it.units) + $sum(sku, $it.base)"]}],
    "outputs": {"total": "$sum(sku, $it.base)"},
}


def _scans(contract):
    return [i for i in fg_env.check(contract, rounds=0) if "grows with the square" in i.message]


def test_a_block_that_reschedules_itself_and_scans_every_call_is_reported_with_a_queue_fix():
    found = {issue.path: issue for issue in _scans(QUEUE)}
    assert set(found) == {"blocks.arrive.do[1].if", "blocks.pull.do[0].then[0]"}
    through_def = found["blocks.arrive.do[1].if"]
    assert through_def.severity == "warning"
    assert ("$waiting (defs.waiting uses $count(call, …)) visits every call for each run of block "
            "arrive") in through_def.message
    assert "$world.queue += $made.id" in through_def.fix
    assert "$sort(call, …) visits every call for each run of block pull" in found["blocks.pull.do[0].then[0]"].message


def test_the_same_queue_kept_as_a_world_list_is_not_reported():
    fixed = copy.deepcopy(QUEUE)
    fixed["defs"] = {"waiting": "$len($world.queue)"}
    fixed["blocks"]["pull"]["do"][0]["then"][0] = "$next = $entity($world.queue[0])"
    assert _scans(fixed) == []


def test_a_population_row_or_an_entity_filtering_an_input_table_is_told_to_use_lookup():
    found = {issue.path: issue for issue in _scans(SHOP)}
    assert set(found) == {"entities.sku.props.base", "events[0].do[1]"}
    row = found["entities.sku.props.base"]
    assert "$filter($inputs.sales, …) visits every row of $inputs.sales for each entity entities.sku generates" in row.message
    assert "$lookup($inputs.sales, field, value)" in row.fix
    assert "for each sku" in found["events[0].do[1]"].message


def test_pairing_every_entity_with_its_type_and_once_per_round_scans_are_quiet():
    quiet = copy.deepcopy(SHOP)
    quiet["population"][0]["props"]["base"] = "$avg($lookup($inputs.sales, sku, $row.sku), $it.units)"
    quiet["events"][0]["do"][1] = "$it.recent = $sum($lookup($inputs.sales, sku, $it.id), $it.units)"
    assert _scans(quiet) == []  # `$sum(sku, …)` per sku and the once-per-round event are not reported


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_shipped_examples_raise_no_scan_warnings(path):
    assert [f"{i.path}: {i.message}" for i in _scans(path)] == []
