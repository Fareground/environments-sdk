# patterns

## `patterns`: {name: {kind, …}}

The world's own regularities, declared once like its physics and read anywhere as values: a season, a trend, how
demand answers price, a random walk, a draw per customer, noisy counts, an effect that carries over. Write
`$pattern.winter` to read one, `$pattern.price_effect($it.price)` to call one with its driver, and pass the key last
when it has keys: `$pattern.season($it.category)`, `$pattern.sales($mean, $it)`.

* Parameters are fixed for a run: numbers, or expressions over `$inputs`, `$key` (the key), `$row` (the key's
  `table` row) and draw patterns. Put a knob in `inputs` and sweeps, arms, sensitivity, calibration and forks
  change the pattern with it. Run-time values (a price, a stock level) are arguments or a memory `input`.
* Keys: `keys` (an entity type — keys are its ids —, a list, or an expression over `$inputs`) and/or `table` +
  `column` (one row of parameters per key: per-SKU bases, per-category profiles).
* Time: `t` counts clock units from round 1 (round 1 is t = 0; with unit day and step 7 round 2 is t = 7), the clock
  time when continuous. With `clock.start`, yearly, weekly and daily positions follow the real calendar and times
  may be ISO dates. Random paths step once per round (or every `every` clock units).
* Randomness comes from the pattern's own stream (run seed, name, key): adding a pattern never shifts another draw,
  every arm sees the same paths, and a snapshot, clone or fork reads the same values. Observations use one uniform
  draw per key and round, so the same key in the same round always draws alike: pass a key per item.
* Memory patterns read an `input` from the running world and commit it at the end of every round (after end events,
  before metrics); their state is the world property `patterns_memory`.
* Every pattern also takes `description`, `unit`, `min`, `max` (clamp), `record` (a metric of the same name, so
  `$series.<name>` and calibration targets see it), `uncertainty` ({parameter: standard error}: each run draws the
  parameter once around its value) and `fit`.
* Fit from data: `fg_env.fit_patterns(contract, data_dir=...)` estimates each pattern with a `fit` block from its
  data and returns the contract with the estimates written back as inputs (plus their standard errors); see the
  estimators below. `fg_env.decompose(contract, "demand", key=...)` shows what each factor of a product or sum adds.
* State that agents and events change is not a pattern: keep it in props written by events or `physics`, which read
  patterns (`"$it.trust += $pattern.trust_noise($it)"`).

### Fitting and explaining

Add `fit` to a pattern — `{data, value, time, key, x, mean, censored, where, adjust, noise}` — and run
`fg_env.fit_patterns(contract, data_dir=...)`. Each fit reads its rows, estimates, and returns `result.contract` with
the estimates written back as inputs (`<pattern>_<parameter>`, or a `<pattern>_fit` table per key) plus their standard
errors, which become the pattern's `uncertainty` scaled by the input `parameter_uncertainty` (1 draws each run's
parameters around the estimates, 0 uses the estimates). `result.report()` says, per pattern, the method, rows, RMSE,
MAPE and R², what was estimated and what was assumed. `fit` blocks stay in the contract, so a refit is one call.

* One pattern at a time: trend (least squares; log-linear for exponential), seasonal (slot means over the overall
  mean; harmonic regression), calendar (regression on the share of days each effect matches), elasticity (log-log),
  promotion lift, counts dispersion (method of moments), random walk, mean reversion (AR(1)), autoregression, weather,
  draws (moments), carry-over (grid search), saturation, diffusion and hazards (search / maximum likelihood).
  `adjust: ["trend"]` divides the value by already-fitted patterns first.
* A `product` fits jointly — its base per key, its seasonal profiles and exponential trend, and every response named in
  `x` (a constant elasticity, an exponential promotion) — as one log-link count regression, so a promotion's lift is not
  mistaken for price response. `censored: "stockout"` marks rows where demand went unmet (sales capped by stock, so
  demand was more than what sold): they are fitted as censored (expectation–maximisation), not dropped. `noise: "sales"` estimates that counts pattern's
  dispersion around the fitted means.
