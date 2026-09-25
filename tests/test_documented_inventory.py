"""Known business outcomes of the weekly inventory example."""
from pathlib import Path

import fg_env

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "examples/contracts/weekly_inventory.json"
POLICY = {"retailer": "policy:steady"}


def test_documented_inventory_balances_and_shortage():
    baseline = fg_env.run(CONTRACT, POLICY, seed=7)
    assert baseline.outputs.items() >= {"units_sold": 24, "lost_sales": 0, "closing_cash": 244.0}.items()
    busy = fg_env.run(CONTRACT, POLICY, inputs={"weekly_demand": 9}, seed=7)
    assert busy.outputs.items() >= {"units_sold": 34, "lost_sales": 2, "closing_cash": 344.0}.items()
    # Each sold unit must come from opening inventory or a paid purchase.
    assert busy.outputs["units_sold"] == 10 + 4 * 6


def test_documented_snapshot_continuation():
    env = fg_env.load(CONTRACT, seed=7)
    env.step(POLICY)
    continued = fg_env.Env.restore(CONTRACT, env.snapshot()).run(POLICY)
    assert continued.outputs == fg_env.run(CONTRACT, POLICY, seed=7).outputs
