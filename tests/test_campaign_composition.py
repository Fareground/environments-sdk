"""Business-level acceptance: overlap, intent, purchases and fulfillment stay distinct."""
import json
from pathlib import Path

import pytest

import fg_env

PATH = Path(__file__).parents[1] / "examples/contracts/business_composition/campaign_fulfillment.json"


def checked(**inputs):
    result = fg_env.run(PATH, seed=7, inputs=inputs)
    assert result.status == "completed", result.error
    assert result.stats["invalid_calls"] == result.stats["rejected_actions"] == result.stats["truncated"] == 0
    out = result.outputs
    assert out["cash_conserved"] and out["goods_conserved"]
    assert out["orders"] == out["delivered"] + out["pending"]
    assert out["pending"] == out["transit"]
    assert out["delivered"] == out["customer_units"]
    assert inputs.get("stock", 20) == out["available"] + out["transit"] + out["customer_units"]
    assert out["brand_cash"] == 100 - out["spend"] + inputs.get("price", 8) * out["orders"]
    return out


def test_overlap_reach_and_attributed_sales_are_not_incrementality():
    baseline = checked(campaign=False)
    full = checked(influence=1)
    no_influence = checked(influence=0)
    assert baseline["unique_customer_reach"] == baseline["customer_contacts"] == baseline["spend"] == 0
    assert baseline["orders"] == 3
    assert full["unique_customer_reach"] == 9  # two groups of six, with three shared customers
    assert full["customer_contacts"] == 9
    assert full["orders"] == 8
    assert full["reached_buyers"] == 6
    assert full["reached_buyers"] > full["orders"] - baseline["orders"]
    assert no_influence["unique_customer_reach"] == 9 and no_influence["customer_contacts"] == 12
    assert no_influence["orders"] == baseline["orders"]
    assert no_influence["spend"] == 10 and no_influence["reached_buyers"] == 1


def test_budget_value_stock_and_delivery_are_separate_constraints():
    full = checked(influence=1)
    thin = checked(influence=1, stock=2)
    empty = checked(influence=1, stock=0)
    high_price = checked(influence=1, price=20)
    delayed = checked(influence=1, delivery_delay=10)
    assert thin["orders"] == thin["delivered"] == 2
    assert empty["orders"] == high_price["orders"] == 0
    assert thin["unique_customer_reach"] == empty["unique_customer_reach"] == full["unique_customer_reach"]
    assert delayed["orders"] == delayed["pending"] == full["orders"]
    assert delayed["delivered"] == 0


@pytest.mark.parametrize("seed", [1, 7, 19, 41])
def test_stochastic_composition_replays_pending_orders_and_preserves_counterfactual_reach(seed):
    env = fg_env.load(PATH, seed=seed)
    partial = env.run(rounds=2)
    assert partial.status != "failed", partial.error
    assert partial.outputs["pending"] > 0
    snapshot = json.loads(json.dumps(env.snapshot()))
    result = env.run()
    for customer in env.entities("customer"):
        props = customer["props"]
        assert (props["ordered"], props["delivered"], props["goods"].get("unit", 0)) in (
            (False, False, 0), (True, True, 1))
    resumed = fg_env.Env.restore(PATH, snapshot).run()
    assert result.to_dict() == resumed.to_dict()
    thin = fg_env.run(PATH, seed=seed, inputs={"stock": 1})
    control = fg_env.run(PATH, seed=seed, inputs={"campaign": False})
    assert thin.status == control.status == result.status == "completed"
    assert thin.outputs["unique_customer_reach"] == result.outputs["unique_customer_reach"]
    assert result.outputs["orders"] >= control.outputs["orders"]
    assert result.outputs["cash_conserved"] and result.outputs["goods_conserved"]
