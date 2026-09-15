# Northline contact centre: staffing a day of calls

## What the model says

- The optimised plan and the manager's rule are within noise on staffing cost: −$295 (95% CI −$913 to +$323), so the rule cannot pick between them.
- Decide between them on something else: nothing measured here separates them.
- With the optimised plan, service level is clearly above 80% (mean 90.4%, 95% CI 83.2%–97.5%).
- Staff between 10 and 27 agents per half-hour, most (27) 09:30–10:00; the plan table lists every change.
- Expected: service level 89.7% (80% range 88.2%–92.8%); staffing cost $8,930; service level in the worst half-hour 69.5% (80% range 60.3%–73.3%); abandonment 3.1% (80% range 2.1%–3.2%); average wait to answer 4 s (80% range 3 s–5 s).

**Options**

| Option | Service level | Staffing cost | Service level in the worst half-hour | Abandonment | Average wait to answer |
|---|---|---|---|---|---|
| The optimised plan ≈ | 89.7% (80% range 88.2%–92.8%) | $8,930 | 69.5% (80% range 60.3%–73.3%) | 3.1% (80% range 2.1%–3.2%) | 4 s (80% range 3 s–5 s) |
| The manager's rule ≈ | 93.9% (80% range 92.4%–94.1%) | $9,175 (80% range $9,040–$9,432) | 74.3% (80% range 70%–75.7%) | 1.7% (80% range 1.5%–2.2%) | 3 s (80% range 2 s–3 s) |
| A network outage at 09:30 with the recommended plan | 35.4% (80% range 34.7%–38%) | $8,930 | 0% | 36.2% (80% range 35.2%–38%) | 32 s (80% range 31 s–32 s) |

**Staffing plan**

| When | Agents | Customers | Service level (80% range) | Worst half-hour |
|---|---|---|---|---|
| 08:00–08:30 | 12 | 44 | 95% (89%–99%) | 95% |
| 08:30–09:00 | 20 | 77 | 99% (98%–100%) | 99% |
| 09:00–09:30 | 22 | 102 | 86% (84%–91%) | 86% |
| 09:30–10:00 | 27 | 92 | 100% (100%–100%) | 100% |
| 10:00–10:30 | 25 | 107 | 84% (83%–94%) | 84% |
| 10:30–11:00 | 26 | 109 | 96% (75%–99%) | 96% |
| 11:00–11:30 | 22 | 92 | 86% (82%–93%) | 86% |
| 11:30–12:30 | 23 | 190 | 89% (78%–98%) | 83% |
| 12:30–13:30 | 25 | 208 | 87% (85%–95%) | 83% |
| 13:30–14:00 | 23 | 93 | 81% (79%–90%) | 81% |
| 14:00–14:30 | 22 | 86 | 94% (93%–95%) | 94% |
| 14:30–16:00 | 20 | 241 | 90% (89%–93%) | 79% |
| 16:00–17:00 | 18 | 126 | 88% (87%–98%) | 84% |
| 17:00–17:30 | 17 | 57 | 86% (85%–92%) | 86% |
| 17:30–18:00 | 15 | 54 | 91% (83%–94%) | 91% |
| 18:00–18:30 | 16 | 53 | 100% (95%–100%) | 100% |
| 18:30–19:00 | 14 | 40 | 100% (100%–100%) | 100% |
| 19:00–19:30 | 11 | 35 | 94% (80%–99%) | 94% |
| 19:30–20:00 | 10 | 33 | 86% (77%–97%) | 86% |

## What drives it

- The busiest half-hour is 10:30–11:00, with about 111 customers: of the 108 expected, time of day adds 28, day of the week (Monday) adds 27.
- Over the day, of about 1,826 expected calls: time of day adds 44% at 09:30–10:00 and takes away 58% at 19:30–20:00; the day of the week (Monday) adds 33%.
- A network outage at 09:30 with the recommended plan: service level −54.2 points against the optimised plan (95% CI −56.1 points to −52.2 points).
- In a typical run, waiting rose 5 at 09:00–09:30, then gave back all of it by 09:30–10:00.
- In a typical run, service level fell 24 points at 19:00–19:30, then recovered all of it by 19:30–20:00.

## Risks

- 2 half-hours fall below the service target in a typical run.
- With a network outage at 09:30 with the recommended plan: service level 35.4% (80% range 34.7%–38%).

## What the model assumes

- Calls arrive at random at the expected rate of each half-hour; service takes 379.5 seconds on average; customers give up after waiting 165.7 seconds on average, changed by the contract at times; a callback is offered when the wait would pass 90 seconds.
- ASSUMED: spread of handle times (sd / mean); the history holds only half-hourly averages, set to 0.6.
- ASSUMED: minutes for an outage surge to halve, set to 80.
- ASSUMED: callers' patience during an outage surge, as a share of normal patience, set to 0.55.
- ASSUMED: share of callers offered a callback who take it, set to 0.6.
- 3 parameters are estimated from the data; the analyst report lists them.

## How well it matched the data

- Not checked against data here: pass validation=fg_env.validate(contract, cases).