* `fit` and `calibration` answer different questions. `fit` estimates parameters from recorded data, once, and
  writes them into the contract; the `calibration` section tunes inputs at every load so simulated outputs hit
  targets — for what only the simulation identifies. Never list a fitted input in `calibration.params` (the checker
  warns): the load would replace the estimate.
* Judge a fitted contract on history it did not see: `fg_env.validate(result.contract, cases, season=52, test=0.25)`.
  `result.priors` holds the number estimates as `{input: {dist: "normal", mean, sd}}` for `uncertainty=` on
  experiment, sweep, backtest and validate — pass the input `parameter_uncertainty: 0` with them, since the contract
  already draws every fitted parameter itself.
* `fg_env.decompose(contract, "demand", key="BRP-TOY-V")` shows every factor of a product or sum and what it adds,
  round by round; `fg_env.describe(contract)` lists every pattern in plain words.

### Groups

**time** — values that follow time: trends, seasons, calendars, cycles, lifecycles, steps and data series: `trend`, `seasonal`, `calendar`, `cycle`, `lifecycle`, `step`, `series`.
`"patterns": {"season": {"kind": "seasonal", "period": "year", "table": "$inputs.categories", "column": "category", "profile": "$row.profile"}, "growth": {"kind": "trend", "form": "exponential", "rate": "$inputs.growth"}}` · read "$pattern.demand($it) * $pattern.growth"

**random** — random paths drawn from seeded streams: walks, mean reversion, autoregression, volatility, regimes, shocks, noise, weather: `random_walk`, `mean_reversion`, `autoregressive`, `volatility`, `regimes`, `shocks`, `noise`, `weather`.
`"patterns": {"fuel": {"kind": "mean_reversion", "mean": 3.4, "rate": 0.2, "sd": 0.15, "min": 0}}` · read "$world.fuel_price = $pattern.fuel"

**response** — how a quantity answers a driver: price elasticity, substitution, promotions, saturation, thresholds, reference prices, learning curves, network effects, hazards: `elasticity`, `cross_price`, `saturation`, `threshold`, `learning_curve`, `network`, `promotion`, `reference_price`.
`"patterns": {"price_effect": {"kind": "elasticity", "elasticity": "$inputs.elasticity", "reference": 24.99}}` · read "$pattern.price_effect($it.price)"

**population** — differences between entities and how things spread: draws, segments, diffusion, habit and fatigue: `draw`, `segments`, `diffusion`, `hazard`, `habit`.
`"patterns": {"patience": {"kind": "draw", "keys": "customer", "dist": "lognormal", "mu": 1.2, "sigma": 0.4}}` · read "$chance(0.1 * $pattern.patience($it))"

**observation** — what gets recorded: counts with over-dispersion, measurement error, censoring, missing values: `counts`, `measurement`, `censored`, `missing`.
`"patterns": {"sales": {"kind": "counts", "dist": "negative_binomial", "dispersion": 4}}` · read "$it.sold = $min($it.stock, $pattern.sales($mean, $it))"

**memory** — effects that carry over from earlier rounds: adstock and lags: `carryover`.
`"patterns": {"ads": {"kind": "carryover", "input": "$world.ad_spend", "half_life": 2}}` · read "$world.visits = 500 * (1 + 0.001 * $pattern.ads)"

**composition** — patterns built from other patterns: products and sums: `product`, `sum`.
`"patterns": {"demand": {"kind": "product", "keys": "sku", "scale": "$row.base", "table": "$inputs.skus", "column": "sku", "of": ["growth", {"pattern": "season", "key": "$row.category"}]}}` · read "$pattern.demand($it)"

### Kinds

#### `trend` (time, signal)

