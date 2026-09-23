# agreements / negotiation

### `agreements.negotiation`
Negotiation over several issues: `<name>_propose`, `<name>_counter` (up to `max_depth`), `<name>_accept`, `<name>_reject` and `<name>_withdraw`, each offered only for offers open to you, with issue bounds as tool bounds, an optional deadline and expiry. Walk-away values stay private (`<name>_reservation`) and `value` shows each party what terms are worth to it alone; with both, no party can offer or accept terms worth less to it than its walk-away value. A signed deal (`<name>_deal`) schedules `obligations` as duties (`<name>_duty`) executed as conserved payments or deliveries; a duty not met by its due round is a breach with a penalty, optional termination and `on_breach` effects. `transfers` hand unique entities (a lot of phones) to a party at signing and `on_sign` effects settle the rest; a settlement that cannot happen refuses the acceptance. Totals in $world.<name>_stats.

Config:
- `who` (required): Agent type(s) that negotiate.
- `issues` (required): {issue: {type, min, max, values, unit}}.
- `max_depth` (default 6): Longest chain of counter-offers.
- `expires` (default null): Rounds an offer stays open.
- `deadline` (default null): Last round offers can be made or accepted (number or expression).
- `coalition` (default false): Offers go to several parties at once; all of them must accept.
- `reservation` (default null): Private walk-away value of each party (prop `<name>_reservation`); with `value`, no party offers or accepts terms worth less to it.
- `value` (default null): Worth of terms to a party, shown only to that party: expression over $party and $terms.
- `once` (default true): The first signed deal closes the negotiation.
- `obligations` (default []): What a signed deal makes parties pay or deliver.
- `transfers` (default []): Unique entities a signed deal hands over at once (`<name>_deal.items` lists them).
- `on_sign` (default []): Effects when a deal is signed, after its transfers and duties ($deal, $proposer, $acceptor, $parties, $terms); a `fail` refuses the signing.
- `breach` (default null): What a breach costs: {penalty, currency, terminate, on_breach}; nothing by default.
- `actions` (default ["propose", "counter", "accept", "reject", "withdraw", "fulfill"]): Tools generated for the parties.
- `tools` (default "each"): How the generated tools are offered: each (one tool per action) | one (one tool named after the mechanism, whose `action` argument lists the actions legal now) | auto (one tool only when every action takes the same arguments).

Nested config:
**IssueSpec** — One dimension of a deal.
- `type`: any = "number"
- `min`: number
- `max`: number
- `values`: [any] — Choices (type enum).
- `unit`: text
- `description`: text
**ObligationSpec** — What a signed deal makes someone pay or deliver, in installments.
- `label`: text
- `from`: text (required) — Who owes it: expression over $proposer, $acceptor, $parties, $terms.
- `to`: text (required) — Who receives it (same roots).
- `pay`: text — Currency paid.
- `give`: text — Item delivered.
- `amount`: number | text (required) — Per installment: number or expression over $terms and $k (1, 2, …).
- `times`: int | text = 1 — Installments (number or expression over $terms).
- `every`: int = 1 — Rounds between installments.
- `start`: int = 1 — Rounds after signing until the first is due.
- `manual`: bool = false — The obligor must fulfil it with a tool by its due round; otherwise it is automatic.
**TransferSpec** — Unique entities a signed deal hands over (a lot of phones, a house): each one's `field` is set to the recipient.
- `label`: text — What moves, in words ("phones").
- `items`: text (required) — The entities on offer, in order: expression over $proposer, $acceptor, $parties and $terms, e.g. `$filter(phone, $it.owner == $proposer.id)`.
- `count`: int | text — How many of them move: number or expression over $terms (default all). Fewer on offer refuses the signing.
- `to`: text (required) — Who receives them (same roots).
- `field`: text = "owner" — The property of each item set to the recipient's id.
**BreachSpec** — 
- `penalty`: number | text = 0.0 — Owed to the other side on each breach: number or expression over $terms and $duty.
- `currency`: text — Currency of the penalty (default: the breached payment's, or the first ledger's).
- `terminate`: bool = false — A breach ends the deal: remaining installments are cancelled.
- `on_breach`: [any] — Effects on a breach ($deal, $duty, $breacher, $victim, $terms).

Actions of the `agreements` op:
- `propose` — takes `who`, `to`, `terms`, `note` (needs `who`, `to`, `terms`): {"agreements": "trade", "action": "propose", "who": "$actor", "to": "$params.to", "terms": {"tariff": 10, "quota": 200}}  (an offer to a party; a list of parties for a coalition)
- `counter` — takes `who`, `offer`, `terms`, `note` (needs `who`, `offer`, `terms`): {"agreements": "trade", "action": "counter", "who": "$actor", "offer": "$params.offer", "terms": {"tariff": 12, "quota": 150}}  (answer an offer made to you with other terms; it replaces that offer)
- `accept` — takes `who`, `offer` (needs `who`, `offer`): {"agreements": "trade", "action": "accept", "who": "$actor", "offer": "$params.offer"}  (accept an offer made to you; it binds once everyone it went to accepts)
- `reject` — takes `who`, `offer` (needs `who`, `offer`): {"agreements": "trade", "action": "reject", "who": "$actor", "offer": "$params.offer"}  (turn down an offer made to you)
- `withdraw` — takes `who`, `offer` (needs `who`, `offer`): {"agreements": "trade", "action": "withdraw", "who": "$actor", "offer": "$params.offer"}  (take back an offer you made)
- `fulfill` — takes `who`, `duty` (needs `who`, `duty`): {"agreements": "trade", "action": "fulfill", "who": "$actor", "duty": "$params.duty"}  (pay or deliver an installment you owe)

```json
{"mechanisms": {"my_negotiation": {"kind": "agreements", "mode": "negotiation", "who": "country", "deadline": 8, "issues": {"tariff": {"min": 0, "max": 30, "unit": "%"}, "quota": {"type": "int", "min": 0, "max": 500}}, "reservation": 40, "value": "$party.weight * $terms.quota - $terms.tariff", "obligations": [{"from": "$acceptor", "to": "$proposer", "pay": "credits", "amount": "$terms.quota", "times": 4}], "breach": {"penalty": 100, "terminate": true}}}}
```
