# agreements / labor

### `agreements.labor`
A labor market: employers `<name>_post` jobs (title, wage, openings) and `<name>_hire`, `<name>_reject` or `<name>_fire`; workers `<name>_apply` to open postings, `<name>_withdraw` and `<name>_quit`. With `hiring: rule` pending applicants are hired at the start of each round by `rank`. Wages are paid from employer to worker every `pay_every` rounds (a named ledger tax withheld); an unpaid wage ends the job or is owed. With `firm`, each employer makes output from its workers (and inputs) at the end of each round and posts a price. Entities: `<name>_posting`, `<name>_application`, `<name>_job`; totals in $world.<name>_stats.

Config:
- `who` (required): Type(s) that work.
- `employers` (required): Type(s) that hire.
- `currency` (required): Ledger currency wages are paid in.
- `hiring` (default "choice"): choice: employers hire applicants with a tool; rule: applicants are hired automatically each round.
- `rank` (default null): Rule hiring order: expression over $it (the worker), higher first; default first come.
- `wage_min` (default 0): Lowest wage a posting may offer.
- `wage_max` (default null): Highest wage a posting may offer.
- `pay_every` (default 1): Rounds between paydays; each pays the per-round wage for every round since the last.
- `tax` (default null): A ledger tax withheld from wages.
- `max_jobs` (default 1): Jobs one worker may hold.
- `max_openings` (default 10): Most openings one posting may have.
- `on_unpaid` (default "quit"): An unpaid wage ends the job (quit) or is owed and paid first next payday (owe).
- `inventory` (default null): Inventory of a firm's goods (needed with `firm`).
- `firm` (default null): Employers are firms: {output, per_worker, inputs, price, price_min, price_max}.
- `actions` (default ["post", "close", "hire", "reject", "fire", "apply", "withdraw", "quit", "set_price"]): Tools generated for agents.

Nested config:
**FirmSpec** — Employers as firms: workers make output, sold at a posted price.
- `output`: text (required) — Item made.
- `per_worker`: number | text = 1.0 — Units per worker per round (number or expression over $firm and $workers).
- `inputs`: object — Goods used up per unit {item: qty}.
- `price`: number | text = 1.0 — Starting posted price (prop `<name>_price`).
- `price_min`: number = 0 — Lowest price a firm may post.
- `price_max`: number — Highest price a firm may post.

Actions of the `agreements` op:
- `hire` — takes `application` (needs `application`): {"agreements": "jobs", "action": "hire", "application": "$params.application"}  (turn a pending application into a job)
- `quit` — takes `job` (needs `job`): {"agreements": "jobs", "action": "quit", "job": "$params.job"}  (the worker leaves: wages stop)
- `fire` — takes `job` (needs `job`): {"agreements": "jobs", "action": "fire", "job": "$params.job"}  (the employer lets the worker go: wages stop)

```json
{"mechanisms": {"my_labor": {"kind": "agreements", "mode": "labor", "who": "person", "employers": "bakery", "currency": "cash", "wage_min": 5, "wage_max": 30, "inventory": "goods", "firm": {"output": "bread", "per_worker": 4, "inputs": {"flour": 1}, "price": 3}}}}
```