A long-run trend: linear, exponential or a logistic S-curve.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "trend", "form": "exponential", "start": 100, "rate": "$inputs.growth"}`
- `form`: linear | exponential | logistic = "linear" — linear: start + slope·t | exponential: start·e^(rate·t) | logistic: capacity / (1 + e^(−steepness·(t − midpoint))).
- `start`: number | text = 1.0 — Value at `origin` (linear, exponential).
- `slope`: number | text = 0.0 — Change per clock unit (linear).
- `rate`: number | text = 0.0 — Growth per clock unit, as a log rate: 0.01 ≈ +1% a unit (exponential).
- `capacity`: number | text = 1.0 — The level it saturates at (logistic).
- `midpoint`: number | text = 0.0 — Clock units after `origin` when it is half way (logistic).
- `steepness`: number | text = 1.0 — How fast it rises around the midpoint (logistic).
- `origin`: number | text = 0.0 — Where t counts from: clock units from round 1, or an ISO date.

#### `seasonal` (time, signal)

A repeating season: a profile per month, weekday or hour, a smooth wave, or harmonics — around 1 or 0.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "seasonal", "period": "year", "profile": [0.8, 0.8, 0.9, 1, 1.1, 1.2, 1.3, 1.2, 1.1, 1, 0.9, 0.7]}`
- `period`: year | quarter | month | week | day | hour | number = "year" — year, quarter, month, week, day, hour — or a number of clock units. With clock.start, year, week and day follow the calendar.
- `profile`: list | text — One value per slot of the period: 12 over a year are calendar months, 7 over a week weekdays (Monday first), 24 over a day hours; other counts are equal slices.
- `amplitude`: number | text = 0.0 — Height of a smooth yearly-style wave (0.2 = ±20% with form multiply).
- `peak`: number | text = 0.0 — Where in the period the wave peaks, from 0 to 1 (0.5 = the middle).
- `harmonics`: list | text — [[sin, cos], …]: the k-th pair is a wave k times per period (fitted by harmonic regression).
- `form`: multiply | add = "multiply" — multiply: an index around 1 (profile × (1 + waves)) | add: an amount around 0 (profile + waves).

#### `calendar` (time, signal)

Calendar effects — weekends, named days, holidays, paydays, month ends, months — per day; a longer round averages the days it covers. Needs clock.start and a calendar unit.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "calendar", "effects": [{"on": "weekend", "effect": 1.3}, {"on": "dates", "dates": ["12-25"], "effect": 0.1, "before": 0}]}`
- `effects`: list (required) — [{on, effect, dates, days, months, before, after}]: every matching effect applies to a day.
- `form`: multiply | add = "multiply" — multiply: effects multiply a base of 1 | add: effects add to 0.

#### `cycle` (time, signal)

A regular cycle of any length: sine, square, triangle or sawtooth.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "cycle", "period": 26, "amplitude": 0.1, "level": 1}`
- `period`: number | text (required) — Clock units per cycle (a business cycle of 20 weeks: 20).
- `amplitude`: number | text = 1.0 — Height above and below the level.
- `level`: number | text = 0.0 — The centre it swings around.
- `phase`: number | text = 0.0 — Share of a cycle it is shifted later, from 0 to 1.
- `shape`: sine | square | triangle | sawtooth = "sine" — The wave's shape.

#### `lifecycle` (time, signal)

A life after a date: before → ramp up to a peak → decay toward a floor (a product launch, a price falling after a new model). Several starts multiply.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "lifecycle", "start": "2025-09-19", "before": 1, "peak": 0.93, "floor": 0.6, "half_life": 40}`
- `start`: number | text | list (required) — When it begins: clock units, an ISO date, or a list (one curve per start, multiplied: each later model launch).
- `before`: number | text = 1.0 — Value before the start.
- `peak`: number | text = 1.0 — Value when the ramp ends.
- `floor`: number | text = 0.0 — Level it decays toward.
- `ramp`: number | text = 0.0 — Clock units rising from `before` to `peak` after the start.
- `half_life`: number | text — Clock units for the gap above the floor to halve.
- `rate`: number | text — Decay per clock unit as a log rate (instead of half_life).

#### `step` (time, signal)

Step changes that last: a value that jumps to, by or times an amount at set times.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "step", "start": 0.2, "changes": [{"at": "2026-01-01", "to": 0.23}]}`
- `start`: number | text = 0.0 — The value before any change.
- `changes`: list (required) — [{at, to | by | times}]: changes that last (a new tax, a price list).

#### `series` (time, signal)

