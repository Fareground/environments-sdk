# pattern / reference_price

### `pattern.reference_price`
Reference-price effects: customers remember past prices; a price under the memory lifts demand, one above cuts it more (loss aversion). The memory drifts toward prices paid.

Config:
- `description` (default ""): What it stands for, in plain words.
- `unit` (default ""): 
- `keys` (default null): One instance per key: an entity type (keys are its ids), a list, or an expression over $inputs giving the keys. Read with the key as the last argument: $pattern.season($it.sku).
- `table` (default null): Expression over $inputs giving one row per key; parameters read the row as $row (per-SKU profiles). Keys default to its `column`.
- `column` (default null): The table column holding each row's key.
- `min` (default null): Lowest value it gives.
- `max` (default null): Highest value it gives.
- `record` (default false): Record it every round as a metric of the same name ($series.<name>).
- `fit` (default null): Estimate its parameters from data with fg_env.analysis.fit_patterns.
- `uncertainty` (default {}): {parameter: standard error} (written by fit): each run draws the parameter once from a normal around its value, so forecasts carry estimation uncertainty. An error of 0 uses the value as it is.
- `input` (required): What drives it, read every round: an expression over the world ($world.promo_spend, $it.price with entity keys).
- `every` (default null): Clock units per step of carry-over (default: one round).
- `retain` (default 0.7): Weight of the old reference when it updates toward the price paid.
- `gain` (default 1.0): Demand gained per share the price is below the reference.
- `loss` (default 2.0): Demand lost per share the price is above it (losses loom larger).
- `output` (default "effect"): effect: the demand multiplier | reference: the remembered price.

Nested config:
**FitSpec** — Where a pattern's parameters are estimated from: rows of a data input and the columns to read.
- `data`: text (required) — Expression over $inputs giving the rows (a table input, e.g. $inputs.history).
- `value`: text (required) — Column holding the observed quantity.
- `time`: text — Column holding when each row happened: an ISO date, or clock units from round 1 (0, 1, 2 …). Needed by time patterns.
- `key`: text — Column holding each row's key (keyed patterns fit one set of parameters per key).
- `x`: text | object — Column holding the driver a response answers (price, spend, exposure); for a product, {pattern: column or {column, key}} for every response that multiplies it in the data (its price response, its promotion), fitted together with it.
- `noise`: text — A product's counts pattern: its dispersion is estimated from the same rows, around the fitted means.
- `mean`: text — Column holding the expected value of each row (counts: the spread around it is what is estimated).
- `censored`: text — Column that is 1 (or true) where demand went unmet — sales capped by a stockout, so the true value was more than the one recorded. Those rows are fitted as censored (expectation–maximisation), not dropped.
- `where`: text — Keep only rows where this holds ($row), e.g. $row.returns == 0.
- `adjust`: [text] — Patterns already fitted that the value is divided by first (a season before a price response); for counts, their product is the expected value.
**FitFactor** — A response fitted together with a product, and the column its driver is in.
- `column`: text (required) — Column holding the driver (price, promotion depth).
- `key`: text — Its key, as an expression over $key and $row (the product's table row): $row.category.

```json
{"types": {"sku": {"props": {"price": 10.0, "promo": 0.0, "list_price": 10.0, "shop_promo": 0.0, "unit_cost": 4.0, "category": "tools", "sibling": "sku_1", "shop_rate": 2.0, "lead_weeks": 1, "case_pack": 6, "stock": 20}}}, "entities": {"sku": {"type": "sku", "count": 2}}, "mechanisms": {"my_reference_price": {"kind": "pattern", "mode": "reference_price", "input": "$it.price", "keys": "sku", "retain": 0.8, "gain": 0.8, "loss": 1.6}}}
```
