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

## What the model assumes

- Demand lost after a promotion per unit of remembered promotion (assumed, not fitted), set to 0.25.
- How strongly demand moves between tiers when their relative price changes (assumed), set to 0.8.
- 8 parameters are estimated from the data; the analyst report lists them.

## How well it matched the data

- Not checked against data here: pass validation=fg_env.analysis.validate(contract, cases).
