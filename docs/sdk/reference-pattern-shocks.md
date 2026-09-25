# pattern / shocks

### `pattern.shocks`
Shocks: one-off (at), recurring (recur) or random (chance) jumps that last and then fade (half_life). An event can act while one is on: when: $pattern.strike > 0.

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
- `every` (default null): Clock units per step of the path (default: one round).
- `chance` (default 0.0): Probability a shock starts in a step.
- `at` (default []): Times shocks certainly start (clock units or ISO dates).
- `recur` (default null): Clock units between shocks that recur on schedule (from the window's start).
- `size` (default 1.0): Each shock's size (form add: added; multiply: 1 + size).
- `size_sd` (default 0.0): Spread of each shock's size (normal).
- `lasts` (default 1): Steps a shock stays at full size.
- `half_life` (default null): Steps for what is left of a shock to halve once it has lasted (without one it ends at once).
- `window` (default null): [first, last] times shocks may start (last may be null).
- `limit` (default null): Most shocks in a run.
- `gap` (default 0): Steps after a shock starts before another can.
- `form` (default "add"): add: a baseline of 0 plus every shock | multiply: 1 × (1 + each shock).

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
{"types": {}, "mechanisms": {"my_shocks": {"kind": "pattern", "mode": "shocks", "chance": 0.05, "size": -0.4, "lasts": 2, "half_life": 3, "form": "multiply"}}}
```
