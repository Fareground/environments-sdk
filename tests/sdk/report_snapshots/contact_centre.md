# Northline contact centre: staffing a day of calls

## Recommendation

- Choose the optimised plan.
- How sure: its staffing cost is lower than with the manager's rule by $276 (95% CI −$551 to −$2, 3 paired runs).
- Its service level is clearly above 80% (mean 90.2%, 95% CI 83.7%–96.7%).
- Staff between 11 and 27 agents per half-hour, most (27) 09:30–10:00; the plan table lists every change.
- Expected: service level 90.3% (80% range 88.1%–92.3%); staffing cost $8,836; service level in the worst half-hour 73.3% (80% range 66.6%–73.7%); abandonment 2.9% (80% range 2.4%–3.2%); average wait to answer 4 s (80% range 3 s–5 s).

**Options**

| Option | Service level | Staffing cost | Service level in the worst half-hour | Abandonment | Average wait to answer |
|---|---|---|---|---|---|
| The optimised plan ✓ | 90.3% (80% range 88.1%–92.3%) | $8,836 | 73.3% (80% range 66.6%–73.7%) | 2.9% (80% range 2.4%–3.2%) | 4 s (80% range 3 s–5 s) |
| The manager's rule | 93.3% (80% range 92.4%–93.9%) | $9,157 (80% range $9,021–$9,187) | 73.3% (80% range 65.1%–74.3%) | 2% (80% range 1.8%–2%) | 3 s (80% range 2 s–3 s) |
| A network outage at 09:30 with the recommended plan | 35.6% (80% range 34.3%–37.4%) | $8,836 | 0% (80% range 0%–0.3%) | 37.1% (80% range 35.4%–37.4%) | 30 s (80% range 30 s–31 s) |

**Staffing plan**

| When | Agents | Customers | Service level (80% range) | Worst half-hour |
|---|---|---|---|---|
| 08:00–08:30 | 11 | 38 | 97% (86%–99%) | 97% |
| 08:30–09:00 | 16 | 59 | 95% (92%–98%) | 95% |
| 09:00–09:30 | 22 | 87 | 91% (86%–95%) | 91% |
| 09:30–10:00 | 27 | 94 | 100% (99%–100%) | 100% |
| 10:00–11:00 | 26 | 219 | 86% (84%–96%) | 83% |
| 11:00–11:30 | 22 | 95 | 95% (73%–96%) | 95% |
| 11:30–12:00 | 21 | 75 | 100% (98%–100%) | 100% |
| 12:00–12:30 | 22 | 99 | 74% (67%–95%) | 74% |
| 12:30–13:00 | 25 | 115 | 93% (74%–94%) | 93% |
| 13:00–14:00 | 24 | 197 | 93% (89%–94%) | 90% |
| 14:00–14:30 | 22 | 84 | 93% (82%–96%) | 93% |
| 14:30–15:00 | 21 | 73 | 88% (83%–98%) | 88% |
| 15:00–15:30 | 19 | 80 | 92% (81%–93%) | 92% |
| 15:30–16:00 | 20 | 84 | 89% (84%–98%) | 89% |
| 16:00–16:30 | 19 | 69 | 94% (84%–99%) | 94% |
| 16:30–17:00 | 18 | 63 | 100% (89%–100%) | 100% |
| 17:00–17:30 | 17 | 63 | 87% (83%–93%) | 87% |
| 17:30–18:00 | 16 | 57 | 86% (84%–91%) | 86% |
| 18:00–18:30 | 15 | 49 | 98% (96%–100%) | 98% |
| 18:30–19:00 | 13 | 43 | 100% (79%–100%) | 100% |
| 19:00–19:30 | 12 | 41 | 98% (80%–100%) | 98% |
| 19:30–20:00 | 11 | 35 | 87% (86%–97%) | 87% |

## What drives it

- The busiest half-hour is 12:30–13:00, with about 112 customers: of the 103 expected, time of day adds 26, day of the week (Monday) adds 25.
- Over the day, of about 1,821 expected calls: time of day adds 45% at 09:30–10:00 and takes away 57% at 19:30–20:00; the day of the week (Monday) adds 33%.
- A network outage at 09:30 with the recommended plan: service level −54.4 points against the optimised plan (95% CI −56.1 points to −52.7 points).
- In a typical run, waiting rose 2 at 15:30–16:00, then gave back all of it by 16:00–16:30.
- In a typical run, service level fell 26 points at 12:00–12:30, then recovered all of it by 14:30–15:00.

## Risks

- 3 half-hours fall below the service target in a typical run.
- With a network outage at 09:30 with the recommended plan: service level 35.6% (80% range 34.3%–37.4%).

## What the model assumes

- Calls arrive at random at the expected rate of each half-hour; service takes 379.5 seconds on average; customers give up after waiting 155.1 seconds on average, changed by the contract at times; a callback is offered when the wait would pass 90 seconds.
- ASSUMED: spread of handle times (sd / mean); the history holds only half-hourly averages, set to 0.6.
- ASSUMED: minutes for an outage surge to halve, set to 80.
- ASSUMED: callers' patience during an outage surge, as a share of normal patience, set to 0.55.
- ASSUMED: share of callers offered a callback who take it, set to 0.6.
- 3 parameters are estimated from the data; the analyst report lists them.

## How well it matched the data

- Not checked against data here: pass validation=fg_env.analysis.validate(contract, cases).