Values from data: a real history (weather, prices, footfall) read at the current time — how a pattern is driven by the customer's own series.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "series", "data": "$inputs.weather", "time": "date", "value": "temp_c", "missing": "interpolate"}`
- `data`: text (required) — Expression over $inputs: a list with one value per round, or rows (a table input).
- `time`: text — Rows: the column saying when (an ISO date, or clock units from round 1). Without it rows are one per round, in order.
- `value`: text — Rows: the column holding the value.
- `match`: text — Keyed: the column holding each row's key.
- `missing`: hold | interpolate | error = "hold" — Between known times: hold the last value, interpolate, or stop with an error.
- `after`: hold | repeat | error = "hold" — Past the last value: hold it, start over, or stop with an error.

#### `random_walk` (random, process)

A random walk, additive or geometric, with drift.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "random_walk", "start": 100, "drift": 0.001, "sd": 0.02, "form": "multiply"}`
- `every`: number — Clock units per step of the path (default: one round).
- `start`: number | text = 0.0 — Value at round 1.
- `drift`: number | text = 0.0 — Added each step (form add) or log growth each step (form multiply).
- `sd`: number | text = 1.0 — Standard deviation of each step's change (of its log with form multiply).
- `form`: add | multiply = "add" — add: x + drift + sd·z | multiply (geometric): x·e^(drift + sd·z).

#### `mean_reversion` (random, process)

Mean reversion (Ornstein–Uhlenbeck, exact at any step length): wanders but is pulled back to a mean.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "mean_reversion", "mean": 0.7, "rate": 0.05, "sd": 0.02}`
- `every`: number — Clock units per step of the path (default: one round).
- `mean`: number | text (required) — The level it is pulled back to.
- `rate`: number | text (required) — Pull per clock unit (0.1: a gap shrinks by e^−0.1 ≈ 10% a unit).
- `sd`: number | text = 0.0 — Noise per √unit (the long-run spread is sd / √(2·rate)).
- `start`: number | text — Value at round 1 (default: the mean).

#### `autoregressive` (random, process)

Autoregression AR(p): each step carries a share of the last ones, plus a new shock (persistence, momentum).

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "autoregressive", "coefficients": [0.6, 0.2], "mean": 20, "sd": 2}`
- `every`: number — Clock units per step of the path (default: one round).
- `coefficients`: list | text (required) — [φ1, φ2, …]: how much each earlier step carries into the next.
- `mean`: number | text = 0.0 — The level deviations are measured from.
- `sd`: number | text = 1.0 — Standard deviation of each step's new shock.
- `start`: number | text — Value of the first steps (default: the mean).

#### `volatility` (random, process)

Volatility clustering (GARCH(1,1)): calm and turbulent spells, as returns, a price level or the volatility itself.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "volatility", "omega": 1e-05, "alpha": 0.08, "beta": 0.9, "output": "level", "start": 50}`
- `every`: number — Clock units per step of the path (default: one round).
- `omega`: number | text (required) — Baseline variance added each step (> 0).
- `alpha`: number | text = 0.1 — How much the last return's size raises the next variance.
- `beta`: number | text = 0.85 — How much the last variance carries over (alpha + beta < 1 is stable).
- `mean`: number | text = 0.0 — Mean return per step.
- `start`: number | text = 100.0 — Level at round 1 (output level).
- `output`: returns | level | volatility = "returns" — returns: each step's log return | level: start compounded by the returns | volatility: the standard deviation now.

#### `regimes` (random, process)

