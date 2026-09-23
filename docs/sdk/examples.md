# Examples and recipes

Examples are editable contracts. Use them to learn a pattern, then test the assumptions required by your own scenario.

| Example | What to study |
|---|---|
| [Weekly inventory](https://github.com/Fareground/environments-sdk/blob/main/examples/contracts/weekly_inventory.json) | Small complete contract with hand-calculated outputs |
| [Auto parts store](https://github.com/Fareground/environments-sdk/blob/main/examples/contracts/auto_parts_store.json) | SKU tables, seasonality, substitution, demand and replenishment |
| [Coffee market](https://github.com/Fareground/environments-sdk/blob/main/examples/contracts/coffee_market.json) | Households, pricing and repeated purchases |
| [Phone reseller](https://github.com/Fareground/environments-sdk/blob/main/examples/contracts/phone_reseller.json) | A larger business model to inspect and adapt |
| [Trade negotiation](https://github.com/Fareground/environments-sdk/blob/main/examples/contracts/trade_negotiation.json) | Several participants making interdependent decisions |

## Run from a checkout

<!-- not run: clones and installs the repository -->
```bash
git clone https://github.com/Fareground/environments-sdk.git
cd environments-sdk
python -m pip install -e .
fg-env check examples/contracts/weekly_inventory.json
fg-env run examples/contracts/weekly_inventory.json --agent retailer=policy:steady --seed 7
```

Keep data directories beside contracts that refer to CSVs or assets. Copying only a JSON file may omit required inputs.

## Find a reusable primitive

[Business modeling](business-modeling.md) maps requirements to contract features. [Recipes](reference-recipes.md) shows composition patterns. The [full reference](reference.md) covers exact syntax and defaults.
