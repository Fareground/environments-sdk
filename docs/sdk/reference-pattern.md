# pattern

## Mechanism family `pattern`

Named patterns of the world, read as $pattern.<name>: trends, seasons, responses, random processes, draws, observation noise and memory; `guide('patterns')` teaches them.

Named the same in every mode:
- `record`: record the pattern every round as a metric of its name ($series.<name>)

Modes (`"kind": "pattern", "mode": ...`; read one with `guide('pattern.<mode>')`):
- `product`: A product of patterns: a base level times every factor — decompose shows each factor's share; fit estimates the base and every factor together.
- `sum`: A weighted sum of patterns plus a base: level + seasonal swing + noise.
- `trend`: A long-run trend: linear, exponential or a logistic S-curve.
- `seasonal`: A repeating season: a profile per month, weekday or hour, a smooth wave, or harmonics — around 1 or 0.
- `calendar`: Calendar effects — weekends, named days, holidays, paydays, month ends, months — per day; a longer round averages the days it covers.
- `cycle`: A regular cycle of any length: sine, square, triangle or sawtooth.
- `lifecycle`: A life after a date: before → ramp up to a peak → decay toward a floor (a product launch, a price falling after a new model).
- `step`: Step changes that last: a value that jumps to, by or times an amount at set times.
- `series`: Values from data: a real history (weather, prices, footfall) read at the current time — how a pattern is driven by the customer's own series.
- `draw`: A value drawn once per run — a prior on an uncertain quantity — or once per key: heterogeneous traits per entity, correlated with mvnormal.
- `segments`: Segments: each key (entity) falls in one segment by share, and reads the segment's values.
- `diffusion`: Bass diffusion: adoption through innovation and word of mouth — the S-shaped curve over time, or the adoption chance for a given share already adopted (driven by the run's own adopters).
- `elasticity`: Price elasticity: a multiplier on demand for a price, constant-elasticity or linear.
- `cross_price`: Substitution between items: each item's demand multiplier from every item's price relative to its reference (own and cross elasticities, or a full matrix).
- `saturation`: Diminishing returns: spend, effort or exposure that helps less and less (Hill, logistic or exponential).
- `threshold`: A threshold or tipping point: one value below it, another above, switching hard or smoothly.
- `learning_curve`: A learning curve (Wright's law): cost per unit falls by a fixed ratio each time cumulative output doubles.
- `network`: Network effects: value (or appeal) growing with the number or share of users.
- `hazard`: A hazard curve: the chance something happens now (churn, failure, leaving) given how long it has lasted — use as $chance($pattern.churn($it.tenure)).
- `carryover`: Carry-over (adstock) and lags: past inputs keep counting, fading by `retain` each step — advertising, word of mouth, a backlog.
- `promotion`: Promotion lift with a post-promotion dip: demand rises by `lift` × intensity while a promotion runs, then dips while customers work through what they bought early.
- `reference_price`: Reference-price effects: customers remember past prices; a price under the memory lifts demand, one above cuts it more (loss aversion).
- `habit`: Habit and fatigue: repeated exposure (purchases, ads, messages) builds a habit that raises response, or a fatigue that wears it down; both fade when exposure stops.
- `counts`: Whole-number counts around an expected value: Poisson, or negative binomial for over-dispersed sales and arrivals.
- `measurement`: Measurement error: a reading of a true value with bias and noise (surveys, sensors, stock counts).
- `censored`: Censoring: what is observed when a quantity is capped — sales = min(demand, stock) — with what was lost.
- `missing`: Missing observations: the value, or null with some chance (gaps in a dashboard, unreported sales).
- `random_walk`: A random walk, additive or geometric, with drift.
- `mean_reversion`: Mean reversion (Ornstein–Uhlenbeck, exact at any step length): wanders but is pulled back to a mean.
- `autoregressive`: Autoregression AR(p): each step carries a share of the last ones, plus a new shock (persistence, momentum).
- `volatility`: Volatility clustering (GARCH(1,1)): calm and turbulent spells, as returns, a price level or the volatility itself.
- `regimes`: Regime switching: a Markov chain of states (boom, recession) each with its own values.
- `shocks`: Shocks: one-off (at), recurring (recur) or random (chance) jumps that last and then fade (half_life).
- `noise`: Fresh noise every step, independent over time; a key (or a keyed pattern) gives each item its own draws.
- `weather`: Weather-like driver: a yearly seasonal normal plus persistent departures (autocorrelated), e.g.

Functions:
- `$pattern_values(name)` — Every key's value of a keyed pattern now, as {key: value}: $pattern_values('season').
