# agreements

## Mechanism family `agreements`

Commitments between agents over time: negotiated deals, jobs, subscriptions and bookings.

Named the same in every mode:
- `who`: agent type(s) making the commitments
- `currency`: the property (or ledger currency) holding money
- `tools`: how generated tools are offered: each (one tool per action, the default) | one (one tool named after the mechanism, with an `action` argument listing the actions legal now) | auto (one tool only when every action takes the same arguments)

Modes (`"kind": "agreements", "mode": ...`; read one with `guide('agreements.<mode>')`):
- `bookings`: Capacity-limited places: with `format: slots` guests book a future round (paying on booking) and join a waitlist when it is full, promoted first-fit when a place frees; with `format: queue` they wait in line and are served (paying on service) as capacity allows each round.
- `labor`: A labor market: employers `<name>_post` jobs (title, wage, openings) and `<name>_hire`, `<name>_reject` or `<name>_fire`; workers `<name>_apply` to open postings, `<name>_withdraw` and `<name>_quit`.
- `negotiation`: Negotiation over several issues: `<name>_propose`, `<name>_counter` (up to `max_depth`), `<name>_accept`, `<name>_reject` and `<name>_withdraw`, each offered only for offers open to you, with issue bounds as tool bounds, an optional deadline and expiry.
- `subscriptions`: Recurring plans: `<name>_subscribe` (plans you do not have and can afford or try free), `<name>_cancel` (ends at the next renewal), `<name>_resume`, and `<name>_set_price` for providers.

Functions:
- `$booking_text(agent, bookings)` — The agent's latest booking of a bookings mechanism, in words.
- `$places_text(resource, bookings)` — Free places of a resource in the coming rounds (slots) or the line length (queue).
- `$subscribed(agent, plan_or_provider)` — True when the agent has a live subscription (trial or paid) to the plan, or to any plan of the provider.
- `$terms_text(terms, negotiation)` — Terms of an offer or deal as plain words, with units.
