# pattern / calendar

### `pattern.calendar`
Calendar effects — weekends, named days, holidays, paydays, month ends, months — per day; a longer round averages the days it covers. Needs clock.start and a calendar unit.

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
- `effects` (required): [{on, effect, dates, days, months, before, after}]: every matching effect applies to a day.
- `form` (default "multiply"): multiply: effects multiply a base of 1 | add: effects add to 0.

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
**CalendarEffect** — An effect on the days it matches.
- `on`: any (required) — Which days: weekend, weekday, a named day, listed `dates`, `days` of the month (paydays), the first or last `days` of a month, or listed `months`.
- `effect`: number | text (required) — Multiplier on those days (form multiply) or amount added (form add).
- `dates`: [text] — ISO dates (2025-11-28) or yearly dates (12-25).
- `days`: int | [int] — days_of_month: the days of the month ([1, 15]); month_start/month_end: how many days (default 1).
- `months`: [int] — months: month numbers 1–12.
- `before`: int = 0 — Days before each matched date also affected (dates, days_of_month).
- `after`: int = 0 — Days after each matched date also affected (dates, days_of_month).

```json
{"clock": {"rounds": 3, "unit": "day", "start": "2025-12-20"}, "mechanisms": {"my_calendar": {"kind": "pattern", "mode": "calendar", "effects": [{"on": "weekend", "effect": 1.3}, {"on": "dates", "dates": ["12-25"], "effect": 0.1, "before": 0}]}}}
```
