# economy / ledger

### `economy.ledger`
Money: each currency is a number property of every holder (`$actor.cash`) with an optional credit limit. `pay` moves money (never creating it), `mint`/`burn` name their source or sink, scheduled `sources` pay UBI or allowances, `taxes` withhold a share of payments that name them, and `loans` add `<name>_borrow`, `<name>_repay` and `<name>_set_rate` with per-round interest, due dates and default. The invariant `$conserved(<name>)` proves balances equal $world.<name>_supply, counting the money markets hold for their traders (reserves, escrow, vaults, fees), so markets trade in the ledger's currency; $world.<name>_flows totals every source and sink.

Config:
- `who` (required): Type(s) holding money (subtypes included).
- `currencies` (required): {currency: {start, credit, unit, value}}; each is a holder property ($actor.cash).
- `sources` (default {}): Scheduled money creation: {name: {to, amount, every, mode}}.
- `taxes` (default {}): Levies payments can name: {name: {rate, on, to}}.
- `loans` (default null): Loans at posted rates with interest, due dates and default.
- `actions` (default []): Tools generated for agent holders: pay (pay any holder). Loans generate their own.
- `tools` (default "each"): How the generated tools are offered: each (one tool per action) | one (one tool named after the mechanism, whose `action` argument lists the actions legal now) | auto (one tool only when every action takes the same arguments).

Nested config:
**CurrencySpec** — 
- `start`: number | text = 0.0 — Starting balance of every holder (number or expression).
- `credit`: number | text — How far below zero a holder may go (number or expression); none when omitted.
- `unit`: text — Unit shown with amounts.
- `value`: number = 1 — Worth of one unit in $net_worth.
- `description`: text
**SourceSpec** — Money created on a schedule: UBI, allowances, subsidies.
- `to`: text (required) — Type that receives it.
- `amount`: number | text (required) — Amount per recipient (number or expression over $it).
- `currency`: text — Currency (needed when the ledger has several).
- `where`: text — Which recipients ($it).
- `every`: int = 1 — Rounds between payments.
- `start`: int = 1 — First round it pays.
- `mode`: any = "add" — add | top_up (up to amount) | reset (unspent money expires, then amount).
- `say`: text — News headline when it pays.
**TaxSpec** — A levy on payments that name it: {"economy": <ledger>, "action": "pay", ..., "tax": name}.
- `rate`: number | text (required) — Share of the payment (number or expression over $payer, $payee, $amount).
- `on`: any = "payee" — payee: withheld from what is received; payer: added on top.
- `to`: text — Entity id collecting it; omitted = the money leaves the economy (a sink).
- `description`: text
**LoanSpec** — 
- `lenders`: text | [text] (required) — Type(s) that lend at a posted rate.
- `borrowers`: text | [text] (required) — Type(s) that may borrow.
- `currency`: text
- `rate_min`: number = 0 — Lowest interest per round.
- `rate_max`: number = 0.2 — Highest interest per round.
- `max_amount`: number | text = 1000.0 — Largest loan (number or expression over $actor).
- `max_term`: int = 12 — Longest term in rounds.
- `grace`: int = 0 — Rounds after the due date before an unpaid loan defaults.
- `on_default`: [any] — Effects when a loan defaults ($loan, $lender, $borrower, $unpaid).

Actions of the `economy` op:
- `pay` — takes `currency`, `from`, `to`, `amount`, `tax` (needs `from`, `to`, `amount`): {"economy": "money", "action": "pay", "from": "$actor", "to": "$params.shop", "amount": 12, "tax": "sales_tax"}  (moves money, using credit; a declared tax is withheld; fails the action if short; `currency` only when the ledger has several)
- `mint` — takes `currency`, `to`, `amount`, `source` (needs `to`, `amount`, `source`): {"economy": "money", "action": "mint", "to": "$it", "amount": 50, "source": "subsidy"}  (new money from a named source)
- `burn` — takes `currency`, `from`, `amount`, `sink` (needs `from`, `amount`, `sink`): {"economy": "money", "action": "burn", "from": "$actor", "amount": 3, "sink": "fees"}  (money leaves the economy to a named sink; fails if short)
- `lend` — takes `from`, `to`, `amount`, `rate`, `term` (needs `from`, `to`, `amount`, `term`): {"economy": "money", "action": "lend", "from": "$params.bank", "to": "$actor", "amount": 100, "term": 6}  (a loan: pays the principal now; rate defaults to the lender's posted rate)
- `repay` — takes `loan`, `amount` (needs `loan`, `amount`): {"economy": "money", "action": "repay", "loan": "$params.loan", "amount": 50}  (pays a loan down; the borrower pays, never on credit)

```json
{"mechanisms": {"my_ledger": {"kind": "economy", "mode": "ledger", "who": ["household", "shop"], "currencies": {"cash": {"start": 100, "credit": 20}}, "sources": {"allowance": {"to": "household", "amount": 300, "every": 30, "mode": "reset"}}, "taxes": {"sales_tax": {"rate": 0.08, "on": "payer"}}}}}
```
