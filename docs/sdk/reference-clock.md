# clock

## `clock`: Clock

How long a run lasts (`rounds`, default 20) and what one round is called.

**Clock** — How long a run lasts and how rounds are labelled.
- `rounds`: int | text = 20 — Round budget (number or expression over $inputs).
- `unit`: text = "round" — Name of one round: day, week, turn, hour …
- `start`: text — ISO date of round 1 (adds a calendar date), or an expression over $inputs giving one (`"$inputs.start"`).
- `step`: int = 1 — Units per round (e.g. 7 with unit 'day' = weekly rounds).
- `mode`: text = "rounds" — rounds (every round is one step) | continuous (time is a number: actions take `duration`, `scheduled` stages wake agents when their time comes).
- `tick`: number = 1.0 — Continuous: how far time moves when nothing is due sooner.
- `jump`: bool = true — Continuous: jump straight to the next moment something is due (an agent's turn or an `after` effect) instead of moving by `tick`.
- `horizon`: number | text — Continuous: the run completes when time would pass this (number or expression over $inputs).
