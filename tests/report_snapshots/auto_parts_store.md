# Auto parts store: demand patterns and reorder policies

## Recommendation

- Choose order up to the expected demand over lead time and review, with safety stock for a 95% service level. It is the only option that meets the requirement.
- Its fill rate is clearly above 95% (mean 98.9%, 95% CI 96.6%–100%).
- Expected: fill rate 98.4% (80% range 98.3%–99.6%); profit $31,581 (80% range $27,632–$31,950).

**Options**

| Option | Fill rate | Profit |
|---|---|---|
| The store's current rule | 92.4% (80% range 86.3%–94%) | $28,499 (80% range $26,651–$30,243) |
| Order up to the expected demand over lead time and review, with safety stock for a 95% service level ✓ | 98.4% (80% range 98.3%–99.6%) | $31,581 (80% range $27,632–$31,950) |

## What drives it

- Demand over the 13 weeks is about 1,248 units.
- Wipers (572 units): promotions add 31% over the 3 weeks they raise it; price changes add 23% over the 13 weeks; the time of year adds 4% in March and takes away 10% in January.
- Brake pads (424 units): promotions add 31% over the 2 weeks they raise it and take away 1% over the 9 weeks they lower it; the time of year adds 9% in March and takes away 11% in February; price changes add 4% over the 13 weeks.
- Batteries (252 units): promotions add 31% over the 2 weeks they raise it and take away 1% over the 7 weeks they lower it; the time of year adds 22% in January and takes away 1% in March; price changes add 2% over the 13 weeks.
- In a typical run, units asked for rose 52 (+50%) to 157 in week 13 (2026-03-30): the largest one-week move, 6.1× the typical move, while units sold rose 52 (+50%).
- In a typical run, stock value rose $3,208 (+33%) in week 6 (2026-02-09), then gave back 89% of it by week 13 (2026-03-30).

## Risks

- With the store's current rule: fill rate 92.4% (80% range 86.3%–94%).
- Reported ranges describe variation in the supplied runs, not all real-world uncertainty. Identical recorded outcomes do not prove the model is deterministic. Repeated stochastic runs estimate variability under the configured model; varying assumptions tests sensitivity, and independent observations test accuracy.

## What the model assumes

