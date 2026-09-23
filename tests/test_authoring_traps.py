"""Traps a business modeller hit: a property named `make`, a local named `$row`, world defaults reading each other."""
import pytest

import fg_env
from fg_env.errors import ContractError


def _errors(contract):
    return [i for i in fg_env.check(contract) if i.severity == "error"]


def _car_lot(props):
    return {"name": "Car lot", "clock": {"rounds": 1}, "types": {"car": {"props": props}},
            "entities": {"c1": {"type": "car"}}, "outputs": {"make": {"type": "any", "expr": "$entity(c1).make"}}}


def test_a_property_named_make_is_data_not_a_macro():
    contract = _car_lot({"make": {"type": "text", "default": "Ford"}, "base": 1.0})
    assert _errors(contract) == []
    assert fg_env.run(contract, seed=1).outputs["make"] == "Ford"


def test_an_object_with_make_and_other_fields_is_left_as_written():
    data = fg_env.expand({"name": "x", "rows": [{"make": "Ford", "model": "Focus"}]})
    assert data["rows"] == [{"make": "Ford", "model": "Focus"}]


def test_a_macro_missing_for_is_still_reported_with_a_rename_hint():
    with pytest.raises(ContractError) as excinfo:
        fg_env.expand({"name": "x", "rows": [{"as": "c", "make": "{c}"}]})
    issue = excinfo.value.issues[0]
    assert "needs `for`" in issue.message and "rename its `make` field" in issue.fix


def test_data_with_for_and_make_fields_says_to_rename_one():
    with pytest.raises(ContractError) as excinfo:
        fg_env.expand({"name": "x", "rows": [{"for": "fleet", "make": "Ford", "model": "Focus"}]})
    assert "rename one of them" in excinfo.value.issues[0].fix


def test_a_local_named_row_gets_a_rename_hint():
    contract = {"name": "x", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
                "events": [{"do": ["$row = 3"]}]}
    [issue] = _errors(contract)
    assert issue.path == "events[0].do[0]" and "reserved name" in issue.message and "$row_value" in issue.fix


def test_a_world_default_may_read_other_world_properties_declared_later():
    contract = {"name": "x", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
                "inputs": {"scale": {"type": "number", "default": 2}},
                "world": {"plan": {"type": "list", "default": "$map($world.rates, $it * $inputs.scale)"},
                          "rates": {"type": "list", "default": [1, 2, 3]},
                          "busiest": {"type": "number", "default": "$max($world.plan)"}},
                "outputs": {"plan": {"type": "list", "expr": "$world.plan"}, "busiest": "$world.busiest"}}
    assert _errors(contract) == []
    result = fg_env.run(contract, seed=1)
    assert result.outputs == {"plan": [2, 4, 6], "busiest": 6}


def test_a_world_default_reading_a_property_that_needs_entities_waits_for_them():
    contract = {"name": "x", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 2}}},
                "entities": {"a": {"type": "t"}, "b": {"type": "t"}},
                "world": {"double": {"type": "number", "default": "$world.total * 2"},
                          "total": {"type": "number", "default": "$sum(t, $it.v)"}},
                "outputs": {"double": "$world.double"}}
    assert fg_env.run(contract, seed=1).outputs["double"] == 8


def test_world_defaults_reading_each_other_in_a_circle_are_an_error_naming_the_circle():
    contract = {"name": "x", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
                "world": {"a": {"type": "number", "default": "$world.b + 1"},
                          "b": {"type": "number", "default": "$world.a + 1"}}}
    [issue] = _errors(contract)
    assert issue.path == "world.a.default" and "$world.a → $world.b → $world.a" in issue.message


def test_a_world_default_naming_an_undeclared_world_property_is_an_error():
    contract = {"name": "x", "clock": {"rounds": 1}, "types": {"t": {"props": {"v": 0}}},
                "world": {"a": {"type": "number", "default": "$world.missing + 1"}}}
    [issue] = _errors(contract)
    assert issue.path == "world.a.default" and "no such world property" in issue.message


@pytest.mark.parametrize("binding", ["it", "item"])
def test_expression_loop_shadows_the_enclosing_item_type(binding):
    contract = {
        "name": "Merchants update a cached product list", "clock": {"rounds": 1},
        "types": {"merchant": {"props": {"cash": 10}}, "product": {"props": {"stock": 2}}},
        "entities": {"seller": {"type": "merchant"}, "sku": {"type": "product"}},
        "events": [{"each": "merchant", "as": binding, "do": [
            "$products = $filter(product, true)",
            {"each": "$products", "as": binding, "do": [f"${binding}.stock += 1"]},
            f"${binding}.cash += 1"]}],
        "outputs": {"stock": "$entity(sku).stock", "cash": "$entity(seller).cash"},
    }
    assert _errors(contract) == []
    assert fg_env.run(contract).outputs == {"stock": 3, "cash": 11}


def test_literal_loop_still_checks_its_known_item_type():
    contract = {"name": "Known product loop", "types": {"merchant": {"props": {"cash": 10}},
                 "product": {"props": {"stock": 2}}},
                "events": [{"each": "merchant", "do": [{"each": "product", "do": ["$it.cash += 1"]}]}]}
    assert any("product has no property 'cash'" in i.message for i in _errors(contract))
