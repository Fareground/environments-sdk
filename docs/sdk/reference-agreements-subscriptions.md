# agreements / subscriptions

### `agreements.subscriptions`
Recurring plans: `<name>_subscribe` (plans you do not have and can afford or try free), `<name>_cancel` (ends at the next renewal), `<name>_resume`, and `<name>_set_price` for providers. Renewals are charged automatically at the start of the round they are due; a subscriber is woken when a trial ends or the price changed, and a renewal that cannot be paid lapses. Plans are entities of `<name>_plan`, subscriptions of `<name>_sub`; totals (started, trials, converted, renewed, cancelled, lapsed, revenue) are in $world.<name>_stats. $subscribed(agent, plan_or_provider) reads membership.

Config:
- `who` (required): Type(s) that may subscribe.
- `currency` (required): Ledger currency plans are paid in.
- `plans` (default {}): {plan id: {provider, name, price, period, trial}}.
- `providers` (default null): Agent type(s) that may reprice their own plans.
- `price_min` (default 0): Lowest price a provider may set.
- `price_max` (default null): Highest price a provider may set.
- `actions` (default ["subscribe", "cancel", "resume", "set_price"]): Tools generated for agents.

Nested config:
**PlanSpec** — A plan offered from the start (more can be added as entities of type `<name>_plan`).
- `provider`: text (required) — Entity id that offers the plan and receives the payments.
- `name`: text
- `price`: number (required) — Charged at the start of every period.
- `period`: int = 30 — Rounds between charges.
- `trial`: int = 0 — Free rounds before the first charge (once per subscriber and plan).
- `description`: text

Actions of the `agreements` op:
- `subscribe` — takes `who`, `plan` (needs `who`, `plan`): {"agreements": "coffee", "action": "subscribe", "who": "$actor", "plan": "$params.plan"}  (start a subscription: a trial or a first charge)
- `set_price` — takes `plan`, `price` (needs `plan`, `price`): {"agreements": "coffee", "action": "set_price", "plan": "coffee_club", "price": 36}  (reprice a plan and tell its subscribers)

```json
{"mechanisms": {"my_subscriptions": {"kind": "agreements", "mode": "subscriptions", "who": "household", "currency": "cash", "providers": "cafe", "plans": {"coffee_club": {"provider": "bean_bar", "price": 30, "period": 30, "trial": 7}}}}}
```
