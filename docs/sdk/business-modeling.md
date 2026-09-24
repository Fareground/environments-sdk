# Model business behavior

Use ordinary contract sections for custom rules. Use a built-in mechanism when its semantics match your problem. Mechanisms expand into inspectable contract sections; they are optional building blocks, not fixed scenario templates.

## Capability map

| Requirement | Building blocks | Important modeling choice |
|---|---|---|
| Multiple products and customer segments | `types`, `population`, table inputs | Stable IDs, units, segment weights |
| Seasonal and price-sensitive demand | [Patterns](reference-patterns.md), [demand](reference-economy-demand.md) | Lost demand versus observed sales; substitution |
| Inventory and reorder rules | [Inventory](reference-economy-inventory.md), [replenishment](reference-economy-replenishment.md) | Available versus reserved stock; lead times |
| Shared production capacity | [Production](reference-economy-production.md), actions and stages | Allocation order, partial fulfillment, bottlenecks |
| Supplier networks | [Supply chain](reference-economy-supply_chain.md), relations | Disruption correlation and payment terms |
| Money and settlement | [Ledger](reference-economy-ledger.md), atomic effects | Cash versus revenue versus profit |
| Sales pipeline and approvals | Entity state, stages, delayed effects | Stage exit conditions, dependencies, churn |
| Bookings and subscriptions | [Bookings](reference-agreements-bookings.md), [subscriptions](reference-agreements-subscriptions.md) | Cancellation, renewal and capacity release |
| Campaign exposure and diffusion | [Diffusion](reference-social-diffusion.md), records posted to an audience, relations | Audience overlap, repeated exposure, conversion lag |
| Queued work and scarce staff | [Queues](reference-economy-queue.md), actions, events | Priority, skills, abandonment and service duration |
| External judgments or data | [Feeds](reference-host-feed.md), host mechanisms | Host response validation, availability and replay |

## Money and settlement

Use `metrics` for per-round expressions and `outputs` for final results. `format`
belongs on outputs, not metrics. For per-entity rows, use an output expression
such as `$map(product, {id: $it.id, name: $it.name, stock: $it.stock})`.
Include entity IDs in row reports: display names may repeat, so names alone
cannot identify which entity a result belongs to.

Use integer minor units for accounting that must be exact. Dollar inputs and
outputs can remain ordinary numbers: convert them to cents once, perform budget
checks and whole-unit ratios in cents, then divide by 100 for reporting. A money
format changes presentation, not numeric arithmetic. The `money` formatter takes
dollars in outputs, views and action receipts. Convert every cent-valued expression
there, not only the final report. State the rounding policy.
Configuration inputs and action parameters have different schemas. On `inputs`,
`multiple_of: 0.01` validates cents and `step` is a display hint. On action
`params`, `step: 0.01` validates increments from `min` (or zero); describe them
with `description`, not input-only `label` or `display`. Do not put `multiple_of`
on action parameters. For whole cents, choose a cent-aligned minimum.
This example rejects fractional cents before changing the budget.

```python
import fg_env

money = {
    "name": "Exact budget", "clock": {"rounds": 1},
    "inputs": {
        "budget": {"type": "number", "default": 0.30, "min": 0, "step": 0.01, "multiple_of": 0.01,
                   "description": "Dollars in whole-cent increments; fractional cents are rejected"},
        "unit_cost": {"type": "number", "default": 0.10, "min": 0.01, "step": 0.01, "multiple_of": 0.01,
                      "description": "Dollars in whole-cent increments; fractional cents are rejected"}
    },
    "world": {"available_cents": "$round($inputs.budget * 100)",
              "unit_cents": "$round($inputs.unit_cost * 100)", "spent_cents": 0},
    "types": {"operator": {"agent": True}},
    "entities": {"manager": {"type": "operator"}},
    "stages": [{"name": "allocate", "max_actions": 4}],
    "actions": {"spend": {
        "by": "operator",
        "params": {"amount": {"type": "number", "min": 0.01, "step": 0.01,
                              "max": "$world.available_cents / 100",
                              "description": "Amount in dollars, in whole cents."}},
        "do": ["$cost_cents = $round($params.amount * 100)",
               "$world.available_cents -= $cost_cents", "$world.spent_cents += $cost_cents"],
        "outcome": "Paid {$cost_cents / 100|money}."
    }},
    "outputs": {"spent": "$world.spent_cents / 100",
                "units": "$world.spent_cents // $world.unit_cents"}
}
def spend_in_parts(wake):
    assert not wake.call("spend", {"amount": 0.105}).ok
    for amount, receipt in [(0.10, "Paid $0.10."), (0.20, "Paid $0.20.")]:
        paid = wake.call("spend", {"amount": amount})
        assert paid.ok and paid.text == receipt
    assert not wake.call("spend", {"amount": 0.01}).ok
    wake.end()

result = fg_env.run(money, spend_in_parts)
assert result.ok and result.outputs == {"spent": 0.30, "units": 3}
money = fg_env.run({"name": "Money", "types": {}, "clock": {"rounds": 1}, "world": {"cash_cents": 16600},
    "metrics": {"cash": "$world.cash_cents / 100"},
    "outputs": {"cash": {"expr": "$world.cash_cents / 100", "format": "money"}}})
assert money.series["cash"] == [166] and money.outputs["cash"] == 166
assert "$166.00" in money.summary()
```

