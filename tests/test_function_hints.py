"""An error repair must not substitute a different statistical quantity."""
import pytest

import fg_env
from fg_env.expr import ExprError, Scope, evaluate


def contract(expr):
    return {"name": "Summary measure", "clock": {"rounds": 1}, "types": {"observer": {}},
            "entities": {"observer": {"type": "observer"}}, "outputs": {"value": expr}}


def test_checker_points_mean_to_arithmetic_average_at_the_authored_path():
    errors = [i for i in fg_env.check(contract("$mean([1, 1, 100])"), rounds=0) if i.severity == "error"]
    assert len(errors) == 1
    assert errors[0].path == "outputs.value"
    assert "unknown function $mean" in errors[0].message
    assert "did you mean $avg?" in errors[0].fix
    assert "$median" not in errors[0].fix
    result = fg_env.run(contract("$avg([1, 1, 100])"))
    assert result.status == "completed", result.error
    assert result.outputs["value"] == 34
    assert evaluate("$median([1, 1, 100], $it)") == 1


@pytest.mark.parametrize("live", [False, True])
def test_runtime_uses_the_same_hint_without_accepting_an_undeclared_alias(live):
    scope = Scope(world=fg_env.load(contract("1")).world) if live else None
    with pytest.raises(ExprError, match=r"unknown function \$mean.*did you mean \$avg\?"):
        evaluate("$mean([1, 1, 100])", scope)


def test_a_declared_mean_function_keeps_its_authored_definition():
    c = contract("$mean([1, 1, 100])")
    c["defs"] = {"mean": {"args": ["values"], "expr": "$sum($values)"}}
    assert not [i for i in fg_env.check(c, rounds=0) if i.severity == "error"]
    assert fg_env.run(c).outputs["value"] == 102


@pytest.mark.parametrize("source, expected", [("$medain([1, 2, 3])", "$median?"),
                                               ("$notionl(2, 3)", "$notional?")])
def test_other_builtin_and_authored_function_spelling_hints_remain(source, expected):
    c = contract(source)
    c["defs"] = {"notional": {"args": ["price", "qty"], "expr": "$price * $qty"}}
    errors = [i for i in fg_env.check(c, rounds=0) if i.severity == "error"]
    assert any(expected in (i.fix or "") for i in errors)
    with pytest.raises(ExprError) as info:
        evaluate(source, Scope(world=fg_env.load({**c, "outputs": {"value": "1"}}).world))
    assert expected in str(info.value)
