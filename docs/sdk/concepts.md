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

Submitted choices commit one agent after another. Without an `order`, that order is drawn at random from the seed each time, so no seat always wins a contested item. A choice is tried when it is submitted, after the agent's own earlier choices in the stage, so two purchases cannot spend the same money. When choices must be resolved together, such as a sealed-bid auction or a pro-rata allocation, let the action only record the choice and resolve all of them in the stage's `on_exit`:

```json
"actions": {"bid": {"by": "bidder", "private": true, "params": {"amount": "number"},
                    "do": ["$actor.bid = $params.amount"]}},
"stages": [{"name": "bid", "turns": "simultaneous", "on_enter": [{"each": "bidder", "do": ["$it.bid = 0"]}],
            "on_exit": ["$top = $max(bidder, $it.bid)",
                        {"if": "$top > 0", "then": ["$winner = $choice($filter(bidder, $it.bid == $top))",
                                                    "$winner.won = $winner.won + 1"]}]}]
```

See [stages](reference-stages.md), [events](reference-events.md) and [end conditions](reference-end.md) for exact fields.

## Actions and atomicity

An action declares who may use it, typed arguments, eligibility requirements, effects and outcome text. The engine rejects invalid arguments and refuses actions that violate requirements. If an action effect fails, its changes are rolled back. Handle the returned `ToolResult` rather than assuming a tool call succeeded.

Atomic actions are useful for transferring money and goods together. Atomicity does not fix an incorrect business equation: an author must still test conservation, allocation order and units.

## State, expressions and effects

Properties hold values. Expressions read those values; effects change them. For example, `$actor.cash >= $params.qty * $inputs.unit_cost` tests affordability. `$actor.stock += $params.qty` changes stock.

`$actor` is the acting entity, `$params` contains action arguments, and `$it` is the current item in a collection operation. Available roots depend on context. [Expression reference](reference-expressions.md) lists the exact rules.

## Information and privacy

A participant receives a static brief, an update and typed tools. Private properties and record visibility determine what is available to each viewer. Preview every role, and inspect recorded exposures when information boundaries matter. Keep secret values out of public announcement templates.

The defaults keep hidden information hidden:

- The `inspect` tool shows an agent only its own entity. Set `inspect: true` (or an expression over `$viewer` and `$it`) on a type whose entities agents may look at; inspect then shows every property that is not private.
- In a simultaneous stage, others learn that an agent acted, not what it chose (`Ada: bid.`). Write an `announce` template to reveal the choice.
- An agent's private property is shown only to that agent. Reading another agent's while the SDK works something out for one agent — its views and their sort keys, tool choices and bounds, outcome text, brief, policy, and any def those call — is an error at run time, however the read is spelled. Game logic (`do`, events, triggers, `end`, metrics, outputs) reads everything, so reveal what an agent may learn by working it out there: `"do": ["$seen = $params.target.role"], "outcome": "{$params.target.name} is {$seen}."`. A `when` that reads another agent's private property does not hide the tool: it stays listed, and a call is refused when the `when` fails. A private property of an entity that is not an agent is hidden from inspect, and the contract's views decide who sees it.
- A view that lists every entity with a private property and no `where`, and an entity choice whose `where` reads another agent's private property, are check errors; the check warns when a view or a tool reads one some other way.
- Text a participant writes is shown «quoted» on one line, so it cannot pass for a heading or a new section of another agent's update.
- A refusal tells the actor something. A `when` or `fail` that reads hidden state reveals it through the refusal; if the rule itself should stay secret, accept the action and settle the hidden part in `do`.

A host that loads the environment can inspect its full state. Participant visibility is not a security boundary against the host itself.

## Determinism

Seeded engine randomness supports repeatable runs. Each event, trigger, stage hook and agent's action draws from its own stream, keyed by where it is written in the contract and the round. With the same seed and contract, the world's draws are the same whatever the participants choose, so policies and arms compare on the same luck. A refused action gives its draws back, so a retry in the same round rolls the same luck. Reproducing a whole run also requires the same inputs, participant decisions and external responses. An LLM call may produce a different decision even with the same environment seed. Record exposures and replay calls when you need to reproduce those runs.
