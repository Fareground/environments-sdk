# Used-iPhone reseller: graded stock, two channels and launches

## What the model says

- Buy to a 90% service level instead of three weeks of cover and the baseline are within noise on profit: +$32 (95% CI −$335 to +$400), so the rule cannot pick between them.
- Decide between them on something else: nothing measured here separates them.
- With buy to a 90% service level instead of three weeks of cover, fill rate is above 95% only just: the interval crosses it (mean 97.5%, 95% CI 91.7%–100%).
- Expected: fill rate 98.8% (80% range 95.6%–98.9%); profit $15,224 (80% range $14,934–$16,835).

**Options**

| Option | Fill rate | Profit |
|---|---|---|
| The baseline ≈ | 97.2% (80% range 94.8%–98.4%) | $15,328 (80% range $14,946–$16,704) |
| List retail 8% cheaper and sell to wholesale at 90% of value | 96.0% (80% range 92.8%–97.6%) | $14,706 (80% range $14,275–$15,327) |
| Buy to a 90% service level instead of three weeks of cover ≈ | 98.8% (80% range 95.6%–98.9%) | $15,224 (80% range $14,934–$16,835) |

## What drives it

- Retail demand over the 28 days is about 244 units. For every model, the day of the week adds 10% on Thursdays and takes away 15% on Mondays.
- Wholesale demand over the 28 days is about 254 units. For every model, the trend adds 5% over the 28 days, against its level on 2026-01-01.
- In a typical run, units on order rose 140 (+97%) on day 8 (2026-08-10), then gave back all of it by day 28 (2026-08-30).
- In a typical run, units in stock rose 44 (+20%) to 261 on day 22 (2026-08-24): the largest one-day move, 2.6× the typical move, while stock value rose $9,337 (+22%).

## Risks

- Fill rate was below 95% in 1 of 3 runs.
- Reported ranges describe variation in the supplied runs, not all real-world uncertainty. Identical recorded outcomes do not prove the model is deterministic. Repeated stochastic runs estimate variability under the configured model; varying assumptions tests sensitivity, and independent observations test accuracy.

## What the model assumes