### Move money with one operation

Give each cash holder an entity with a `cash_cents` property. Use `transfer` for
payments and reverse its endpoints for refunds. The engine debits one holder and
credits the other atomically, rejecting a transfer when funds are insufficient.
Keep income and liabilities separate from cash. Where the brief has no external
cash sources or sinks, assert that total cash is conserved.

```python
import fg_env

accounts = {
    "name": "Account transfers", "clock": {"rounds": 1},
    "types": {"operator": {"agent": True}, "account": {"props": {"cash_cents": 0}}},
    "entities": {"manager": {"type": "operator"},
                 "payer": {"type": "account", "props": {"cash_cents": 1000}},
                 "payee": {"type": "account"}},
    "stages": [{"name": "payments", "max_actions": 5}],
    "actions": {"move": {"by": "operator", "params": {
        "source": {"type": "entity", "of": "account"},
        "target": {"type": "entity", "of": "account"},
        "cents": {"type": "int", "min": 0}}, "do": {
            "transfer": "cash_cents", "from": "$params.source",
            "to": "$params.target", "amount": "$params.cents"}}},
    "invariants": [{"expr": "$sum(account, $it.cash_cents) == 1000",
                    "why": "Transfers must conserve total cash."}],
    "outputs": {"payer": "$entity(payer).cash_cents / 100",
                "payee": "$entity(payee).cash_cents / 100"}
}
def payments(wake):
    assert wake.call("move", {"source": "payer", "target": "payee", "cents": 700}).ok
    assert not wake.call("move", {"source": "payer", "target": "payee", "cents": 400}).ok
    assert wake.call("move", {"source": "payee", "target": "payer", "cents": 200}).ok
    assert wake.call("move", {"source": "payer", "target": "payee", "cents": 0}).ok
    wake.end()

result = fg_env.run(accounts, payments)
assert result.ok and result.outputs == {"payer": 5, "payee": 5}
```

## Nesting and reuse

Use relations for ownership and dependencies: a company has branches, a branch serves accounts, and an account owns orders. Iterate over actual entities or input rows rather than a fixed count. Use `defs` for repeated expressions, `blocks` for repeated effects, and imports for reusable contract fragments. Start with a single file and split only when it helps maintenance.

## Three example briefs

**Inventory under promotion.** Two product tiers share warehouse space. A promotion brings demand forward, some customers substitute when stock runs out, and suppliers have correlated delays. Measure margin, stockouts, working capital and leftover stock. Include an arm without the promotion.

**Enterprise sales and onboarding.** Sales teams compete for accounts, discount requests need approval, implementations share specialist capacity, and late delivery increases churn. Track booked value separately from activated revenue and cash collection. Test whether selling faster overwhelms onboarding.

**Local-service campaign.** Several channels reach overlapping households; jobs require different technician skills, customers may cancel, and successful visits can generate referrals. Track unique reach, qualified demand, completed jobs and contribution after campaign spend. Do not count impressions as unique customers.

These are compositions of general rules, not special engines. Realism comes from the explicit causal model and supporting data.

## Avoid false precision

More entities or equations do not automatically improve a forecast. Prefer a simple baseline first, add mechanisms that explain a material effect, and check the improvement on held-out cases. If conversion rates are assumed, label results conditional on those rates and show sensitivity.
