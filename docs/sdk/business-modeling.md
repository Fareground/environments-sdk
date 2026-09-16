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
| Campaign exposure and diffusion | [Channels](reference-social-channels.md), [diffusion](reference-social-diffusion.md), relations | Audience overlap, repeated exposure, conversion lag |
| Queued work and scarce staff | [Queues](reference-operations-queue.md), actions, events | Priority, skills, abandonment and service duration |
| External judgments or data | [Feeds](reference-feeds.md), host mechanisms | Host response validation, availability and replay |

## Nesting and reuse

Use relations for ownership and dependencies: a company has branches, a branch serves accounts, and an account owns orders. Iterate over actual entities or input rows rather than a fixed count. Use `defs` for repeated expressions, `blocks` for repeated effects, and imports for reusable contract fragments. Start with a single file and split only when it helps maintenance.

## Three example briefs

**Inventory under promotion.** Two product tiers share warehouse space. A promotion brings demand forward, some customers substitute when stock runs out, and suppliers have correlated delays. Measure margin, stockouts, working capital and leftover stock. Include an arm without the promotion.

**Enterprise sales and onboarding.** Sales teams compete for accounts, discount requests need approval, implementations share specialist capacity, and late delivery increases churn. Track booked value separately from activated revenue and cash collection. Test whether selling faster overwhelms onboarding.

**Local-service campaign.** Several channels reach overlapping households; jobs require different technician skills, customers may cancel, and successful visits can generate referrals. Track unique reach, qualified demand, completed jobs and contribution after campaign spend. Do not count impressions as unique customers.

These are compositions of general rules, not special engines. Realism comes from the explicit causal model and supporting data.

## Avoid false precision

More entities or equations do not automatically improve a forecast. Prefer a simple baseline first, add mechanisms that explain a material effect, and check the improvement on held-out cases. If conversion rates are assumed, label results conditional on those rates and show sensitivity.
