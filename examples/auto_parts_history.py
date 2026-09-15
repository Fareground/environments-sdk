"""Regenerate the auto-parts store's sales history from its `truth` arm, then fit the demand patterns from it.

    python examples/auto_parts_history.py            # history.csv and the fitted contract
    python examples/auto_parts_history.py --report   # also print the fit report

The truth arm runs three years of weekly trade with the true parameters and the store's lean reorder rule, recording
every SKU-week (units sold, whether stock ran out, price, promotion depth) — the kind of export a store's point of sale
gives. The contract's demand patterns are then fitted from that file with ``fg_env.fit_patterns`` and the estimates
are written back as the contract's inputs, so the example ships fitted to its own history.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import fg_env

CONTRACT = Path(__file__).parent / "contracts" / "auto_parts_store.json"
HISTORY = CONTRACT.parent / "auto_parts_store" / "history.csv"
FIELDS = ["week_start", "sku", "units", "stockout", "price", "rel_price", "promo"]
#: The seed of the truth run behind the bundled history.
SEED = 2023


def history_rows(contract: Path = CONTRACT, seed: int = SEED) -> list:
    """Every SKU-week the truth arm records, as CSV rows (texts, like the file)."""
    env = fg_env.load(contract, arm="truth", seed=seed)
    result = env.run()
    if result.status not in ("completed", "ended"):
        raise SystemExit(f"the truth run {result.status}: {result.error}")
    return [{field: str(entry[field]) for field in FIELDS} for entry in env.world.records_store["history"]]


def main() -> None:
    rows = history_rows()
    with open(HISTORY, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    fitted = fg_env.fit_patterns(CONTRACT)
    CONTRACT.write_text(json.dumps(fitted.contract, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} SKU-weeks to {HISTORY} and the fitted contract to {CONTRACT}")
    if "--report" in sys.argv:
        print(fitted.report())


if __name__ == "__main__":
    main()
