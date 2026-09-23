"""Accounting must not certify non-finite totals or malformed supply values."""
import math

import pytest

import fg_env
from fg_env.errors import InvariantViolation
from fg_env.mechanisms.econ_assets import conserved


def contract(balance):
    return {
        "name": "Monetary scale", "clock": {"rounds": 1},
        "types": {"holder": {}},
        "entities": {name: {"type": "holder", "props": {"cash": balance}} for name in ("a", "b")},
        "mechanisms": {"money": {"kind": "economy", "mode": "ledger", "who": "holder",
                                   "currencies": {"cash": {}}}},
        "outputs": {"conserved": "$conserved(money)"},
    }


def test_overflowing_holdings_cannot_complete_with_certified_conservation():
    with pytest.raises(InvariantViolation, match="conserved.*after build"):
        fg_env.load(contract(1e308))


@pytest.mark.parametrize("supply", [math.inf, -math.inf, math.nan, 10**400, None, True, "20"])
def test_non_finite_or_non_numeric_supply_fails_conservation(supply):
    env = fg_env.load(contract(10))
    # Map properties are intentionally untyped inside; conservation owns their monetary meaning.
    env.world.set_world("money_supply", {"cash": supply})
    ok, reason = conserved(env.world, "money", "probe")
    assert not ok and "supply must be a finite number" in reason
    assert env.run("idle").status == "failed"


def test_large_but_finite_money_remains_supported():
    result = fg_env.load(contract(1e300)).run("idle")
    assert result.status == "completed", result.error
    assert result.outputs["conserved"] is True
