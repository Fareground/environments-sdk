# pattern / cross_price

### `pattern.cross_price`
Substitution between items: each item's demand multiplier from every item's price relative to its reference (own and cross elasticities, or a full matrix). Called with {key: price} and the item's key.

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
- `reference` (required): Reference price: one for all keys, or {key: price}.
- `own` (required): Own-price elasticity (on the diagonal).
- `cross` (default 0.0): Cross-price elasticity toward the other keys (> 0: substitutes, < 0: complements).
- `groups` (default null): {key: group}: cross effects only within a group (tiers of the same part).
- `matrix` (default null): Full elasticities instead of own/cross: row i is how item i answers each price, in key order.

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
{"mechanisms": {"my_cross_price": {"kind": "pattern", "mode": "cross_price", "keys": ["economy", "premium"], "reference": {"economy": 20, "premium": 35}, "own": -1.8, "cross": 0.6}}}
```
