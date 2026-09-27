# Northline contact centre: staffing a day of calls

## Recommendation

- Choose the optimised plan.
- How sure: its staffing cost is lower than with the manager's rule by $277 (95% CI −$551 to −$3, 3 paired runs).
- Its service level is clearly above 80% (mean 90.2%, 95% CI 83.7%–96.7%).
- Staff between 11 and 27 agents per half-hour, most (27) 09:30–10:00; the plan table lists every change.
- Expected: service level 90.3% (80% range 88.1%–92.3%); staffing cost $8,836 (80% range $8,836–$8,837); service level in the worst half-hour 73.3% (80% range 66.6%–73.7%); abandonment 2.9% (80% range 2.4%–3.2%); average wait to answer 4 s (80% range 3 s–5 s).

**Options**

| Option | Service level | Staffing cost | Service level in the worst half-hour | Abandonment | Average wait to answer |
|---|---|---|---|---|---|
| The optimised plan ✓ | 90.3% (80% range 88.1%–92.3%) | $8,836 (80% range $8,836–$8,837) | 73.3% (80% range 66.6%–73.7%) | 2.9% (80% range 2.4%–3.2%) | 4 s (80% range 3 s–5 s) |
| The manager's rule | 93.3% (80% range 92.4%–93.9%) | $9,157 (80% range $9,022–$9,187) | 73.3% (80% range 65.1%–74.3%) | 2% (80% range 1.8%–2%) | 3 s (80% range 2 s–3 s) |
| A network outage at 09:30 with the recommended plan | 35.6% (80% range 34.3%–37.4%) | $8,838 (80% range $8,838–$8,839) | 0% (80% range 0%–0.3%) | 37.1% (80% range 35.4%–37.4%) | 30 s (80% range 30 s–31 s) |

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
- In a typical run, customers rose 44 (+63%) at 12:00–12:30, then gave back all of it by 19:30–20:00, while service level fell 35 points.
- In a typical run, waiting rose 2 at 12:30–13:00, then gave back all of it by 13:00–13:30.

## Risks

- 3 half-hours fall below the service target in a typical run.
- With a network outage at 09:30 with the recommended plan: service level 35.6% (80% range 34.3%–37.4%).
- Reported ranges describe variation in the supplied runs, not all real-world uncertainty. Identical recorded outcomes do not prove the model is deterministic. Repeated stochastic runs estimate variability under the configured model; varying assumptions tests sensitivity, and independent observations test accuracy.

## What the model assumes

