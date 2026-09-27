# functions / quantities

## Functions: quantities

- `$convert(value, from_unit, to_unit)` — Convert numeric units explicitly, e.g. $convert(30, minute, hour). Units may be named or dimension/scale objects. Absolute temperatures and intervals are distinct; currency rates are never inferred.
- `$convert_currency(value, from_currency, to_currency, rate, as_of, source)` — Multiply by an explicit positive target-per-source exchange rate, with ISO date and nonempty source reference. Records an authored conversion assumption; does not fetch or authenticate rates.
- `$quantity(value, unit)` — Declare the unit of a number already expressed in that unit; does not convert or validate its source. Use for dimensional constants; contract inputs/outputs can instead declare quantity metadata.