Regime switching: a Markov chain of states (boom, recession) each with its own values.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "regimes", "states": {"boom": {"growth": 0.02}, "bust": {"growth": -0.01}}, "transitions": {"boom": {"bust": 0.05}, "bust": {"boom": 0.2}}}`
- `every`: number — Clock units per step of the path (default: one round).
- `states`: object (required) — {state: value}: what each state gives (a number, or a map read as $pattern.economy.growth); null gives the state's name.
- `transitions`: object (required) — {from: {to: probability per step}}; the rest of the probability stays in the state.
- `start`: text — The state at round 1 (default: the first declared).

#### `shocks` (random, process)

Shocks: one-off (at), recurring (recur) or random (chance) jumps that last and then fade (half_life). An event can act while one is on: when: $pattern.strike > 0.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "shocks", "chance": 0.05, "size": -0.4, "lasts": 2, "half_life": 3, "form": "multiply"}`
- `every`: number — Clock units per step of the path (default: one round).
- `chance`: number | text = 0.0 — Probability a shock starts in a step.
- `at`: list — Times shocks certainly start (clock units or ISO dates).
- `recur`: number | text — Clock units between shocks that recur on schedule (from the window's start).
- `size`: number | text = 1.0 — Each shock's size (form add: added; multiply: 1 + size).
- `size_sd`: number | text = 0.0 — Spread of each shock's size (normal).
- `lasts`: int = 1 — Steps a shock stays at full size.
- `half_life`: number | text — Steps for what is left of a shock to halve once it has lasted (without one it ends at once).
- `window`: list — [first, last] times shocks may start (last may be null).
- `limit`: int — Most shocks in a run.
- `gap`: int = 0 — Steps after a shock starts before another can.
- `form`: add | multiply = "add" — add: a baseline of 0 plus every shock | multiply: 1 × (1 + each shock).

#### `noise` (random, process)

Fresh noise every step, independent over time; a key (or a keyed pattern) gives each item its own draws.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "noise", "dist": "normal", "sd": 0.01, "keys": "resident"}`
- `every`: number — Clock units per step of the path (default: one round).
- `dist`: normal | uniform | lognormal | laplace = "normal" — The distribution of each step's draw.
- `mean`: number | text = 0.0 — Centre (normal, laplace); log-mean (lognormal).
- `sd`: number | text = 1.0 — Spread (normal, laplace: scale·√2; lognormal: log-sd).
- `low`: number | text = 0.0 — Lowest value (uniform).
- `high`: number | text = 1.0 — Highest value (uniform).

#### `weather` (random, process)

Weather-like driver: a yearly seasonal normal plus persistent departures (autocorrelated), e.g. temperature.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "weather", "mean": 18, "amplitude": 9, "peak": 0.55, "persistence": 0.75, "sd": 3}`
- `every`: number — Clock units per step of the path (default: one round).
- `mean`: number | text (required) — Average over the year.
- `amplitude`: number | text = 0.0 — Seasonal swing above and below the mean.
- `peak`: number | text = 0.55 — Where in the year it is highest, from 0 to 1 (0.55 ≈ mid-July).
- `persistence`: number | text = 0.7 — How much of a step's departure from normal carries into the next (0–1).
- `sd`: number | text = 1.0 — Typical departure from the seasonal normal.

#### `elasticity` (response, response)

Price elasticity: a multiplier on demand for a price, constant-elasticity or linear.

Read: `$pattern.<name>(price[, key])`. Example: `{"kind": "elasticity", "elasticity": "$inputs.elasticity", "reference": 24.99}`
- `elasticity`: number | text (required) — % change in quantity per % change in price at the reference (−1.5).
- `reference`: number | text = 1.0 — The price where the effect is 1.
- `form`: constant | linear = "constant" — constant: (price/reference)^elasticity | linear: 1 + elasticity·(price/reference − 1), never below 0.

#### `cross_price` (response, response)

Substitution between items: each item's demand multiplier from every item's price relative to its reference (own and cross elasticities, or a full matrix). Called with {key: price} and the item's key.

Read: `$pattern.<name>(prices[, key])`. Example: `{"kind": "cross_price", "keys": ["economy", "premium"], "reference": {"economy": 20, "premium": 35}, "own": -1.8, "cross": 0.6}`
- `reference`: number | text | object (required) — Reference price: one for all keys, or {key: price}.
- `own`: number | text (required) — Own-price elasticity (on the diagonal).
- `cross`: number | text = 0.0 — Cross-price elasticity toward the other keys (> 0: substitutes, < 0: complements).
- `groups`: object | text — {key: group}: cross effects only within a group (tiers of the same part).
- `matrix`: list | text — Full elasticities instead of own/cross: row i is how item i answers each price, in key order.

#### `saturation` (response, response)

Diminishing returns: spend, effort or exposure that helps less and less (Hill, logistic or exponential).

