# Auto parts store: demand patterns and reorder policies

## Recommendation

- Choose order up to the expected demand over lead time and review, with safety stock for a 95% service level. It is the only option that meets the requirement.
- Its fill rate is clearly above 95% (mean 99%, 95% CI 97.1%–100%).
- Expected: fill rate 98.7% (80% range 98.5%–99.7%); profit $30,943 (80% range $26,976–$31,866).

**Options**

| Option | Fill rate | Profit |
|---|---|---|
| The store's current rule | 92.6% (80% range 88.7%–93.1%) | $27,749 (80% range $25,855–$30,130) |
| Order up to the expected demand over lead time and review, with safety stock for a 95% service level ✓ | 98.7% (80% range 98.5%–99.7%) | $30,943 (80% range $26,976–$31,866) |

## What drives it

- Demand over the 13 weeks is about 1,247 units.
- Wipers (571 units): promotions add 31% over the 3 weeks they raise it; price changes add 23% over the 13 weeks; the time of year adds 4% in March and takes away 10% in January.
- Brake pads (424 units): promotions add 31% over the 2 weeks they raise it and take away 1% over the 9 weeks they lower it; the time of year adds 9% in March and takes away 11% in February; price changes add 4% over the 13 weeks.
- Batteries (252 units): promotions add 31% over the 2 weeks they raise it and take away 1% over the 7 weeks they lower it; the time of year adds 22% in January and takes away 1% in March; price changes add 2% over the 13 weeks.
- Order up to the expected demand over lead time and review, with safety stock for a 95% service level: fill rate +7.9 points against the store's current rule (95% CI +0.9 points to +14.8 points).
- In a typical run, units asked for rose 44 (+48%) to 136 in week 13 (2026-03-30): the largest one-week move, 8.8× the typical move, while units sold rose 44 (+48%).
- In a typical run, stock value rose $3,022 (+33%) in week 6 (2026-02-09), then gave back 67% of it by week 8 (2026-02-23).

## Risks

- With the store's current rule: fill rate 92.6% (80% range 88.7%–93.1%).

## What the model assumes

- Demand lost after a promotion per unit of remembered promotion (assumed, not fitted), set to 0.25.
- How strongly demand moves between tiers when their relative price changes (assumed), set to 0.8.
- 8 parameters are estimated from the data; the analyst report lists them.

## How well it matched the data

- Not checked against data here: pass validation=fg_env.validate(contract, cases).
