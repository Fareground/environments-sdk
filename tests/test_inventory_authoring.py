"""Inventory literals fail at their authored path, before world construction."""
import copy

import pytest

import fg_env

BASE = {
    "name": "Inventory authoring",
    "inputs": {"stock": {"type": "int", "default": 3}},
    "types": {"holder": {}, "shop": {"extends": "holder"}},
    "entities": {"shop": {"type": "shop"}},
    "mechanisms": {"goods": {"kind": "economy", "mode": "inventory", "who": "holder",
                              "prop": "stock", "items": {"unit": {}, "machine": {"unique": True}}, "actions": []}},
}


@pytest.mark.parametrize("value", [-1, 1.5, True, "3", "$inputs.stock"])
def test_invalid_literal_quantity_has_exact_path_and_repair(value):
    contract = copy.deepcopy(BASE)
    contract["entities"]["shop"]["props"] = {"stock": {"unit": value}}
    errors = [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert len(errors) == 1
    assert errors[0].path == "entities.shop.props.stock.unit"
    assert "non-negative integer" in errors[0].message
    assert errors[0].fix
    if value == "$inputs.stock":
        assert "entire map" in errors[0].fix
        assert "{'unit': $inputs.stock}" in errors[0].fix


@pytest.mark.parametrize("place", ["default", "generated"])
def test_initial_inventory_checks_defaults_and_generated_holders(place):
    contract = copy.deepcopy(BASE)
    if place == "default":
        contract["types"]["holder"]["props"] = {"stock": {"default": {"unit": -1}}}
        path = "types.holder.props.stock.default.unit"
    else:
        contract["entities"]["shops"] = {"type": "shop", "count": 1, "props": {"stock": {"unit": -1}}}
        path = "entities.shops.props.stock.unit"
    errors = [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert [i.path for i in errors] == [path]


@pytest.mark.parametrize("item", ["unknown", "machine"])
def test_literal_stacks_must_name_declared_stackable_items(item):
    contract = copy.deepcopy(BASE)
    contract["entities"]["shop"]["props"] = {"stock": {item: 1}}
    errors = [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert len(errors) == 1
    assert errors[0].path == f"entities.shop.props.stock.{item}"
    assert "stackable item" in errors[0].message


def test_whole_map_expression_and_valid_literals_remain_supported():
    for stock in ("{'unit': $inputs.stock}", {"unit": 3}, {"unit": 0}, {}):
        contract = copy.deepcopy(BASE)
        contract["entities"]["shop"]["props"] = {"stock": stock}
        assert not [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
        fg_env.load(contract)


def test_unrelated_map_data_is_not_interpreted_as_inventory():
    contract = copy.deepcopy(BASE)
    contract["types"]["record"] = {"props": {"metadata": {"type": "map", "default": {"unit": "a label"}}}}
    contract["entities"]["note"] = {"type": "record"}
    contract["types"]["holder"]["props"] = {"metadata": {"type": "map", "default": {"unit": "another label"}}}
    assert not [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    fg_env.load(contract)


def test_types_declaring_the_inventory_property_are_checked_like_runtime_holders():
    contract = copy.deepcopy(BASE)
    contract["types"]["outside"] = {"props": {"stock": {"type": "map", "default": {"unit": "a label"}}}}
    errors = [i for i in fg_env.check(contract, rounds=0) if i.severity == "error"]
    assert len(errors) == 1
    assert errors[0].path == "types.outside.props.stock.default.unit"