Read: `$pattern.<name>(x[, key])`. Example: `{"kind": "saturation", "form": "hill", "limit": 0.35, "half": 2000, "shape": 1.2}`
- `form`: hill | logistic | exponential = "hill" — hill: limit·x^shape/(half^shape + x^shape) | logistic: limit/(1 + e^(−steepness·(x − midpoint))) | exponential: limit·(1 − e^(−x/scale)).
- `limit`: number | text = 1.0 — The most it gives.
- `base`: number | text = 0.0 — Added to the result (the level with no driver).
- `half`: number | text = 1.0 — hill: the driver giving half the limit.
- `shape`: number | text = 1.0 — hill: steepness (> 1 is S-shaped).
- `midpoint`: number | text = 0.0 — logistic: the driver at half the limit.
- `steepness`: number | text = 1.0 — logistic: how sharp the rise is.
- `scale`: number | text = 1.0 — exponential: the driver that gives 63% of the limit.

#### `threshold` (response, response)

A threshold or tipping point: one value below it, another above, switching hard or smoothly.

Read: `$pattern.<name>(x[, key])`. Example: `{"kind": "threshold", "at": 0.3, "below": 1, "above": 1.8, "width": 0.05}`
- `at`: number | text (required) — The tipping point.
- `below`: number | text = 0.0 — Value below it.
- `above`: number | text = 1.0 — Value above it.
- `width`: number | text = 0.0 — 0: a hard switch | > 0: a smooth one over about this width.

#### `learning_curve` (response, response)

A learning curve (Wright's law): cost per unit falls by a fixed ratio each time cumulative output doubles.

Read: `$pattern.<name>(units[, key])`. Example: `{"kind": "learning_curve", "first": 120, "rate": 0.85, "floor": 40}`
- `first`: number | text (required) — Cost (or time) of the first unit.
- `rate`: number | text = 0.8 — Progress ratio: each doubling of cumulative units multiplies the cost by it.
- `floor`: number | text = 0.0 — Lowest it gets.

#### `network` (response, response)

Network effects: value (or appeal) growing with the number or share of users.

Read: `$pattern.<name>(users[, key])`. Example: `{"kind": "network", "form": "log", "strength": 0.2}`
- `form`: power | log = "power" — power: base + strength·users^exponent | log: base + strength·ln(1 + users).
- `strength`: number | text (required) — How much users add.
- `exponent`: number | text = 1.0 — power: 1 linear, 2 Metcalfe-like, < 1 diminishing.
- `base`: number | text = 1.0 — Value with no users.

#### `promotion` (response, memory)

Promotion lift with a post-promotion dip: demand rises by `lift` × intensity while a promotion runs, then dips while customers work through what they bought early.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "promotion", "input": "$it.promo", "keys": "sku", "lift": 0.8, "dip": 0.25, "half_life": 1.5}`
- `input`: text (required) — What drives it, read every round: an expression over the world ($world.promo_spend, $it.price with entity keys).
- `every`: number — Clock units per step of carry-over (default: one round).
- `lift`: number | text (required) — Extra demand per unit of promotion intensity (form linear: 0.6 = +60% at 1).
- `form`: linear | exponential = "linear" — linear: 1 + lift·intensity | exponential: e^(lift·intensity) (log-linear; 20% off at lift 1.65 ≈ +39%).
- `dip`: number | text = 0.0 — Demand lost after a promotion per unit of promotion still remembered (pull-forward).
- `retain`: number | text = 0.5 — Share of the remembered promotion kept each step (how long the dip lasts).
- `half_life`: number | text — Steps for the remembered promotion to halve (instead of retain).

#### `reference_price` (response, memory)

Reference-price effects: customers remember past prices; a price under the memory lifts demand, one above cuts it more (loss aversion). The memory drifts toward prices paid.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "reference_price", "input": "$it.price", "keys": "sku", "retain": 0.8, "gain": 0.8, "loss": 1.6}`
- `input`: text (required) — What drives it, read every round: an expression over the world ($world.promo_spend, $it.price with entity keys).
- `every`: number — Clock units per step of carry-over (default: one round).
- `retain`: number | text = 0.7 — Weight of the old reference when it updates toward the price paid.
- `gain`: number | text = 1.0 — Demand gained per share the price is below the reference.
- `loss`: number | text = 2.0 — Demand lost per share the price is above it (losses loom larger).
- `output`: effect | reference = "effect" — effect: the demand multiplier | reference: the remembered price.

