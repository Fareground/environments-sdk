"""Regenerate the auto-parts store's sales history from its `truth` arm, then fit the demand patterns from it.

    python examples/auto_parts_history.py            # history.csv and the fitted contract
    python examples/auto_parts_history.py --report   # also print the fit report

The truth arm runs three years of weekly trade with the true parameters and the store's lean reorder rule; its demand
mechanism records every SKU-week (units sold, whether stock ran out, units on hand, price, promotion depth and the price
relative to list) — the kind of export a store's point of sale gives. The contract's demand patterns are then fitted
from that file with ``fg_env.fit_patterns`` and the estimates are written back as the contract's inputs, so the example
ships fitted to its own history.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import fg_env

CONTRACT = Path(__file__).parent / "contracts" / "auto_parts_store.json"
HISTORY = CONTRACT.parent / "auto_parts_store" / "history.csv"
ORDERS = CONTRACT.parent / "auto_parts_store" / "orders.csv"
FIELDS = ["time", "item", "units", "stockout", "stock", "price", "promo", "price_effect"]
ORDER_FIELDS = ["time", "item", "placed", "arrived", "qty", "lead_time", "factor"]
#: The seed of the truth run behind the bundled history.
SEED = 2023


def truth_records(contract: Path = CONTRACT, seed: int = SEED) -> tuple:
    """Every SKU-week and every purchase order the truth arm records, as CSV rows (texts, like the files)."""
    env = fg_env.load(contract, arm="truth", seed=seed, inputs={"history": [], "orders": []})
    result = env.run()
    if result.status not in ("completed", "ended"):
        raise SystemExit(f"the truth run {result.status}: {result.error}")
    records = env.world.records_store
    return ([{field: str(entry[field]) for field in FIELDS} for entry in records["shop_history"]],
            [{field: str(entry[field]) for field in ORDER_FIELDS} for entry in records["reorder_orders"]])


def _write(path: Path, fields: list, rows: list) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    history, orders = truth_records()
    _write(HISTORY, FIELDS, history)
    _write(ORDERS, ORDER_FIELDS, orders)
    fitted = fg_env.fit_patterns(CONTRACT)
    CONTRACT.write_text(json.dumps(fitted.contract, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(history)} SKU-weeks to {HISTORY}, {len(orders)} orders to {ORDERS} and the fitted contract to {CONTRACT}")
    if "--report" in sys.argv:
        print(fitted.report())


if __name__ == "__main__":
    main()