- All options: The first day — start = "2026-08-03" (matches declared default).
- All options: Days to run — days = 28 (differs from declared default).
- All options: Item lines (model × grade): grade value factor, refurbishment per unit, retail return rate, and the demand each line saw when the history began — items = 16 records (item, model, grade, grade_factor, refurb_cost, retail_return, +2 more) (differs from declared default).
- All options: Models: launch date, price when new, and the launches since — models = 4 records (model, launch, msrp, later) (matches declared default).
- All options: Daily sales per item and channel: units, whether stock ran out, units on hand, price and the retail markup (retail rows only) — history = 11648 records (time, item, segment, units, stockout, stock, +2 more) (differs from declared default).
- All options: Lots bought per item: day placed and day listed, units, days from purchase to listing, and that over the usual time — orders = 562 records (time, item, placed, arrived, qty, lead_time, +1 more) (differs from declared default).
- All options: Phones on hand per item at the start, {item: units}; items left out start with 20 — opening stock = {} (matches declared default).
- All options: Lots on their way at the start, {item: [[day due, units, day placed, lead time, factor], …]} — opening orders = {} (matches declared default).
- All options: Cash at the start — cash = 150000 (matches declared default).
- All options: A new iPhone launches on new_launch_date — new launch = false (matches declared default).
- All options: new launch date — new launch date = "2026-09-18" (matches declared default).
- All options: Wholesale value of a new grade-A phone, as a share of its price new — resale share = 0.6 (matches declared default).
- All options: Log value lost per month of age — monthly decay = 0.017 (matches declared default).
- All options: Value kept at each later launch — launch drop = 0.93 (matches declared default).
- The baseline, Buy to a 90% service level instead of three weeks of cover: Retail listing over wholesale value, per grade (1.31 is the marketplace's fair uplift) — retail markup = {"A": 1.31, "B": 1.31, "C": 1.31, "D": 1.31} (matches declared default).
- List retail 8% cheaper and sell to wholesale at 90% of value: Retail listing over wholesale value, per grade (1.31 is the marketplace's fair uplift) — retail markup = {"A": 1.2, "B": 1.2, "C": 1.2, "D": 1.2} (differs from declared default).
- All options: Marketplace fee on retail sales — retail fee = 0.1325 (matches declared default).
- All options: Spread of day-to-day repricing around the markup (log) — markup wiggle = 0 (matches declared default).
- The baseline, Buy to a 90% service level instead of three weeks of cover: What wholesale buyers pay, as a share of value — wholesale share = 0.86 (matches declared default).
- List retail 8% cheaper and sell to wholesale at 90% of value: What wholesale buyers pay, as a share of value — wholesale share = 0.9 (differs from declared default).
- All options: What a graded lot costs, as a share of value — bid share = 0.72 (matches declared default).
- All options: Cost of money tied up in stock, per day — capital rate daily = 0.0004 (matches declared default).
- The baseline, List retail 8% cheaper and sell to wholesale at 90% of value: cover: every week buy up to cover_days of forecast demand | service: up to the forecast over lead time and a week, with safety stock for service_level — buy policy = "cover" (matches declared default).
- Buy to a 90% service level instead of three weeks of cover: cover: every week buy up to cover_days of forecast demand | service: up to the forecast over lead time and a week, with safety stock for service_level — buy policy = "service" (differs from declared default).
- All options: cover days — cover days = 21 (matches declared default).
- All options: service level — service level = 0.9 (matches declared default).
- All options: Usual days from buying a lot to listing its phones — lead days = 10 (matches declared default).
- All options: Fewest phones of an item a supplier sells — lot minimum = 10 (matches declared default).
- All options: weekly budget — weekly budget = 40000 (matches declared default).
- All options: Record every day's sales and every lot (the truth arm does) — record history = false (matches declared default).
- All options: Scales the standard errors of fitted pattern parameters: 1 draws each run's parameters around their estimates, 0 uses the estimates as they are — parameter uncertainty = 1 (matches declared default).
- All options: Pattern 'weekday', profile: fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — weekday profile = [0.8541, 0.8758, 1.0123, 1.0959, 1.0595, 1.0771, 1.0254] (matches declared default).
- All options: Standard error of weekday_profile — weekday profile se = [0.0438, 0.0445, 0.048, 0.05, 0.0493, 0.0498, 0.0482] (matches declared default).
- All options: Pattern 'retail_price_effect', elasticity: fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — retail price effect elasticity = -2.4169 (matches declared default).
- All options: Standard error of retail_price_effect_elasticity — retail price effect elasticity se = 0.1941 (matches declared default).
- All options: Pattern 'retail_demand', one row per key: scale fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — retail demand fit = 16 records (item, model, grade, grade_factor, refurb_cost, retail_return, +4 more) (matches declared default).
- All options: Pattern 'sales', dispersion: fitted by fg_env.analysis.fit_patterns from $inputs.history (method of moments around the fitted means) — sales dispersion = 3.4645 (matches declared default).
- All options: Pattern 'wholesale_trend', rate: fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — wholesale trend rate = 0.000195 (matches declared default).
- All options: Standard error of wholesale_trend_rate — wholesale trend rate se = 0.000198 (matches declared default).
- All options: Pattern 'wholesale_demand', one row per key: scale fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — wholesale demand fit = 16 records (item, model, grade, grade_factor, refurb_cost, retail_return, +4 more) (matches declared default).
- All options: Pattern 'bulk', dispersion: fitted by fg_env.analysis.fit_patterns from $inputs.history (method of moments around the fitted means) — bulk dispersion = 1.6827 (matches declared default).
- All options: Pattern 'lead_noise', mean: fitted by fg_env.analysis.fit_patterns from $inputs.orders (method of moments) — lead noise mean = 0.0162 (matches declared default).
- All options: Standard error of lead_noise_mean — lead noise mean se = 0.0147 (matches declared default).
- All options: Pattern 'lead_noise', sd: fitted by fg_env.analysis.fit_patterns from $inputs.orders (method of moments) — lead noise sd = 0.3473 (matches declared default).
- Input values above are bounded display summaries; exact values are retained in each recorded run's inputs. Matching a default does not establish how a value was supplied.
- 9 parameters are estimated from the data; the analyst report lists them.
- A bulk reseller of used iPhones (four models, four grades each) on the economy demand and replenishment modes. A graded phone's wholesale value is the model's price when new × a resale share × its grade, falling with age and stepping down over three weeks at every later iPhone launch (a lifecycle pattern). Retail buyers online pay the listing (value × markup, less the marketplace fee), answer the markup with a fitted elasticity and a weekday profile, and send 5–13% of phones back; wholesale buyers take bulk at a share of value and rarely return. The reseller buys graded lots every week up to a few weeks of forecast demand (or to a service level), paying a share of value plus refurbishment, within a lot minimum, case packs and a weekly budget, with delivery and grading times drawn per lot. Demand and lead times are fitted from bundled sales and purchase histories that the `truth` arm records (examples/phone_reseller_study.py regenerates them, compares channels, forks a new-model launch and validates on held-out months).

## How well it matched the data

- No outcome-validation results were supplied. These runs do not establish predictive accuracy.
