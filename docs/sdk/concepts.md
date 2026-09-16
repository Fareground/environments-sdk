# Core concepts

## The model at a glance

| Concept | Contract section | Inventory example |
|---|---|---|
| Adjustable assumptions | `inputs` | Unit cost and weekly demand |
| Shared state | `world` | A shared warehouse capacity |
| Kinds of participants or objects | `types` | Retailer, supplier, product |
| Named objects | `entities` | A particular shop or product |
| Generated people or organizations | `population` | Customer segments drawn from rows |
| Decisions | `actions` | Place a replenishment order |
| Sequence of decisions | `stages` | Order, allocate, sell, settle |
| Autonomous changes | `events`, `triggers` | Arrivals, demand, expiration |
| Information a participant receives | `brief`, `views`, `records` | Own cash, visible prices, private bids |
| Measurements | `metrics`, `outputs` | Stock over time and lost sales |
| Non-negotiable rules | `invariants` | Inventory cannot be negative |

Types need not be agents. Products, orders, campaigns and contracts can be ordinary entities updated by actions and events.

## A round

Start events run, then each stage runs in order, then end events and metrics. Ending conditions can stop the simulation after a stage; action-level ending conditions can stop it immediately after an action commits. A round can mean a day, week, campaign cycle or negotiation round.

Use **sequential** stages when later participants should see earlier decisions. Use **simultaneous** stages when decisions should be made from the same view, such as sealed bids. In simultaneous stages, choices are submitted before they are committed; do not assume a successful submission is already a settled outcome.

See [stages](reference-stages.md), [events](reference-events.md) and [end conditions](reference-end.md) for exact fields.

## Actions and atomicity

An action declares who may use it, typed arguments, eligibility requirements, effects and outcome text. The engine rejects invalid arguments and refuses actions that violate requirements. If an action effect fails, its changes are rolled back. Handle the returned `ToolResult` rather than assuming a tool call succeeded.

Atomic actions are useful for transferring money and goods together. Atomicity does not fix an incorrect business equation: an author must still test conservation, allocation order and units.

## State, expressions and effects

Properties hold values. Expressions read those values; effects change them. For example, `$actor.cash >= $params.qty * $inputs.unit_cost` tests affordability. `$actor.stock += $params.qty` changes stock.

`$actor` is the acting entity, `$params` contains action arguments, and `$it` is the current item in a collection operation. Available roots depend on context. [Expression reference](reference-expressions.md) lists the exact rules.

## Information and privacy

A participant receives a static brief, an update and typed tools. Private properties and record visibility determine what is available to each viewer. Preview every role, and inspect recorded exposures when information boundaries matter. Keep secret values out of public announcement templates.

A host that loads the environment can inspect its full state. Participant visibility is not a security boundary against the host itself.

## Determinism

Seeded engine randomness supports repeatable runs. Reproduction also requires the same contract, inputs, participant decisions and external responses. An LLM call may produce a different decision even with the same environment seed. Record exposures and replay calls when you need to reproduce those runs.
