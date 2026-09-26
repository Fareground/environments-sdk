# economy / queue

### `economy.queue`
A service system played natively, interval by interval: customers arrive on each channel (a Poisson process at the interval's expected `arrivals`, or exact `scheduled` rows), are answered at once by a free server of a pool with the skill, or wait in line — by `priority`, then arrival — and give up when their `patience` runs out; `callback` offers customers facing a long wait a call back, served when nobody is waiting, and `retry` brings some who gave up back later. Servers finish what they started when staff drops. Every number is read when the interval is played and may read `$interval` (0 for the first), `$inputs`, `$world` and `$pattern`. Each round is one interval, played after the round's stages. Results: $world.<name>_intervals (one record per interval: offered, answered, within, abandoned, callbacks, retrials, service_level, asa, abandon_rate, staff, utilisation, queue, max_queue, paid_hours, cost, and per channel and pool) and $world.<name>_totals; outputs <name>_service_level, _asa, _abandon_rate, _utilisation, _offered, _abandoned, _cost, _paid_hours, _intervals_below_target, and _offered_by_interval, _staff_by_interval, _service_level_by_interval, _abandon_rate_by_interval; metrics <name>_service_level, _offered, _staff and _waiting (the latest interval). Rates count customers who joined the line (offered less callbacks taken): service level is the share answered within the channel's `threshold`. Queue operations cost O(log n); arrivals and each customer's durations come from streams of their own, so arms with different staffing see the same customers.

Config:
- `record_customers` (default false): Record arrivals, service starts, observed completions, abandonments and callbacks in $world.<name>_customer_events and output <name>_customer_events. Customer sequence IDs link events; retry attempts retain their ID. Completions at interval end are recorded once; scheduled_finish on a start is a plan, not observed completion. Opt in for per-customer diagnosis; histories grow with activity.
- `channels` (required): {channel: {arrivals, service, patience, priority, threshold, target, callback, retry}}.
- `servers` (required): {pool: {staff, skills, cost, shrinkage}}.
- `unit` (default "second"): Unit of every duration and threshold.
- `interval` (default null): Length of one interval in `unit` (default: one round of a clock whose unit is second, minute, hour, day or week). Needed when the clock has no time unit.

Nested config:
**ChannelSpec** — A kind of customer: calls, chats, emails, walk-ins.
- `arrivals`: number | text — Expected arrivals in the interval (number or expression over $interval, $inputs, $world, $pattern); arrivals are a Poisson process at that rate.
- `service`: DurationSpec — Service (handle) distribution for Poisson arrivals.
- `scheduled`: [ScheduledArrivalSpec] | text — Exact arrivals: [{at, service, patience?}], or an expression giving that list. Times are absolute from run start in the mode's unit; service and patience are durations. Alternative to arrivals/service/patience distributions. Zero service duration is allowed. Equal-time rows retain list order. Intervals include their start and exclude their end; future rows are not backlog.
- `patience`: DurationSpec — How long a customer waits before giving up (null: never).
- `priority`: int = 0 — Higher is served first; equal priorities are served in arrival order.
- `threshold`: number = 20.0 — Service level threshold: answered within this long (the mode's unit).
- `target`: number — Service level the channel aims for; an interval below it counts in <name>_intervals_below_target.
- `callback`: CallbackSpec — Offer callbacks to customers facing a long wait.
- `retry`: RetrySpec — Customers who gave up try again.
- `description`: text
**DurationSpec** — How long something takes, in the mode's `unit`.
- `dist`: any = "exponential" — exponential (memoryless: Erlang C and A assume it) | lognormal and gamma (mean and cv) | erlang (k phases) | fixed | uniform (low to high).
- `mean`: number | text (required) — Mean duration (number or expression; not used by uniform).
- `cv`: number | text = 1.0 — lognormal, gamma: coefficient of variation (sd ÷ mean).
- `k`: int = 2 — erlang: phases (cv = 1/√k).
- `low`: number | text = 0.0 — uniform: shortest.
- `high`: number | text = 0.0 — uniform: longest.
**ScheduledArrivalSpec** — One observed or authored customer, at absolute time from the start of the run.
- `at`: number (required)
- `service`: number (required)
- `patience`: number
**CallbackSpec** — A callback offered to customers facing a long wait; callbacks are served when nobody is waiting.
- `service_estimate`: number | text — Expected service duration used only in the callback offer's wait estimate. Required with scheduled arrivals; supply information available when deciding, not future realized service times. Poisson channels default to their service distribution mean.
- `when`: number | text = 0.0 — Offer it when the expected wait is longer than this (the mode's unit): (customers waiting on the channel + 1) × mean service ÷ servers on the channel.
- `accept`: number | text = 1.0 — Share of customers offered a callback who take it, from 0 to 1.
- `reserve`: number | text = 0.0 — Servers kept free for live customers: a callback is served only while more than this many servers of the pool are free (0: whenever nobody is waiting, which can take the server the next caller needed).
**RetrySpec** — Customers who gave up trying again later.
- `chance`: number | text (required) — Chance a customer who gave up tries again, from 0 to 1.
- `delay`: DurationSpec (required) — How long after giving up they try again.
- `max`: int = 1 — Most retries per customer.
**PoolSpec** — Servers with the same skills: agents, doctors, counters, technicians.
- `staff`: number | text (required) — Servers on duty in the interval: a whole number or an expression giving one ($inputs.staffing[$interval]); a shift is staff that changes by interval.
- `skills`: [text] | any = "all" — Channels the pool serves, most preferred first (a free server takes the waiting customer first by priority, then arrival).
- `cost`: number | text = 0.0 — Cost of one server per paid hour.
- `overrun_cost`: number | text — Cost per hour of continuing service above scheduled staffing after staff drops. Defaults to cost; set an explicit rate for premiums or unpaid overrun. Overrun hours count actual service time without shrinkage gross-up.
- `shrinkage`: number | text = 0.0 — Share of paid time not on duty (breaks, training), from 0 to below 1: paid hours = staff × hours ÷ (1 − shrinkage).
- `description`: text

```json
{"inputs": {"calls": {"type": "list", "default": [20, 30, 25]}, "staffing": {"type": "list", "default": [4, 5, 5]}}, "clock": {"rounds": 3, "unit": "second"}, "types": {}, "mechanisms": {"my_queue": {"kind": "economy", "mode": "queue", "unit": "second", "interval": 1800, "channels": {"calls": {"arrivals": "$inputs.calls[$interval]", "service": {"dist": "lognormal", "mean": 380, "cv": 0.6}, "patience": {"mean": 160}, "threshold": 20, "target": 0.8, "callback": {"when": 90, "accept": 0.6}}}, "servers": {"agents": {"staff": "$inputs.staffing[$interval]", "cost": 26, "shrinkage": 0.3}}}}}
```