- All options: The first week (a Monday) — start = "2026-01-05" (matches declared default).
- All options: Weeks to run — weeks = 13 (differs from declared default).
- All options: Units on hand per SKU at the start, {sku: units}; SKUs left out start with four weeks of forecast demand — opening stock = {} (matches declared default).
- All options: Orders on their way at the start, {sku: [[week due, units, week placed, lead time, lead-time factor], …]} with weeks counted from the first — opening orders = {} (matches declared default).
- All options: The SKUs: part, category, tier, sibling in the other tier, prices, cost, lead time and case pack — skus = 12 records (sku, part, category, tier, sibling, list_price, +3 more) (differs from declared default).
- All options: Weekly sales history the demand patterns are fitted from: units sold, whether stock ran out, units on hand, price, promotion depth and price relative to list (the price response's driver) — history = 1872 records (time, item, units, stockout, stock, price, +2 more) (differs from declared default).
- All options: Purchase orders as they arrived: SKU, week placed and week arrived (weeks of the history), units, lead time in weeks and the lead time over the SKU's usual one — the lead-time pattern is fitted from them — orders = 767 records (time, item, placed, arrived, qty, lead_time, +1 more) (differs from declared default).
- All options: Each category's promotion calendar offset (weeks) — categories = 3 records (category, promo_offset) (matches declared default).
- The store's current rule: lean: the store's rule — reorder when stock covers less than lean_cover of the lead time at the average of the last 8 weeks' sales, up to 1.5 weeks more | service: order up to the expected demand over lead time and review plus safety stock for the category's service level — policy = "lean" (matches declared default).
- Order up to the expected demand over lead time and review, with safety stock for a 95% service level: lean: the store's rule — reorder when stock covers less than lean_cover of the lead time at the average of the last 8 weeks' sales, up to 1.5 weeks more | service: order up to the expected demand over lead time and review plus safety stock for the category's service level — policy = "service" (differs from declared default).
- All options: The lean rule reorders when stock covers less than this share of the lead time at the trailing 8-week average — lean cover = 0.8 (matches declared default).
- All options: Chance of not running out before an order can arrive (service policy) — service level = 0.95 (matches declared default).
- All options: A service level per category, {category: level}; categories left out use service_level. A decision fg_env.analysis.optimise can search — service level by category = {} (matches declared default).
- All options: Holding cost per week, as a share of the unit cost — holding rate = 0.004 (matches declared default).
- All options: Cost of placing one order — order cost = 6.0 (matches declared default).
- All options: Weeks between a category's promotions — promo every = 6 (matches declared default).
- All options: Share off during a promotion — promo depth = 0.2 (matches declared default).
- All options: Demand lost after a promotion per unit of remembered promotion (assumed, not fitted) — promo dip = 0.25 (matches declared default).
- All options: Share of the remembered promotion kept each week — promo retain = 0.5 (matches declared default).
- All options: How strongly demand moves between tiers when their relative price changes (assumed) — cross elasticity = 0.8 (matches declared default).
- All options: Share of a premium stockout's lost demand that buys the value tier instead — spill share = 0.3 (matches declared default).
- All options: Extra share on premium prices (a pricing experiment) — premium markup = 0.0 (matches declared default).
- All options: Post every SKU-week to the history record (the truth arm does) — record history = false (matches declared default).
- All options: Scales the standard errors of fitted pattern parameters: 1 draws each run's parameters around their estimates, 0 uses the estimates as they are — parameter uncertainty = 1 (matches declared default).
- All options: Pattern 'growth', rate: fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — growth rate = 0.00102 (matches declared default).
- All options: Standard error of growth_rate — growth rate se = 0.000339 (matches declared default).
- All options: Pattern 'promo', lift: fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — promo lift = 1.3364 (matches declared default).
- All options: Standard error of promo_lift — promo lift se = 0.3762 (matches declared default).
- All options: Pattern 'sales', dispersion: fitted by fg_env.analysis.fit_patterns from $inputs.history (method of moments around the fitted means) — sales dispersion = 5.3159 (matches declared default).
- All options: Pattern 'season', one row per key: profile fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — season fit = 3 records (category, profile, profile_se) (matches declared default).
- All options: Pattern 'price_effect', one row per key: elasticity fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — price effect fit = 3 records (category, elasticity, elasticity_se) (matches declared default).
- All options: Pattern 'demand', one row per key: scale fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — demand fit = 12 records (sku, category, scale, scale_se) (matches declared default).
- All options: Pattern 'lead_noise', mean: fitted by fg_env.analysis.fit_patterns from $inputs.orders (method of moments) — lead noise mean = 0.00587 (matches declared default).
- All options: Standard error of lead_noise_mean — lead noise mean se = 0.0108 (matches declared default).
- All options: Pattern 'lead_noise', sd: fitted by fg_env.analysis.fit_patterns from $inputs.orders (method of moments) — lead noise sd = 0.2979 (matches declared default).
- Input values above are bounded display summaries; exact values are retained in each recorded run's inputs. Matching a default does not establish how a value was supplied.
- 8 parameters are estimated from the data; the analyst report lists them.
- A parts store's weekly trade in 12 SKUs (brake pads, batteries and wiper blades for two makes, each in a premium and a value tier), on the economy demand and replenishment modes. Demand per SKU is a fitted base × a trend × a per-category season × a price response × a promotion with a dip after it × substitution between the tiers, drawn as negative-binomial counts; sales are capped by stock, part of a premium stockout buys the value tier, and orders arrive after a lead time drawn per order. The demand patterns are fitted from auto_parts_store/history.csv (fg_env.analysis.fit_patterns; examples/auto_parts_history.py regenerates the history from the `truth` arm and refits). Arms compare the store's lean reorder rule with a service-level policy; examples/auto_parts_policies.py compares them, optimises a service level per category and validates the forecast on held-out quarters.

## How well it matched the data

- No outcome-validation results were supplied. These runs do not establish predictive accuracy.