#### `draw` (population, draw)

A value drawn once per run — a prior on an uncertain quantity — or once per key: heterogeneous traits per entity, correlated with mvnormal. Parameters of other patterns may read it.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "draw", "dist": "normal", "mean": -1.4, "sd": 0.3, "max": -0.2, "keys": "sku"}`
- `dist`: normal | lognormal | uniform | beta | gamma | triangular | choice | mvnormal | poisson (required) — The distribution. mvnormal draws correlated values (a list, or a map with `names`).
- `mean`: number | text
- `sd`: number | text
- `mu`: number | text
- `sigma`: number | text
- `low`: number | text
- `high`: number | text
- `mode`: number | text
- `a`: number | text
- `b`: number | text
- `shape`: number | text
- `scale`: number | text
- `values`: list — choice: the values.
- `weights`: list — choice: relative weights.
- `means`: list | text — mvnormal: the means.
- `cov`: list | text — mvnormal: the covariance matrix.
- `names`: list — mvnormal: names for the values, giving a map ($pattern.traits($it).elasticity).
- `log`: bool = false — mvnormal: exponentiate every value (correlated lognormals).
- `integer`: bool = false — A whole number (uniform draws whole numbers from low to high).

#### `segments` (population, draw)

Segments: each key (entity) falls in one segment by share, and reads the segment's values.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "segments", "keys": "customer", "segments": {"bargain": {"share": 0.6, "values": {"elasticity": -2.4}}, "loyal": {"share": 0.4, "values": {"elasticity": -0.8}}}}`
- `segments`: object (required) — {segment: {share, values}}: each key falls in one segment, drawn once per run; reads give {segment, …values}.

#### `diffusion` (population, signal)

Bass diffusion: adoption through innovation and word of mouth — the S-shaped curve over time, or the adoption chance for a given share already adopted (driven by the run's own adopters).

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "diffusion", "p": 0.03, "q": 0.38, "market": 5000, "start": "2025-03-01", "output": "new"}`
- `p`: number | text (required) — Innovation: the share adopting on their own each unit.
- `q`: number | text (required) — Imitation: how strongly adopters draw in others (word of mouth).
- `market`: number | text = 1.0 — Everyone who will eventually adopt.
- `start`: number | text = 0.0 — When adoption begins: clock units or an ISO date.
- `output`: adopters | new | share | hazard = "adopters" — adopters: total so far | new: adopting this round | share: of the market | hazard: called with the adopted share, the chance a non-adopter adopts now (p + q·share).

#### `hazard` (population, response)

A hazard curve: the chance something happens now (churn, failure, leaving) given how long it has lasted — use as $chance($pattern.churn($it.tenure)).

Read: `$pattern.<name>(age[, key])`. Example: `{"kind": "hazard", "form": "weibull", "shape": 0.7, "scale": 30}`
- `form`: constant | weibull | loglogistic | table = "constant" — constant: the same chance at every age | weibull: rising (shape > 1) or falling (< 1) | loglogistic: rising then falling | table: one chance per age.
- `rate`: number | text = 0.05 — constant: chance per `span`.
- `shape`: number | text = 1.0 — weibull, loglogistic: the curve's shape.
- `scale`: number | text = 10.0 — weibull, loglogistic: typical age, in clock units.
- `values`: list | text — table: chance at age 0, 1, 2 … (the last repeats).
- `span`: number | text = 1.0 — Clock units the chance covers (usually one round).

#### `habit` (population, memory)

Habit and fatigue: repeated exposure (purchases, ads, messages) builds a habit that raises response, or a fatigue that wears it down; both fade when exposure stops.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "habit", "form": "fatigue", "input": "$it.ads_seen", "keys": "viewer", "strength": 0.3, "half_life": 3}`
- `input`: text (required) — What drives it, read every round: an expression over the world ($world.promo_spend, $it.price with entity keys).
- `every`: number — Clock units per step of carry-over (default: one round).
- `form`: habit | fatigue = "habit" — habit: 1 + strength·S/(1 + S), growing with repetition | fatigue: 1/(1 + strength·S), wearing out with exposure.
- `strength`: number | text (required) — How strongly the remembered exposure S acts.
- `retain`: number | text = 0.8 — Share of S kept each step.
- `half_life`: number | text — Steps for S to halve (instead of retain).

