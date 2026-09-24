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

- The `inspect` tool is offered only when a type sets `inspect: true` (or an expression over `$viewer` and `$it`) on entities agents may look at; inspect then shows every property that is not private. Without one it is not offered — its only choice would be the agent itself — so show an agent its own state in a view.
- In a simultaneous stage, others learn that an agent acted, not what it chose (`Ada: bid.`). Write an `announce` template to reveal the choice.
- A `private` property is hidden from every agent but its owner. An agent owns its own properties. The world's and any other entity's private properties have no owner, so they are hidden from every agent — unless a view's or an entity choice's `where` picks the items by the reader and one of their properties (`"where": "$it.owner == $actor.id"`, `"$it.side == $actor.side"`): the reader then owns the items it picks and may be shown or offered them, private properties and all. Picking by id (`$it.id != $actor.id`) names no owner, and no `where` shows one agent another agent's private property. Reading a hidden value in anything the SDK works out for one agent — its views (their `where`, sort keys and attachments too), tool choices, bounds and defaults, outcome text, refusals, brief, policy, and any def those call — is an error at run time, however the read is spelled; so is a stage `who` that reads one while the stage's actions are announced (everyone learns whom it woke). Game logic (`do`, events, triggers, `end`, metrics, outputs) reads everything, so reveal what an agent may learn by working it out there: `"do": ["$seen = $params.target.role"], "outcome": "{$params.target.name} is {$seen}."`. A `when` that reads a hidden value does not hide the tool: it stays listed, and a call is refused when the `when` fails.
- Text sent to several agents — an `announce`, an event's or trigger's `say`, an emit's `say` without a lone `to`, the fields of an entry `post`ed to a record every agent reads — may read no private property, not even the actor's own. Reveal it the same way: `"do": ["$shown = $actor.card"], "announce": "{$actor.name} shows {$shown}."`. The default announcement leaves out arguments the action writes into a private property, and the engine's own refusals (a transfer that does not fit, a bound) never show a hidden value or whose it was.
- An entity that is not an agent reaches agents only through what the contract shows (inspect is off unless its type sets it), so a property the views already gate — an exhibit's text shown once it is offered — needs no `private`; mark `private` what no agent may see but the owner a `where` names.
- An entity's type is public: inspect names it. Keep a secret role in a private property (the roles mechanism deals them out), not in a subtype.
- A view or an entity choice that reads a hidden value without a `where` that names the owner, and outcome text, bounds, announcements or a `who` that read one directly are check errors; the check warns when a view, a tool or a requirement reads one some other way. Its smoke play includes an agent that tries to read each hidden number out through the refusals of its actions and reports any it could.
- Text a participant writes is shown «quoted» on one line, so it cannot pass for a heading or a new section of another agent's update.
- A refusal tells the actor something. Any refusal whose rules read a value hidden from the actor — a `when` (with or without `$params`), a `fail` or an error in `do`, a transfer from someone's private funds — spends the action, so a hidden value costs a guess per try; any other refusal (a taken cell, bad arguments) costs nothing. A `when` that reads a hidden value is a check warning: the actor cannot know when the action is allowed. If the rule itself should stay secret, accept the action and settle the hidden part in `do`.
- Arguments nested more than 32 lists or objects deep, or text holding a lone surrogate character, are refused as an invalid call before anything reads them.
- A simultaneous stage announces each sealed choice to everyone by its action's name as it commits. When the choice is secret (a ballot of `vote_yes` and `vote_no`), give the actions `private: true` and announce only the outcome; check warns otherwise.

A host that loads the environment can inspect its full state. Participant visibility is not a security boundary against the host itself.

## Determinism

Seeded engine randomness supports repeatable runs. Each event, trigger, stage hook and agent's action draws from its own stream, keyed by where it is written in the contract and the round. With the same seed and contract, the world's draws are the same whatever the participants choose, so policies and arms compare on the same luck; an `each` event and a stage's `who` draw per entity, so one entity coming or going never shifts another's luck.

Luck cannot be probed. A call refused before its `do` begins (bad arguments, a `when` requirement) costs nothing unless its rules read a hidden value, and those checks may not draw. A call refused after its `do` rolled luck (or after its rules read a hidden value) has been played: the attempt counts against `per_turn`, `per_round` and the turn's actions, and luck it drew is spent, so the next try rolls fresh luck. Submitting a sealed choice in a simultaneous stage checks only what does not depend on luck (a refusal found then that read a hidden value spends the choice too); its luck is rolled when the choices commit. In an atomic turn an action that draws settles the turn so far at once (checked against `valid` then), so nothing done later in the turn can undo its luck; if that check fails, the turn is undone and over. Legal-action lists and masks are worked out without drawing, so they never reveal an outcome. Reproducing a whole run also requires the same inputs, participant decisions and external responses. An LLM call may produce a different decision even with the same environment seed. Record exposures and replay calls when you need to reproduce those runs.
