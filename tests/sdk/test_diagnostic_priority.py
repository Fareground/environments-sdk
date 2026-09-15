"""An agent sees the primary repair before diagnostics that can follow from it."""
import pytest

import fg_env


def contract(expr):
    return {"name": "Revenue summary", "types": {"buyer": {"props": {"cash": 3}}},
            "entities": {"a": {"type": "buyer"}}, "world": {"stock": 1}, "outputs": {"value": expr}}


def errors(c):
    return [i for i in fg_env.check(c, rounds=0) if i.severity == "error"]


def test_misspelled_aggregate_leads_with_the_function_repair():
    found = errors(contract("$mean(buyer, $it.cash)"))
    assert found[0].path == "outputs.value"
    assert found[0].message == "unknown function $mean"
    assert "$avg" in found[0].fix
    assert errors(contract("$avg(buyer, $it.cash)")) == []


def test_independent_property_errors_remain_visible_and_need_their_own_repair():
    found = errors(contract("$mean(buyer, $it.cash) + $world.stok"))
    assert found[0].message == "unknown function $mean"
    assert any("no such world property" in i.message and "stock" in i.fix for i in found)
    remaining = errors(contract("$avg(buyer, $it.cash) + $world.stok"))
    assert len(remaining) == 1 and "no such world property" in remaining[0].message


def test_declared_functions_do_not_hide_real_argument_scope_errors():
    c = contract("$mean(buyer, $it.cash)")
    c["defs"] = {"mean": {"args": ["population", "value"], "expr": "$value"}}
    found = errors(c)
    assert any("$it is not available" in i.message for i in found)
    assert not any("unknown function" in i.message for i in found)


def test_multiple_unknown_functions_have_stable_order_before_other_errors():
    found = errors(contract("$zzz($missing) + $aaa(1)"))
    assert [i.message for i in found[:2]] == ["unknown function $aaa", "unknown function $zzz"]
    assert any("$missing is not available" in i.message for i in found)


@pytest.mark.parametrize("source", ["Result: {$mean(buyer, $it.cash)}", "Result: {$avg(buyer, $it.cash)}"])
def test_template_placeholders_use_the_same_diagnostic_order(source):
    c = contract("1")
    c["views"] = {"summary": {"show": source}}
    found = errors(c)
    if "$mean" in source:
        assert found[0].path == "views.summary.show"
        assert found[0].message == "unknown function $mean"
    else:
        assert found == []
