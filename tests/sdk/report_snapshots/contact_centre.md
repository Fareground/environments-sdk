# Northline contact centre: staffing a day of calls

## What the model says

- The manager's rule and the optimised plan are within noise on staffing cost: −$383 (95% CI −$1,001 to +$235), so the rule cannot pick between them.
- Decide between them on something else: nothing measured here separates them.
- With the manager's rule, service level is clearly above 80% (mean 93.4%, 95% CI 90.6%–96.2%).
- Staff between 9 and 27 agents per half-hour, most (27) 09:30–11:00; the plan table lists every change.
- Expected: service level 93.9% (80% range 92.4%–94.1%); staffing cost $9,175 (80% range $9,040–$9,432); service level in the worst half-hour 74.3% (80% range 70%–75.7%); abandonment 1.7% (80% range 1.5%–2.2%); average wait to answer 3 s (80% range 2 s–3 s).

**Options**

| Option | Service level | Staffing cost | Service level in the worst half-hour | Abandonment | Average wait to answer |
|---|---|---|---|---|---|
| The optimised plan ≈ | 94.3% (80% range 94.2%–96.5%) | $9,609 | 77.1% (80% range 66.5%–79.8%) | 1.5% (80% range 0.8%–1.7%) | 2 s (80% range 1 s–2 s) |
| The manager's rule ≈ | 93.9% (80% range 92.4%–94.1%) | $9,175 (80% range $9,040–$9,432) | 74.3% (80% range 70%–75.7%) | 1.7% (80% range 1.5%–2.2%) | 3 s (80% range 2 s–3 s) |
| A network outage at 09:30 with the recommended plan | 39.2% (80% range 38.4%–41.5%) | $9,609 | 0% | 33.5% (80% range 32.6%–35.3%) | 28 s (80% range 28 s–29 s) |

**Staffing plan**

| When | Agents | Customers | Service level (80% range) | Worst half-hour |
|---|---|---|---|---|
| 08:00–08:30 | 14 | 44 | 100% (99%–100%) | 100% |
| 08:30–09:00 | 21 | 77 | 99% (99%–100%) | 99% |
| 09:00–09:30 | 23 | 102 | 94% (94%–97%) | 94% |
| 09:30–11:00 | 27 | 306 | 97% (97%–98%) | 94% |
| 11:00–11:30 | 22 | 92 | 93% (87%–95%) | 93% |
| 11:30–12:00 | 23 | 79 | 100% (98%–100%) | 100% |
| 12:00–12:30 | 25 | 95 | 85% (72%–97%) | 85% |
| 12:30–13:30 | 26 | 208 | 92% (90%–95%) | 88% |
| 13:30–14:00 | 25 | 93 | 90% (83%–98%) | 90% |
| 14:00–14:30 | 22 | 86 | 96% (88%–97%) | 96% |
| 14:30–15:30 | 20 | 154 | 89% (88%–95%) | 88% |
| 15:30–16:00 | 21 | 87 | 99% (94%–100%) | 99% |
| 16:00–17:00 | 18 | 126 | 87% (85%–97%) | 84% |
| 17:00–17:30 | 17 | 57 | 95% (86%–96%) | 95% |
| 17:30–18:30 | 16 | 110 | 97% (87%–99%) | 94% |
| 18:30–19:00 | 13 | 40 | 100% (90%–100%) | 100% |
| 19:00–19:30 | 11 | 35 | 94% (80%–99%) | 94% |
| 19:30–20:00 | 9 | 33 | 82% (76%–96%) | 82% |

## What drives it

- The busiest half-hour is 10:30–11:00, with about 111 customers: of the 108 expected, time of day adds 28, day of the week (Monday) adds 27.
- Over the day, of about 1,826 expected calls: time of day adds 44% at 09:30–10:00 and takes away 58% at 19:30–20:00; the day of the week (Monday) adds 33%.
- A network outage at 09:30 with the recommended plan: service level −55.3 points against the optimised plan (95% CI −56.9 points to −53.7 points).
- In a typical run, waiting rose 3 at 12:30–13:00, then gave back all of it by 13:00–13:30.
- In a typical run, customers rose 40 (+51%) at 12:00–12:30, then gave back all of it by 19:30–20:00, while service level fell 31 points.

## Risks

- 2 half-hours fall below the service target in a typical run.
- With a network outage at 09:30 with the recommended plan: service level 39.2% (80% range 38.4%–41.5%).

## What the model assumes

- Calls arrive at random at the expected rate of each half-hour; service takes 379.5 seconds on average; customers give up after waiting 165.7 seconds on average, changed by the contract at times; a callback is offered when the wait would pass 90 seconds.
- ASSUMED: spread of handle times (sd / mean); the history holds only half-hourly averages, set to 0.6.
- ASSUMED: minutes for an outage surge to halve, set to 80.
- ASSUMED: callers' patience during an outage surge, as a share of normal patience, set to 0.55.
- ASSUMED: share of callers offered a callback who take it, set to 0.6.
- 3 parameters are estimated from the data; the analyst report lists them.

## How well it matched the data

- Not checked against data here: pass validation=fg_env.validate(contract, cases).