#### `counts` (observation, response)

Whole-number counts around an expected value: Poisson, or negative binomial for over-dispersed sales and arrivals.

Read: `$pattern.<name>(mean[, key])`. Example: `{"kind": "counts", "dist": "negative_binomial", "dispersion": 6}`
- `dist`: poisson | negative_binomial = "negative_binomial" — poisson: variance = mean | negative_binomial: variance = mean + mean²/dispersion (over-dispersed).
- `dispersion`: number | text = 10.0 — negative_binomial: k; smaller is noisier (fitted by the method of moments).
- `every`: number — Clock units per fresh draw (default: one round).

#### `measurement` (observation, response)

Measurement error: a reading of a true value with bias and noise (surveys, sensors, stock counts).

Read: `$pattern.<name>(value[, key])`. Example: `{"kind": "measurement", "sd": 0.08, "bias": -0.03, "whole": true}`
- `sd`: number | text (required) — Spread of the error (a share of the value with form multiply).
- `bias`: number | text = 0.0 — Systematic error (a share with form multiply: 0.05 reads 5% high).
- `form`: add | multiply = "multiply" — add: value + bias + sd·z | multiply: value·(1 + bias + sd·z).
- `whole`: bool = false — Round the reading to a whole number.
- `every`: number — Clock units per fresh draw (default: one round).

#### `censored` (observation, response)

Censoring: what is observed when a quantity is capped — sales = min(demand, stock) — with what was lost. Gives {value, lost, censored}: $pattern.sold($demand, $it.stock).value.

Read: `$pattern.<name>(demand, capacity[, key])`. Example: `{"kind": "censored"}`

#### `missing` (observation, response)

Missing observations: the value, or null with some chance (gaps in a dashboard, unreported sales).

Read: `$pattern.<name>(value[, key])`. Example: `{"kind": "missing", "chance": 0.05}`
- `chance`: number | text (required) — Probability a reading is missing (null).
- `every`: number — Clock units per fresh draw (default: one round).

#### `carryover` (memory, memory)

Carry-over (adstock) and lags: past inputs keep counting, fading by `retain` each step — advertising, word of mouth, a backlog.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "carryover", "input": "$world.ad_spend", "half_life": 2, "lag": 1}`
- `input`: text (required) — What drives it, read every round: an expression over the world ($world.promo_spend, $it.price with entity keys).
- `every`: number — Clock units per step of carry-over (default: one round).
- `retain`: number | text = 0.5 — Share carried into the next step (adstock decay).
- `half_life`: number | text — Steps for a past input's effect to halve (instead of retain).
- `lag`: int = 0 — Steps before an input starts to count.
- `form`: sum | average = "sum" — sum: input + retain·stock (adstock) | average: (1 − retain)·input + retain·stock (a moving average).
- `start`: number | text = 0.0 — The stock before round 1 (and the input before a lag has filled).

#### `product` (composition, composite)

A product of patterns: a base level times every factor — decompose shows each factor's share; fit estimates the base and every factor together.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "product", "of": ["trend", {"pattern": "season", "key": "$row.category"}], "scale": "$row.base", "table": "$inputs.skus", "column": "sku"}`
- `of`: list (required) — The patterns combined: names (keyed ones get this pattern's key) or {pattern, key}.
- `scale`: number | text = 1.0 — Multiplies the product (a base level).

#### `sum` (composition, composite)

A weighted sum of patterns plus a base: level + seasonal swing + noise.

Read: `$pattern.<name>` (keyed: `$pattern.<name>(key)`). Example: `{"kind": "sum", "of": ["normal_temp", "anomaly"], "base": 0}`
- `of`: list (required) — The patterns combined: names (keyed ones get this pattern's key) or {pattern, key}.
- `weights`: list — One weight per pattern (default 1 each).
- `base`: number | text = 0.0 — Added to the sum.