- Calls arrive at random at the expected rate of each half-hour; service takes 379.5 seconds on average; customers give up after waiting 155.1 seconds on average, changed by the contract at times; a callback is offered when the wait would pass 90 seconds.
- Service continuing above scheduled staffing is counted as overrun time and priced at the configured overrun rate (the ordinary hourly rate by default).
- All options: The day simulated: its opening time (ISO date-time; opening must stay 08:00) — day = "2026-09-14T08:00" (matches declared default).
- All options: Half-hourly history: calls offered, answered, abandoned and answered within 20 s, handle and wait times, agents on duty, outage — history = 1344 records (time, date, weekday, interval, offered, answered, +6 more) (differs from declared default).
- The optimised plan, A network outage at 09:30 with the recommended plan: Agents on duty in each of the 24 half-hours from 08:00; empty: the manager's rule — staffing = 24 values; first 5: [11, 16, 22, 27, 26] (differs from declared default).
- The manager's rule: Agents on duty in each of the 24 half-hours from 08:00; empty: the manager's rule — staffing = [] (matches declared default).
- All options: Cost of one agent per paid hour — agent cost = 26.0 (matches declared default).
- All options: Share of paid time not on the phones (breaks, training, meetings), from the workforce plan — shrinkage = 0.31 (matches declared default).
- All options: Average handle time in seconds: estimated from the history (answered-weighted) by examples/contact_centre_history.py — aht sec = 379.5 (matches declared default).
- All options: ASSUMED: spread of handle times (sd / mean); the history holds only half-hourly averages — aht cv = 0.6 (matches declared default).
- All options: Mean time a caller waits before hanging up: calibrated to the history's daily abandonment by examples/contact_centre_history.py — patience sec = 155.1 (matches declared default).
- All options: When a network outage starts (only matters with outage_uplift above 0) — outage at = "2026-09-14T09:30" (matches declared default).
- The optimised plan, The manager's rule: Extra calls at an outage's peak, as a multiple of the normal volume (0: no outage) — outage uplift = 0.0 (matches declared default).
- A network outage at 09:30 with the recommended plan: Extra calls at an outage's peak, as a multiple of the normal volume (0: no outage) — outage uplift = 3.105 (differs from declared default).
- All options: Peak uplift estimated from the outage days in the history (first half-hour's calls over the fitted forecast) by examples/contact_centre_history.py; the outage arms use it — outage uplift estimate = 3.105 (matches declared default).
- All options: ASSUMED: minutes for an outage surge to halve — outage half life = 80 (matches declared default).
- All options: ASSUMED: callers' patience during an outage surge, as a share of normal patience — outage patience share = 0.55 (matches declared default).
- All options: Offer callers facing a long wait a call back — callbacks = false (matches declared default).
- All options: Offer a callback when the expected wait is longer than this (seconds) — callback wait sec = 90 (matches declared default).
- All options: ASSUMED: share of callers offered a callback who take it — callback accept = 0.6 (matches declared default).
- All options: Agents kept free for live calls: a callback is made only while more agents than this are free — callback reserve = 3 (matches declared default).
- All options: Scales the standard errors of fitted pattern parameters: 1 draws each run's parameters around their estimates, 0 uses the estimates as they are — parameter uncertainty = 1 (matches declared default).
- All options: Pattern 'weekday', profile: fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — weekday profile = [1.3283, 1.1801, 1.0736, 1.0659, 1.0252, 0.743, 0.584] (matches declared default).
- All options: Standard error of weekday_profile — weekday profile se = [0.0123, 0.0117, 0.0134, 0.0112, 0.0111, 0.00962, 0.00862] (matches declared default).
- All options: Pattern 'hours', profile: fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — hours profile = 24 values; first 5: [0.5622, 0.8367, 1.2109, 1.4464, 1.431] (matches declared default).
- All options: Standard error of hours_profile — hours profile se = 24 values; first 5: [0.0165, 0.0201, 0.0239, 0.026, 0.0259] (matches declared default).
- All options: Pattern 'calls', scale: fitted by fg_env.analysis.fit_patterns from $inputs.history (joint count regression) — calls scale = 57.129 (matches declared default).
- All options: Standard error of calls_scale — calls scale se = 0.2632 (matches declared default).
- Input values above are bounded display summaries; exact values are retained in each recorded run's inputs. Matching a default does not establish how a value was supplied.
- 3 parameters are estimated from the data; the analyst report lists them.
- One day (08:00-20:00, half-hours) at a broadband provider's contact centre, played call by call by an operations queue. Calls arrive at a fitted base x a day-of-week index x a time-of-day index (fitted from contact_centre/history.csv with fg_env.analysis.fit_patterns), surging after a network outage; handle times are lognormal around the average handle time in the history; callers give up after a patience calibrated to the history's abandonment, shorter during an outage. Agents on duty per half-hour are an input vector (empty: the manager's rule, 10% over the forecast workload plus two). Arms compare the manager's rule with the plan fg_env.analysis.optimise found (the cheapest where every half-hour's expected service level reaches 80% of calls answered within 20 seconds and the day's does in 90% of runs, each held with 90% confidence), an outage at 09:30, and callbacks that keep agents free for live calls. examples/contact_centre_history.py regenerates the history from the truth arm and re-estimates the model; examples/contact_centre_plan.py searches the plan (kept in contact_centre/plan.json) and, with --report, validates the model on the two held-out weeks and prints the owner report.

## How well it matched the data

- No outcome-validation results were supplied. These runs do not establish predictive accuracy.
