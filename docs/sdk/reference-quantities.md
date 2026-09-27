# quantities

# Quantities and explicit conversions

`unit` is a display label. It never converts values or establishes dimensions.
Opt in with `quantity` on numeric inputs and outputs: use a known unit name,
such as `"minute"` or `"item/hour"`, or an explicit object. Its `dimensions` map names
base dimensions with integer powers; `scale` converts to the base scale. Use
`offset` and `absolute: true` for an absolute quantity such as Celsius.

For example, 60 items/hour and 30 minutes must produce 30 items, not 1,800:

```json
{
  "name": "Production",
  "types": {},
  "inputs": {
    "rate": {"default": 60, "unit": "items/hour",
             "quantity": "item/hour"},
    "duration": {"default": 30, "unit": "minutes",
                 "quantity": "minute"}
  },
  "outputs": {
    "produced": {"expr": "$inputs.rate * $convert($inputs.duration, minute, hour)",
                 "unit": "items", "quantity": "item"}
  }
}
```

`check` diagnoses the missing conversion if `produced` is just `rate * duration`.
It checks supported output arithmetic, comparisons, conditionals, output references,
rounding and explicit conversions. It does not prove dimensions through arbitrary
world state, reducers, custom functions, actions or physics effects. An output with
a declared quantity and an unsupported expression receives an **unchecked** warning.
This is a scoped dimensional check, not a proof of physical or empirical realism.

`$convert(value, from_unit, to_unit)` changes the number explicitly. Known names
include seconds, minutes, hours, days, m/cm/mm/km, kg/g, L/mL/m3, item, fraction,
percent, K, degC, degF, and delta_K/delta_degC/delta_degF. Compound names must be
quoted, e.g. `"item/hour"`, `"m^3"`. Custom units use explicit objects:
`$convert(3, {dimensions: {widget: 1}, scale: 10}, {dimensions: {widget: 1}})` → 30.
Unknown names are never guessed. Custom display labels remain unchecked.

`$quantity(value, unit)` declares a numeric constant already expressed in that unit;
it does not convert or authenticate a measurement. Prefer declared quantity metadata
for model inputs so mismatched conversions are detected.

Percent and fraction differ by a scale of 100. Absolute temperatures differ from
temperature intervals: 30 degC minus 20 degC is 10 delta_degC, not an absolute
10 degC. Convert offset units to kelvin before products or powers. For temperature
intervals, use delta units; offsets must not be applied to differences.

Different currencies are different dimensions. `$convert` refuses to invent an
exchange rate. `$convert_currency(value, USD, EUR, rate, as_of, source)` requires
an explicit positive target-per-source rate, ISO date and nonempty source reference.
Rates and provenance are authored assumptions; this function does not fetch or
verify them. Record the rate/date/source as inputs to retain them in the report.

Run independent known-answer cases as well as dimensional checks. Dimensionally
consistent arithmetic can still model the wrong mechanism or use unrealistic data.

